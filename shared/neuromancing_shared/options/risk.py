"""Defined-risk math for option structures — the safety-critical module.

Everything is expiry-payoff based, which handles all four archetypes uniformly AND
rejects undefined-risk (naked) legs: a structure is "defined risk" iff its worst-case
loss is bounded. A naked short call loses without bound as the underlying rises, so it
is rejected; a covering long call (or long stock) caps it. Max loss (per contract, ×100
shares) is the negative of the minimum P&L over the payoff breakpoints, and drives
position sizing. Premiums/strikes are per share.
"""

from __future__ import annotations

from dataclasses import dataclass

MULT = 100  # shares per contract


def _is_call(right: str) -> bool:
    r = str(right).strip().lower()
    if r in ("call", "c"):
        return True
    if r in ("put", "p"):
        return False
    raise ValueError(f"unknown option right: {right!r}")


@dataclass(frozen=True)
class Leg:
    right: str          # call | put
    side: str           # buy (long) | sell (short)
    strike: float
    ratio: int = 1      # legs per unit structure (e.g. 1 each for a vertical)


def _leg_intrinsic_ps(leg: Leg, P: float) -> float:
    itr = max(0.0, P - leg.strike) if _is_call(leg.right) else max(0.0, leg.strike - P)
    return (1.0 if leg.side == "buy" else -1.0) * itr * leg.ratio


def pnl_per_contract(legs: list[Leg], net_credit_ps: float, P: float, *,
                     has_stock: bool = False, stock_entry: float = 0.0) -> float:
    """P&L (dollars) of ONE structure (×100 shares) if the underlying expires at `P`.
    `net_credit_ps` is the opening premium per share (credit > 0, debit < 0). A covered
    call's long 100 shares/contract is modeled via has_stock/stock_entry."""
    opt_ps = sum(_leg_intrinsic_ps(l, P) for l in legs)
    stock_ps = (P - stock_entry) if has_stock else 0.0
    return (net_credit_ps + opt_ps + stock_ps) * MULT


def _probes(legs: list[Leg], has_stock: bool, stock_entry: float) -> list[float]:
    strikes = sorted({l.strike for l in legs} | ({stock_entry} if has_stock else set()))
    hi = (max(strikes) if strikes else 1.0)
    return [0.0, *strikes, 4 * hi, 8 * hi]  # 0, breakpoints, and two large probes for slope


def max_loss(legs: list[Leg], net_credit_ps: float, *,
             has_stock: bool = False, stock_entry: float = 0.0) -> float:
    """Worst-case loss in dollars per contract (positive number), or math.inf if the
    loss is unbounded (undefined risk)."""
    probes = _probes(legs, has_stock, stock_entry)
    pnls = [pnl_per_contract(legs, net_credit_ps, P, has_stock=has_stock, stock_entry=stock_entry)
            for P in probes]
    # Unbounded-above detection: P&L still falling at the two large probes (naked short call).
    if pnls[-1] < pnls[-2] - 1e-6:
        return float("inf")
    worst = min(pnls)
    return max(0.0, -worst)


def is_defined_risk(legs: list[Leg], net_credit_ps: float = 0.0, *,
                    has_stock: bool = False, stock_entry: float = 0.0) -> bool:
    """True iff worst-case loss is bounded. Categorically rejects naked short calls
    (and any structure whose loss is unbounded). The 'secured'/coverage requirement
    (CSP holds cash, covered call holds shares) is enforced additionally at sizing /
    the live guardrail — this is the hard floor that blocks undefined risk."""
    ml = max_loss(legs, net_credit_ps, has_stock=has_stock, stock_entry=stock_entry)
    return ml != float("inf")


def sized_contracts(equity: float, risk_pct: float, max_loss_per_contract: float) -> int:
    """Number of structures to open so total defined risk ≤ risk_pct of equity."""
    if max_loss_per_contract <= 0 or max_loss_per_contract == float("inf"):
        return 0
    return max(0, int((equity * risk_pct) // max_loss_per_contract))
