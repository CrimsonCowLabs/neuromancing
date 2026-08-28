"""Endpoint-level coverage for the strategies router after the #8 collapse: every endpoint —
evaluate_strategy, backtest_adhoc, backtest_strategy, options_backtest_adhoc — routes through
build_strategy → required_timeframes → the one _load_bars_by_tf loader → evaluate/backtest.

These call the endpoint coroutines directly (offline: `load_bars` is monkeypatched and the DB
session is faked) and assert the response models are byte-for-byte what a direct interface
computation over the same bars produces — so a future edit that drifts an endpoint away from
the interface, or reintroduces a `kind ==` branch, fails here."""

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
from app.strategies import build_strategy
from app.strategies.base import Bar

UTC = timezone.utc
_T0 = datetime(2026, 1, 1, tzinfo=UTC)

_RULE_DSL = {
    "buy_when": {"all": [{"indicator": "rsi", "period": 14, "op": "<", "value": 35}]},
    "exit_when": {"any": [{"indicator": "rsi", "period": 14, "op": ">", "value": 65}]},
}
_INDICATOR_DSL = {
    "base_timeframe": "5m",
    "indicators": [{"id": "r", "fn": "rsi", "period": 14, "timeframe": "5m"}],
    "buy_when": {"all": [{"indicator": "r", "cross": "below", "value": 30}]},
    "exit_when": {"any": [{"indicator": "r", "cross": "above", "value": 55}]},
}
_CSP = validate_structure({"archetype": "cash_secured_put", "dte": 30,
                           "short_delta": 0.30, "alloc_pct": 0.5})


def _series(timeframe: str) -> list[Bar]:
    """Deterministic synthetic bars per timeframe — the same series the fake loader serves and
    the direct-interface expectation is computed over."""
    step = {"1m": 1, "5m": 5, "1h": 60, "1d": 1440}[timeframe]
    if timeframe == "1d":
        closes = [100 * (1.0015 ** i) for i in range(150)]  # options uptrend
    else:
        prices, p = [], 100.0  # decline→rally cycles so RSI crosses (dip-buyer + indicator_dsl)
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

    def __init__(self, row):
        self._row = row

    async def get(self, model, id):  # noqa: A002 — mirrors AsyncSession.get(model, id)
        return self._row


def _row(kind: str, spec: dict) -> Strategy:
    return Strategy(name=f"t-{kind}", kind=StrategyKind(kind), spec=spec,
                    owner_type=StrategyOwner.house)


# ---------------- backtest_adhoc ----------------
@pytest.mark.parametrize("kind,spec", [("rule_dsl", _RULE_DSL), ("indicator_dsl", _INDICATOR_DSL)])
async def test_backtest_adhoc_matches_the_interface(kind, spec):
    body = AdhocBacktestRequest(kind=kind, spec=spec, symbol="BTC", cost_bps=0.0)
    got = await R.backtest_adhoc(body, session=_FakeSession(None))
    assert isinstance(got, BacktestResult)
    strat = build_strategy(kind, spec)
    bars_by_tf = {tf: _series(tf) for tf in strat.required_timeframes("1m")}
    expected = BacktestResult(symbol="BTC",
                              **strat.backtest(bars_by_tf, R._bt_config(body)).to_dict())
    assert got == expected


# ---------------- backtest_strategy (by id) ----------------
async def test_backtest_strategy_by_id_routes_through_interface():
    row = _row("indicator_dsl", _INDICATOR_DSL)
    body = BacktestRequest(symbol="ETH", timeframe="5m", limit=5000, cost_bps=0.0)
    got = await R.backtest_strategy(1, body, session=_FakeSession(row))
    strat = build_strategy("indicator_dsl", _INDICATOR_DSL)
    bars_by_tf = {tf: _series(tf) for tf in strat.required_timeframes("5m")}
    expected = BacktestResult(symbol="ETH",
                              **strat.backtest(bars_by_tf, R._bt_config(body)).to_dict())
    assert got == expected


async def test_backtest_strategy_404_when_missing():
    body = BacktestRequest(symbol="ETH", timeframe="5m", limit=100)
    with pytest.raises(Exception) as ei:  # noqa: PT011 — HTTPException; asserting status below
        await R.backtest_strategy(999, body, session=_FakeSession(None))
    assert getattr(ei.value, "status_code", None) == 404


# ---------------- evaluate_strategy ----------------
async def test_evaluate_strategy_routes_through_interface():
    row = _row("indicator_dsl", _INDICATOR_DSL)
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
