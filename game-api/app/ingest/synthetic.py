"""Synthetic random-walk feed used when no Alpaca key is configured, so the whole
game runs with no keys and swaps to live data when keys land. Each tick emits
one 1m bar + a fresh quote per symbol into the Redis hot cache; Parquet durability
is handled by the shared flush job (single writer). Prices continue from the last
stored bar so a restart doesn't jump the walk.
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timezone

from neuromancing_shared import price_store

from ..config import get_settings
from .universe import DEFAULT_CRYPTO, equity_universe, is_crypto, seed_price

log = logging.getLogger("neuromancing.ingest.synthetic")

TICK_SECONDS = 5.0  # one synthetic 1m bar every 5s so charts fill quickly


async def run(redis=None) -> None:  # redis arg kept for call-site compatibility
    settings = get_settings()
    symbols = list(equity_universe(settings.price_universe)) + DEFAULT_CRYPTO
    rng = random.Random(1337)  # seeded for reproducible-ish dev walks
    prices: dict[str, float] = {}

    # Seed from last stored close (survives restarts) or the deterministic seed.
    for sym in symbols:
        last = await price_store.get_last_bar(sym, "1m")
        prices[sym] = last["close"] if last else seed_price(sym)

    log.info("synthetic feed started for %d symbols (tick=%.1fs)", len(symbols), TICK_SECONDS)
    while True:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        for sym in symbols:
            px = prices[sym]
            vol = 0.02 if is_crypto(sym) else 0.008  # crypto is jumpier
            drift = rng.uniform(-vol, vol)
            new_px = max(0.01, px * (1 + drift))
            o, c = px, new_px
            h = max(o, c) * (1 + rng.uniform(0, vol / 2))
            l = min(o, c) * (1 - rng.uniform(0, vol / 2))
            prices[sym] = new_px
            bar = {"ts": now, "open": o, "high": h, "low": l, "close": c,
                   "volume": rng.uniform(1000, 100000)}
            await price_store.ingest_bars(sym, "1m", [bar], archive=False)
            spread = new_px * 0.0005
            await price_store.write_quote(sym, bid=round(new_px - spread, 4),
                                          ask=round(new_px + spread, 4),
                                          last=round(new_px, 4), ts=now)
        await asyncio.sleep(TICK_SECONDS)
