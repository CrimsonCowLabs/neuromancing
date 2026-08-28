"""Feed self-healing: freshness watchdog, health probe, deadman latch, liveness escalation."""

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.ingest import health as ingest_health
from app.ingest import main as ingest_main
from app.ingest.health import crypto_feed_age, heartbeat_age, is_stale


# ── fake redis (GET on quote:* plus the plain liveness keys) ──
class _FakeRedis:
    def __init__(self, quotes: dict, plain: dict | None = None):
        self._q = quotes          # symbol -> ts (datetime) or None
        self._plain = plain or {}  # bare key -> ts (datetime) or None

    async def get(self, key: str):
        if not key.startswith("quote:"):
            ts = self._plain.get(key)
            return None if ts is None else ts.isoformat()
        sym = key.split("quote:", 1)[1]
        ts = self._q.get(sym)
        return None if ts is None else json.dumps({"bid": None, "ask": None, "last": "1", "ts": ts.isoformat()})

    async def set(self, key: str, value: str, **kw):
        self._plain[key] = datetime.fromisoformat(value)


def _q(age_s):
    return datetime.now(timezone.utc) - timedelta(seconds=age_s)


def _live_redis(*, feed_age, hb_age, uptime):
    """A redis whose crypto feed / heartbeat / uptime ages are set independently.
    `None` for hb_age or uptime means the key is absent."""
    return _FakeRedis(
        {"BTC/USD": None if feed_age is None else _q(feed_age)},
        {
            ingest_health.HEARTBEAT_KEY: None if hb_age is None else _q(hb_age),
            ingest_health.STARTED_AT_KEY: None if uptime is None else _q(uptime),
        },
    )


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


# ── liveness: heartbeat probe ──
async def test_heartbeat_age_reads_the_beat():
    r = _live_redis(feed_age=10, hb_age=42, uptime=9000)
    age = await heartbeat_age(r)
    assert 40 < age < 50


async def test_heartbeat_age_none_when_never_written():
    r = _live_redis(feed_age=10, hb_age=None, uptime=9000)
    assert await heartbeat_age(r) is None


async def test_heartbeat_write_then_read_roundtrip():
    r = _live_redis(feed_age=10, hb_age=None, uptime=None)
    await ingest_health.write_heartbeat(r)
    assert await heartbeat_age(r) < 5


# ── the healthcheck verdict: feed staleness and loop-wedge are INDEPENDENT ──
@pytest.fixture(autouse=True)
def _restore_settings():
    """get_settings() is lru_cached, so tests mutating it leak into their neighbours.
    Snapshot the knobs this module touches and put them back."""
    from app.config import get_settings

    keys = ("ingest_health_stale_s", "ingest_heartbeat_stale_s", "ingest_escalation_grace_s",
            "ingest_watch_interval_s", "ingest_heartbeat_interval_s", "ingest_debug_freeze_enabled")
    s = get_settings()
    saved = {k: getattr(s, k) for k in keys}
    yield
    for k, v in saved.items():
        setattr(s, k, v)


def _settings(**over):
    from app.config import get_settings

    s = get_settings()
    s.ingest_health_stale_s = over.get("health", 300.0)
    s.ingest_heartbeat_stale_s = over.get("hb", 900.0)
    s.ingest_escalation_grace_s = over.get("grace", 300.0)
    return s


async def test_all_healthy_when_feed_and_loop_are_fresh():
    v = await ingest_health.evaluate(_live_redis(feed_age=10, hb_age=10, uptime=9000), _settings())
    assert v.healthy is True
    assert v.loop_wedged is False


async def test_stale_feed_with_a_live_loop_is_unhealthy_but_never_escalates():
    """The in-process watchdog owns this case — killing the process would not help."""
    v = await ingest_health.evaluate(_live_redis(feed_age=9999, hb_age=10, uptime=9000), _settings())
    assert v.feed_stale is True
    assert v.healthy is False
    assert v.loop_wedged is False   # <- the whole point: do not escalate an upstream outage


async def test_wedged_loop_escalates_even_while_the_feed_looks_fresh():
    """The incident shape: quotes linger in Redis (26h TTL) so the feed can look OK
    while the loop that writes them is dead. The heartbeat is what catches it."""
    v = await ingest_health.evaluate(_live_redis(feed_age=10, hb_age=9999, uptime=9000), _settings())
    assert v.loop_wedged is True
    assert v.healthy is False


async def test_no_escalation_inside_the_start_grace_window():
    """A fresh container inherits the previous process's stale heartbeat; grace stops
    that from becoming an instant restart loop."""
    v = await ingest_health.evaluate(_live_redis(feed_age=10, hb_age=9999, uptime=5), _settings())
    assert v.loop_wedged is False


async def test_no_escalation_when_uptime_is_unknown():
    """No started-at key -> we cannot judge grace, so fail safe and never kill."""
    v = await ingest_health.evaluate(_live_redis(feed_age=10, hb_age=9999, uptime=None), _settings())
    assert v.loop_wedged is False


async def test_missing_heartbeat_past_grace_counts_as_wedged():
    v = await ingest_health.evaluate(_live_redis(feed_age=10, hb_age=None, uptime=9000), _settings())
    assert v.loop_wedged is True


# ── the CLI exit contract + the kill escalation ──
async def test_cli_exits_zero_when_healthy(monkeypatch):
    killed = []
    monkeypatch.setattr(ingest_health, "_force_restart", lambda: killed.append(1))
    monkeypatch.setattr(ingest_health, "_probe_redis", lambda: _live_redis(feed_age=10, hb_age=10, uptime=9000))
    assert await ingest_health._main() == 0
    assert killed == []


async def test_cli_exits_nonzero_on_stale_feed_without_killing(monkeypatch):
    killed = []
    monkeypatch.setattr(ingest_health, "_force_restart", lambda: killed.append(1))
    monkeypatch.setattr(ingest_health, "_probe_redis", lambda: _live_redis(feed_age=9999, hb_age=10, uptime=9000))
    assert await ingest_health._main() == 1
    assert killed == []


async def test_cli_kills_pid1_when_the_loop_is_wedged(monkeypatch):
    killed = []
    monkeypatch.setattr(ingest_health, "_force_restart", lambda: killed.append(1))
    monkeypatch.setattr(ingest_health, "_probe_redis", lambda: _live_redis(feed_age=10, hb_age=9999, uptime=9000))
    assert await ingest_health._main() == 1
    assert killed == [1]


async def test_cli_never_kills_when_the_probe_itself_errors(monkeypatch):
    """A Redis blip is an unhealthy report, never a restart — else Redis flapping
    would thrash the ingest container."""
    killed = []

    class _Boom:
        async def get(self, key):
            raise RuntimeError("redis down")

    monkeypatch.setattr(ingest_health, "_force_restart", lambda: killed.append(1))
    monkeypatch.setattr(ingest_health, "_probe_redis", lambda: _Boom())
    assert await ingest_health._main() == 1
    assert killed == []


# ── deadman latch survives a task restart (the regression behind the 8h45m outage) ──
def test_deadman_latch_is_process_global_not_task_local():
    """If the deadman task raises, _supervise restarts it. A task-LOCAL latch would
    reset to False and — the feed already being stale — could never re-arm, silently
    disarming the deadman for the rest of the process's life."""
    ingest_main._reset_deadman_latch()
    assert ingest_main._deadman_should_exit(5.0, health_stale_s=300, deadman_stale_s=300) is False  # live: arms
    # simulate the deadman task dying and being restarted while the feed is stale
    assert ingest_main._deadman_should_exit(9999.0, health_stale_s=300, deadman_stale_s=300) is True


def test_deadman_never_fires_on_a_feed_that_never_came_up():
    """Alpaca outage at boot must not become a restart loop."""
    ingest_main._reset_deadman_latch()
    assert ingest_main._deadman_should_exit(None, health_stale_s=300, deadman_stale_s=300) is False
    assert ingest_main._deadman_should_exit(9999.0, health_stale_s=300, deadman_stale_s=300) is False


# ── against a real Redis client contract (not the hand-rolled fake above) ──
async def test_liveness_keys_roundtrip_through_a_real_redis_client():
    """Guards the wire format: decode_responses, key names, and ISO parsing all have to
    agree between the writer (ingest loop) and the reader (healthcheck process)."""
    import fakeredis.aioredis

    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    assert await ingest_health.heartbeat_age(r) is None      # nothing written yet
    assert await ingest_health.uptime_s(r) is None

    await ingest_health.mark_started(r)
    await ingest_health.write_heartbeat(r)
    assert await ingest_health.heartbeat_age(r) < 5
    assert await ingest_health.uptime_s(r) < 5

    # A fresh process with no quotes: feed stale, but inside grace -> never escalate.
    v = await ingest_health.evaluate(r, _settings())
    assert v.feed_stale is True
    assert v.loop_wedged is False
    await r.aclose()


async def test_wedged_loop_wins_when_the_feed_is_stale_too():
    """The real incident cascade: the loop wedges, so quotes stop being written and BOTH
    signals go stale together. The wedge must still be recognised — otherwise the cure
    (restart) is never applied to the one failure that needs it."""
    v = await ingest_health.evaluate(_live_redis(feed_age=9999, hb_age=9999, uptime=9000), _settings())
    assert v.feed_stale is True
    assert v.loop_wedged is True
    assert v.healthy is False


async def test_cli_kills_on_the_full_incident_shape(monkeypatch):
    killed = []
    monkeypatch.setattr(ingest_health, "_force_restart", lambda: killed.append(1))
    monkeypatch.setattr(ingest_health, "_probe_redis", lambda: _live_redis(feed_age=9999, hb_age=9999, uptime=9000))
    assert await ingest_health._main() == 1
    assert killed == [1]


# ── the heartbeat job actually beats ──
async def test_heartbeat_job_writes_beats_while_the_loop_runs():
    import fakeredis.aioredis
    from neuromancing_shared import price_store

    from app.config import get_settings

    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    get_settings().ingest_heartbeat_interval_s = 0.01
    get_settings().ingest_debug_freeze_enabled = False
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(price_store, "_redis", r)
        task = asyncio.create_task(ingest_main._heartbeat_job())
        await asyncio.sleep(0.05)
        assert await ingest_health.heartbeat_age(r) is not None   # it beat
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    await r.aclose()


async def test_wedge_drill_suppresses_the_beat_so_escalation_can_be_exercised():
    """Story 22: freezing quotes alone cannot exercise the SIGKILL path, because the beat
    keeps running. The wedge key is what makes the drill reach escalation."""
    import fakeredis.aioredis
    from neuromancing_shared import price_store

    from app.config import get_settings

    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    get_settings().ingest_heartbeat_interval_s = 0.01
    get_settings().ingest_debug_freeze_enabled = True
    await r.set(ingest_main.WEDGE_KEY, "1")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(price_store, "_redis", r)
        task = asyncio.create_task(ingest_main._heartbeat_job())
        await asyncio.sleep(0.05)
        assert await ingest_health.heartbeat_age(r) is None   # suppressed -> looks wedged
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    await r.aclose()
