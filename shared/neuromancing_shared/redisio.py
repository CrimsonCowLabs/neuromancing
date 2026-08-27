"""Redis client factory (async). Used for hot-quote cache, leaderboard sorted-set,
pub/sub fan-out to SSE, and rate-limit counters."""

from __future__ import annotations

import redis as syncredis
import redis.asyncio as aioredis


def make_redis(url: str) -> aioredis.Redis:
    return aioredis.from_url(url, encoding="utf-8", decode_responses=True)


def make_redis_sync(url: str) -> syncredis.Redis:
    """Blocking client, same options as `make_redis`. For the rare caller that must not
    depend on an event loop — the ingest deadman runs on its own OS thread precisely so a
    wedged loop cannot disarm it, so it cannot share the async client."""
    return syncredis.from_url(url, encoding="utf-8", decode_responses=True)
