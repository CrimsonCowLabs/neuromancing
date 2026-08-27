"""Build mark prices for a set of symbols.

Fast path: the latest Redis quote (`quote:{symbol}`, written by market-ingest).
Fallback: the last bar close from the price store (`get_last_bar`, Redis-first,
Parquet on miss) — so a position is ALWAYS marked at a real last-known price,
never $0. Without this, equity quotes expiring after market close would value
held equity positions at $0 and crater the leaderboard overnight (a valuation
artifact, not a real loss).
"""

from __future__ import annotations

import json
import logging

from neuromancing_shared import price_store
from neuromancing_shared.redisio import make_redis

from .config import get_settings

log = logging.getLogger("neuromancing.marks")
_redis = make_redis(get_settings().redis_url)


async def get_marks(symbols: list[str]) -> dict[str, str]:
    """Return {symbol: price_str} for the given symbols. Redis quote wins; missing
    symbols fall back to the last bar close from the price store."""
    symbols = list(dict.fromkeys(s for s in symbols if s))
    marks: dict[str, str] = {}
    if not symbols:
        return marks

    # Fast path — latest quotes from Redis (one round trip).
    pipe = _redis.pipeline()
    for s in symbols:
        pipe.get(f"quote:{s}")
    raws = await pipe.execute()

    missing: list[str] = []
    for s, raw in zip(symbols, raws):
        if raw:
            try:
                marks[s] = str(json.loads(raw)["last"])
                continue
            except (json.JSONDecodeError, KeyError, TypeError):
                pass
        missing.append(s)

    # Fallback — last known bar close from the price store.
    for s in missing:
        try:
            bar = await price_store.get_last_bar(s, "1m")
            if bar is not None:
                marks[s] = str(bar["close"])
        except Exception as e:  # noqa: BLE001 — never let marking break a tick
            log.warning("price_store mark fallback failed for %s: %s", s, e)

    return marks


async def get_marks_with_feed_age(symbols: list[str]) -> tuple[dict[str, str], float | None]:
    """`get_marks`, plus the age of the freshest 24/7 crypto quote — the system's single
    "is the pricing pipeline live?" signal, the same one behind the UI's stale banner.

    Deliberately NOT the age of these particular marks. The equity poller is gated to
    market hours, so equity quotes are hours old by design every night and weekend;
    judging a snapshot on its own marks' age would flag almost every out-of-hours
    valuation and make the flag noise. Crypto never closes, so its age isolates a broken
    pipeline from a closed market — which is exactly what went wrong on 2026-08-27."""
    from .ingest.health import crypto_feed_age

    marks = await get_marks(symbols)
    try:
        return marks, await crypto_feed_age(_redis)
    except Exception as e:  # noqa: BLE001 — a probe blip must never break a tick
        log.warning("feed-age probe failed: %s", e)
        return marks, None
