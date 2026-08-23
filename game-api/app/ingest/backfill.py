"""Historical bar backfill across the universe × timeframes, into the two-tier
price store (Parquet archive + Redis hot).

Runs as a BACKGROUND batch — it does not block ingest startup; agents run on
whatever is warmed. Batched multi-symbol Alpaca requests (chunked to
``price_batch_size``) with throttling keep us under rate limits. Idempotent: the
store dedups by ts, so overlapping re-fetches (and the periodic gap-fill) are safe.

⚠️ History depth depends on the Alpaca data feed/subscription — IEX (free) is
limited, so deep 1m history may need SIP. Depth is configured per timeframe via
PRICE_BACKFILL_DAYS.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from neuromancing_shared import price_store

from ..config import get_settings
from .rest import call_rest, inject_timeout
from .store import alpaca_bar_dict
from .tf import alpaca_tf
from .universe import DEFAULT_CRYPTO, equity_universe, is_crypto

log = logging.getLogger("neuromancing.ingest.backfill")

_THROTTLE_S = 0.3  # between batched requests


def _chunks(items: list[str], n: int):
    for i in range(0, len(items), n):
        yield items[i:i + n]


async def _persist(barset, timeframe: str, *, archive: bool) -> int:
    """Write one Alpaca BarSet (dict symbol -> [Bar]) into hot (+Parquet)."""
    data = getattr(barset, "data", {}) or {}
    n = 0
    for symbol, bars in data.items():
        if not bars:
            continue
        dicts = [alpaca_bar_dict(b) for b in bars]
        await price_store.ingest_bars(symbol, timeframe, dicts, archive=archive)
        last = bars[-1]
        await price_store.write_quote(symbol, bid=None, ask=None,
                                      last=float(last.close), ts=last.timestamp)
        n += len(dicts)
    return n


async def _equity_pass(stock_client, symbols, timeframe, start, feed, batch, *, archive):
    from alpaca.data.requests import StockBarsRequest

    total = 0
    for chunk in _chunks(symbols, batch):
        try:
            req = StockBarsRequest(symbol_or_symbols=chunk, timeframe=alpaca_tf(timeframe),
                                   start=start, feed=feed)
            barset = await call_rest(stock_client.get_stock_bars, req,
                                     timeout=get_settings().ingest_rest_timeout_s)
            total += await _persist(barset, timeframe, archive=archive)
        except Exception as e:  # noqa: BLE001
            log.warning("equity backfill %s chunk failed: %s", timeframe, e)
        await asyncio.sleep(_THROTTLE_S)
    return total


async def _crypto_pass(crypto_client, symbols, timeframe, start, *, archive):
    from alpaca.data.requests import CryptoBarsRequest

    try:
        req = CryptoBarsRequest(symbol_or_symbols=symbols, timeframe=alpaca_tf(timeframe),
                                start=start)
        barset = await call_rest(crypto_client.get_crypto_bars, req,
                                 timeout=get_settings().ingest_rest_timeout_s)
        return await _persist(barset, timeframe, archive=archive)
    except Exception as e:  # noqa: BLE001
        log.warning("crypto backfill %s failed: %s", timeframe, e)
        return 0


async def run(*, archive: bool = True, days_override: int | None = None) -> None:
    """Full backfill: each timeframe to its configured depth (or days_override)."""
    from alpaca.data.historical import CryptoHistoricalDataClient, StockHistoricalDataClient

    settings = get_settings()
    equities = list(equity_universe(settings.price_universe))
    now = datetime.now(timezone.utc)
    stock_client = inject_timeout(
        StockHistoricalDataClient(settings.alpaca_api_key, settings.alpaca_api_secret),
        settings.ingest_rest_timeout_s)
    crypto_client = inject_timeout(CryptoHistoricalDataClient(), settings.ingest_rest_timeout_s)

    for timeframe, days in settings.backfill_days.items():
        start = now - timedelta(days=days_override if days_override is not None else days)
        eq = await _equity_pass(stock_client, equities, timeframe, start,
                                settings.alpaca_data_feed, settings.price_batch_size,
                                archive=archive)
        cr = await _crypto_pass(crypto_client, DEFAULT_CRYPTO, timeframe, start, archive=archive)
        log.info("backfilled %s: %d equity + %d crypto bars", timeframe, eq, cr)


async def run_recent(days: int = 1) -> None:
    """Short-window re-fetch used by the periodic gap-fill (holes are re-filled;
    dedup makes it idempotent)."""
    await run(archive=True, days_override=days)
