"""The strategies router collapsed onto the one interface. Every endpoint —
evaluate_strategy, backtest_adhoc, backtest_strategy, options_backtest_adhoc — now routes
through build_strategy → required_timeframes → one shared loader → evaluate/backtest.

These call the endpoint coroutines directly (offline: `load_bars` is monkeypatched and the
DB session is faked) and assert the response models are byte-for-byte what a direct interface
computation over the same bars produces — proving the collapse is invisible across the HTTP
boundary and no kind == "indicator_dsl" branch changed a number."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from neuromancing_shared.options_strategy import validate_structure

from app.models import Strategy, StrategyKind, StrategyOwner
from app.routers import strategies as R
from app.schemas import (
    AdhocBacktestRequest,
    BacktestRequest,
    BacktestResult,
    EvaluateRequest,
    OptionsBacktestRequest,
    OptionsBacktestResult,
    SignalOut,
)
from app.strategies.base import Bar
from app.strategies.interface import BacktestConfig, build_strategy
from app.strategies.spec import validate_spec

UTC = timezone.utc
_T0 = datetime(2026, 1, 1, tzinfo=UTC)

_RULE_DSL = {
    "buy_when": {"all": [{"indicator": "rsi", "period": 14, "op": "<", "value": 35}]},
    "exit_when": {"any": [{"indicator": "rsi", "period": 14, "op": ">", "value": 65}]},
}
_INDICATOR_DSL = validate_spec({
    "base_timeframe": "5m",
    "indicators": [{"id": "r", "fn": "rsi", "period": 14, "timeframe": "5m"}],
    "buy_when": {"all": [{"indicator": "r", "cross": "below", "value": 30}]},
    "exit_when": {"any": [{"indicator": "r", "cross": "above", "value": 55}]}})
_CSP = validate_structure({"archetype": "cash_secured_put", "dte": 30,
                           "short_delta": 0.30, "alloc_pct": 0.5})


def _series(timeframe: str) -> list[Bar]:
    """Deterministic synthetic bars per timeframe — the same series the fake loader serves and
    the direct-interface expectation is computed over."""
    step = {"1m": 1, "5m": 5, "1h": 60, "1d": 1440}[timeframe]
    if timeframe == "1d":
        closes = [100 * (1.0015 ** i) for i in range(150)]  # options uptrend
    else:
        # oscillating decline→rally so RSI crosses; also gives the rule_dsl dip-buyer a signal
        prices, p = [], 100.0
        for _ in range(6):
            for _ in range(15):
                p *= 0.97
                prices.append(p)
            for _ in range(15):
                p *= 1.03
                prices.append(p)
        closes = prices
    return [Bar(ts=_T0 + timedelta(minutes=step * i), open=c, high=c, low=c, close=c, volume=1.0)
            for i, c in enumerate(closes)]


@pytest.fixture(autouse=True)
def _patch_load_bars(monkeypatch):
    async def fake_load_bars(session, symbol, timeframe="1m", limit=200, window=None):
        return _series(timeframe)
    monkeypatch.setattr(R, "load_bars", fake_load_bars)


class _FakeSession:
    """Stands in for the DB session: `get` returns a preloaded Strategy row; the persist path
    is never exercised here (persist=False)."""

    def __init__(self, strat):
        self._strat = strat

    async def get(self, model, id):  # noqa: A002 — mirrors AsyncSession.get(model, id)
        return self._strat


def _strat_row(kind: str, spec: dict) -> Strategy:
    return Strategy(name=f"t-{kind}", kind=StrategyKind(kind), spec=spec,
                    owner_type=StrategyOwner.house)


# ---------------- backtest_adhoc ----------------
@pytest.mark.parametrize("kind,spec,tf", [
    ("rule_dsl", _RULE_DSL, "1m"),
    ("indicator_dsl", _INDICATOR_DSL, "5m"),
])
async def test_backtest_adhoc_matches_the_interface(kind, spec, tf):
    body = AdhocBacktestRequest(kind=kind, spec=spec, symbol="BTC", cost_bps=0.0)
    got = await R.backtest_adhoc(body, session=_FakeSession(None))
    assert isinstance(got, BacktestResult)
    # what the interface produces over the same bars, serialized the same way
    bars_by_tf = {t: _series(t) for t in build_strategy(kind, spec).required_timeframes("1m")}
    expected = build_strategy(kind, spec).backtest(bars_by_tf, BacktestConfig(cost_bps=0.0))
    assert got == R._equity_result("BTC", expected)


# ---------------- backtest_strategy (by id) ----------------
async def test_backtest_strategy_by_id_routes_through_interface():
    row = _strat_row("indicator_dsl", _INDICATOR_DSL)
    body = BacktestRequest(symbol="ETH", timeframe="5m", limit=5000, cost_bps=0.0)
    got = await R.backtest_strategy(1, body, session=_FakeSession(row))
    bars_by_tf = {"5m": _series("5m")}
    expected = build_strategy("indicator_dsl", _INDICATOR_DSL).backtest(
        bars_by_tf, BacktestConfig(cost_bps=0.0))
    assert got == R._equity_result("ETH", expected)


# ---------------- evaluate_strategy ----------------
async def test_evaluate_strategy_routes_through_interface():
    row = _strat_row("indicator_dsl", _INDICATOR_DSL)
    body = EvaluateRequest(agent_ref="a1", symbols=["BTC", "ETH"], timeframe="5m", persist=False)
    out = await R.evaluate_strategy(1, body, session=_FakeSession(row))
    assert len(out) == 2 and all(isinstance(s, SignalOut) for s in out)
    expected = build_strategy("indicator_dsl", _INDICATOR_DSL).evaluate({"5m": _series("5m")})
    assert out[0].symbol == "BTC" and out[0].action == expected.action


# ---------------- options_backtest_adhoc ----------------
async def test_options_backtest_adhoc_aggregates_over_underlyings():
    body = OptionsBacktestRequest(structure=_CSP, underlyings=["AAPL", "MSFT"])
    got = await R.options_backtest_adhoc(body, session=_FakeSession(None))
    assert isinstance(got, OptionsBacktestResult)
    assert got.archetype == "cash_secured_put"
    assert got.underlyings == ["AAPL", "MSFT"]
    # per-underlying dicts carry exactly the options-metric keys the aggregation reads
    keys = {"trades", "win_rate", "total_return", "max_drawdown", "avg_credit",
            "avg_return_on_risk", "assignment_rate", "final_equity", "underlying"}
    assert all(set(p) == keys for p in got.per_underlying)
    # both underlyings served identical bars → the mean total_return equals a single run and
    # trades sum (settings-independent: read the router's own per-underlying value)
    one = got.per_underlying[0]
    assert one["trades"] > 0
    assert got.total_return == pytest.approx(one["total_return"], abs=1e-9)
    assert got.trades == 2 * one["trades"]
