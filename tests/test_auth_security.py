"""
Covers app.auth.get_authorized_user_id - the dependency every route
touching a user's bills relies on (see the security review that
introduced it). Each test here mirrors one of the branches called out in
that function's own docstring; together they're a regression test for the
exact IDOR this function was written to close - a caller with a valid
session for one account passing a different account's id and having it
honored.

No real database or network access anywhere in this file:
app.auth._authenticate is monkeypatched directly rather than exercised
through a real cookie/JWT round trip, since get_authorized_user_id's own
job is what to *do* with whatever _authenticate returns, not to
re-verify JWT decoding (that's PyJWT's job, not this suite's).
"""

import app.auth as auth_module
import pytest
from fastapi import HTTPException

from app.auth import INTERNAL_API_KEY_HEADER, get_authorized_user_id
from app.config import settings


class _FakeRequest:
    def __init__(self, headers=None):
        self.cookies = {}
        self.headers = headers or {}


class _FakeUser:
    def __init__(self, id):
        self.id = id


@pytest.fixture(autouse=True)
def no_session_by_default(monkeypatch):
    """Every test starts as if no session cookie was ever sent - matches
    a real anonymous request. Tests that need an authenticated session
    override this via the `authenticated_as` fixture below."""

    async def _no_session(request, db):
        return None

    monkeypatch.setattr(auth_module, "_authenticate", _no_session)


@pytest.fixture
def authenticated_as(monkeypatch):
    """Simulates a valid, already-decoded session cookie for the given
    user id - the same outcome a real POST /auth/login would produce."""

    def _set(user_id: int):
        async def _authenticated(request, db):
            return _FakeUser(user_id)

        monkeypatch.setattr(auth_module, "_authenticate", _authenticated)

    return _set


async def test_session_user_with_no_user_id_param_is_authoritative(authenticated_as):
    """The dashboard's own JS no longer sends user_id at all (see
    dashboard.js) - the common case in practice."""
    authenticated_as(111)
    result = await get_authorized_user_id(_FakeRequest(), None, db=None)
    assert result == 111


async def test_session_user_with_matching_user_id_param_is_allowed(authenticated_as):
    authenticated_as(111)
    result = await get_authorized_user_id(_FakeRequest(), 111, db=None)
    assert result == 111


async def test_session_user_with_mismatched_user_id_param_is_forbidden(authenticated_as):
    """The exact scenario this function exists to stop: logged in as one
    account, but the request claims a different one."""
    authenticated_as(111)
    with pytest.raises(HTTPException) as exc_info:
        await get_authorized_user_id(_FakeRequest(), 999, db=None)
    assert exc_info.value.status_code == 403


async def test_valid_internal_api_key_trusts_the_supplied_user_id():
    """telegram_bot.py's path - no session cookie exists for it to
    present, only the shared internal key."""
    request = _FakeRequest(headers={INTERNAL_API_KEY_HEADER: settings.internal_api_key})
    result = await get_authorized_user_id(request, 555, db=None)
    assert result == 555


async def test_valid_internal_api_key_without_user_id_is_bad_request():
    request = _FakeRequest(headers={INTERNAL_API_KEY_HEADER: settings.internal_api_key})
    with pytest.raises(HTTPException) as exc_info:
        await get_authorized_user_id(request, None, db=None)
    assert exc_info.value.status_code == 400


async def test_wrong_internal_api_key_is_unauthorized():
    request = _FakeRequest(headers={INTERNAL_API_KEY_HEADER: "not-the-real-key"})
    with pytest.raises(HTTPException) as exc_info:
        await get_authorized_user_id(request, 555, db=None)
    assert exc_info.value.status_code == 401


async def test_no_credentials_at_all_is_unauthorized():
    with pytest.raises(HTTPException) as exc_info:
        await get_authorized_user_id(_FakeRequest(), 555, db=None)
    assert exc_info.value.status_code == 401
