"""market-ingest entrypoint — the SINGLE writer of the bar plane.

Every task that writes bars (backfill, equity poller, crypto stream/refresh, the
Parquet flush job, and gap-fill) runs here as a supervised in-process asyncio
task with restart-on-error backoff. Nothing else writes Parquet/Redis bars — a
second writer would corrupt the archive. See docs/06-orchestration.md.

Live Alpaca when ALPACA_API_KEY is set; otherwise a synthetic random-walk feed so
the game runs without keys. Both write to the two-tier price store.

Recovery is layered, and the redundancy is deliberate — each layer fails in a way the
next one is specifically built to survive:

    layer                  handles                        blind to
    ─────────────────────  ─────────────────────────────  ──────────────────────────
    _supervise watchdog    a silent stream, loop alive    a wedged loop
    deadman (OS thread)    prolonged feed staleness       a dead/unschedulable process
    healthcheck escalation a WEDGED LOOP (SIGKILLs PID 1) an unresponsive container

The ordering matters: thresholds run watchdog < deadman < heartbeat, so the cheapest
in-process fix always gets first attempt and only a genuinely wedged loop reaches the
out-of-process kill. See app/ingest/health.py for why that last layer has to exist.

Run: `uv run python -m app.ingest.main`
"""

from __future__ import annotations

import asyncio
import logging
import threading
from contextlib import suppress

from neuromancing_shared import price_store

from app.config import get_settings

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("neuromancing.ingest")


class _Stalled(Exception):
    """Raised by the watchdog when a task is alive but has gone silent (no fresh data) —
    a *restart* signal, distinct from a real shutdown CancelledError."""


async def _supervise(name: str, factory, *, freshness=None, max_silence: float | None = None) -> None:
    """Run a long-lived task, restarting it with backoff if it raises OR — when a
    `freshness` probe (async `() -> age_seconds|None`) is given — if it goes SILENT
    (age > max_silence) while still running. The silent-stall case is the one the plain
    restart-on-exception model can't see (a half-open socket hangs without raising)."""
    backoff = 2
    interval = get_settings().ingest_watch_interval_s
    while True:
        task = asyncio.create_task(factory())
        try:
            if freshness is not None and max_silence is not None:
                await _watch(name, task, freshness, max_silence, interval)
            else:
                await task
            backoff = 2  # clean return -> reset (loops normally never return)
        except asyncio.CancelledError:
            task.cancel()
            raise
        except _Stalled:
            log.error("FEED STALL: %r silent > %ss — cancelling + reconnecting", name, max_silence)
            task.cancel()
            with suppress(BaseException):
                await task
            await asyncio.sleep(backoff)
            backoff = min(60, backoff * 2)
        except Exception as e:  # noqa: BLE001
            log.warning("ingest task %r crashed (%s); restarting in %ss", name, e, backoff)
            await asyncio.sleep(backoff)
            backoff = min(60, backoff * 2)


async def _watch(name, task, freshness, max_silence, interval) -> None:
    """Await `task`, but if `freshness()` reports staleness > max_silence while it's still
    running, raise _Stalled (the supervisor cancels + restarts it). A freshly-(re)started
    task gets `max_silence` grace before it's judged, so pre-existing stale quotes (from
    before this run started, or a slow first connect) don't trigger a false restart."""
    started = asyncio.get_running_loop().time()
    while True:
        done, _ = await asyncio.wait({task}, timeout=interval)
        if task in done:
            await task  # propagate its return / exception
            return
        if asyncio.get_running_loop().time() - started < max_silence:
            continue  # grace: give the run time to produce its first fresh data
        age = await freshness()
        if age is not None and age > max_silence:
            raise _Stalled()


async def _run_once(name: str, factory) -> None:
    """Run a one-shot task once, logging (not raising) on failure."""
    try:
        await factory()
        log.info("ingest task %r completed", name)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        log.warning("ingest one-shot %r failed: %s", name, e)


async def _flush_job() -> None:
    settings = get_settings()
    while True:
        await asyncio.sleep(settings.price_flush_seconds)
        try:
            n = await price_store.flush_all()
            log.info("flushed %d bars to Parquet", n)
        except Exception as e:  # noqa: BLE001
            log.warning("parquet flush failed: %s", e)


async def _gapfill_job() -> None:
    from app.ingest import backfill

    settings = get_settings()
    while True:
        await asyncio.sleep(settings.price_gapfill_seconds)
        try:
            await backfill.run_recent(days=1)
        except Exception as e:  # noqa: BLE001
            log.warning("gap-fill failed: %s", e)


WEDGE_KEY = "ingest:debug:wedge"


async def _heartbeat_job() -> None:
    """Stamp a liveness beat so an OUT-OF-PROCESS observer can tell "the feed stalled"
    apart from "the event loop is wedged". This is an ordinary asyncio task, which is
    exactly the point: if the loop stops scheduling, the beat stops — and only the
    container healthcheck (a separate process) can act on that. See app/ingest/health.py.

    A dedicated task rather than a beat inside `_supervise`: that supervisor's outer loop
    only iterates when a task RESTARTS, so beating from there would signal liveness on
    crash and never during healthy running — backwards. The trade-off is that this proves
    the loop schedules, not that every individual writer is progressing; feed freshness is
    the signal that covers the latter."""
    from app.ingest import health

    settings = get_settings()
    while True:
        # Drill hook (inert unless INGEST_DEBUG_FREEZE_ENABLED): setting the wedge key
        # stops the beat, simulating a frozen loop so the healthcheck's SIGKILL path can
        # be exercised live. Freezing quotes alone can't do that — the beat continues.
        if settings.ingest_debug_freeze_enabled and await price_store._redis.get(WEDGE_KEY):
            log.error("DEBUG: heartbeat SUPPRESSED (fault injection) — expect escalation")
        else:
            await health.write_heartbeat(price_store._redis)
        await asyncio.sleep(settings.ingest_heartbeat_interval_s)


# Deadman latch — PROCESS-global on purpose. It was task-local, so when the deadman task
# raised, _supervise restarted it with the latch reset to False; the feed being stale by
# then, it could never re-arm and the deadman was silently disarmed for the life of the
# process. That is the regression behind the 2026-08-27 8h45m outage.
_seen_fresh = False

# Backoff before the deadman thread re-arms after an unexpected error (e.g. Redis not up
# yet at boot). Short: a disarmed deadman is the thing we are protecting against.
_DEADMAN_RETRY_S = 30.0


def _reset_deadman_latch() -> None:
    """Test seam: restore the never-been-live state."""
    global _seen_fresh
    _seen_fresh = False


def _deadman_should_exit(age: float | None, *, health_stale_s: float, deadman_stale_s: float) -> bool:
    """True when the feed has gone stale AFTER having been live at least once. A feed that
    never came up (Alpaca outage at boot) must never fire this, or boot becomes a restart loop."""
    from app.ingest import health

    global _seen_fresh
    if age is not None and age <= health_stale_s:
        _seen_fresh = True
        return False
    return _seen_fresh and health.is_stale(age, deadman_stale_s)


def _deadman_thread() -> None:
    """Escalation backstop for a stale-but-recoverable feed: hard-exit so
    `restart: unless-stopped` gives a fresh container (fresh sockets/SDK client) — the case
    the in-loop watchdog reconnect can't fix.

    Runs on a daemon OS THREAD with its own synchronous Redis connection so that a starved
    or wedged event loop cannot disarm it. As an asyncio task it froze along with the loop
    it was meant to police. (A fully wedged loop is still the healthcheck's job — this only
    shortens recovery for the far more common feed-stall case.)"""
    import os
    import time

    from neuromancing_shared.redisio import make_redis_sync

    from app.ingest import health

    # Nothing supervises this thread, so it must supervise itself: connecting can fail
    # (Redis is still starting when we are), and an unguarded raise here would end the
    # thread silently for the life of the process — the same permanent-disarm failure the
    # process-global latch exists to prevent. The latch survives these retries by design.
    while True:
        try:
            settings = get_settings()
            client = make_redis_sync(settings.redis_url)
            while True:
                time.sleep(settings.ingest_deadman_poll_s)
                age = health.crypto_feed_age_sync(client)
                if _deadman_should_exit(
                    age,
                    health_stale_s=settings.ingest_health_stale_s,
                    deadman_stale_s=settings.ingest_deadman_stale_s,
                ):
                    log.critical(
                        "DEADMAN: crypto feed stale (age=%s > %ss) after being live — "
                        "hard-exiting for a fresh container restart",
                        "none" if age is None else f"{age:.0f}s", settings.ingest_deadman_stale_s)
                    os._exit(1)
        except Exception as e:  # noqa: BLE001 — never let the deadman die quietly
            log.warning("deadman thread error (%s); re-arming in %ss", e, _DEADMAN_RETRY_S)
            time.sleep(_DEADMAN_RETRY_S)


async def main() -> None:
    settings = get_settings()
    from app.ingest import health

    # Stamp the start marker BEFORE any slow work: it gates the healthcheck's escalation
    # grace, and until it lands a fresh container is indistinguishable from a wedged one.
    await health.mark_started(price_store._redis)
    tasks: list = [
        asyncio.create_task(_supervise("flush", _flush_job)),
        asyncio.create_task(_supervise("heartbeat", _heartbeat_job)),
    ]

    if settings.alpaca_api_key:
        from app.ingest import alpaca_feed, backfill, equity_poll

        log.info("ALPACA_API_KEY present — live feed (crypto ws + equity poller)")
        # Warm history in the background (one-shot); do NOT block the live feed on it.
        asyncio.create_task(_run_once("backfill", lambda: backfill.run(archive=True)))
        # The crypto stream is the always-on 24/7 feed — guard it with a freshness watchdog
        # (a half-open socket hangs without raising, so restart-on-exception can't see it).
        tasks.append(asyncio.create_task(_supervise(
            "crypto-stream", alpaca_feed.run_crypto_stream,
            freshness=lambda: health.crypto_feed_age(price_store._redis),
            max_silence=settings.ingest_crypto_max_silence_s)))
        # Daemon OS thread, not an asyncio task — see _deadman_thread.
        threading.Thread(target=_deadman_thread, name="deadman", daemon=True).start()
        if settings.ingest_debug_freeze_enabled:  # drills only — inert unless enabled
            tasks.append(asyncio.create_task(_supervise("freeze-flag", alpaca_feed.watch_freeze_flag)))
        tasks.append(asyncio.create_task(
            _supervise("crypto-higher-tf", alpaca_feed.refresh_crypto_higher_tf)))
        for tf in settings.timeframes:
            tasks.append(asyncio.create_task(
                _supervise(f"equity-poll-{tf}", lambda tf=tf: equity_poll.poll_timeframe(tf))))
        tasks.append(asyncio.create_task(_supervise("gapfill", _gapfill_job)))
    else:
        from app.ingest import synthetic

        log.info("no ALPACA_API_KEY — synthetic feed (dev)")
        tasks.append(asyncio.create_task(_supervise("synthetic", synthetic.run)))

    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
