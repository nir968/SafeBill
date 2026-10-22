import asyncio
import base64
import io
import json
import logging
from abc import ABC, abstractmethod
from datetime import date
from decimal import Decimal
from typing import Literal
import anthropic
import pypdfium2 as pdfium
from google import genai
from google.genai import errors as genai_errors
# The Interactions client raises its own exception hierarchy, separate from
# genai_errors - safe to import from this private module only because
# google-genai is pinned exactly in requirements.txt.
from google.genai._interactions import RateLimitError as GeminiRateLimitError
from pydantic import BaseModel, ValidationError
from app.config import settings



logger = logging.getLogger("safebill.vision")


class VisionExtractionError(Exception):
    """Base class for Sanitizer extraction failures."""


class BillValidationError(VisionExtractionError):
    """The image is not a legible bill: wrong document type, too degraded to
    read, or (Gemini) a provider response that didn't parse into our
    schema at all."""


class VisionProviderError(VisionExtractionError):
    """The vision provider call itself failed (network, auth, rate limit, server error)."""


class VisionProviderOverloadedError(VisionProviderError):
    """The provider is rate-limiting us (HTTP 429) - temporary; retry in a minute."""


_IMAGE_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


def _sniff_image_media_type(image_bytes: bytes) -> str:
    """Fast, cheap rejection of non-image uploads before ever paying for a
    model call. Distinct from `document_type` below, which is the model's
    job: "this is a JPEG, but it's a photo of a cat, not a bill." """
    if image_bytes[:12].startswith(b"RIFF") and image_bytes[8:12] == b"WEBP":
        return "image/webp"
    for signature, media_type in _IMAGE_SIGNATURES:
        if image_bytes.startswith(signature):
            return media_type
    raise BillValidationError("Unrecognized image format - expected JPEG, PNG, GIF, or WebP")


_PDF_SIGNATURE = b"%PDF-"


def _is_pdf(data: bytes) -> bool:
    return data.startswith(_PDF_SIGNATURE)


def _rasterize_pdf_first_page(pdf_bytes: bytes) -> bytes:
    """Renders page 1 of a PDF to PNG bytes. Bills are almost always a
    single page, so only the first page is processed - a multi-page PDF
    silently has everything past page 1 ignored, not an error.

    This is a CPU-bound, blocking call (PDFium is a native renderer, not
    I/O) - always run it via asyncio.to_thread, never awaited inline, or
    it blocks the event loop for every other in-flight request for the
    duration of the render.

    Converting here, at the VisionSanitizerService layer, rather than
    inside each adapter, is what makes PDF support work uniformly across
    every VisionProvider backend without touching any of them - by the
    time any adapter sees these bytes, it's always a plain PNG image
    exactly like a photo upload.
    """
    document = None
    page = None
    try:
        document = pdfium.PdfDocument(pdf_bytes)
        page = document.get_page(0)
        bitmap = page.render(scale=200 / 72)  # ~200 DPI
        pil_image = bitmap.to_pil()
        buffer = io.BytesIO()
        pil_image.save(buffer, format="PNG")
        return buffer.getvalue()
    except (pdfium.PdfiumError, IndexError, OSError) as exc:
        raise BillValidationError(
            "Could not read the uploaded PDF - it may be corrupt, encrypted, or empty"
        ) from exc
    finally:
        if page is not None:
            page.close()
        if document is not None:
            document.close()


_CATEGORY_ELECTRICITY = "חשמל"
_CATEGORY_WATER = "מים"
_CATEGORY_ARNONA_TAXES = "ארנונה ומיסים"
_CATEGORY_COMMUNICATION = "תקשורת ואינטרנט"
_CATEGORY_GAS = "גז"
_CATEGORY_INSURANCE = "ביטוחים"
_CATEGORY_CAR_TRANSPORT = "רכב ותחבורה"
# Public (no leading underscore) - app/routers/web.py's dashboard route
# imports this exact value to split a user's bills into the "tickets" and
# "regular" tables, so that split can't drift from what the vision
# pipeline (or a manual edit via PATCH /bills/{id}) actually assigns.
TICKET_CATEGORY = "קנסות ודוחות"
_CATEGORY_SERVICES_SUBSCRIPTIONS = "שירותים ומנויים"
_CATEGORY_GENERAL = "הוצאה כללית"

# (label, English gloss, example items) for each allowed category, in the
# exact order presented to the model below - single source of truth for
# both the classification prompt and ALLOWED_CATEGORIES, so a category
# added or reworded here can't drift out of sync between the two the way
# two independently hand-maintained lists eventually would.
_CATEGORIES: list[tuple[str, str, str]] = [
    (_CATEGORY_ELECTRICITY, "Electricity", "electric utility bills"),
    (_CATEGORY_WATER, "Water", "water utility bills"),
    (
        _CATEGORY_ARNONA_TAXES,
        "Arnona & Taxes",
        "municipal property tax (arnona), other government/municipal taxes",
    ),
    (
        _CATEGORY_COMMUNICATION,
        "Communication & Internet",
        "mobile phone, landline, internet, TV/cable subscriptions",
    ),
    (_CATEGORY_GAS, "Gas", "gas utility bills"),
    (
        _CATEGORY_INSURANCE,
        "Insurances",
        "insurance premiums of any kind - health, car, home, life, travel",
    ),
    (
        _CATEGORY_CAR_TRANSPORT,
        "Car & Transportation",
        "fuel, parking fees, car maintenance or repairs, public transit, tolls",
    ),
    (
        TICKET_CATEGORY,
        "Fines & Tickets",
        "parking tickets, speeding or traffic tickets, police reports, other "
        "government-issued penalties",
    ),
    (
        _CATEGORY_SERVICES_SUBSCRIPTIONS,
        "Services & Subscriptions",
        "streaming/software subscriptions, gym memberships, professional "
        "services, rent, loan or mortgage payments",
    ),
    (
        _CATEGORY_GENERAL,
        "General Expense",
        "supermarkets, retail stores, restaurants, cafes, one-off purchases, "
        "or anything that genuinely doesn't fit the categories above",
    ),
]

ALLOWED_CATEGORIES = {label for label, _gloss, _examples in _CATEGORIES}

_CATEGORY_RULES = "\n".join(
    f'  - "{label}" ({gloss}): {examples}.' for label, gloss, examples in _CATEGORIES
)


_SYSTEM_PROMPT = f"""You are an expert financial-document data-extraction system. You are shown a single image and must extract structured bill data from it - nothing else.

Rules:
- Extract only what is visible in the image. Never guess, infer, or fabricate a value.
- "institution" is the biller issuing the bill (the utility, bank, or service provider) - not the recipient.
- "amount" is the total amount due, as a plain number with no currency symbol or thousands separator.
- "currency" is the ISO 4217 three-letter currency code for the amount (e.g. "ILS", "USD", "EUR", "GBP"). Infer it from any symbol or code shown on the bill: ₪ -> ILS, $ -> USD, € -> EUR, £ -> GBP. If a symbol could mean more than one currency and no other context narrows it down, use the most common association (e.g. plain "$" -> USD). If no currency is shown anywhere on the bill, use "ILS".
- "category" classifies what the bill is for, as exactly one of these ten Hebrew labels:
{_CATEGORY_RULES}
  Use "{_CATEGORY_GENERAL}" for a genuine one-off or miscellaneous expense that isn't covered by the other nine - it is not a "none of the above" fallback for uncertainty. Return null only when you are not confident enough to classify the bill at all.
- "due_date" is the payment due date in YYYY-MM-DD format. If no due date is visible, return null - never invent one or substitute today's date.
- Set "document_type" to "bill" only when the image clearly shows a bill, invoice, or statement with an amount owed.
- Set "document_type" to "not_a_bill" when the image shows something else entirely.
- Set "document_type" to "illegible" when the image is a bill but too blurry, dark, cropped, or degraded to read reliably.
- When document_type is not "bill", set institution, amount, currency, category, and due_date to null."""


class ExtractedBill(BaseModel):
    institution: str
    amount: Decimal
    currency: str
    category: str | None
    due_date: date | None


class _BillExtractionResult(BaseModel):
    """Wire schema every provider must produce. `document_type` is the
    discriminator that lets the shared post-processing step fail fast on
    bad images without any adapter needing to know about the others.

    `category` is typed as a plain `str | None`, not `Literal[*ALLOWED_
    CATEGORIES] | None`, even though only ten values are ever valid: a
    Literal would hard-fail the *entire* extraction over one bad category
    guess. VisionSanitizerService.extract_bill_fields validates it
    instead, so a categorization miss is a soft failure (category=None)
    and the bill still saves.
    """

    document_type: Literal["bill", "not_a_bill", "illegible"]
    institution: str | None
    amount: float | None
    currency: str | None
    category: str | None
    due_date: str | None


class VisionProvider(ABC):
    """Adapter interface. Every backend - hosted API or local model - must
    implement exactly this: image bytes in, our schema out. Nothing about
    the caller (VisionSanitizerService, and the router above it) knows or
    cares which concrete adapter is behind this."""

    @abstractmethod
    async def extract_bill(self, image_bytes: bytes) -> _BillExtractionResult: ...


class AnthropicVisionProvider(VisionProvider):
    """
    Hosted adapter. Calls the Claude Messages API with structured outputs,
    so the response is guaranteed to match `_BillExtractionResult` - no
    markdown fencing, no conversational text, no manual JSON parsing.

    Privacy note: this calls Anthropic's standard hosted API. The "no PII
    saved" claim in this project's design is about SafeBill's own
    infrastructure - it does NOT by itself cover the provider's data
    handling. For a real zero-retention claim, negotiate a
    zero-data-retention (ZDR) agreement with the provider.
    """

    _MODEL = "claude-sonnet-5"

    def __init__(self) -> None:
        # Zero-arg client: resolves ANTHROPIC_API_KEY (or an `ant auth
        # login` profile) from the environment. Never hardcode a key here.
        self._client = anthropic.AsyncAnthropic()

    async def extract_bill(self, image_bytes: bytes) -> _BillExtractionResult:
        media_type = _sniff_image_media_type(image_bytes)

        base64_data = base64.standard_b64encode(image_bytes).decode("utf-8")
        # Only needed to build the payload above - drop the raw copy before
        # the network round trip rather than holding it for the call's
        # duration.
        del image_bytes

        try:
            response = await self._client.messages.parse(
                model=self._MODEL,
                max_tokens=1024,
                system=_SYSTEM_PROMPT,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": media_type,
                                    "data": base64_data,
                                },
                            },
                            {"type": "text", "text": "Extract the bill fields from this image."},
                        ],
                    }
                ],
                output_format=_BillExtractionResult,
            )
        except anthropic.APIConnectionError as exc:
            raise VisionProviderError("Could not reach the vision provider") from exc
        except anthropic.RateLimitError as exc:
            raise VisionProviderError("Vision provider rate limit exceeded") from exc
        except anthropic.APIStatusError as exc:
            raise VisionProviderError(f"Vision provider returned HTTP {exc.status_code}") from exc
        finally:
            del base64_data

        return response.parsed_output


class GeminiVisionProvider(VisionProvider):
    """
    Hosted adapter for Google Gemini, via the Interactions API (GA since
    2026-06-22 on `google-genai>=2.3.0`).

    Third model/API surface change in a row here, for the record:
    `google-generativeai` -> `google-genai`'s `generate_content` ->
    `google-genai`'s `interactions.create`. `generate_content` still works
    but is now the legacy surface - new capabilities land on Interactions
    first, and Google has been pulling flash-tier models (2.5-flash
    included) out from under new projects ahead of their posted shutdown
    dates. The model ID is a Settings field, not a constant, specifically
    because of that pattern - expect to need to bump it again without
    a code change.

    Privacy note: same caveat as AnthropicVisionProvider - this is a
    hosted API call, not a zero-retention guarantee by itself.
    """

    def __init__(self, model: str) -> None:
        self._model = model
        # Resolves GEMINI_API_KEY (or GOOGLE_API_KEY) from the environment -
        # never hardcode a key here. http_options.timeout is set explicitly
        # (in ms) rather than left at the SDK's own default: the caller
        # (app/routers/sanitizer.py) already wraps extract_bill in
        # asyncio.wait_for(timeout=settings.vlm_timeout_seconds) as an
        # outer safety net, but that only cancels the call - it doesn't
        # make the underlying httpx/aiohttp request itself give up any
        # faster, and a slow-to-cancel transport can make the observed
        # wall-clock time noticeably exceed the configured timeout. Setting
        # the SDK's own timeout to the same value makes it the one that
        # actually fires first, so failures are fast and predictable
        # instead of depending on cancellation propagating cleanly through
        # the SDK's own client layers.
        self._client = genai.Client(
            http_options=genai.types.HttpOptions(
                timeout=int(settings.vlm_timeout_seconds * 1000)
            )
        )

    async def extract_bill(self, image_bytes: bytes) -> _BillExtractionResult:
        media_type = _sniff_image_media_type(image_bytes)

        base64_data = base64.standard_b64encode(image_bytes).decode("utf-8")
        # Only needed to build the payload above - drop the raw copy before
        # the network round trip rather than holding it for the call's
        # duration.
        del image_bytes

        try:
            interaction = await self._client.aio.interactions.create(
                model=self._model,
                input=[
                    {
                        "type": "text",
                        "text": f"{_SYSTEM_PROMPT}\n\nExtract the bill fields from this image.",
                    },
                    {"type": "image", "data": base64_data, "mime_type": media_type},
                ],
                response_format={
                    "type": "text",
                    "mime_type": "application/json",
                    "schema": _BillExtractionResult.model_json_schema(),
                },
            )
        except GeminiRateLimitError as exc:
            raise VisionProviderOverloadedError("Gemini is rate-limiting requests (429)") from exc
        except genai_errors.ClientError as exc:
            if exc.code == 429:
                raise VisionProviderOverloadedError("Gemini is rate-limiting requests (429)") from exc
            raise VisionProviderError(f"Gemini rejected the request: {exc}") from exc
        except genai_errors.ServerError as exc:
            raise VisionProviderError(f"Gemini server error: {exc}") from exc
        except genai_errors.APIError as exc:
            raise VisionProviderError(f"Gemini API error: {exc}") from exc
        finally:
            del base64_data

        try:
            payload = json.loads(interaction.output_text)
            return _BillExtractionResult.model_validate(payload)
        except (ValueError, json.JSONDecodeError, ValidationError) as exc:
            # ValueError also covers `.output_text` raising when the
            # interaction has no valid final output (e.g. safety-blocked
            # content) - treated the same as "didn't return a usable
            # extraction" rather than a provider-level failure.
            raise BillValidationError("Gemini did not return a parseable bill extraction") from exc


class VisionSanitizerService:
    """
    Provider-agnostic facade the rest of the app talks to (the Sanitizer
    router only ever imports `vision_client` from here). Owns the two
    pieces of behavior that must be identical no matter which adapter is
    selected: normalizing a PDF upload down to a plain image before any
    adapter ever sees it, and turning a raw `_BillExtractionResult` into
    either a validated `ExtractedBill` or a `BillValidationError`.
    """

    def __init__(self, provider: VisionProvider) -> None:
        self._provider = provider

    async def extract_bill_fields(self, image_bytes: bytes) -> ExtractedBill:
        if _is_pdf(image_bytes):
            image_bytes = await asyncio.to_thread(_rasterize_pdf_first_page, image_bytes)

        result = await self._provider.extract_bill(image_bytes)

        if result.document_type != "bill":
            raise BillValidationError(f"Image rejected by vision model: {result.document_type}")
        if result.institution is None or result.amount is None:
            raise BillValidationError("Vision model returned an incomplete bill extraction")

        # Defensive normalization, not just the prompt's "default to ILS"
        # instruction: models occasionally return a symbol instead of a
        # code, lowercase, or nothing at all despite the instruction, and
        # this is the one place that has to guarantee a valid-shaped value
        # regardless of which adapter produced it.
        currency = (result.currency or "").strip().upper()
        if len(currency) != 3 or not currency.isalpha():
            currency = "ILS"

        # Unlike currency, there's no safe universal default to fall back
        # to here - forcing an uncertain bill into one of the ten
        # buckets would systematically bias the data, so an out-of-set or
        # missing value just becomes None (a fully successful save, only
        # without a category) rather than a guess or a failure.
        category = result.category if result.category in ALLOWED_CATEGORIES else None

        return ExtractedBill(
            institution=result.institution,
            amount=Decimal(str(result.amount)),
            currency=currency,
            category=category,
            due_date=date.fromisoformat(result.due_date) if result.due_date else None,
        )


def get_vision_provider() -> VisionProvider:
    """Reads VISION_PROVIDER (via Settings) and returns the matching
    adapter. This is the only place that needs to know all adapters
    exist - swapping providers is a config change, not a code change."""
    if settings.vision_provider == "gemini":
        return GeminiVisionProvider(model=settings.gemini_model)
    return AnthropicVisionProvider()


vision_client = VisionSanitizerService(get_vision_provider())
