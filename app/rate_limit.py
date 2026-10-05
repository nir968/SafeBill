import logging
import time

from fastapi import Depends, HTTPException, Request
from redis.exceptions import RedisError

from app.auth import get_authorized_user_id

logger = logging.getLogger("safebill.rate_limit")

SCAN_LIMIT_PER_MINUTE = 5
_WINDOW_SECONDS = 60


async def limit_bill_scans(
    request: Request, user_id: int = Depends(get_authorized_user_id)
) -> None:
    """Fixed-window limit of SCAN_LIMIT_PER_MINUTE uploads per user per
    calendar minute, shared across every API process/replica via Redis -
    unlike sanitizer.py's _extraction_semaphore, which only bounds
    concurrency within one process.

    Keyed on the authorized user id, not client IP: the Telegram bot sends
    every user's uploads from the same container IP, so an IP key would
    throttle all bot users together. Depends on get_authorized_user_id
    directly, which FastAPI resolves once per request and shares with the
    route's own identical dependency, so auth isn't run twice.

    Fails open on a Redis error - the semaphore still caps concurrency, and
    blocking every upload because the limiter's store is down would be a
    worse outcome than briefly not enforcing a per-user quota."""
    window = int(time.time() // _WINDOW_SECONDS)
    key = f"ratelimit:scan:{user_id}:{window}"
    try:
        # INCR + EXPIRE in one MULTI/EXEC, so a crash between the two can't
        # leave a counter key with no TTL that never resets.
        async with request.app.state.redis.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, _WINDOW_SECONDS)
            count, _ = await pipe.execute()
    except RedisError as exc:
        logger.warning("scan rate limit check failed for user %s (%s) - allowing", user_id, exc)
        return

    if count > SCAN_LIMIT_PER_MINUTE:
        retry_after = _WINDOW_SECONDS - int(time.time()) % _WINDOW_SECONDS
        raise HTTPException(
            429,
            "יותר מדי סריקות - נסו שוב בעוד דקה",
            headers={"Retry-After": str(retry_after)},
        )
