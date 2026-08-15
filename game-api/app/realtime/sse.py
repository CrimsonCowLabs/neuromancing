"""SSE fan-out. Each web process subscribes ONCE to Redis pub/sub (channels
leaderboard/feed/social) and multiplexes to all connected clients — 1 Redis sub
per process, not per client. New clients get a leaderboard snapshot then deltas."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from neuromancing_shared.redisio import make_redis

from ..config import get_settings
from ..leaderboard import current as leaderboard_current

router = APIRouter(tags=["realtime"])
_settings = get_settings()

CHANNELS = ["leaderboard", "feed", "social"]


@router.get("/sse/stream")
async def stream(request: Request) -> EventSourceResponse:
    redis = make_redis(_settings.redis_url)

    async def event_gen():
        # Snapshot-on-connect (no DB hit — served from Redis cache).
        yield {"event": "leaderboard", "data": json.dumps(await leaderboard_current())}
        pubsub = redis.pubsub()
        await pubsub.subscribe(*CHANNELS)
        try:
            while True:
                if await request.is_disconnected():
                    break
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=15.0)
                if msg is None:
                    yield {"event": "ping", "data": "{}"}  # keep-alive
                    continue
                yield {"event": msg["channel"], "data": msg["data"]}
        finally:
            await pubsub.unsubscribe(*CHANNELS)
            await pubsub.aclose()
            await asyncio.shield(redis.aclose())

    return EventSourceResponse(event_gen())
