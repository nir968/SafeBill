"""
Password hashing, JWT session tokens, and the FastAPI dependencies that
read them - app/routers/auth.py has the signup/login/logout endpoints
that actually issue these tokens.

get_current_user / get_current_user_for_page are cookie-only (browser
sessions). get_authorized_user_id is the one every route touching a
user's bills/data actually depends on: it resolves a *trusted* user_id
from either a browser session cookie OR - for telegram_bot.py, which has
no browser session to present - a shared internal API key, and rejects
outright (403) a caller who's logged in as one user but supplies a
different user_id. Every route in main.py, app/routers/sanitizer.py, and
POST /api/chat (app/routers/web.py) that used to take `user_id: int` as a
bare, unauthenticated query parameter now takes
`user_id: int = Depends(get_authorized_user_id)` instead - see that
function's own docstring for the exact rules.
"""

import secrets
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, Request
from passlib.context import CryptContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.models import User

# Header name, not a query param/body field - keeps the trust signal
# structurally separate from `user_id` itself (which the bot still sends
# as a normal query param, same as before) so there's no ambiguity about
# which one is "the credential" versus "the value being requested".
INTERNAL_API_KEY_HEADER = "X-Internal-Api-Key"

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
_JWT_ALGORITHM = "HS256"

# Public (no leading underscore) - app/routers/auth.py's login/logout
# endpoints set/clear this exact cookie, so the name can't drift between
# where it's issued and where it's read.
COOKIE_NAME = "access_token"


def hash_password(password: str) -> str:
    return _pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return _pwd_context.verify(plain_password, hashed_password)


def create_access_token(user_id: int) -> str:
    """`sub` is a User.id - a Telegram id for bot-linked accounts, or a
    synthetic negative id for web-only ones (see app/routers/
    auth.py::_generate_web_user_id). Always encoded as a string: JWT's
    registered `sub` claim is conventionally a string, and PyJWT doesn't
    itself constrain the type, so encoding an int here would work until
    some other JWT library/tool in the chain assumed the convention and
    didn't."""
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_expire_minutes)
    payload = {"sub": str(user_id), "exp": expire}
    return jwt.encode(payload, settings.secret_key, algorithm=_JWT_ALGORITHM)


def decode_access_token(token: str) -> int | None:
    """None for any invalid/expired/malformed token - callers all want the
    same response to every one of these (require a fresh login), so
    there's no reason to distinguish them past this point."""
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[_JWT_ALGORITHM])
        return int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        return None


async def _authenticate(request: Request, db: AsyncSession) -> User | None:
    """Shared by get_current_user and get_current_user_for_page - same
    cookie-read/decode/lookup logic either way, they only differ in what
    happens when it comes back empty."""
    token = request.cookies.get(COOKIE_NAME)
    if token is None:
        return None

    user_id = decode_access_token(token)
    if user_id is None:
        return None

    return await db.get(User, user_id)


async def get_current_user(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    """
    FastAPI dependency for fetch()-driven API routes. Reads the JWT from
    the httponly cookie POST /auth/login sets, verifies it, and loads the
    matching User row - or raises a plain 401 at any point that fails, so
    client-side JS can catch it and react (e.g. show an inline error)
    instead of the browser silently following a redirect out from under a
    fetch() call. For a route that renders a full HTML page instead, see
    get_current_user_for_page below.

    Not used by main.py's bill CRUD routes, app/routers/sanitizer.py's
    upload route, or POST /api/chat - those are also called by
    telegram_bot.py as a trusted server-to-server caller with no browser
    session to present, so a cookie-only dependency would break the bot
    outright. See get_authorized_user_id below for the dependency those
    routes actually use.
    """
    user = await _authenticate(request, db)
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


async def get_current_user_for_page(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    """
    Same authentication as get_current_user, but redirects to /login
    instead of raising a bare 401 - for routes that render a full HTML
    page for direct browser navigation (GET /dashboard), where a raw 401
    JSON body would just render as ugly text in the browser instead of
    taking the user anywhere useful. Do not use this for fetch()-driven
    API routes - use get_current_user there instead, so client-side JS
    gets a plain, catchable 401 rather than the browser silently
    following a redirect the calling code never asked for.
    """
    user = await _authenticate(request, db)
    if user is None:
        raise HTTPException(status_code=302, headers={"Location": "/login"})
    return user


async def get_authorized_user_id(
    request: Request, user_id: int | None = None, db: AsyncSession = Depends(get_db)
) -> int:
    """
    The trust boundary for every route that reads or writes a specific
    user's bills. `user_id: int | None = None` here is a real FastAPI
    query parameter (FastAPI flattens a dependency's own parameters into
    the route's, so `?user_id=...` still works exactly as before at the
    HTTP level) - the point of this function is that the *value* of that
    parameter is never trusted on its own; it's only ever accepted after
    being checked against who's actually calling.

    Two ways to be authorized here, matching this app's two caller types:

    1. Browser session (cookie present and valid): the session's own
       user - never the query parameter - is authoritative. If a
       user_id WAS also supplied and it doesn't match, this is a caller
       who's logged in as one account trying to reach another's data by
       editing the URL/request body - rejected with 403, not silently
       corrected to their own id, so the attempt is visible in logs
       rather than papered over. If user_id matches or was omitted
       entirely (the dashboard's own JS no longer sends it - see
       dashboard.js), the session's id is returned.
    2. No session cookie, but a valid X-Internal-Api-Key header: this is
       telegram_bot.py, which has no browser session to present for an
       arbitrary Telegram user, but has already verified that user's
       identity via Telegram's own auth before ever calling this backend
       (message.from_user.id in telegram_bot.py comes from Telegram's
       signed update payload, not from anything the end user typed into
       this API directly). The supplied user_id is trusted as-is in this
       case - it's required (no default the bot could omit and still
       reach someone's data by accident).

    Neither -> 401. compare_digest, not `==`, for the API key check - a
    naive equality check leaks timing information about how many leading
    characters matched, which is exactly the kind of side channel that
    turns "the key is 64 hex chars" into a practically brute-forceable
    search instead of an infeasible one.
    """
    user = await _authenticate(request, db)
    if user is not None:
        if user_id is not None and user_id != user.id:
            raise HTTPException(403, "Forbidden: user_id does not match the authenticated session")
        return user.id

    api_key = request.headers.get(INTERNAL_API_KEY_HEADER)
    if api_key is not None and secrets.compare_digest(api_key, settings.internal_api_key):
        if user_id is None:
            raise HTTPException(400, "user_id is required")
        return user_id

    raise HTTPException(401, "Not authenticated")
