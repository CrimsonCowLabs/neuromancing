"""Volatility model for the synthetic options table.

`realized_vol` is the empirical annualized vol of the underlying. `iv_model` turns it
into the implied vol we PRICE at — the single most important fidelity lever (see the
plan's critical review): real IV ≠ realized vol (variance-risk premium) and has skew +
term structure, both of which dominate the OTM strikes that CSPs/condors sell. The
functional form here is a crude parametric surface tuned by config knobs; calibrate the
knobs against Alpaca IV snapshots rather than trusting the defaults.
"""

from __future__ import annotations

import math

# Trading periods per year for annualizing daily-bar vol.
_PERIODS = {"1d": 252.0, "1h": 252.0 * 6.5, "5m": 252.0 * 78.0, "1m": 252.0 * 390.0}


def realized_vol(closes: list[float], window: int = 20, timeframe: str = "1d") -> float | None:
    """Annualized stdev of trailing log returns over `window` bars. Returns None if
    there isn't enough history. Caller must pass ONLY bars at//before the as-of time
    (no lookahead)."""
    px = [float(c) for c in closes if c and float(c) > 0]
    if len(px) < window + 1:
        return None
    rets = [math.log(px[i] / px[i - 1]) for i in range(len(px) - window, len(px))]
    n = len(rets)
    mean = sum(rets) / n
    var = sum((r - mean) ** 2 for r in rets) / (n - 1) if n > 1 else 0.0
    return math.sqrt(var) * math.sqrt(_PERIODS.get(timeframe, 252.0))


def iv_model(spot: float, strike: float, t_years: float, rv: float, *,
             vrp_mult: float = 1.15, skew: float = 0.35, term: float = 0.0,
             floor: float = 0.05) -> float:
    """Implied vol we price at = realized vol × variance-risk premium, then tilted by a
    linear log-moneyness skew (OTM puts richer) and a mild term slope.

    - `vrp_mult` (~1.1–1.3): real IV runs above realized vol. The biggest single knob.
    - `skew`: equity put skew — lower strikes (K<S) get a higher IV. 0 = flat.
    - `term`: contango if >0 (longer-dated slightly higher). 0 = flat term.
    All are config-tunable and meant to be calibrated to Alpaca IV snapshots.
    """
    if spot <= 0 or strike <= 0 or rv is None:
        return max(floor, rv or floor)
    iv = rv * vrp_mult
    m = math.log(strike / spot)          # <0 for strikes below spot
    iv *= (1.0 - skew * m)               # m<0 → factor>1 (richer downside)
    iv *= (1.0 + term * max(0.0, t_years))
    return max(floor, iv)
