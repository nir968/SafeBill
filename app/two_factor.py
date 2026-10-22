"""
Two-factor authentication via a 6-digit code sent to the user's linked
Telegram chat. Applies automatically to every account with Telegram
linked, and only while a real TELEGRAM_BOT_TOKEN is configured - without
one no code could be delivered, so 2FA stays off rather than locking
anyone out.

Redis keys (all short-lived, nothing in Postgres):
  2fa:<user_id>             active code: {code, purpose, attempts}, 5 min
  2fa:cooldown:<user_id>    blocks re-sending a code for 60 s
  2fa:sent:<user_id>        codes sent this hour (max 5)
  2fa:elevated:<user_id>    verified recently - sensitive actions allowed, 10 min
  2fa:pending:<token>       password checked, waiting for the code (login), 5 min
  2fa:reset_ok:<token>      code verified for this password-reset link, 5 min

A code is bound to the purpose it was issued for ("login", "reset",
"action") and allows at most MAX_ATTEMPTS guesses, enforced with an
atomic counter so parallel guesses can't exceed it. Together with the
hourly send cap that bounds an attacker who has the password to 15
guesses an hour against a 1,000,000-code space - and every code they
trigger lands in the real user's Telegram as a warning.
"""

import logging
import re
import secrets

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app import telegram_client
from app.auth import COOKIE_NAME, decode_access_token
from app.config import settings
from app.db import get_db
from app.models import User

logger = logging.getLogger("safebill.two_factor")

OTP_TTL_SECONDS = 300
MAX_ATTEMPTS = 3
RESEND_COOLDOWN_SECONDS = 60
MAX_CODES_PER_HOUR = 5
ELEVATION_TTL_SECONDS = 600
PENDING_TTL_SECONDS = 300

PENDING_LOGIN_COOKIE = "safebill_2fa_pending"
# Only ever needed by /auth/2fa/*, so the browser doesn't send it anywhere else.
PENDING_LOGIN_COOKIE_PATH = "/auth"

_CODE_FORMAT = re.compile(r"^[0-9]{6}$")
_PURPOSE_LABELS = {"login": "התחברות", "reset": "איפוס סיסמה", "action": "ייצוא נתונים"}


def delivery_configured() -> bool:
    return bool(settings.telegram_bot_token) and settings.telegram_bot_token != "mock-token"


def is_required(user: User) -> bool:
    # Bot token checked first: with no way to deliver a code, 2FA is off.
    return delivery_configured() and user.telegram_chat_id is not None


def _text(value) -> str | None:
    return value.decode() if isinstance(value, bytes) else value


async def issue_code(redis, user: User, purpose: str) -> None:
    """Creates a fresh code for `purpose` (replacing any earlier one) and
    sends it to the user's Telegram. Raises 429 when re-sent too soon or
    too often, 502 if Telegram couldn't be reached."""
    cooldown_key = f"2fa:cooldown:{user.id}"
    if not await redis.set(cooldown_key, "1", ex=RESEND_COOLDOWN_SECONDS, nx=True):
        retry_after = max(await redis.ttl(cooldown_key), 1)
        raise HTTPException(
            429,
            f"כבר נשלח קוד - אפשר לבקש קוד חדש בעוד {retry_after} שניות",
            headers={"Retry-After": str(retry_after)},
        )

    sent_key = f"2fa:sent:{user.id}"
    async with redis.pipeline(transaction=True) as pipe:
        pipe.incr(sent_key)
        pipe.expire(sent_key, 3600, nx=True)
        sent_count, _ = await pipe.execute()
    if sent_count > MAX_CODES_PER_HOUR:
        raise HTTPException(
            429, "נשלחו יותר מדי קודים בשעה האחרונה - נסו שוב מאוחר יותר",
            headers={"Retry-After": "3600"},
        )

    code = f"{secrets.randbelow(1_000_000):06d}"
    code_key = f"2fa:{user.id}"
    async with redis.pipeline(transaction=True) as pipe:
        pipe.delete(code_key)
        pipe.hset(code_key, mapping={"code": code, "purpose": purpose, "attempts": 0})
        pipe.expire(code_key, OTP_TTL_SECONDS)
        await pipe.execute()

    text = (
        f"🔐 קוד האימות שלך ל-SafeBill ({_PURPOSE_LABELS[purpose]}): {code}\n"
        "הקוד תקף ל-5 דקות. אם לא ביקשת אותו, אל תמסור אותו לאף אחד ומומלץ להחליף סיסמה."
    )
    try:
        await telegram_client.send_message(user.telegram_chat_id, text)
    except telegram_client.TelegramDeliveryError as exc:
        # Undo, so the user can try again right away instead of waiting
        # out a cooldown for a code that never arrived.
        await redis.delete(code_key, cooldown_key)
        logger.warning("2FA code delivery failed for user %s: %s", user.id, exc)
        raise HTTPException(502, "לא הצלחנו לשלוח את הקוד לטלגרם - נסו שוב בעוד רגע")

    logger.info("2FA code issued for user %s (purpose=%s)", user.id, purpose)


async def verify_code(redis, user_id: int, purpose: str, code: str) -> None:
    """Returns normally if `code` is the active code for this user and
    purpose (and consumes it). Raises 400 for a wrong/expired code, 429
    once MAX_ATTEMPTS wrong guesses have burned the code."""
    code = code.strip()
    if not _CODE_FORMAT.match(code):
        # Malformed input isn't a real guess, so it doesn't use up an attempt.
        raise HTTPException(400, "הקוד צריך להכיל 6 ספרות")

    code_key = f"2fa:{user_id}"
    # Counting the attempt in the same transaction as reading the code is
    # what keeps parallel guesses from slipping past MAX_ATTEMPTS.
    async with redis.pipeline(transaction=True) as pipe:
        pipe.hincrby(code_key, "attempts", 1)
        pipe.hgetall(code_key)
        attempts, raw = await pipe.execute()
    stored = {_text(k): _text(v) for k, v in raw.items()}

    if "code" not in stored:
        # Expired or never issued (HINCRBY just recreated an empty key).
        await redis.delete(code_key)
        raise HTTPException(400, "הקוד פג תוקף - בקשו קוד חדש")

    if attempts <= MAX_ATTEMPTS and stored.get("purpose") == purpose and secrets.compare_digest(
        stored["code"], code
    ):
        await redis.delete(code_key)
        logger.info("2FA code verified for user %s (purpose=%s)", user_id, purpose)
        return

    if attempts >= MAX_ATTEMPTS:
        await redis.delete(code_key)
        logger.warning("2FA code burned after %s wrong attempts for user %s", attempts, user_id)
        raise HTTPException(429, "יותר מדי ניסיונות שגויים - בקשו קוד חדש")

    remaining = MAX_ATTEMPTS - attempts
    raise HTTPException(400, f"קוד שגוי - נותרו {remaining} ניסיונות")


async def grant_elevation(redis, user_id: int) -> None:
    await redis.set(f"2fa:elevated:{user_id}", "1", ex=ELEVATION_TTL_SECONDS)


async def start_pending_login(redis, request: Request, response: Response, user_id: int) -> None:
    """Password was correct but a code is still needed: remember who is
    logging in (httponly cookie -> Redis), without issuing a session yet."""
    token = secrets.token_urlsafe(32)
    await redis.set(f"2fa:pending:{token}", str(user_id), ex=PENDING_TTL_SECONDS)
    response.set_cookie(
        key=PENDING_LOGIN_COOKIE,
        value=token,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
        max_age=PENDING_TTL_SECONDS,
        path=PENDING_LOGIN_COOKIE_PATH,
    )


async def require_recent_two_factor(request: Request, db: AsyncSession = Depends(get_db)) -> None:
    """Route dependency for sensitive actions (CSV export). Applies only to
    browser sessions: the Telegram bot authenticates with the internal API
    key instead of a session cookie and is exempt - it already proves
    Telegram access. Unauthenticated requests pass through here and are
    rejected by the route's own auth dependency."""
    if not delivery_configured():
        return
    token = request.cookies.get(COOKIE_NAME)
    session_user_id = decode_access_token(token) if token else None
    if session_user_id is None:
        return
    user = await db.get(User, session_user_id)
    if user is None or not is_required(user):
        return
    if await request.app.state.redis.get(f"2fa:elevated:{user.id}") is not None:
        return
    raise HTTPException(
        403,
        detail={
            "code": "two_factor_required",
            "message": "נדרש אימות דו-שלבי - הזינו את הקוד שנשלח לטלגרם שלכם",
        },
    )
