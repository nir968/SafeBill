"""
Telegram 2FA (app/two_factor.py + the /auth/2fa/* endpoints and the login,
password-reset and CSV-export hooks). Fakes only: an in-memory Redis
covering the commands two_factor.py issues, and send_message replaced so
no real Telegram call is ever made. 2FA is switched on per test by giving
settings a non-mock bot token.
"""

import re

import pytest
from fastapi import HTTPException

import app.two_factor as two_factor
from app.auth import COOKIE_NAME, create_access_token, hash_password, verify_password
from app.config import settings
from app.routers.auth import (
    LoginRequest,
    ResetPasswordRequest,
    TwoFactorRequest,
    TwoFactorVerifyRequest,
    generate_two_factor_code,
    login,
    reset_password,
    verify_two_factor_code,
)

PASSWORD = "Str0ng!Pass"
LINKED_ID = -42
UNLINKED_ID = -43


# --- fakes -------------------------------------------------------------------


class _FakeRedis:
    def __init__(self):
        self.store = {}
        self.ttls = {}

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.store:
            return None
        self.store[key] = str(value)
        if ex is not None:
            self.ttls[key] = ex
        return True

    async def get(self, key):
        value = self.store.get(key)
        return value.encode() if isinstance(value, str) else value

    async def delete(self, *keys):
        for key in keys:
            self.store.pop(key, None)
            self.ttls.pop(key, None)

    async def ttl(self, key):
        return self.ttls.get(key, -1)

    async def incr(self, key):
        self.store[key] = str(int(self.store.get(key, 0)) + 1)
        return int(self.store[key])

    async def expire(self, key, seconds, nx=False):
        if not (nx and key in self.ttls):
            self.ttls[key] = seconds
        return True

    async def hset(self, key, mapping):
        self.store.setdefault(key, {}).update({k: str(v) for k, v in mapping.items()})

    async def hincrby(self, key, field, amount):
        data = self.store.setdefault(key, {})
        data[field] = str(int(data.get(field, 0)) + amount)
        return int(data[field])

    async def hgetall(self, key):
        return {k.encode(): v.encode() for k, v in self.store.get(key, {}).items()}

    def pipeline(self, transaction=True):
        return _FakePipeline(self)


class _FakePipeline:
    def __init__(self, redis):
        self._redis = redis
        self._calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __getattr__(self, name):
        def queue(*args, **kwargs):
            self._calls.append((name, args, kwargs))
        return queue

    async def execute(self):
        return [await getattr(self._redis, n)(*a, **k) for n, a, k in self._calls]


class _FakeUser:
    def __init__(self, id, telegram_chat_id=None):
        self.id = id
        self.email = f"user{abs(id)}@example.com"
        self.hashed_password = hash_password(PASSWORD)
        self.telegram_chat_id = telegram_chat_id


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeDb:
    def __init__(self, *users, select_result=None):
        self.users = {u.id: u for u in users}
        self._select_result = select_result

    async def get(self, model, id):
        return self.users.get(id)

    async def execute(self, stmt):
        return _FakeResult(self._select_result)

    async def commit(self):
        pass


class _FakeRequest:
    def __init__(self, redis, cookies=None):
        self.app = type("App", (), {"state": type("State", (), {"redis": redis})()})()
        self.cookies = dict(cookies or {})
        self.headers = {}
        self.url = type("URL", (), {"scheme": "https", "netloc": "safebill.test"})()


class _FakeResponse:
    def __init__(self):
        self.cookies = {}
        self.deleted = []

    def set_cookie(self, key, value, **kwargs):
        self.cookies[key] = value

    def delete_cookie(self, key, **kwargs):
        self.deleted.append(key)


@pytest.fixture
def telegram_on(monkeypatch):
    """Turns 2FA on (real-looking bot token) and captures outgoing codes."""
    monkeypatch.setattr(settings, "telegram_bot_token", "123456:real-looking-token")
    sent = []

    async def fake_send(chat_id, text):
        sent.append((chat_id, text))

    monkeypatch.setattr(two_factor.telegram_client, "send_message", fake_send)
    return sent


def _code_from(sent):
    return re.search(r"\b(\d{6})\b", sent[-1][1]).group(1)


def _linked():
    return _FakeUser(LINKED_ID, telegram_chat_id="945422313")


# --- when 2FA applies --------------------------------------------------------


def test_off_without_real_bot_token_even_if_linked():
    assert settings.telegram_bot_token == "mock-token"
    assert two_factor.is_required(_linked()) is False


def test_on_only_for_linked_accounts(telegram_on):
    assert two_factor.is_required(_linked()) is True
    assert two_factor.is_required(_FakeUser(UNLINKED_ID)) is False


# --- issuing codes -----------------------------------------------------------


async def test_issue_stores_6_digit_code_with_5_minute_ttl_and_sends_to_telegram(telegram_on):
    redis = _FakeRedis()
    await two_factor.issue_code(redis, _linked(), "login")

    stored = redis.store[f"2fa:{LINKED_ID}"]
    assert re.fullmatch(r"\d{6}", stored["code"])
    assert stored["purpose"] == "login" and stored["attempts"] == "0"
    assert redis.ttls[f"2fa:{LINKED_ID}"] == 300
    chat_id, text = telegram_on[0]
    assert chat_id == "945422313" and stored["code"] in text


async def test_resend_blocked_during_cooldown(telegram_on):
    redis = _FakeRedis()
    await two_factor.issue_code(redis, _linked(), "login")
    with pytest.raises(HTTPException) as exc_info:
        await two_factor.issue_code(redis, _linked(), "login")
    assert exc_info.value.status_code == 429
    assert len(telegram_on) == 1


async def test_hourly_send_cap(telegram_on):
    redis = _FakeRedis()
    for _ in range(two_factor.MAX_CODES_PER_HOUR):
        await two_factor.issue_code(redis, _linked(), "login")
        await redis.delete(f"2fa:cooldown:{LINKED_ID}")  # skip the 60 s wait
    with pytest.raises(HTTPException) as exc_info:
        await two_factor.issue_code(redis, _linked(), "login")
    assert exc_info.value.status_code == 429


async def test_telegram_failure_returns_502_and_allows_immediate_retry(telegram_on, monkeypatch):
    async def failing_send(chat_id, text):
        raise two_factor.telegram_client.TelegramDeliveryError("down")

    monkeypatch.setattr(two_factor.telegram_client, "send_message", failing_send)
    redis = _FakeRedis()
    with pytest.raises(HTTPException) as exc_info:
        await two_factor.issue_code(redis, _linked(), "login")
    assert exc_info.value.status_code == 502
    assert f"2fa:{LINKED_ID}" not in redis.store
    assert f"2fa:cooldown:{LINKED_ID}" not in redis.store


# --- verifying codes ---------------------------------------------------------


async def test_correct_code_verifies_once(telegram_on):
    redis = _FakeRedis()
    await two_factor.issue_code(redis, _linked(), "login")
    code = _code_from(telegram_on)

    await two_factor.verify_code(redis, LINKED_ID, "login", code)
    with pytest.raises(HTTPException) as exc_info:
        await two_factor.verify_code(redis, LINKED_ID, "login", code)
    assert exc_info.value.status_code == 400  # single use


async def test_three_wrong_attempts_burn_the_code(telegram_on):
    redis = _FakeRedis()
    await two_factor.issue_code(redis, _linked(), "login")
    code = _code_from(telegram_on)
    wrong = "000000" if code != "000000" else "111111"

    for expected_status in (400, 400, 429):
        with pytest.raises(HTTPException) as exc_info:
            await two_factor.verify_code(redis, LINKED_ID, "login", wrong)
        assert exc_info.value.status_code == expected_status

    with pytest.raises(HTTPException) as exc_info:
        await two_factor.verify_code(redis, LINKED_ID, "login", code)
    assert exc_info.value.status_code == 400, "the correct code must not work after 3 misses"


async def test_code_is_bound_to_its_purpose(telegram_on):
    redis = _FakeRedis()
    await two_factor.issue_code(redis, _linked(), "action")
    with pytest.raises(HTTPException):
        await two_factor.verify_code(redis, LINKED_ID, "login", _code_from(telegram_on))


async def test_malformed_code_does_not_use_an_attempt(telegram_on):
    redis = _FakeRedis()
    await two_factor.issue_code(redis, _linked(), "login")
    with pytest.raises(HTTPException) as exc_info:
        await two_factor.verify_code(redis, LINKED_ID, "login", "12ab")
    assert exc_info.value.status_code == 400
    assert redis.store[f"2fa:{LINKED_ID}"]["attempts"] == "0"


# --- login -------------------------------------------------------------------


async def test_login_unlinked_account_unchanged(telegram_on):
    user = _FakeUser(UNLINKED_ID)
    response = _FakeResponse()
    result = await login(
        LoginRequest(email=user.email, password=PASSWORD),
        _FakeRequest(_FakeRedis()), response, _FakeDb(select_result=user),
    )
    assert result == {"id": UNLINKED_ID, "email": user.email}
    assert COOKIE_NAME in response.cookies


async def test_login_linked_account_requires_code_then_gets_session(telegram_on):
    user = _linked()
    redis = _FakeRedis()
    response = _FakeResponse()
    result = await login(
        LoginRequest(email=user.email, password=PASSWORD),
        _FakeRequest(redis), response, _FakeDb(select_result=user),
    )
    assert result == {"two_factor_required": True}
    assert COOKIE_NAME not in response.cookies, "no session before the code is verified"
    pending = response.cookies[two_factor.PENDING_LOGIN_COOKIE]

    request = _FakeRequest(redis, cookies={two_factor.PENDING_LOGIN_COOKIE: pending})
    db = _FakeDb(user)
    await generate_two_factor_code(request, TwoFactorRequest(purpose="login"), db)

    verify_response = _FakeResponse()
    await verify_two_factor_code(
        request, verify_response,
        TwoFactorVerifyRequest(purpose="login", code=_code_from(telegram_on)), db,
    )
    assert COOKIE_NAME in verify_response.cookies
    assert two_factor.PENDING_LOGIN_COOKIE in verify_response.deleted
    assert f"2fa:pending:{pending}" not in redis.store
    assert f"2fa:elevated:{LINKED_ID}" in redis.store


async def test_generate_without_pending_login_is_rejected(telegram_on):
    with pytest.raises(HTTPException) as exc_info:
        await generate_two_factor_code(
            _FakeRequest(_FakeRedis()), TwoFactorRequest(purpose="login"), _FakeDb()
        )
    assert exc_info.value.status_code == 401


async def test_generate_for_unlinked_account_is_rejected(telegram_on):
    user = _FakeUser(UNLINKED_ID)
    request = _FakeRequest(_FakeRedis(), cookies={COOKIE_NAME: create_access_token(UNLINKED_ID)})
    with pytest.raises(HTTPException) as exc_info:
        await generate_two_factor_code(request, TwoFactorRequest(purpose="action"), _FakeDb(user))
    assert exc_info.value.status_code == 400


# --- password reset ----------------------------------------------------------


async def test_reset_requires_code_without_burning_the_link(telegram_on):
    user = _linked()
    redis = _FakeRedis()
    await redis.set("pwd_reset:abc", str(LINKED_ID), ex=1800)
    db = _FakeDb(user)
    payload = ResetPasswordRequest(token="abc", password="N3w!Password")

    with pytest.raises(HTTPException) as exc_info:
        await reset_password(_FakeRequest(redis), _FakeResponse(), payload, db)
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["code"] == "two_factor_required"
    assert "pwd_reset:abc" in redis.store, "the reset link must still work after the code"
    assert verify_password(PASSWORD, user.hashed_password), "password must be unchanged"

    request = _FakeRequest(redis)
    await generate_two_factor_code(request, TwoFactorRequest(purpose="reset", reset_token="abc"), db)
    await verify_two_factor_code(
        request, _FakeResponse(),
        TwoFactorVerifyRequest(purpose="reset", reset_token="abc", code=_code_from(telegram_on)),
        db,
    )

    await reset_password(_FakeRequest(redis), _FakeResponse(), payload, db)
    assert verify_password("N3w!Password", user.hashed_password)
    assert "pwd_reset:abc" not in redis.store
    assert "2fa:reset_ok:abc" not in redis.store


# --- CSV export (sensitive action) -------------------------------------------


def _session_request(redis, user_id):
    return _FakeRequest(redis, cookies={COOKIE_NAME: create_access_token(user_id)})


async def test_export_blocked_for_linked_browser_session_until_verified(telegram_on):
    redis = _FakeRedis()
    db = _FakeDb(_linked())
    request = _session_request(redis, LINKED_ID)

    with pytest.raises(HTTPException) as exc_info:
        await two_factor.require_recent_two_factor(request, db)
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["code"] == "two_factor_required"

    await generate_two_factor_code(request, TwoFactorRequest(purpose="action"), db)
    await verify_two_factor_code(
        request, _FakeResponse(),
        TwoFactorVerifyRequest(purpose="action", code=_code_from(telegram_on)), db,
    )
    assert redis.ttls[f"2fa:elevated:{LINKED_ID}"] == 600
    await two_factor.require_recent_two_factor(request, db)  # now allowed


async def test_export_not_gated_for_bot_or_unlinked_or_without_bot_token(telegram_on, monkeypatch):
    redis = _FakeRedis()
    db = _FakeDb(_linked(), _FakeUser(UNLINKED_ID))

    await two_factor.require_recent_two_factor(_FakeRequest(redis), db)  # bot: no session cookie
    await two_factor.require_recent_two_factor(_session_request(redis, UNLINKED_ID), db)

    monkeypatch.setattr(settings, "telegram_bot_token", "mock-token")
    await two_factor.require_recent_two_factor(_session_request(redis, LINKED_ID), db)
