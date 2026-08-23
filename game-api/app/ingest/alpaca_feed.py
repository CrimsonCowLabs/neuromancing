"""Live crypto market-data feed (used when ALPACA_API_KEY is set).

Crypto is few-symbol and 24/7, so it stays on the websocket (1m bars + quotes →
Redis hot). Equities moved to the batched REST poller (see equity_poll.py). A
periodic higher-timeframe crypto refresh keeps 5m/1h/1d hot. All writes go through
price_store (hot only here; Parquet durability is the flush job's).
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

from neuromancing_shared import price_store

from ..config import get_settings
from .rest import call_rest, inject_timeout
from .store import alpaca_bar_dict
from .tf import alpaca_tf, tf_seconds
from .universe import DEFAULT_CRYPTO

log = logging.getLogger("neuromancing.ingest.alpaca")


async def run_crypto_stream() -> None:
    """Supervised crypto websocket: 1m bars + quotes → Redis hot."""
    from alpaca.data.live import CryptoDataStream

    settings = get_settings()

    async def on_bar(bar) -> None:
        await price_store.ingest_bars(bar.symbol, "1m", [alpaca_bar_dict(bar)], archive=False)

    async def on_quote(q) -> None:
        bid = float(q.bid_price) if getattr(q, "bid_price", None) else None
        ask = float(q.ask_price) if getattr(q, "ask_price", None) else None
        last = ask or bid
        if last:
            await price_store.write_quote(q.symbol, bid=bid, ask=ask, last=last, ts=q.timestamp)

    # One connect+run per call. The SDK's _run_forever self-reconnects RAISED websocket
    # errors; _supervise (main.py) owns crash-restart AND the freshness-watchdog stall
    # restart (a half-open socket hangs inside _run_forever without raising — the SDK's own
    # reconnect can't see it, so the watchdog cancels this task and re-invokes us). The
    # finally guarantees the socket is torn down before the fresh reconnect.
    crypto = CryptoDataStream(settings.alpaca_api_key, settings.alpaca_api_secret)
    crypto.subscribe_bars(on_bar, *DEFAULT_CRYPTO)
    crypto.subscribe_quotes(on_quote, *DEFAULT_CRYPTO)
    log.info("starting Alpaca crypto stream (%d symbols)", len(DEFAULT_CRYPTO))
    try:
        await crypto._run_forever()  # private API — contained here; see docs / alpaca-py pin
    finally:
        with suppress(Exception):
            await asyncio.wait_for(crypto.close(), 10)


async def refresh_crypto_higher_tf() -> None:
    """Periodically pull the latest 5m/1h/1d crypto bars (the websocket only gives
    1m). One supervised task."""
    from datetime import datetime, timedelta, timezone

    from alpaca.data.historical import CryptoHistoricalDataClient
    from alpaca.data.requests import CryptoBarsRequest

    settings = get_settings()
    client = inject_timeout(CryptoHistoricalDataClient(), settings.ingest_rest_timeout_s)
    higher = [tf for tf in ("5m", "1h", "1d") if tf in settings.timeframes]
    log.info("crypto higher-tf refresh started: %s", higher)
    while True:
        for timeframe in higher:
            start = datetime.now(timezone.utc) - timedelta(seconds=tf_seconds(timeframe) * 4)
            try:
                req = CryptoBarsRequest(symbol_or_symbols=DEFAULT_CRYPTO,
                                        timeframe=alpaca_tf(timeframe), start=start)
                barset = await call_rest(client.get_crypto_bars, req,
                                         timeout=settings.ingest_rest_timeout_s)
                data = getattr(barset, "data", {}) or {}
                for symbol, bars in data.items():
                    if bars:
                        await price_store.ingest_bars(
                            symbol, timeframe, [alpaca_bar_dict(b) for b in bars], archive=False)
            except Exception as e:  # noqa: BLE001
                log.warning("crypto %s refresh failed: %s", timeframe, e)
        await asyncio.sleep(300)  # every 5 min covers 5m; 1h/1d change slowly
