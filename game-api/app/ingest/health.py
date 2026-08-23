"""Feed-freshness health: the single source of truth for "is the market-data feed live?"

Crypto trades 24/7, so a stale crypto quote means the feed has stalled — this is what the
watchdog (reconnect), the deadman (process restart), the container healthcheck, and the UI
"data stale" banner all key off. `crypto_feed_age` returns the age (seconds) of the FRESHEST
crypto quote, or None if none exist. The CLI (`python -m app.ingest.health`) exits 0 when the
feed is fresh, 1 when stale/missing — wired as the market-ingest container healthcheck.

Reuses the `quote:{symbol}` JSON written by price_store.write_quote.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from .universe import DEFAULT_CRYPTO


async def crypto_feed_age(redis, symbols: list[str] | None = None) -> float | None:
    """Age (seconds) of the freshest crypto quote across `symbols` (default DEFAULT_CRYPTO),
    or None if none are present. Lower = fresher; None = total loss / not yet warm."""
    symbols = symbols or DEFAULT_CRYPTO
    now = datetime.now(timezone.utc)
    youngest: float | None = None
    for sym in symbols:
        raw = await redis.get(f"quote:{sym}")
        if not raw:
            continue
        try:
            ts = datetime.fromisoformat(json.loads(raw)["ts"])
        except (ValueError, KeyError, TypeError):
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age = (now - ts).total_seconds()
        if youngest is None or age < youngest:
            youngest = age
    return youngest


def is_stale(age: float | None, threshold: float) -> bool:
    """A feed is stale if the freshest quote is older than `threshold` — or absent (None)."""
    return age is None or age > threshold


async def _main() -> int:
    from neuromancing_shared import price_store

    from ..config import get_settings

    try:
        age = await crypto_feed_age(price_store._redis)
    except Exception as e:  # noqa: BLE001 — a Redis blip is itself an unhealthy feed
        print(f"health probe error: {e} -> UNHEALTHY")
        return 1
    threshold = get_settings().ingest_health_stale_s
    stale = is_stale(age, threshold)
    print(f"crypto_feed_age={'none' if age is None else f'{age:.0f}s'} "
          f"threshold={threshold:.0f}s -> {'STALE' if stale else 'fresh'}")
    return 1 if stale else 0


if __name__ == "__main__":
    import asyncio
    import sys

    sys.exit(asyncio.run(_main()))
