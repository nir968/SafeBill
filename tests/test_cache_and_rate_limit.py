"""
Covers app/cache.py and app/rate_limit.py against an in-memory stand-in
for the Redis client - same no-real-network approach as the rest of this
suite. Only the handful of commands those two modules actually issue are
implemented.
"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from redis.exceptions import ConnectionError as RedisConnectionError

from app.cache import STATS_KEYS, _key, get_or_set, invalidate_user_stats
from app.rate_limit import SCAN_LIMIT_PER_MINUTE, limit_bill_scans


class _FakePipeline:
    def __init__(self, redis):
        self._redis = redis
        self._ops = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def incr(self, key):
        self._ops.append(("incr", key))

    def expire(self, key, seconds):
        self._ops.append(("expire", key))

    async def execute(self):
        results = []
        for op, key in self._ops:
            if op == "incr":
                self._redis.store[key] = int(self._redis.store.get(key, 0)) + 1
                results.append(self._redis.store[key])
            else:
                results.append(True)
        return results


class _FakeRedis:
    def __init__(self):
        self.store = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = value

    async def delete(self, *keys):
        for key in keys:
            self.store.pop(key, None)

    def pipeline(self, transaction=True):
        return _FakePipeline(self)


class _DownRedis:
    async def get(self, key):
        raise RedisConnectionError("down")

    async def set(self, key, value, ex=None):
        raise RedisConnectionError("down")

    async def delete(self, *keys):
        raise RedisConnectionError("down")

    def pipeline(self, transaction=True):
        raise RedisConnectionError("down")


def _request(redis):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=redis)))


async def test_second_read_is_served_from_cache():
    redis = _FakeRedis()
    calls = []

    async def loader():
        calls.append(1)
        return {"ILS": 100.0}

    assert await get_or_set(redis, 7, "stats_totals", loader) == {"ILS": 100.0}
    assert await get_or_set(redis, 7, "stats_totals", loader) == {"ILS": 100.0}
    assert len(calls) == 1


async def test_invalidation_clears_every_stats_key_for_that_user_only():
    redis = _FakeRedis()
    for name in STATS_KEYS:
        redis.store[_key(7, name)] = "1"
        redis.store[_key(8, name)] = "1"

    await invalidate_user_stats(redis, 7)

    assert all(_key(7, name) not in redis.store for name in STATS_KEYS)
    assert all(_key(8, name) in redis.store for name in STATS_KEYS)


async def test_cache_fails_open_when_redis_is_down():
    async def loader():
        return 42.0

    assert await get_or_set(_DownRedis(), 7, "total_paid", loader) == 42.0
    await invalidate_user_stats(_DownRedis(), 7)


async def test_scan_limit_blocks_after_five_per_user():
    request = _request(_FakeRedis())
    for _ in range(SCAN_LIMIT_PER_MINUTE):
        await limit_bill_scans(request, user_id=7)

    with pytest.raises(HTTPException) as exc_info:
        await limit_bill_scans(request, user_id=7)
    assert exc_info.value.status_code == 429
    assert "Retry-After" in exc_info.value.headers

    # A different user's quota is independent.
    await limit_bill_scans(request, user_id=8)


async def test_scan_limit_fails_open_when_redis_is_down():
    await limit_bill_scans(_request(_DownRedis()), user_id=7)
