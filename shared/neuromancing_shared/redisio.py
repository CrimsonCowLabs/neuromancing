"""Redis client factory (async). Used for hot-quote cache, leaderboard sorted-set,
pub/sub fan-out to SSE, and rate-limit counters."""

from __future__ import annotations

import redis.asyncio as aioredis


def make_redis(url: str) -> aioredis.Redis:
    return aioredis.from_url(url, encoding="utf-8", decode_responses=True)
