"""Ingest health: the single source of truth for "is the market-data pipeline alive?"

Two INDEPENDENT signals, because they have different causes and different cures:

  * **Feed freshness** (`crypto_feed_age`) — crypto trades 24/7, so a stale crypto quote
    means the feed stalled. Cure: reconnect the stream. The in-loop watchdog and the
    deadman own this; restarting the process does not fix an Alpaca outage.
  * **Loop liveness** (`heartbeat_age`) — a beat written by a plain asyncio task, so a
    fresh beat proves the *event loop is turning*, not merely that the process exists.
    Cure: nothing in-process — a wedged loop takes its own watchdog down with it.

That second signal is why this module can kill. On 2026-08-27 the ingest event loop
wedged for 8h45m: PID 1 stayed alive at 0% CPU, so `restart: unless-stopped` never
fired and the in-process deadman (an asyncio task on the wedged loop) froze with it.
This healthcheck reported the failure correctly 1,002 consecutive times and had no way
to act on it. It runs as a SEPARATE process inside the container, so it is the one piece
that survives a wedged loop — hence it escalates: on a stale heartbeat past the grace
window it SIGKILLs PID 1 and lets `restart: unless-stopped` supply a fresh container.

A supervisor must not share a failure domain with what it supervises.

Escalation is deliberately conservative — it never fires when the probe itself errors
(a Redis blip must not thrash the container), when uptime is unknown, or inside the
start-grace window (a fresh container inherits the previous process's stale heartbeat).

CLI (`python -m app.ingest.health`, wired as the container healthcheck): exits 0 when
healthy, 1 when the feed is stale or the loop is wedged.
"""

from __future__ import annotations

import json
import logging
import os
import signal
from dataclasses import dataclass
from datetime import datetime, timezone

from .universe import DEFAULT_CRYPTO

log = logging.getLogger("neuromancing.ingest.health")

# Liveness keys (plain ISO timestamps, distinct from the `quote:{symbol}` JSON blobs).
HEARTBEAT_KEY = "ingest:heartbeat"
STARTED_AT_KEY = "ingest:started_at"


def _parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        ts = datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        return None
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts


def _age_of(ts: datetime | None) -> float | None:
    return None if ts is None else (datetime.now(timezone.utc) - ts).total_seconds()


def _youngest_quote_age(raws: list[str | None]) -> float | None:
    """Age of the freshest parseable quote among already-fetched payloads. Shared by the
    async and blocking probes so the two can never disagree about what 'fresh' means."""
    youngest: float | None = None
    for raw in raws:
        if not raw:
            continue
        try:
            age = _age_of(_parse_ts(json.loads(raw)["ts"]))
        except (ValueError, KeyError, TypeError):
            continue
        if age is not None and (youngest is None or age < youngest):
            youngest = age
    return youngest


async def crypto_feed_age(redis, symbols: list[str] | None = None) -> float | None:
    """Age (seconds) of the freshest crypto quote across `symbols` (default DEFAULT_CRYPTO),
    or None if none are present. Lower = fresher; None = total loss / not yet warm."""
    symbols = symbols or DEFAULT_CRYPTO
    return _youngest_quote_age([await redis.get(f"quote:{s}") for s in symbols])


def crypto_feed_age_sync(client, symbols: list[str] | None = None) -> float | None:
    """Blocking twin of `crypto_feed_age`, for the deadman's OS thread — it must not
    depend on the event loop it exists to outlive."""
    symbols = symbols or DEFAULT_CRYPTO
    return _youngest_quote_age([client.get(f"quote:{s}") for s in symbols])


async def heartbeat_age(redis) -> float | None:
    """Age (seconds) of the last ingest heartbeat, or None if it has never been written.

    The beat comes from an ordinary asyncio task, so a fresh value means the event loop
    is still scheduling work — the liveness signal a hung process cannot fake."""
    return _age_of(_parse_ts(await redis.get(HEARTBEAT_KEY)))


async def uptime_s(redis) -> float | None:
    """Seconds since the running ingest process wrote its start marker, or None if absent.
    Rewritten on every boot, so it measures THIS process, not the container's lifetime."""
    return _age_of(_parse_ts(await redis.get(STARTED_AT_KEY)))


async def write_heartbeat(redis) -> None:
    await redis.set(HEARTBEAT_KEY, datetime.now(timezone.utc).isoformat())


async def mark_started(redis) -> None:
    """Stamp this process's start. Gates escalation: without it, grace cannot be judged
    and the healthcheck refuses to kill."""
    await redis.set(STARTED_AT_KEY, datetime.now(timezone.utc).isoformat())


def is_stale(age: float | None, threshold: float) -> bool:
    """A feed is stale if the freshest quote is older than `threshold` — or absent (None)."""
    return age is None or age > threshold


@dataclass(frozen=True)
class Verdict:
    """The two signals and the two conclusions drawn from them."""

    feed_age: float | None
    beat_age: float | None
    up_s: float | None
    feed_stale: bool
    loop_wedged: bool

    @property
    def healthy(self) -> bool:
        return not (self.feed_stale or self.loop_wedged)

    def summary(self) -> str:
        def fmt(v):
            return "none" if v is None else f"{v:.0f}s"

        return (f"crypto_feed_age={fmt(self.feed_age)} heartbeat_age={fmt(self.beat_age)} "
                f"uptime={fmt(self.up_s)} -> "
                f"{'WEDGED' if self.loop_wedged else 'STALE' if self.feed_stale else 'fresh'}")


async def evaluate(redis, settings) -> Verdict:
    """Probe both signals and decide. Raises if Redis is unreachable — the caller treats
    that as unhealthy but NEVER as grounds to escalate."""
    feed_age = await crypto_feed_age(redis)
    beat_age = await heartbeat_age(redis)
    up = await uptime_s(redis)

    # Escalate only once past grace AND with a known uptime: a fresh container inherits
    # the previous process's stale heartbeat, and killing on that is a restart loop.
    past_grace = up is not None and up > settings.ingest_escalation_grace_s
    return Verdict(
        feed_age=feed_age,
        beat_age=beat_age,
        up_s=up,
        feed_stale=is_stale(feed_age, settings.ingest_health_stale_s),
        loop_wedged=past_grace and is_stale(beat_age, settings.ingest_heartbeat_stale_s),
    )


def _force_restart() -> None:
    """SIGKILL PID 1 so `restart: unless-stopped` replaces the container. Seam: tests
    patch this rather than signalling themselves."""
    os.kill(1, signal.SIGKILL)


def _probe_redis():
    """Seam: the Redis handle the CLI probes. Patched in tests."""
    from neuromancing_shared import price_store

    return price_store._redis


async def _main() -> int:
    from ..config import get_settings

    try:
        verdict = await evaluate(_probe_redis(), get_settings())
    except Exception as e:  # noqa: BLE001 — a Redis blip is unhealthy, but never a restart
        print(f"health probe error: {e} -> UNHEALTHY")
        return 1

    print(verdict.summary())
    if verdict.loop_wedged:
        # The one case nothing in-process can fix: the loop that would restart the feed
        # is itself dead. Log before killing so the container log explains the restart.
        log.critical(
            "LOOP WEDGED: no ingest heartbeat for %s (uptime %.0fs) — SIGKILL PID 1 for a "
            "fresh container. %s",
            "ever" if verdict.beat_age is None else f"{verdict.beat_age:.0f}s",
            verdict.up_s, verdict.summary(),
        )
        print("LOOP WEDGED -> escalating: SIGKILL PID 1")
        _force_restart()
        return 1
    return 0 if verdict.healthy else 1


if __name__ == "__main__":
    import asyncio
    import sys

    logging.basicConfig(level=logging.INFO)
    sys.exit(asyncio.run(_main()))
