"""Integration test of the evolution graph end-to-end, offline: MemorySaver
checkpointer, a stub trade client (canned backtests), and a monkeypatched digest —
so reflect → propose(heuristic) → backtest → decide runs with no DB / no HTTP / no LLM."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest
from langgraph.checkpoint.memory import MemorySaver

from app.evolve import graph as G
from app.evolve import tools
from app.evolve.gate import GateConfig

INCUMBENT = {
    "type": "mean_reversion", "base_timeframe": "5m", "states": {},
    "indicators": [{"id": "r", "fn": "rsi", "period": 14, "source": "close", "timeframe": "5m"}],
    "buy_when": {"all": [{"indicator": "r", "op": "<", "value": 30}]},
    "exit_when": {"any": [{"indicator": "r", "op": ">", "value": 60}]},
}


@asynccontextmanager
async def _fake_session_factory():
    yield None  # gather_performance is monkeypatched to ignore the session


class _FakeTrade:
    """backtest_spec returns a HIGH return for candidates (period != 14, the incumbent)
    and a LOW return for the incumbent — so a nudged candidate should win the gate."""

    def __init__(self, cand_return=0.10, inc_return=0.005):
        self.cand_return, self.inc_return = cand_return, inc_return
        self.calls: list[dict] = []  # every backtest_spec kwargs bag, for threading assertions

    async def backtest_spec(self, spec, symbol, *, kind="indicator_dsl", window=None,
                            starting_cash=10000.0, **knobs):
        self.calls.append(knobs)
        period = spec["indicators"][0].get("period")
        ret = self.inc_return if period == 14 else self.cand_return
        return {"total_return": ret, "trades": 8, "max_drawdown": 0.1}


async def _run(trade, risk_profile=None):
    async def fake_digest(session, agent_id, since_days=30):
        return {"episodes": 20, "win_rate": 0.4, "avg_return": -0.001}

    import pytest as _p
    # monkeypatch via the module attribute (no fixture needed for a coroutine swap)
    orig = tools.gather_performance
    tools.gather_performance = fake_digest
    try:
        ctx = {"session_factory": _fake_session_factory, "trade": trade, "settings": None,
               "now": datetime(2026, 3, 3, tzinfo=timezone.utc), "backtest_symbols": 2,
               "cfg": GateConfig(0.02, 5, 0.4), "risk_profile": risk_profile or {}}
        g = G.build_graph(ctx, MemorySaver())
        initial = {"run_id": "t1", "agent_id": 1, "universe": ["AAPL", "MSFT"],
                   "incumbent_spec": INCUMBENT, "refine_count": 0, "dry_run": True}
        final = {}
        async for st in g.astream(initial, {"configurable": {"thread_id": "evolve:1:t1"}},
                                  stream_mode="values"):
            final = st
        return final
    finally:
        tools.gather_performance = orig


async def test_graph_adopts_when_candidate_wins():
    final = await _run(_FakeTrade(cand_return=0.10, inc_return=0.005))
    assert final["decision"] == "adopted"
    assert final["adopted_spec"] is not None
    # the adopted spec is a nudged variant (period changed from the incumbent's 14)
    assert final["adopted_spec"]["indicators"][0]["period"] != 14


async def test_graph_rejects_when_no_edge():
    # candidates and incumbent both mediocre → no edge → reject
    final = await _run(_FakeTrade(cand_return=0.006, inc_return=0.005))
    assert final["decision"] == "rejected"
    assert final["adopted_spec"] is None


async def test_risk_profile_threads_into_backtest_knobs():
    # A construct's own risk profile must reach every backtest call so candidates are
    # measured under the discipline they'd trade under (max_position_pct → alloc_pct,
    # stop/take-profit passed through; absent keys are dropped, not sent as None).
    trade = _FakeTrade(cand_return=0.10, inc_return=0.005)
    await _run(trade, risk_profile={"max_position_pct": 0.15, "stop_loss_pct": 0.05,
                                    "take_profit_pct": 0.12})
    assert trade.calls, "backtest_spec was never called"
    for knobs in trade.calls:
        assert knobs["alloc_pct"] == 0.15
        assert knobs["stop_loss_pct"] == 0.05
        assert knobs["take_profit_pct"] == 0.12
        assert "trailing_stop_pct" not in knobs  # absent in the profile → not sent


def test_heuristic_variants_are_valid_and_varied():
    cands = G._heuristic_candidates(INCUMBENT)
    assert len(cands) >= 2
    periods = {c["indicators"][0]["period"] for c in cands}
    assert periods - {14}  # at least one nudged period
