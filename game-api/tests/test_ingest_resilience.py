"""Feed self-healing: freshness watchdog, health probe, deadman latch."""

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.ingest import main as ingest_main
from app.ingest.health import crypto_feed_age, is_stale


# ── fake redis (just GET on quote:* keys) ──
class _FakeRedis:
    def __init__(self, quotes: dict):
        self._q = quotes  # symbol -> ts (datetime) or None

    async def get(self, key: str):
        sym = key.split("quote:", 1)[1]
        ts = self._q.get(sym)
        return None if ts is None else json.dumps({"bid": None, "ask": None, "last": "1", "ts": ts.isoformat()})


def _q(age_s):
    return datetime.now(timezone.utc) - timedelta(seconds=age_s)


# ── health / freshness ──
async def test_crypto_feed_age_picks_freshest():
    r = _FakeRedis({"BTC/USD": _q(200), "ETH/USD": _q(20), "SOL/USD": None})
    age = await crypto_feed_age(r, ["BTC/USD", "ETH/USD", "SOL/USD"])
    assert 18 < age < 30  # the freshest (ETH ~20s)


async def test_crypto_feed_age_none_when_all_missing():
    r = _FakeRedis({"BTC/USD": None, "ETH/USD": None, "SOL/USD": None})
    assert await crypto_feed_age(r, ["BTC/USD", "ETH/USD", "SOL/USD"]) is None


def test_is_stale_threshold_and_missing():
    assert is_stale(None, 300) is True        # missing = stale
    assert is_stale(400, 300) is True
    assert is_stale(100, 300) is False


# ── watchdog in _supervise ──
async def test_supervise_restarts_a_stalled_task():
    """A live-then-silent task: freshness reports stale → watchdog cancels + restarts it."""
    starts = 0
    age_box = {"age": 5.0}

    async def factory():
        nonlocal starts
        starts += 1
        await asyncio.sleep(3600)  # "runs forever" (like a hung stream)

    async def freshness():
        return age_box["age"]

    # Speed up the watch interval for the test.
    from app.config import get_settings
    get_settings().ingest_watch_interval_s = 0.02

    async def go():
        await ingest_main._supervise("t", factory, freshness=freshness, max_silence=0.1)

    task = asyncio.create_task(go())
    await asyncio.sleep(0.1)
    assert starts == 1                 # running, fresh -> not restarted
    age_box["age"] = 999.0             # go stale
    # Watchdog cancels within max_silence (~0.1s), then restarts after the 2s backoff.
    for _ in range(60):                # up to ~3s
        await asyncio.sleep(0.05)
        if starts >= 2:
            break
    assert starts >= 2                 # cancelled + restarted
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_supervise_does_not_restart_a_live_task():
    starts = 0

    async def factory():
        nonlocal starts
        starts += 1
        await asyncio.sleep(3600)

    async def freshness():
        return 1.0  # always fresh

    from app.config import get_settings
    get_settings().ingest_watch_interval_s = 0.02

    task = asyncio.create_task(
        ingest_main._supervise("t", factory, freshness=freshness, max_silence=0.1))
    await asyncio.sleep(0.3)
    assert starts == 1  # never restarted while fresh
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
