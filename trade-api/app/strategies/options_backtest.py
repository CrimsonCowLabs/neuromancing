"""Options-aware backtester (v1, defined-risk structures).

The long/flat equity backtester can't value an option, so this steps the underlying's
DAILY bars and, for one structure at a time: opens on eligibility (sized by defined
max-loss), marks daily via the shared pricer (theta decay), manages (profit target /
close-at-DTE), and settles at expiry by moneyness (assignment). All pricing goes through
`options_strategy` → one path shared with the served chain and the risk math.

Footgun discipline (see plan):
- IV at each step uses a strictly TRAILING realized-vol window (bars ≤ i) — no lookahead.
- T is CALENDAR days-to-expiry / 365 (handled in options_strategy), never a bar count.
- ONE structure at a time per underlying (mirrors the long/flat single-position model).
Metrics are relative, not absolute P&L promises.
"""

from __future__ import annotations

from neuromancing_shared.options.risk import Leg
from neuromancing_shared.options.vol import realized_vol
from neuromancing_shared.options_strategy import (
    mtm_pnl_per_contract,
    open_structure,
)

from .base import Bar

RV_WINDOW = 20


def _short_leg_itm(legs: list[Leg], spot: float) -> bool:
    for leg in legs:
        if leg.side != "sell":
            continue
        if (leg.right == "call" and spot > leg.strike) or (leg.right == "put" and spot < leg.strike):
            return True
    return False


def backtest_structure(spec: dict, bars: list[Bar], *, starting_cash: float = 100_000.0,
                       r: float = 0.04, q: float = 0.0, vrp: float = 1.15,
                       skew: float = 0.35, term: float = 0.0) -> dict:
    """Replay `spec` (already validated) over daily `bars`. Returns aggregate metrics."""
    knobs = dict(r=r, q=q, vrp=vrp, skew=skew, term=term)
    cash = starting_cash
    peak = starting_cash
    max_dd = 0.0
    trades: list[dict] = []
    assigned = 0
    settled = 0
    pos = None

    for i in range(RV_WINDOW + 1, len(bars)):
        bar = bars[i]
        spot = bar.close
        asof = bar.ts.date()
        rv = realized_vol([b.close for b in bars[: i + 1]], RV_WINDOW, "1d")
        if rv is None:
            continue

        if pos is None:
            st = open_structure(spec, spot, asof, rv, **knobs)
            if st is None:
                continue
            # Archetype-aware sizing: CSP/covered-call are collateral-heavy → size by a
            # capital allocation; spreads/condors size by the small defined-risk budget.
            if spec["archetype"] in ("cash_secured_put", "covered_call"):
                budget = spec.get("alloc_pct", 0.5) * cash
            else:
                budget = spec["risk_pct"] * cash
            n = int(budget // st.max_loss_per_contract) if st.max_loss_per_contract > 0 else 0
            if n <= 0:
                continue
            st.contracts = n
            pos = st
            continue

        pnl_pc = mtm_pnl_per_contract(pos, spot, asof, rv, **knobs)
        pnl_total = pnl_pc * pos.contracts
        equity_mark = cash + pnl_total
        peak = max(peak, equity_mark)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity_mark) / peak)

        days_left = (pos.expiry - asof).days
        max_credit = pos.net_credit_ps * 100 * pos.contracts
        pt = spec.get("profit_target_pct")
        cad = spec.get("close_at_dte", 0)
        reason = None
        if days_left <= 0:
            reason = "expiry"
            settled += 1
            if _short_leg_itm(pos.legs, spot):
                assigned += 1
        elif pt and pnl_total >= pt * max_credit:
            reason = "profit_target"
        elif cad > 0 and days_left <= cad:
            reason = "close_at_dte"
        if reason:
            cash += pnl_total
            defined_risk = pos.max_loss_per_contract * pos.contracts
            trades.append({"pnl": round(pnl_total, 2), "credit": round(max_credit, 2),
                           "reason": reason, "contracts": pos.contracts,
                           "return_on_risk": (pnl_total / defined_risk) if defined_risk else 0.0})
            pos = None

    # Mark any still-open structure to close at the last bar.
    if pos is not None and len(bars) > RV_WINDOW + 1:
        last = bars[-1]
        rv = realized_vol([b.close for b in bars], RV_WINDOW, "1d") or 0.2
        pnl_total = mtm_pnl_per_contract(pos, last.close, last.ts.date(), rv, **knobs) * pos.contracts
        cash += pnl_total
        trades.append({"pnl": round(pnl_total, 2), "credit": round(pos.net_credit_ps * 100 * pos.contracts, 2),
                       "reason": "eod_close", "contracts": pos.contracts,
                       "return_on_risk": pnl_total / (pos.max_loss_per_contract * pos.contracts)})

    wins = [t for t in trades if t["pnl"] > 0]
    n = len(trades)
    return {
        "trades": n,
        "win_rate": round(len(wins) / n, 4) if n else 0.0,
        "total_return": round((cash - starting_cash) / starting_cash, 6),
        "max_drawdown": round(max_dd, 6),
        "avg_credit": round(sum(t["credit"] for t in trades) / n, 2) if n else 0.0,
        "avg_return_on_risk": round(sum(t["return_on_risk"] for t in trades) / n, 4) if n else 0.0,
        "assignment_rate": round(assigned / settled, 4) if settled else 0.0,
        "final_equity": round(cash, 2),
    }
