import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.crud import get_or_create_user
from app.db import get_db
from app.models import Bill
from app.vision import BillValidationError, VisionProviderError, vision_client

logger = logging.getLogger("safebill.sanitizer")

router = APIRouter(prefix="/bills", tags=["sanitizer"])

# Bounds total concurrent VLM calls in this process — the backpressure valve
# that keeps a traffic burst from turning into N simultaneous image buffers.
# NOTE: this limit is per-process. If you scale the API to multiple uvicorn
# workers or replicas, the effective ceiling is N_replicas * this value —
# either divide accordingly or move to a distributed limiter (e.g. a Redis
# token bucket) before scaling out.
_extraction_semaphore = asyncio.Semaphore(settings.max_concurrent_extractions)


@router.post("/upload", status_code=202)
async def upload_bill(request: Request, user_id: int, db: AsyncSession = Depends(get_db)):
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
    except asyncio.TimeoutError:
        raise HTTPException(504, "Vision extraction timed out")
    except BillValidationError as exc:
        raise HTTPException(422, str(exc))
    except VisionProviderError as exc:
        raise HTTPException(502, str(exc))
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

    # Hand off to the Analyst Agent and return immediately — the caller
    # never blocks on DB-history analysis, only on the vision call above.
    redis = request.app.state.redis
    await redis.enqueue_job("analyze_bill", bill.id)

    return {"bill_id": bill.id, "status": "accepted", "category": bill.category}
