import asyncio
import logging
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_authorized_user_id
from app.cache import invalidate_user_stats
from app.config import settings
from app.crud import get_or_create_user
from app.db import get_db
from app.models import Bill
from app.rate_limit import limit_bill_scans
from app.vision import BillValidationError, VisionProviderOverloadedError, vision_client

logger = logging.getLogger("safebill.sanitizer")

router = APIRouter(prefix="/bills", tags=["sanitizer"])

# Placeholder institution name for a bill saved while vision extraction was
# unavailable (see the `except Exception` branch in upload_bill below). Not
# translated/configurable - it only ever appears in the dashboard until a
# human corrects it, unlike every other user-facing string in this file.
_UNEXTRACTED_INSTITUTION = "חשבון לא זוהה (נדרש עדכון ידני)"

# Bounds total concurrent VLM calls in this process — the backpressure valve
# that keeps a traffic burst from turning into N simultaneous image buffers.
# NOTE: this limit is per-process. If you scale the API to multiple uvicorn
# workers or replicas, the effective ceiling is N_replicas * this value.
# The per-user quota (limit_bill_scans, below) is Redis-backed and already
# shared across replicas; this semaphore is the separate global
# concurrency cap.
_extraction_semaphore = asyncio.Semaphore(settings.max_concurrent_extractions)


# The rate limit runs as a dependency, so it's enforced before the request
# body is read or Gemini is ever called.
@router.post("/upload", status_code=202, dependencies=[Depends(limit_bill_scans)])
async def upload_bill(
    request: Request,
    user_id: int = Depends(get_authorized_user_id),
    db: AsyncSession = Depends(get_db),
):
    """
    Hot path. Deliberately NOT using FastAPI's `UploadFile` / multipart form
    parsing — Starlette's multipart parser spools any part over ~1MB to a
    temp file on disk, which silently breaks the "purely in RAM" guarantee
    no matter how careful the code downstream is. Reading the raw request
    body ourselves keeps the image bytes in a single in-process buffer for
    its entire (short) lifetime, with no disk touched at any point.

    Caller sends the raw image bytes as the request body
    (Content-Type: application/octet-stream), not multipart/form-data.
    """
    content_length = request.headers.get("content-length")
    if content_length is not None and int(content_length) > settings.max_upload_bytes:
        raise HTTPException(413, "Image exceeds max upload size")

    buffer = bytearray()
    async for chunk in request.stream():
        buffer.extend(chunk)
        if len(buffer) > settings.max_upload_bytes:
            # Stop reading immediately rather than continuing to grow the
            # buffer for a request we're going to reject anyway.
            raise HTTPException(413, "Image exceeds max upload size")

    if not buffer:
        raise HTTPException(400, "Empty request body")

    try:
        async with _extraction_semaphore:
            extracted = await asyncio.wait_for(
                vision_client.extract_bill_fields(bytes(buffer)),
                timeout=settings.vlm_timeout_seconds,
            )
    except BillValidationError as exc:
        # Content problem, not an availability problem - the model looked
        # at this and said it isn't a usable bill. Rejected outright
        # rather than saved as a placeholder (see the `except Exception`
        # branch below), or every random photo a user uploads by mistake
        # would leave a garbage row in their bill list.
        raise HTTPException(422, str(exc))
    except VisionProviderOverloadedError:
        # Gemini is rate-limiting us: a short, temporary condition, so the
        # user is told to retry or add the bill manually instead of getting
        # a ₪0 placeholder row. 503, not 429 - this app's own per-user scan
        # limit (app/rate_limit.py) already uses 429 for "you scanned too often".
        logger.warning("upload_bill: Gemini rate-limited - returning 503, nothing saved")
        raise HTTPException(
            503,
            detail={
                "code": "ai_overloaded",
                "message": "שירות ה-AI עמוס כרגע. נסו שוב בעוד דקה או הוסיפו את החשבון ידנית.",
                "retry_after_seconds": 60,
            },
            headers={"Retry-After": "60"},
        )
    except Exception as exc:
        # Deliberately broad, not just asyncio.TimeoutError/VisionProviderError:
        # this also has to cover whatever the vision SDK's HTTP/retry
        # internals raise on a 429/quota-exhausted response that *isn't*
        # one of google.genai's documented exception types - "the AI is
        # temporarily unavailable" must never 500 the request and drop the
        # user's upload. The bill is still saved below, with placeholder
        # fields, so nothing is lost; it just needs a manual correction
        # once whatever's wrong with the AI service clears up.
        logger.warning(
            "upload_bill: vision extraction unavailable (%s: %s) - saving placeholder",
            type(exc).__name__,
            exc,
        )
        extracted = None
    finally:
        # Explicit destruction, not "let GC get to it eventually": zero the
        # buffer in place, then drop the reference. This is best-effort
        # within Python's memory model — `bytes(buffer)` above made one
        # immutable copy to hand to the vision client (most provider SDKs
        # require `bytes`, not `bytearray`), and that copy is owned by the
        # client for the duration of the call only. Belt-and-braces beyond
        # this (mlock'd/non-swappable memory, disabling swap entirely on
        # the host) is an OS-level hardening step, not something Python
        # can guarantee on its own.
        for i in range(len(buffer)):
            buffer[i] = 0
        del buffer

    # First-time uploader: the FK on bills.user_id needs a users row to
    # exist before this insert, and this may be this Telegram user's very
    # first interaction with the bot (no prior /last or /stats call).
    await get_or_create_user(db, user_id)

    if extracted is None:
        # Vision extraction was unavailable (see the `except Exception`
        # above) - save what we actually have, which is just "a bill was
        # uploaded", rather than lose it. No duplicate check (nothing real
        # to compare yet) and no Analyst Agent enqueue: analyze_bill folds
        # this bill's amount into InstitutionStats' rolling Welford
        # baseline, and doing that with a fake $0 placeholder would
        # corrupt anomaly detection for every future real bill from this
        # institution. Both resume naturally once the row has real data -
        # either a future re-upload succeeds and creates a proper bill, or
        # this one gets corrected by hand.
        bill = Bill(
            user_id=user_id,
            institution=_UNEXTRACTED_INSTITUTION,
            amount=Decimal("0"),
            status="extraction_failed",
        )
        db.add(bill)
        await db.commit()
        await db.refresh(bill)
        await invalidate_user_stats(request.app.state.redis, user_id)
        return {
            "bill_id": bill.id,
            "status": "accepted",
            "extracted": False,
            "institution": bill.institution,
            "amount": float(bill.amount),
            "currency": bill.currency,
            "category": bill.category,
            "due_date": None,
        }

    # Duplicate-upload guard: same user, same institution (case-insensitive
    # - two photos of the same physical bill can extract with different
    # casing), same amount, same due date. Skipped when due_date is null:
    # two distinct bills that both failed to extract a due date would
    # otherwise be false-positively flagged as duplicates of each other,
    # which would incorrectly block a real second bill from ever saving -
    # worse than the rare true duplicate this check would miss instead.
    if extracted.due_date is not None:
        existing = await db.execute(
            select(Bill.id)
            .where(
                Bill.user_id == user_id,
                func.lower(Bill.institution) == extracted.institution.lower(),
                Bill.amount == extracted.amount,
                Bill.due_date == extracted.due_date,
            )
            .limit(1)
        )
        if existing.scalar_one_or_none() is not None:
            raise HTTPException(409, "החשבונית כבר קיימת במערכת")

    bill = Bill(
        user_id=user_id,
        institution=extracted.institution,
        amount=extracted.amount,
        currency=extracted.currency,
        category=extracted.category,
        due_date=extracted.due_date,
        status="pending_analysis",
    )
    db.add(bill)
    await db.commit()
    await db.refresh(bill)

    redis = request.app.state.redis
    await invalidate_user_stats(redis, user_id)

    # Hand off to the Analyst Agent and return immediately — the caller
    # never blocks on DB-history analysis, only on the vision call above.
    await redis.enqueue_job("analyze_bill", bill.id)

    return {
        "bill_id": bill.id,
        "status": "accepted",
        "extracted": True,
        "institution": bill.institution,
        "amount": float(bill.amount),
        "currency": bill.currency,
        "category": bill.category,
        "due_date": bill.due_date.isoformat() if bill.due_date else None,
    }
