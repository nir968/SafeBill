"""
Signup/login/logout for the email+password path added alongside the
existing Telegram-only User model - see models.py's User docstring for
why User.id is a Telegram id for bot-linked accounts and why that meant
telegram_chat_id had to become nullable for this to work at all.

get_current_user (app/auth.py) is built and ready, but NOT wired into any
existing route by this change - main.py, app/routers/web.py, and
app/routers/sanitizer.py all still take user_id as a plain, unauthenticated
query parameter. Two different stories apply when migrating them:

- app/routers/web.py's dashboard/chat routes are genuinely browser-facing.
  These are the natural candidates: swap `user_id: int` (query param) for
  `current_user: User = Depends(get_current_user)`, and use
  current_user.id wherever user_id is read now.
- main.py's bill CRUD routes and sanitizer.py's upload route are called by
  BOTH the dashboard's own JS and, more importantly, by telegram_bot.py as
  a trusted server-to-server caller that already has a Telegram-verified
  id and has no browser session/cookie to present. Switching those
  wholesale to cookie auth would break the bot outright. Those need either
  a separate service-level auth mechanism for the bot (e.g. a shared API
  key checked before/alongside get_current_user) or a deliberate,
  per-route decision to leave them on user_id - not something to migrate
  reflexively just because get_current_user now exists.
"""

import re
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    COOKIE_NAME,
    create_access_token,
    get_current_user,
    hash_password,
    require_internal_api_key,
    verify_password,
)
from app.config import settings
from app.db import get_db
from app.models import User

router = APIRouter(prefix="/auth", tags=["auth"])

_MIN_PASSWORD_LENGTH = 8


class SignupRequest(BaseModel):
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _generate_web_user_id() -> int:
    """Always negative - Telegram user ids are always positive (see
    models.py's User.id note), so the two id spaces can never collide by
    construction, with no need to track or coordinate against Telegram's
    own (growing) id range. 62 bits of cryptographic randomness, not a
    counter - signup has no natural sequence to draw from the way
    get_or_create_user's Telegram-supplied id does."""
    return -secrets.randbits(62)


def _validate_password_strength(password: str) -> None:
    """Raises on the first unmet requirement, matching this app's
    convention of surfacing exactly what's wrong rather than a generic
    "invalid password". signup.html shows all five requirements as a live
    checklist while typing and validates client-side before ever
    submitting, so this is mainly a backstop against a raw API call (or
    JS-disabled browser) bypassing that - not the primary place a real
    user is expected to encounter these messages one at a time."""
    if len(password) < _MIN_PASSWORD_LENGTH:
        raise HTTPException(400, f"הסיסמה חייבת להכיל לפחות {_MIN_PASSWORD_LENGTH} תווים")
    if not re.search(r"[A-Z]", password):
        raise HTTPException(400, "הסיסמה חייבת להכיל לפחות אות גדולה אחת (A-Z)")
    if not re.search(r"[a-z]", password):
        raise HTTPException(400, "הסיסמה חייבת להכיל לפחות אות קטנה אחת (a-z)")
    if not re.search(r"[0-9]", password):
        raise HTTPException(400, "הסיסמה חייבת להכיל לפחות ספרה אחת (0-9)")
    if not re.search(r"[^A-Za-z0-9]", password):
        raise HTTPException(400, "הסיסמה חייבת להכיל לפחות תו מיוחד אחד (למשל !@#$%)")


def _set_auth_cookie(response: Response, request: Request, user_id: int) -> None:
    """Shared by signup and login - both end a successful request the same
    way: a fresh session for user_id. Kept as one place so the cookie
    flags (httponly/secure/samesite/max_age) can't drift between the two
    call sites."""
    token = create_access_token(user_id)
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        # Inferred from the actual request instead of a static config
        # flag - correct locally over plain http (a Secure cookie would
        # otherwise silently never be stored by the browser at all) and
        # correct again the moment this is served over https, with
        # nothing to keep in sync between environments.
        secure=request.url.scheme == "https",
        samesite="lax",
        max_age=settings.access_token_expire_minutes * 60,
    )


@router.post("/signup", status_code=201)
async def signup(
    payload: SignupRequest, request: Request, response: Response, db: AsyncSession = Depends(get_db)
):
    email = _normalize_email(payload.email)
    if "@" not in email or len(email) < 3:
        raise HTTPException(400, "כתובת אימייל תקינה היא שדה חובה")
    _validate_password_strength(payload.password)

    existing = await db.execute(select(User.id).where(User.email == email).limit(1))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(409, "כבר קיים חשבון עם כתובת האימייל הזו")

    hashed = hash_password(payload.password)

    # Retried, not looped forever: a collision needs another signup to
    # have independently drawn the exact same 62-bit random value in the
    # same narrow window - already negligible, this loop just turns that
    # into a harmless retry instead of a 500. (It would also retry, less
    # usefully, on a genuine concurrent-duplicate-email race slipping past
    # the check above - rare enough on top of an already-rare case that
    # inspecting which constraint fired didn't seem worth the complexity;
    # worst case is a misleading "try again" instead of "email taken".)
    user = None
    for _ in range(5):
        user = User(id=_generate_web_user_id(), email=email, hashed_password=hashed)
        db.add(user)
        try:
            await db.commit()
            break
        except IntegrityError:
            await db.rollback()
            user = None
    if user is None:
        raise HTTPException(500, "לא הצלחנו ליצור את החשבון, נסו שוב")

    # Auto-login: signup.html shows a success message and redirects
    # straight to /dashboard rather than back to /login, so the session
    # needs to already exist by the time that redirect happens - a second,
    # separate call to /login right after would work too, but adds a
    # network round-trip and a real (if rare) failure mode where the
    # account gets created but the follow-up login call fails, leaving a
    # confused user looking logged-out on a page that told them otherwise.
    _set_auth_cookie(response, request, user.id)

    return {"id": user.id, "email": user.email}


@router.post("/login")
async def login(
    payload: LoginRequest, request: Request, response: Response, db: AsyncSession = Depends(get_db)
):
    email = _normalize_email(payload.email)
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    # Same generic message whether the email doesn't exist or the password
    # is wrong - distinguishing the two would let a caller enumerate which
    # emails have accounts. user.hashed_password is None for a
    # Telegram-only account that never set a password; verify_password
    # would never match None anyway, but the check makes the reason
    # explicit rather than relying on that incidentally.
    if user is None or user.hashed_password is None or not verify_password(
        payload.password, user.hashed_password
    ):
        raise HTTPException(401, "אימייל או סיסמה שגויים")

    _set_auth_cookie(response, request, user.id)
    return {"id": user.id, "email": user.email}


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(COOKIE_NAME)
    return {"status": "logged out"}


# --- Telegram account linking -------------------------------------------
#
# Non-destructive by design: linking never renumbers a User row's id or
# moves Bill rows between accounts. It only sets the web-native account's
# telegram_chat_id to the real Telegram id - the same column worker.py
# already reads to decide where to send alerts (see analyze_bill /
# enforce_due_alerts in app/worker.py), so outbound alerts start working
# for a newly-linked account for free. Inbound requests from the bot are
# resolved back to this same web-native account by
# app/auth.py::_resolve_linked_user_id, called from every bot-authenticated
# request via get_authorized_user_id. One real limitation this leaves: if
# the Telegram account already had its own bill history *before* linking
# (someone used the bot first, then signed up on the site and linked the
# same Telegram account), those older bills stay under the original
# Telegram-id row rather than being merged in - a full merge would mean
# renumbering/re-parenting live data, which is a lot more risk than this
# feature needs to take on for a first version.

_LINK_TOKEN_TTL_SECONDS = 600  # 10 minutes - long enough to switch apps and tap Start
_LINK_TOKEN_REDIS_PREFIX = "tg_link:"


class TelegramLinkTokenResponse(BaseModel):
    deep_link: str


@router.post("/telegram/link-token", response_model=TelegramLinkTokenResponse)
async def create_telegram_link_token(
    request: Request, current_user: User = Depends(get_current_user)
):
    """
    Cookie-authenticated (the dashboard's own "Connect Telegram" button) -
    mints a single-use, short-lived token identifying *this* session's
    user, and hands back a t.me deep link carrying it as the /start
    payload. Redis (already available as request.app.state.redis - the
    same connection app/worker.py's arq jobs use, since ArqRedis is a
    thin subclass of a real redis.asyncio.Redis client, not a narrower
    wrapper) is a natural fit for a value that only needs to survive a
    few minutes and be readable exactly once.
    """
    if not settings.telegram_bot_username:
        raise HTTPException(503, "חיבור לטלגרם אינו מוגדר כרגע")

    token = secrets.token_urlsafe(24)
    await request.app.state.redis.set(
        f"{_LINK_TOKEN_REDIS_PREFIX}{token}", str(current_user.id), ex=_LINK_TOKEN_TTL_SECONDS
    )
    return TelegramLinkTokenResponse(
        deep_link=f"https://t.me/{settings.telegram_bot_username}?start={token}"
    )


class TelegramLinkRequest(BaseModel):
    token: str
    telegram_id: int


class TelegramLinkResponse(BaseModel):
    email: str | None


@router.post("/telegram/link", response_model=TelegramLinkResponse)
async def redeem_telegram_link_token(
    request: Request, payload: TelegramLinkRequest, db: AsyncSession = Depends(get_db)
):
    """
    Called by telegram_bot.py, never by the browser - authenticated via
    the shared internal API key (require_internal_api_key), not a user
    session, since the caller here is the bot acting on behalf of
    whoever just tapped "Start" on the deep link, before any linkage
    exists yet to authenticate *as*.
    """
    await require_internal_api_key(request)

    redis = request.app.state.redis
    key = f"{_LINK_TOKEN_REDIS_PREFIX}{payload.token}"
    raw_user_id = await redis.get(key)
    if raw_user_id is None:
        raise HTTPException(400, "קישור החיבור פג תוקף או שאינו תקין - נסו שוב מהאתר")
    # Deleted immediately, before doing anything else - single-use, so a
    # replayed or reused token can't silently re-link a third account.
    await redis.delete(key)

    web_user_id = int(raw_user_id)
    user = await db.get(User, web_user_id)
    if user is None:
        raise HTTPException(404, "החשבון המקושר לא נמצא")

    telegram_id_str = str(payload.telegram_id)

    # Re-linking / switching accounts: clear this Telegram id from any
    # OTHER web-native row that previously claimed it, so at most one row
    # ever has it as a reassignable link - otherwise
    # _resolve_linked_user_id's lookup would become ambiguous. Never
    # touches the row where id == telegram_id itself (if any) - that's
    # just that bot-native row's own default self-referential value (see
    # get_or_create_user in app/crud.py), not a link to take away.
    await db.execute(
        update(User)
        .where(
            User.telegram_chat_id == telegram_id_str,
            User.id != payload.telegram_id,
            User.id != web_user_id,
        )
        .values(telegram_chat_id=None)
    )

    user.telegram_chat_id = telegram_id_str
    await db.commit()

    return TelegramLinkResponse(email=user.email)
