"""Equity price poller — replaces per-symbol websocket streaming for equities.

For each timeframe, on its own cadence, pulls the latest bars for the whole equity
universe via Alpaca's batched multi-symbol bars endpoint and appends them to the
Redis hot cache (+ latest quote). Gated to market hours so we don't hammer Alpaca
after-hours. Parquet durability is handled by the separate flush job — this path
writes hot only (archive=False). One task per timeframe, each supervised.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from neuromancing_shared import price_store

from ..agents import market_hours
from ..config import get_settings
from .rest import call_rest, inject_timeout
from .store import alpaca_bar_dict
from .tf import alpaca_tf, tf_seconds
from .universe import equity_universe

log = logging.getLogger("neuromancing.ingest.equity_poll")

_THROTTLE_S = 0.3


def _chunks(items, n):
    for i in range(0, len(items), n):
        yield items[i:i + n]


async def _poll_once(stock_client, symbols, timeframe, feed, batch) -> int:
    from alpaca.data.requests import StockBarsRequest

    # Pull a few timeframe-widths so we never miss the freshly-closed bar.
    start = datetime.now(timezone.utc) - timedelta(seconds=tf_seconds(timeframe) * 4)
    total = 0
    for chunk in _chunks(symbols, batch):
        try:
            req = StockBarsRequest(symbol_or_symbols=chunk, timeframe=alpaca_tf(timeframe),
                                   start=start, feed=feed)
            barset = await call_rest(stock_client.get_stock_bars, req,
                                     timeout=get_settings().ingest_rest_timeout_s)
            data = getattr(barset, "data", {}) or {}
            for symbol, bars in data.items():
                if not bars:
                    continue
                await price_store.ingest_bars(
                    symbol, timeframe, [alpaca_bar_dict(b) for b in bars], archive=False)
                last = bars[-1]
                await price_store.write_quote(symbol, bid=None, ask=None,
                                              last=float(last.close), ts=last.timestamp)
                total += len(bars)
        except Exception as e:  # noqa: BLE001
            log.warning("equity poll %s chunk failed: %s", timeframe, e)
        await asyncio.sleep(_THROTTLE_S)
    return total


async def poll_timeframe(timeframe: str) -> None:
    """Long-running per-timeframe poll loop (one supervised task per tf)."""
    from alpaca.data.historical import StockHistoricalDataClient

    settings = get_settings()
    stock_client = inject_timeout(
        StockHistoricalDataClient(settings.alpaca_api_key, settings.alpaca_api_secret),
        settings.ingest_rest_timeout_s)
    symbols = list(equity_universe(settings.price_universe))
    cadence = settings.poll_seconds.get(timeframe, tf_seconds(timeframe))
    log.info("equity poller started: tf=%s cadence=%ss symbols=%d", timeframe, cadence, len(symbols))
    while True:
        if await market_hours.equity_active():
            n = await _poll_once(stock_client, symbols, timeframe, settings.alpaca_data_feed,
                                 settings.price_batch_size)
            if n:
                log.debug("equity poll %s: %d bars", timeframe, n)
        await asyncio.sleep(cadence)
