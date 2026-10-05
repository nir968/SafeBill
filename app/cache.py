import json
import logging
from typing import Any, Awaitable, Callable

from redis.exceptions import RedisError

logger = logging.getLogger("safebill.cache")

# Safety net, not the primary freshness mechanism - every bill write path
# calls invalidate_user_stats explicitly. The TTL only bounds how long a
# stale value can survive if a write ever slips past invalidation (e.g. a
# read that started before a commit repopulating the key just after it
# was cleared).
STATS_TTL_SECONDS = 300

# Every per-user cache entry, listed explicitly so invalidation is one
# DEL of known keys rather than a KEYS/SCAN pattern match (KEYS blocks
# Redis for the whole keyspace; SCAN is multi-round-trip). Add a name here
# whenever a new cached query is introduced, or it will never be cleared.
STATS_KEYS = ("stats_totals", "pending_by_currency", "total_paid", "pending_bills")


def _key(user_id: int, name: str) -> str:
    return f"cache:user:{user_id}:{name}"


async def get_or_set(
    redis, user_id: int, name: str, loader: Callable[[], Awaitable[Any]]
) -> Any:
    """Returns the cached JSON value for (user_id, name), or runs `loader`
    against Postgres, caches its result, and returns it. Fails open: a
    Redis outage degrades to uncached DB queries, never to an error page."""
    key = _key(user_id, name)
    try:
        cached = await redis.get(key)
        if cached is not None:
            return json.loads(cached)
    except RedisError as exc:
        logger.warning("cache read failed for %s (%s) - querying DB", key, exc)

    value = await loader()

    try:
        await redis.set(key, json.dumps(value), ex=STATS_TTL_SECONDS)
    except RedisError as exc:
        logger.warning("cache write failed for %s (%s)", key, exc)
    return value


async def invalidate_user_stats(redis, user_id: int) -> None:
    """Call after committing any change to one of this user's bills."""
    try:
        await redis.delete(*(_key(user_id, name) for name in STATS_KEYS))
    except RedisError as exc:
        logger.warning("cache invalidation failed for user %s (%s)", user_id, exc)
