"""Calibrate the synthetic IV surface to real Alpaca option-chain snapshots.

Our pricer sells premium into `iv_model(spot, K, T, rv) = rv * vrp_mult * (1 - skew*m)
* (1 + term*T)` where m = ln(K/spot). The knobs (vrp_mult, skew, term) are the whole
fidelity story — with the wrong surface, backtested premium-selling edge is fiction.

This tool fetches Alpaca option snapshots (which carry observed IV per contract), forms
y = alpaca_iv / our_realized_vol, and least-squares fits y ≈ a + b·m + c·T, which maps to
vrp_mult=a, skew=-b/a, term=c/a. Prints the calibrated knobs to paste into config.

Entitlement-safe: if the account can't pull options data (the free/IEX plan may not),
it reports that clearly and leaves the synthetic defaults untouched. Backtest-only tool —
run on demand, not in the ingest loop:

    docker compose exec game-api uv run python -m app.ingest.options_calibrate AAPL MSFT SPY
"""

from __future__ import annotations

import asyncio
import logging
import math
import sys
from datetime import date

from neuromancing_shared import price_store
from neuromancing_shared.options.vol import realized_vol

from ..config import get_settings

log = logging.getLogger("neuromancing.options.calibrate")

# Calibrate on the region our structures actually trade — ~30 DTE, near the money
# (the 0.16–0.30 delta band ≈ |log-moneyness| ≤ ~0.15). A single linear fit over the
# WHOLE surface (all DTEs, deep wings) is misspecified (the smile is convex) and lets
# near-dated contracts wreck the term estimate — so we focus tight and fit vrp+skew only.
_RV_WINDOW = 20
_MIN_DTE, _MAX_DTE = 21, 45
_MAX_ABS_M = 0.20
_IV_BAND = (0.05, 2.0)


def parse_occ(sym: str) -> tuple[str, date, float, str] | None:
    """OCC-21: {underlying}{yymmdd}{C|P}{strike*1000, 8 digits}."""
    s = sym.replace(" ", "")
    try:
        strike = int(s[-8:]) / 1000.0
        right = "call" if s[-9].upper() == "C" else "put"
        yy, mm, dd = int(s[-15:-13]), int(s[-13:-11]), int(s[-11:-9])
        return s[:-15], date(2000 + yy, mm, dd), strike, right
    except (ValueError, IndexError):
        return None


def _ols(X: list[list[float]], y: list[float]) -> list[float]:
    """Ordinary least squares via the normal equations (small n regressors)."""
    n = len(X[0])
    xtx = [[sum(X[r][i] * X[r][j] for r in range(len(X))) for j in range(n)] for i in range(n)]
    xty = [sum(X[r][i] * y[r] for r in range(len(X))) for i in range(n)]
    # Gaussian elimination with partial pivoting.
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(xtx[r][c]))
        xtx[c], xtx[p] = xtx[p], xtx[c]
        xty[c], xty[p] = xty[p], xty[c]
        piv = xtx[c][c] or 1e-12
        for r in range(n):
            if r == c:
                continue
            f = xtx[r][c] / piv
            for k in range(c, n):
                xtx[r][k] -= f * xtx[c][k]
            xty[r] -= f * xty[c]
    return [xty[i] / (xtx[i][i] or 1e-12) for i in range(n)]


def _fetch_alpaca_iv(underlyings: list[str], feed: str) -> tuple[list[dict], list[str]]:
    """Pull option-chain snapshots and extract observed IV per contract. Returns
    (rows, errors); rows carry {underlying, expiry, strike, right, iv}."""
    from alpaca.data.historical.option import OptionHistoricalDataClient
    from alpaca.data.requests import OptionChainRequest

    s = get_settings()
    client = OptionHistoricalDataClient(s.alpaca_api_key, s.alpaca_api_secret)
    rows: list[dict] = []
    errors: list[str] = []
    for u in underlyings:
        try:
            try:
                chain = client.get_option_chain(OptionChainRequest(underlying_symbol=u, feed=feed))
            except TypeError:  # older alpaca-py without a feed kwarg
                chain = client.get_option_chain(OptionChainRequest(underlying_symbol=u))
        except Exception as e:  # noqa: BLE001 — entitlement/plan/network
            errors.append(f"{u}: {type(e).__name__}: {e}")
            continue
        got = 0
        for sym, snap in (chain or {}).items():
            iv = getattr(snap, "implied_volatility", None)
            parsed = parse_occ(sym)
            if iv is None or parsed is None:
                continue
            _, expiry, strike, right = parsed
            rows.append({"underlying": u, "expiry": expiry, "strike": strike,
                         "right": right, "iv": float(iv)})
            got += 1
        if got == 0:
            errors.append(f"{u}: chain returned no IV-bearing contracts (feed={feed})")
    return rows, errors


async def calibrate(underlyings: list[str], feed: str = "indicative") -> dict:
    rows, errors = await asyncio.to_thread(_fetch_alpaca_iv, underlyings, feed)

    # Our realized vol per underlying (the base the knobs scale).
    rv: dict[str, float] = {}
    for u in {r["underlying"] for r in rows}:
        last = await price_store.get_last_bar(u, "1d")
        bars = await price_store.get_bars(u, "1d", limit=_RV_WINDOW + 5)
        v = realized_vol([b["close"] for b in bars], _RV_WINDOW, "1d") if last else None
        if v:
            rv[u] = v

    today = date.today()
    X, y, used = [], [], 0
    for r in rows:
        base = rv.get(r["underlying"])
        if not base:
            continue
        dte = (r["expiry"] - today).days
        if not (_MIN_DTE <= dte <= _MAX_DTE) or not (_IV_BAND[0] <= r["iv"] <= _IV_BAND[1]):
            continue
        last = None
        # spot: reuse the daily last-bar close (already fetched into rv computation)
        # (cheap re-read; small N of underlyings)
        spot_bar = await price_store.get_last_bar(r["underlying"], "1d")
        spot = float(spot_bar["close"]) if spot_bar else None
        if not spot:
            continue
        m = math.log(r["strike"] / spot)
        if abs(m) > _MAX_ABS_M:
            continue
        X.append([1.0, m])
        y.append(r["iv"] / base)
        used += 1

    if used < 5:
        return {"ok": False, "reason": "insufficient Alpaca IV data to calibrate",
                "rows": len(rows), "used": used, "errors": errors[:8]}

    # Fit y = a + b·m on the near-the-money / ~30-DTE band → vrp=a, skew=-b/a. term left
    # at 0 for v1 (a single-DTE structure barely uses it, and a narrow band can't fit it).
    a, b = _ols(X, y)
    vrp = round(a, 4)
    skew = round(-b / a, 4) if a else 0.0
    resid = [y[i] - (a + b * X[i][1]) for i in range(used)]
    rmse = round((sum(e * e for e in resid) / used) ** 0.5, 4)
    return {"ok": True, "vrp_mult": vrp, "skew": skew, "term": 0.0, "rmse": rmse,
            "contracts_used": used, "underlyings": sorted(rv), "feed": feed,
            "errors": errors[:8]}


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    feed = "opra" if "--opra" in sys.argv else "indicative"
    underlyings = args or ["AAPL", "MSFT", "SPY", "NVDA", "AMZN"]
    res = asyncio.run(calibrate(underlyings, feed=feed))
    import json
    print(json.dumps(res, indent=2, default=str))
    if res.get("ok"):
        print("\n# Calibrated knobs — set in .env / config:")
        print(f"OPTIONS_VRP_MULT={res['vrp_mult']}")
        print(f"OPTIONS_SKEW={res['skew']}")
        print(f"OPTIONS_TERM={res['term']}")
    else:
        print("\n# Not calibrated — synthetic defaults retained. Reason:", res.get("reason"))


if __name__ == "__main__":
    main()
