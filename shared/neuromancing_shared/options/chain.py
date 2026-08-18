"""Synthetic option-chain generator — "our own options table."

Given the underlying spot + its realized vol + the config knobs, produces a chain
(strike ladder × expiry ladder × {call,put}) priced with Black-Scholes over the IV model.
Pure: same code path the backtester prices on-demand with, so a served chain and a
backtested chain are always consistent. Greeks are returned in trader-friendly units
(theta per day, vega per vol point).
"""

from __future__ import annotations

from datetime import date, timedelta

from .black_scholes import greeks, price
from .vol import iv_model


def build_chain(underlying: str, spot: float, asof: date, rv: float, *,
                expiries_dte: list[int], n_strikes: int = 8, step_pct: float = 0.02,
                r: float = 0.04, q: float = 0.0, vrp_mult: float = 1.15,
                skew: float = 0.35, term: float = 0.0) -> list[dict]:
    if spot <= 0 or rv is None or rv <= 0:
        return []
    strikes = [round(spot * (1 + step_pct * k), 2) for k in range(-n_strikes, n_strikes + 1)]
    rows: list[dict] = []
    for dte in sorted(set(expiries_dte)):
        expiry = (asof + timedelta(days=int(dte))).isoformat()
        t_years = dte / 365.0
        for k in strikes:
            iv = iv_model(spot, k, t_years, rv, vrp_mult=vrp_mult, skew=skew, term=term)
            for right in ("call", "put"):
                g = greeks(spot, k, t_years, r, iv, right, q)
                rows.append({
                    "underlying": underlying, "expiry": expiry, "dte": int(dte),
                    "strike": k, "right": right,
                    "mid": round(price(spot, k, t_years, r, iv, right, q), 4),
                    "delta": round(g["delta"], 4), "gamma": round(g["gamma"], 6),
                    "theta": round(g["theta"] / 365.0, 4),   # per calendar day
                    "vega": round(g["vega"] / 100.0, 4),     # per 1 vol point
                    "iv": round(iv, 4), "underlying_price": round(spot, 4),
                })
    return rows


def strike_for_delta(spot: float, t_years: float, rv: float, right: str, target_delta: float, *,
                     r: float = 0.04, q: float = 0.0, vrp_mult: float = 1.15,
                     skew: float = 0.35, term: float = 0.0) -> float | None:
    """Invert |delta| → strike (bisection) for delta-based strike selection. `target_delta`
    is the absolute delta (e.g. 0.30). Returns the strike, or None if it can't converge."""
    from .black_scholes import _is_call  # local: keep the public surface clean

    call = _is_call(right)
    target = abs(target_delta)
    # Monotonicity in strike differs by right: a CALL's delta FALLS as strike rises
    # (deep-OTM call → 0); a PUT's |delta| RISES as strike rises (ATM/ITM put → 1).
    lo, hi = spot * 0.2, spot * 2.5
    for _ in range(80):
        k = 0.5 * (lo + hi)
        iv = iv_model(spot, k, t_years, rv, vrp_mult=vrp_mult, skew=skew, term=term)
        d = abs(greeks(spot, k, t_years, r, iv, "call" if call else "put", q)["delta"])
        if abs(d - target) < 1e-4:
            return round(k, 2)
        too_high = d > target
        if call:  # decreasing: too high → move to a higher strike
            lo, hi = (k, hi) if too_high else (lo, k)
        else:     # increasing: too high → move to a lower strike
            lo, hi = (lo, k) if too_high else (k, hi)
    return round(0.5 * (lo + hi), 2)
