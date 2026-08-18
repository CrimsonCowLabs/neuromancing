"""Defined-risk option-structure spec + opener/marker (v1: backtest-only).

A structure = an archetype (CSP / covered call / bull-put vertical / iron condor) with
delta-based strike selection, a DTE target, wing width, and management rules. `validate_
structure` is the fail-closed gate (mirrors strategy_spec.validate_spec). `open_structure`
selects strikes and prices the legs at open; `mtm_pnl_per_contract` marks the open
structure at any later date — and by construction converges to the risk.py expiry P&L
(assignment) at T=0, so daily marks and settlement use one consistent path.

Modeling caveats (see the plan's critical review): BS/European, expiry-only assignment,
flat-parametric vol surface. Treat metrics as relative, not absolute.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from .options.black_scholes import price
from .options.chain import strike_for_delta
from .options.risk import Leg, max_loss
from .options.vol import iv_model

ARCHETYPES = ("cash_secured_put", "covered_call", "vertical_spread", "iron_condor")
_DEFAULT_SHORT_DELTA = {"cash_secured_put": 0.30, "covered_call": 0.30,
                        "vertical_spread": 0.30, "iron_condor": 0.16}


def validate_structure(spec: dict) -> dict:
    """Fail-closed validation → canonical dict. Rejects unknown archetypes and bad params."""
    if not isinstance(spec, dict):
        raise ValueError("structure must be an object")
    a = spec.get("archetype")
    if a not in ARCHETYPES:
        raise ValueError(f"unknown archetype {a!r} (allowed: {ARCHETYPES})")
    dte = spec.get("dte")
    if not isinstance(dte, int) or dte <= 0:
        raise ValueError("dte must be a positive integer (calendar days)")
    sd = float(spec.get("short_delta", _DEFAULT_SHORT_DELTA[a]))
    if not (0.0 < sd < 1.0):
        raise ValueError("short_delta must be in (0,1)")
    width = float(spec.get("width", 5.0))
    if a in ("vertical_spread", "iron_condor") and width <= 0:
        raise ValueError("width must be > 0 for spreads/condors")
    risk_pct = float(spec.get("risk_pct", 0.02))
    if not (0.0 < risk_pct <= 1.0):
        raise ValueError("risk_pct must be in (0,1]")
    # CSP/covered-call are collateral-heavy (max loss ≈ full strike/stock), so they size
    # by capital allocation, not a small defined-risk budget.
    alloc_pct = float(spec.get("alloc_pct", 0.5))
    if not (0.0 < alloc_pct <= 1.0):
        raise ValueError("alloc_pct must be in (0,1]")
    close_at_dte = int(spec.get("close_at_dte", 0))
    if close_at_dte < 0:
        raise ValueError("close_at_dte must be >= 0")
    pt = spec.get("profit_target_pct")
    if pt is not None and not (0.0 < float(pt) <= 1.0):
        raise ValueError("profit_target_pct must be in (0,1]")
    return {"archetype": a, "dte": dte, "short_delta": sd, "width": width,
            "risk_pct": risk_pct, "alloc_pct": alloc_pct, "close_at_dte": close_at_dte,
            "profit_target_pct": (float(pt) if pt is not None else None)}


@dataclass
class OpenStructure:
    legs: list[Leg]
    net_credit_ps: float          # premium received per share at open (credit>0)
    has_stock: bool               # covered call holds 100 shares/contract
    stock_entry: float
    expiry: date
    max_loss_per_contract: float
    open_spot: float
    contracts: int = 1
    strikes: dict = field(default_factory=dict)


def open_structure(spec: dict, spot: float, asof: date, rv: float, *,
                   r: float = 0.04, q: float = 0.0, vrp: float = 1.15,
                   skew: float = 0.35, term: float = 0.0) -> OpenStructure | None:
    """Select strikes (by delta) and price the legs at open. Returns None if strikes/
    pricing can't be resolved or the structure isn't defined-risk."""
    a = spec["archetype"]
    dte = int(spec["dte"])
    t = dte / 365.0
    expiry = asof + timedelta(days=dte)
    sd = float(spec.get("short_delta", _DEFAULT_SHORT_DELTA[a]))
    width = float(spec.get("width", 5.0))
    sel = dict(r=r, q=q, vrp_mult=vrp, skew=skew, term=term)

    def px(k: float, right: str) -> float:
        iv = iv_model(spot, k, t, rv, vrp_mult=vrp, skew=skew, term=term)
        return price(spot, k, t, r, iv, right, q)

    legs: list[Leg] = []
    credit = 0.0
    has_stock = False
    stock_entry = 0.0
    strikes: dict = {}
    try:
        if a == "cash_secured_put":
            k = strike_for_delta(spot, t, rv, "put", sd, **sel)
            legs = [Leg("put", "sell", k)]
            credit = px(k, "put")
            strikes = {"short_put": k}
        elif a == "covered_call":
            k = strike_for_delta(spot, t, rv, "call", sd, **sel)
            legs = [Leg("call", "sell", k)]
            credit = px(k, "call")
            has_stock, stock_entry = True, spot
            strikes = {"short_call": k}
        elif a == "vertical_spread":  # bull put credit spread
            ks = strike_for_delta(spot, t, rv, "put", sd, **sel)
            kl = round(ks - width, 2)
            legs = [Leg("put", "sell", ks), Leg("put", "buy", kl)]
            credit = px(ks, "put") - px(kl, "put")
            strikes = {"short_put": ks, "long_put": kl}
        elif a == "iron_condor":
            kps = strike_for_delta(spot, t, rv, "put", sd, **sel)
            kpl = round(kps - width, 2)
            kcs = strike_for_delta(spot, t, rv, "call", sd, **sel)
            kcl = round(kcs + width, 2)
            legs = [Leg("put", "sell", kps), Leg("put", "buy", kpl),
                    Leg("call", "sell", kcs), Leg("call", "buy", kcl)]
            credit = (px(kps, "put") - px(kpl, "put")) + (px(kcs, "call") - px(kcl, "call"))
            strikes = {"short_put": kps, "long_put": kpl, "short_call": kcs, "long_call": kcl}
    except (TypeError, ValueError):
        return None
    if any(l.strike is None for l in legs) or credit is None or credit <= 0:
        return None
    ml = max_loss(legs, credit, has_stock=has_stock, stock_entry=stock_entry)
    if ml == float("inf") or ml <= 0:
        return None
    return OpenStructure(legs, credit, has_stock, stock_entry, expiry, ml, spot, strikes=strikes)


def mtm_pnl_per_contract(struct: OpenStructure, spot: float, asof: date, rv: float, *,
                         r: float = 0.04, q: float = 0.0, vrp: float = 1.15,
                         skew: float = 0.35, term: float = 0.0) -> float:
    """Mark-to-market P&L (dollars) of ONE open structure at `asof`. At/after expiry the
    legs price at intrinsic → equals the risk.py assignment P&L. Long legs are assets,
    short legs liabilities; a covered call's 100 shares/contract are marked too."""
    days = (struct.expiry - asof).days
    t = max(0.0, days / 365.0)
    mtm = struct.net_credit_ps
    for leg in struct.legs:
        iv = iv_model(spot, leg.strike, t, rv, vrp_mult=vrp, skew=skew, term=term)
        p = price(spot, leg.strike, t, r, iv, leg.right, q)
        mtm += (p if leg.side == "buy" else -p)
    if struct.has_stock:
        mtm += (spot - struct.stock_entry)
    return mtm * 100.0
