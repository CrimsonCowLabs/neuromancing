"""Load price bars for the strategy engine.

Bars live in the two-tier price store (`neuromancing_shared.price_store`): recent
bars from the Redis hot cache, deeper history from the Parquet archive via DuckDB.
This is the single bar reader for trade-api — the old cross-schema read of
`game.price_bar` is gone, so trade-api no longer couples to the game schema.
The `session` arg is retained for call-site compatibility (unused now).
"""

from __future__ import annotations

from datetime import datetime

from neuromancing_shared import price_store
from sqlalchemy.ext.asyncio import AsyncSession

from .base import Bar


async def load_bars(
    session: AsyncSession | None, symbol: str, timeframe: str = "1m", limit: int = 200,
    window: tuple[datetime, datetime] | None = None,
) -> list[Bar]:
    if window is not None:  # backtest walk-forward span [start, end)
        rows = await price_store.get_window(symbol, timeframe, window[0], window[1])
    else:
        rows = await price_store.get_bars(symbol, timeframe, limit)  # ascending
    return [
        Bar(
            ts=r["ts"],
            open=float(r["open"]),
            high=float(r["high"]),
            low=float(r["low"]),
            close=float(r["close"]),
            volume=float(r["volume"]),
        )
        for r in rows
    ]
