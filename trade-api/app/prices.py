"""Price source. Latest quotes are written to Redis by market-ingest
(`quote:{symbol}` as a JSON hash of bid/ask/last/ts). The sim matcher reads them
here and refuses to fill on stale/missing prices."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from neuromancing_shared.money import D

# A quote older than this is considered stale and won't be filled against.
STALE_AFTER_S = 90.0


@dataclass(frozen=True)
class Quote:
    symbol: str
    bid: Decimal | None
    ask: Decimal | None
    last: Decimal
    ts: datetime
    stale: bool = False

    def side_price(self, side: str) -> Decimal:
        """Reference price before slippage: buy at ask, sell at bid, else last."""
        if side == "buy" and self.ask is not None:
            return self.ask
        if side == "sell" and self.bid is not None:
            return self.bid
        return self.last


def _redis_key(symbol: str) -> str:
    return f"quote:{symbol}"


async def get_quote(redis, symbol: str, *, now: datetime | None = None) -> Quote | None:
    """Read the latest quote for a symbol from Redis. Returns None if missing."""
    raw = await redis.get(_redis_key(symbol))
    if not raw:
        return None
    data = json.loads(raw)
    return quote_from_dict(symbol, data, now=now)


def quote_from_dict(symbol: str, data: dict, *, now: datetime | None = None) -> Quote:
    now = now or datetime.now(timezone.utc)
    ts = datetime.fromisoformat(data["ts"])
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    stale = (now - ts).total_seconds() > STALE_AFTER_S
    bid = D(data["bid"]) if data.get("bid") is not None else None
    ask = D(data["ask"]) if data.get("ask") is not None else None
    last = D(data["last"])
    return Quote(symbol=symbol, bid=bid, ask=ask, last=last, ts=ts, stale=stale)
