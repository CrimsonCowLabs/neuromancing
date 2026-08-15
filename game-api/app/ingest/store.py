"""Ingest write helpers over the two-tier price store (Redis hot + Parquet).

Bars no longer land in Timescale — `neuromancing_shared.price_store` owns the bar
plane. This module just adapts Alpaca/synthetic bar objects into the plain
`{ts, open, high, low, close, volume}` dicts the store expects, and re-exports the
quote writer so the single ingest writer has one import surface.
"""

from __future__ import annotations

from datetime import datetime

from neuromancing_shared import price_store
from neuromancing_shared.price_store import write_quote  # re-export

__all__ = ["write_quote", "bar_dict", "alpaca_bar_dict", "store_bars"]


def bar_dict(ts: datetime, o: float, h: float, l: float, c: float, volume: float = 0.0) -> dict:
    return {"ts": ts, "open": float(o), "high": float(h), "low": float(l),
            "close": float(c), "volume": float(volume or 0)}


def alpaca_bar_dict(b) -> dict:
    """Adapt an alpaca-py Bar (historical or live) into a store bar dict."""
    return bar_dict(b.timestamp, b.open, b.high, b.low, b.close,
                    getattr(b, "volume", 0) or 0)


async def store_bars(symbol: str, timeframe: str, bars: list[dict], *, archive: bool = False) -> None:
    await price_store.ingest_bars(symbol, timeframe, bars, archive=archive)
