"""market-ingest entrypoint — the SINGLE writer of the bar plane.

Every task that writes bars (backfill, equity poller, crypto stream/refresh, the
Parquet flush job, and gap-fill) runs here as a supervised in-process asyncio
task with restart-on-error backoff. Nothing else writes Parquet/Redis bars — a
second writer would corrupt the archive. See docs/06-orchestration.md.

Live Alpaca when ALPACA_API_KEY is set; otherwise a synthetic random-walk feed so
the game runs without keys. Both write to the two-tier price store.

Run: `uv run python -m app.ingest.main`
"""

from __future__ import annotations

import asyncio
import logging
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


async def _deadman_job() -> None:
    """Escalation backstop: if the crypto feed goes stale past the deadman threshold AFTER
    it has been live at least once, force a hard process exit so `restart: unless-stopped`
    gives a fully fresh container (fresh sockets/SDK client) — the case the in-loop watchdog
    reconnect can't fix. The `_seen_fresh` latch means a feed that never comes up (Alpaca
    outage at boot) never fires this, so it only escalates a genuine live->stale regression."""
    import os

    from app.ingest import health

    settings = get_settings()
    seen_fresh = False
    while True:
        await asyncio.sleep(30)
        age = await health.crypto_feed_age(price_store._redis)
        if age is not None and age <= settings.ingest_health_stale_s:
            seen_fresh = True
            continue
        if seen_fresh and health.is_stale(age, settings.ingest_deadman_stale_s):
            log.critical("DEADMAN: crypto feed stale (age=%s > %ss) after being live — "
                         "hard-exiting for a fresh container restart",
                         "none" if age is None else f"{age:.0f}s", settings.ingest_deadman_stale_s)
            os._exit(1)


async def main() -> None:
    settings = get_settings()
    tasks: list = [asyncio.create_task(_supervise("flush", _flush_job))]

    if settings.alpaca_api_key:
        from app.ingest import alpaca_feed, backfill, equity_poll, health

        log.info("ALPACA_API_KEY present — live feed (crypto ws + equity poller)")
        # Warm history in the background (one-shot); do NOT block the live feed on it.
        asyncio.create_task(_run_once("backfill", lambda: backfill.run(archive=True)))
        # The crypto stream is the always-on 24/7 feed — guard it with a freshness watchdog
        # (a half-open socket hangs without raising, so restart-on-exception can't see it).
        tasks.append(asyncio.create_task(_supervise(
            "crypto-stream", alpaca_feed.run_crypto_stream,
            freshness=lambda: health.crypto_feed_age(price_store._redis),
            max_silence=settings.ingest_crypto_max_silence_s)))
        tasks.append(asyncio.create_task(_supervise("deadman", _deadman_job)))
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
