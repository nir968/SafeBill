"""
Covers the Telegram account-linking endpoints in app/routers/auth.py:
POST /auth/telegram/link-token (cookie-authenticated, mints a token) and
POST /auth/telegram/link (internal-API-key-authenticated, redeemed by
telegram_bot.py after /start <token>). Calls the route functions directly
rather than through a real HTTP request/FastAPI dependency-injection
cycle - what matters here is the redis/db interaction each one performs,
not re-verifying that FastAPI itself calls dependencies correctly (that's
covered by tests/test_auth_security.py and by the app importing cleanly
under pytest.ini).
"""

import pytest
from fastapi import HTTPException

from app.auth import INTERNAL_API_KEY_HEADER
from app.config import settings
from app.routers.auth import (
    TelegramLinkRequest,
    create_telegram_link_token,
    redeem_telegram_link_token,
)


class _FakeUser:
    def __init__(self, id, email=None, telegram_chat_id=None):
        self.id = id
        self.email = email
        self.telegram_chat_id = telegram_chat_id


class _FakeRedis:
    """In-memory stand-in for request.app.state.redis - real key/value/
    delete semantics (a plain dict), TTL accepted but not enforced since
    nothing here tests expiry timing itself."""

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
    """Tracks users by id (like a one-table in-memory DB) and records
    whether commit()/execute() were called - not a full SQL emulator,
    but enough to verify the redeem endpoint's actual read/write/commit
    sequence for real."""

    def __init__(self, users=None):
        self.users = dict(users or {})
        self.executed_statements = []
        self.committed = False

    async def get(self, model, id):
        return self.users.get(id)

    async def execute(self, stmt):
        self.executed_statements.append(stmt)

        class _Result:
            def scalar_one_or_none(self_inner):
                return None

        return _Result()

    async def commit(self):
        self.committed = True


class _FakeRequest:
    def __init__(self, redis=None, headers=None):
        self.headers = headers or {}
        self.app = type("App", (), {"state": type("State", (), {"redis": redis})()})()


# --- create_telegram_link_token ------------------------------------------


async def test_link_token_disabled_when_bot_username_unset(monkeypatch):
    monkeypatch.setattr(settings, "telegram_bot_username", "")
    with pytest.raises(HTTPException) as exc_info:
        await create_telegram_link_token(_FakeRequest(redis=_FakeRedis()), current_user=_FakeUser(-42))
    assert exc_info.value.status_code == 503


async def test_link_token_generates_deep_link_and_stores_in_redis(monkeypatch):
    monkeypatch.setattr(settings, "telegram_bot_username", "SafeBillBot")
    redis = _FakeRedis()
    response = await create_telegram_link_token(_FakeRequest(redis=redis), current_user=_FakeUser(-42))

    assert response.deep_link.startswith("https://t.me/SafeBillBot?start=")
    token = response.deep_link.rsplit("start=", 1)[1]
    assert len(token) > 16, "token should be a real random value, not something trivially short"

    assert len(redis.set_calls) == 1
    key, value, ttl = redis.set_calls[0]
    assert key == f"tg_link:{token}"
    assert value == "-42"
    assert ttl == 600


async def test_link_token_is_random_each_time(monkeypatch):
    monkeypatch.setattr(settings, "telegram_bot_username", "SafeBillBot")
    redis = _FakeRedis()
    r1 = await create_telegram_link_token(_FakeRequest(redis=redis), current_user=_FakeUser(-42))
    r2 = await create_telegram_link_token(_FakeRequest(redis=redis), current_user=_FakeUser(-42))
    assert r1.deep_link != r2.deep_link


# --- redeem_telegram_link_token -------------------------------------------


def _bot_request(redis):
    return _FakeRequest(redis=redis, headers={INTERNAL_API_KEY_HEADER: settings.internal_api_key})


async def test_redeem_rejects_without_internal_api_key():
    redis = _FakeRedis()
    await redis.set("tg_link:abc", "-42", ex=600)
    request = _FakeRequest(redis=redis)  # no header at all
    payload = TelegramLinkRequest(token="abc", telegram_id=945422313)
    with pytest.raises(HTTPException) as exc_info:
        await redeem_telegram_link_token(request, payload, db=_FakeDb())
    assert exc_info.value.status_code == 401


async def test_redeem_rejects_unknown_or_expired_token():
    redis = _FakeRedis()  # nothing stored
    payload = TelegramLinkRequest(token="does-not-exist", telegram_id=945422313)
    with pytest.raises(HTTPException) as exc_info:
        await redeem_telegram_link_token(_bot_request(redis), payload, db=_FakeDb())
    assert exc_info.value.status_code == 400


async def test_redeem_rejects_when_web_user_row_is_missing():
    redis = _FakeRedis()
    await redis.set("tg_link:abc", "-42", ex=600)
    payload = TelegramLinkRequest(token="abc", telegram_id=945422313)
    with pytest.raises(HTTPException) as exc_info:
        await redeem_telegram_link_token(_bot_request(redis), payload, db=_FakeDb(users={}))
    assert exc_info.value.status_code == 404


async def test_redeem_success_links_account_and_is_single_use():
    redis = _FakeRedis()
    await redis.set("tg_link:abc", "-42", ex=600)
    user = _FakeUser(-42, email="nir@example.com")
    db = _FakeDb(users={-42: user})

    payload = TelegramLinkRequest(token="abc", telegram_id=945422313)
    response = await redeem_telegram_link_token(_bot_request(redis), payload, db=db)

    assert response.email == "nir@example.com"
    assert user.telegram_chat_id == "945422313", "the web account's row should now carry the real Telegram id"
    assert db.committed, "the link must actually be persisted"
    assert len(db.executed_statements) == 1, "the re-linking guard (clear any other claim on this Telegram id) should run"

    # Single-use: the same token can't be redeemed twice.
    with pytest.raises(HTTPException) as exc_info:
        await redeem_telegram_link_token(_bot_request(redis), payload, db=db)
    assert exc_info.value.status_code == 400
