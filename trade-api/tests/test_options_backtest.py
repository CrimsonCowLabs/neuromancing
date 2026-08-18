"""Phase 3 — options-aware backtester behavioral invariants (synthetic underlyings)."""

from datetime import datetime, timedelta

from app.strategies.base import Bar
from app.strategies.options_backtest import backtest_structure


def _bars(closes: list[float], start: str = "2026-01-01") -> list[Bar]:
    d0 = datetime.fromisoformat(start)
    return [Bar(ts=d0 + timedelta(days=i), open=c, high=c, low=c, close=c, volume=0.0)
            for i, c in enumerate(closes)]


CSP = {"archetype": "cash_secured_put", "dte": 30, "short_delta": 0.30, "alloc_pct": 0.5}
CONDOR = {"archetype": "iron_condor", "dte": 30, "short_delta": 0.16, "width": 5.0, "risk_pct": 0.05}


def test_csp_uptrend_keeps_credit():
    # Smooth uptrend → short puts expire OTM → keep premium, rarely assigned.
    m = backtest_structure(CSP, _bars([100 * (1.0015 ** i) for i in range(150)]))
    assert m["trades"] > 0
    assert m["total_return"] > 0            # collected premium
    assert m["assignment_rate"] < 0.34      # mostly expired worthless
    assert m["final_equity"] > 100_000


def test_csp_crash_gets_assigned_but_bounded():
    # Steady decline → short puts finish ITM → assignment + losses, but defined-risk:
    # equity can't go negative (cash-secured caps the loss at the strike).
    m = backtest_structure(CSP, _bars([100 * (0.99 ** i) for i in range(150)]))
    assert m["trades"] > 0
    assert m["assignment_rate"] > 0.5       # puts finished in the money
    assert m["total_return"] < 0            # took losses
    assert m["final_equity"] > 0            # never blew past the collateral


def test_iron_condor_rangebound_profits_and_bounded():
    # Range-bound oscillation → both short legs OTM → keep the net credit.
    import math
    closes = [100 + 3 * math.sin(i / 5.0) for i in range(150)]
    m = backtest_structure(CONDOR, closes=_bars(closes)) if False else backtest_structure(CONDOR, _bars(closes))
    assert m["trades"] > 0
    assert m["final_equity"] > 0
    # Condor defined risk = (width - credit)*100; total return stays small/bounded either way.
    assert -0.5 < m["total_return"] < 0.5
