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

from neuromancing_shared import price_store

from app.config import get_settings

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("neuromancing.ingest")


async def _supervise(name: str, factory) -> None:
    """Run a long-lived task, restarting it with backoff if it ever raises."""
    backoff = 2
    while True:
        try:
            await factory()
            backoff = 2  # clean return -> reset (loops normally never return)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("ingest task %r crashed (%s); restarting in %ss", name, e, backoff)
            await asyncio.sleep(backoff)
            backoff = min(60, backoff * 2)


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


async def main() -> None:
    settings = get_settings()
    tasks: list = [asyncio.create_task(_supervise("flush", _flush_job))]

    if settings.alpaca_api_key:
        from app.ingest import alpaca_feed, backfill, equity_poll

        log.info("ALPACA_API_KEY present — live feed (crypto ws + equity poller)")
        # Warm history in the background (one-shot); do NOT block the live feed on it.
        asyncio.create_task(_run_once("backfill", lambda: backfill.run(archive=True)))
        tasks.append(asyncio.create_task(_supervise("crypto-stream", alpaca_feed.run_crypto_stream)))
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
