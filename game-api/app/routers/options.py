"""Public read: the synthetic option chain ("our own options table").

Generated on-demand from the underlying's spot + realized vol via the shared
Black-Scholes chain generator — no persistence, no Alpaca options subscription. This is
the same pricing path the options backtester uses, so a served chain matches a backtested
one. Backtest-only for now: the constructs don't hold live option positions yet.
"""

from __future__ import annotations

from datetime import date, timezone, datetime

from fastapi import APIRouter, HTTPException, Query

from neuromancing_shared import price_store
from neuromancing_shared.options.chain import build_chain
from neuromancing_shared.options.vol import realized_vol

from ..config import get_settings

router = APIRouter(prefix="/options", tags=["options"])

_RV_WINDOW = 20  # trailing daily bars for realized vol


@router.get("/chain/{underlying}")
async def get_chain(underlying: str, dte: int | None = Query(None, description="filter to one DTE")) -> dict:
    underlying = underlying.upper()
    s = get_settings()
    last = await price_store.get_last_bar(underlying, "1d")
    if last is None:
        raise HTTPException(404, f"no price history for {underlying}")
    spot = float(last["close"])
    bars = await price_store.get_bars(underlying, "1d", limit=_RV_WINDOW + 5)
    rv = realized_vol([b["close"] for b in bars], window=_RV_WINDOW, timeframe="1d")
    if rv is None:
        raise HTTPException(400, f"not enough history to estimate vol for {underlying}")

    expiries = s.options_expiry_ladder if dte is None else [dte]
    rows = build_chain(
        underlying, spot, date.today(), rv,
        expiries_dte=expiries, n_strikes=s.options_strikes,
        step_pct=s.options_strike_step_pct, r=s.options_risk_free_rate,
        q=s.options_div_yield, vrp_mult=s.options_vrp_mult, skew=s.options_skew,
        term=s.options_term,
    )
    return {
        "underlying": underlying, "spot": round(spot, 4), "realized_vol": round(rv, 4),
        "asof": datetime.now(timezone.utc).isoformat(),
        "synthetic": True, "contracts": len(rows), "chain": rows,
    }
