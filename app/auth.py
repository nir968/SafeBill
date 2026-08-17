"""
Password hashing, JWT session tokens, and the two FastAPI dependencies
that read them (get_current_user, get_current_user_for_page) - app/
routers/auth.py has the signup/login/logout endpoints that actually issue
these tokens.

GET /dashboard (app/routers/web.py) is the only existing route wired to
either of these so far. main.py's bill CRUD routes, sanitizer.py's upload
route, and POST /api/chat are all deliberately left on the unauthenticated
`user_id` parameter they already had - see app/routers/auth.py's module
docstring for why: they're called by telegram_bot.py as a trusted
server-to-server caller with no browser session to present, and migrating
them to cookie auth would break the bot outright.
"""

from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, Request
from passlib.context import CryptContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.models import User

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

    Not a drop-in replacement for the `user_id` query parameter used
    throughout main.py/app/routers/web.py/app/routers/sanitizer.py today -
    see app/routers/auth.py's module docstring before wiring this into any
    of them, since some of those routes are called by telegram_bot.py as a
    trusted server-to-server caller with no browser session to present.
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
