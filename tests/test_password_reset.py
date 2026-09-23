"""
Covers the password-reset endpoints in app/routers/auth.py:
POST /auth/forgot-password (generates a single-use Redis-backed token,
then either schedules a real email via BackgroundTasks or falls back to
logging the link - see that function's own docstring for why) and
POST /auth/reset-password (redeems the token, sets a new password,
auto-logs in). Calls the route functions directly, same approach as
tests/test_telegram_link.py - what matters here is the redis/db
interaction and the anti-enumeration behavior, not re-verifying FastAPI's
own dependency injection.

forgot_password's BackgroundTasks parameter is a real
fastapi.BackgroundTasks instance in every test below, not a fake - it's
a plain, easily-inspectable class (`.tasks`, each with `.func`/`.args`),
so there's no reason to fake it, and using the real thing means these
tests exercise the exact object the route actually receives in
production.
"""

import pytest
from fastapi import BackgroundTasks, HTTPException

from app import email_client
from app.routers.auth import (
    ForgotPasswordRequest,
    ResetPasswordRequest,
    forgot_password,
    reset_password,
)


class _FakeUser:
    def __init__(self, id, email=None, hashed_password=None):
        self.id = id
        self.email = email
        self.hashed_password = hashed_password


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeRedis:
    def __init__(self):
        self.store = {}
        self.set_calls = []

    async def set(self, key, value, ex=None):
        self.set_calls.append((key, value, ex))
        self.store[key] = str(value)

    async def get(self, key):
        value = self.store.get(key)
        return value.encode() if value is not None else None

    async def delete(self, key):
        self.store.pop(key, None)


class _FakeDb:
    """select_result is what the one `db.execute(select(User)...)` call
    in forgot_password() returns; users is a plain id->User dict for
    reset_password()'s db.get(User, id) lookup. Both endpoints together
    only ever need one of the two, but a single fake covering both keeps
    this file's fixtures simple."""

    def __init__(self, select_result=None, users=None):
        self._select_result = select_result
        self.users = dict(users or {})
        self.committed = False

    async def execute(self, stmt):
        return _FakeResult(self._select_result)

    async def get(self, model, id):
        return self.users.get(id)

    async def commit(self):
        self.committed = True


class _FakeRequest:
    def __init__(self, redis, scheme="https", netloc="safebill.duckdns.org"):
        self.app = type("App", (), {"state": type("State", (), {"redis": redis})()})()

        class _URL:
            pass

        url = _URL()
        url.scheme = scheme
        url.netloc = netloc
        self.url = url


class _FakeResponse:
    """set_cookie is the only thing _set_auth_cookie touches on this."""

    def set_cookie(self, **kwargs):
        pass


# --- forgot_password: anti-enumeration -----------------------------------


async def test_forgot_password_unknown_email_returns_generic_message_no_token():
    redis = _FakeRedis()
    db = _FakeDb(select_result=None)  # no matching user
    payload = ForgotPasswordRequest(email="nobody@example.com")
    background_tasks = BackgroundTasks()

    response = await forgot_password(_FakeRequest(redis), payload, background_tasks, db)

    assert "message" in response
    assert len(redis.set_calls) == 0, "no token should be generated for an email with no account"
    assert background_tasks.tasks == [], "nothing should be scheduled for an unknown email"


async def test_forgot_password_telegram_only_account_returns_same_message_no_token():
    """hashed_password is None for a Telegram-only account - nothing to
    reset, and this must look identical to the unknown-email case from
    the outside."""
    redis = _FakeRedis()
    user = _FakeUser(id=945422313, email="bot-user@example.com", hashed_password=None)
    db = _FakeDb(select_result=user)
    payload = ForgotPasswordRequest(email="bot-user@example.com")
    background_tasks = BackgroundTasks()

    response = await forgot_password(_FakeRequest(redis), payload, background_tasks, db)

    assert "message" in response
    assert len(redis.set_calls) == 0
    assert background_tasks.tasks == []


async def test_forgot_password_falls_back_to_logging_when_smtp_not_configured(monkeypatch, caplog):
    """The default, out-of-the-box state (no SMTP_* env vars set) - the
    original mocked-email behavior, kept as a fallback rather than
    removed (see forgot_password's own comment on why)."""
    monkeypatch.setattr(email_client, "is_configured", lambda: False)
    redis = _FakeRedis()
    user = _FakeUser(id=-42, email="nir@example.com", hashed_password="bcrypt-hash-not-real")
    db = _FakeDb(select_result=user)
    payload = ForgotPasswordRequest(email="nir@example.com")
    background_tasks = BackgroundTasks()

    with caplog.at_level("INFO", logger="safebill.auth"):
        response = await forgot_password(_FakeRequest(redis), payload, background_tasks, db)

    assert "message" in response
    assert len(redis.set_calls) == 1
    key, value, ttl = redis.set_calls[0]
    assert key.startswith("pwd_reset:")
    assert value == "-42"
    assert ttl == 1800

    assert background_tasks.tasks == [], "no email should be scheduled when SMTP isn't configured"
    assert any("reset link" in r.message for r in caplog.records)
    assert any("/reset-password?token=" in r.message for r in caplog.records)


async def test_forgot_password_schedules_background_email_when_smtp_configured(monkeypatch, caplog):
    """The new behavior this task adds: with real SMTP settings present,
    the actual send is scheduled via BackgroundTasks - never awaited
    directly - and nothing is logged as a fallback."""
    monkeypatch.setattr(email_client, "is_configured", lambda: True)
    redis = _FakeRedis()
    user = _FakeUser(id=-42, email="nir@example.com", hashed_password="bcrypt-hash-not-real")
    db = _FakeDb(select_result=user)
    payload = ForgotPasswordRequest(email="nir@example.com")
    background_tasks = BackgroundTasks()

    with caplog.at_level("INFO", logger="safebill.auth"):
        response = await forgot_password(_FakeRequest(redis), payload, background_tasks, db)

    assert "message" in response
    assert len(background_tasks.tasks) == 1
    task = background_tasks.tasks[0]
    assert task.func is email_client.send_password_reset_email
    assert task.args[0] == "nir@example.com"
    assert "/reset-password?token=" in task.args[1]
    assert not any("SMTP not configured" in r.message for r in caplog.records)


async def test_forgot_password_known_and_unknown_email_return_identical_response_shape():
    """The whole point of the anti-enumeration design - a caller
    inspecting only the HTTP response can't tell these two cases apart."""
    redis = _FakeRedis()

    known_user = _FakeUser(id=-1, email="a@example.com", hashed_password="hash")
    response_known = await forgot_password(
        _FakeRequest(redis),
        ForgotPasswordRequest(email="a@example.com"),
        BackgroundTasks(),
        _FakeDb(select_result=known_user),
    )
    response_unknown = await forgot_password(
        _FakeRequest(redis),
        ForgotPasswordRequest(email="nobody@example.com"),
        BackgroundTasks(),
        _FakeDb(select_result=None),
    )

    assert response_known == response_unknown


# --- reset_password --------------------------------------------------------


async def test_reset_password_rejects_weak_password_before_touching_token():
    redis = _FakeRedis()
    await redis.set("pwd_reset:abc", "-42", ex=1800)
    payload = ResetPasswordRequest(token="abc", password="weak")

    with pytest.raises(HTTPException) as exc_info:
        await reset_password(_FakeRequest(redis), _FakeResponse(), payload, _FakeDb())
    assert exc_info.value.status_code == 400
    # The token must still be there - a rejected weak password shouldn't
    # burn the user's one shot at redeeming a valid link.
    assert await redis.get("pwd_reset:abc") is not None


async def test_reset_password_rejects_unknown_or_expired_token():
    redis = _FakeRedis()  # nothing stored
    payload = ResetPasswordRequest(token="does-not-exist", password="Str0ng!Pass")

    with pytest.raises(HTTPException) as exc_info:
        await reset_password(_FakeRequest(redis), _FakeResponse(), payload, _FakeDb())
    assert exc_info.value.status_code == 400


async def test_reset_password_rejects_when_user_row_is_missing():
    redis = _FakeRedis()
    await redis.set("pwd_reset:abc", "-42", ex=1800)
    payload = ResetPasswordRequest(token="abc", password="Str0ng!Pass")

    with pytest.raises(HTTPException) as exc_info:
        await reset_password(_FakeRequest(redis), _FakeResponse(), payload, _FakeDb(users={}))
    assert exc_info.value.status_code == 404


async def test_reset_password_success_updates_hash_and_is_single_use():
    from app.auth import verify_password

    redis = _FakeRedis()
    await redis.set("pwd_reset:abc", "-42", ex=1800)
    user = _FakeUser(id=-42, email="nir@example.com", hashed_password="old-hash")
    db = _FakeDb(users={-42: user})

    payload = ResetPasswordRequest(token="abc", password="Str0ng!Pass")
    response = await reset_password(_FakeRequest(redis), _FakeResponse(), payload, db)

    assert response["email"] == "nir@example.com"
    assert user.hashed_password != "old-hash"
    assert verify_password("Str0ng!Pass", user.hashed_password), "the new password must actually verify"
    assert db.committed

    # Single-use: the same token can't be redeemed twice.
    with pytest.raises(HTTPException) as exc_info:
        await reset_password(_FakeRequest(redis), _FakeResponse(), payload, db)
    assert exc_info.value.status_code == 400
