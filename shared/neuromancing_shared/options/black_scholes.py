"""Black-Scholes-Merton European option pricing + greeks + implied vol.

Pure math (stdlib only — normal CDF via ``math.erf``, no scipy). All prices are per
share (multiply by the 100x contract multiplier at the position layer, not here).

Conventions:
- ``right``: "call"/"put" (also accepts "C"/"P", case-insensitive).
- ``T``: time to expiry in YEARS (calendar days / 365 — never a bar count).
- ``r``: continuously-compounded risk-free rate; ``q``: continuous dividend yield.
- ``sigma``: annualized volatility (e.g. 0.25 = 25%).
- greeks units: delta unitless; gamma per $1 of spot; vega per 1.00 (100 vol pts) of
  sigma; theta per YEAR; rho per 1.00 of rate. Callers scale (e.g. theta/365 per day,
  vega/100 per vol point).

BS is European and assumes a single flat vol — it does not model skew, term structure,
or early assignment. Those live in vol.py / the backtester and are the fidelity levers.
"""

from __future__ import annotations

import math

_SQRT2 = math.sqrt(2.0)
_INV_SQRT_2PI = 1.0 / math.sqrt(2.0 * math.pi)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / _SQRT2))


def _norm_pdf(x: float) -> float:
    return _INV_SQRT_2PI * math.exp(-0.5 * x * x)


def _is_call(right: str) -> bool:
    r = str(right).strip().lower()
    if r in ("call", "c"):
        return True
    if r in ("put", "p"):
        return False
    raise ValueError(f"unknown option right: {right!r}")


def _intrinsic(S: float, K: float, call: bool) -> float:
    return max(0.0, S - K) if call else max(0.0, K - S)


def d1_d2(S: float, K: float, T: float, r: float, sigma: float, q: float = 0.0):
    """The BS d1/d2 terms. Requires S,K,sigma,T > 0 (guarded by callers)."""
    vsqrt = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / vsqrt
    return d1, d1 - vsqrt


def price(S: float, K: float, T: float, r: float, sigma: float, right: str, q: float = 0.0) -> float:
    """BS price per share. Degenerate inputs (T<=0 or sigma<=0) fall back to the
    discounted intrinsic value so the pricer never blows up mid-backtest."""
    call = _is_call(right)
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return _intrinsic(S, K, call)
    d1, d2 = d1_d2(S, K, T, r, sigma, q)
    df_r, df_q = math.exp(-r * T), math.exp(-q * T)
    if call:
        return S * df_q * _norm_cdf(d1) - K * df_r * _norm_cdf(d2)
    return K * df_r * _norm_cdf(-d2) - S * df_q * _norm_cdf(-d1)


def greeks(S: float, K: float, T: float, r: float, sigma: float, right: str, q: float = 0.0) -> dict:
    """delta, gamma, theta (per year), vega (per 1.00 sigma), rho (per 1.00 rate)."""
    call = _is_call(right)
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        # At/after expiry: delta is 0/±1 by moneyness; other greeks vanish.
        itm = (S > K) if call else (S < K)
        return {"delta": (1.0 if call else -1.0) if itm else 0.0,
                "gamma": 0.0, "theta": 0.0, "vega": 0.0, "rho": 0.0}
    d1, d2 = d1_d2(S, K, T, r, sigma, q)
    df_r, df_q = math.exp(-r * T), math.exp(-q * T)
    pdf = _norm_pdf(d1)
    sqrtT = math.sqrt(T)
    gamma = df_q * pdf / (S * sigma * sqrtT)
    vega = S * df_q * pdf * sqrtT
    if call:
        delta = df_q * _norm_cdf(d1)
        theta = (-(S * df_q * pdf * sigma) / (2 * sqrtT)
                 - r * K * df_r * _norm_cdf(d2) + q * S * df_q * _norm_cdf(d1))
        rho = K * T * df_r * _norm_cdf(d2)
    else:
        delta = -df_q * _norm_cdf(-d1)
        theta = (-(S * df_q * pdf * sigma) / (2 * sqrtT)
                 + r * K * df_r * _norm_cdf(-d2) - q * S * df_q * _norm_cdf(-d1))
        rho = -K * T * df_r * _norm_cdf(-d2)
    return {"delta": delta, "gamma": gamma, "theta": theta, "vega": vega, "rho": rho}


def implied_vol(target: float, S: float, K: float, T: float, r: float, right: str,
                q: float = 0.0, lo: float = 1e-4, hi: float = 5.0, tol: float = 1e-6) -> float | None:
    """Invert BS price → sigma by bisection (robust for deep OTM/ITM where Newton on
    vega degenerates). Returns None if the target is outside the no-arb price bounds."""
    call = _is_call(right)
    if T <= 0 or S <= 0 or K <= 0:
        return None
    if target <= _intrinsic(S, K, call) + 1e-9:
        return None  # at/below intrinsic → vol is 0 or the quote is arb-violating
    p_lo = price(S, K, T, r, lo, right, q)
    p_hi = price(S, K, T, r, hi, right, q)
    if not (p_lo <= target <= p_hi):
        return None
    for _ in range(100):
        mid = 0.5 * (lo + hi)
        p = price(S, K, T, r, mid, right, q)
        if abs(p - target) < tol:
            return mid
        if p < target:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)
