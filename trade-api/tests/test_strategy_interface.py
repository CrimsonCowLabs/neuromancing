"""The one `Strategy` interface. Two jobs here:

1. Factory contract — `build_strategy` returns a `Strategy` per signal kind and fails loudly
   on an unknown kind at construction.
2. Equivalence — the interface produces byte-for-byte the SAME numbers as the free functions
   it replaces (`evaluate`/`evaluate_multi`/`backtest`/`backtest_multi`), including the proof
   that a single-tf kind driven by a one-entry bars-by-tf map matches a bare bar list.

The spine/exit/sizing/cost behaviors themselves are exercised end-to-end through the
interface in test_backtest_spine.py; here we pin exact equality to the prior surface."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.strategies import backtest as free
from app.strategies.base import Bar
from app.strategies.engine import evaluate as free_evaluate
from app.strategies.engine import evaluate_multi as free_evaluate_multi
from app.strategies.interface import (
    BacktestConfig,
    EquityMetrics,
    IndicatorDslStrategy,
    RuleDslStrategy,
    SignalFnStrategy,
    Strategy,
    build_strategy,
)
from app.strategies.spec import validate_spec

UTC = timezone.utc
_T0 = datetime(2026, 1, 1, tzinfo=UTC)

_SIGNAL_FN = {"fn": "sma_crossover", "fast": 5, "slow": 20}
_RULE_DSL = {
    "buy_when": {"all": [{"indicator": "rsi", "period": 14, "op": "<", "value": 35}]},
    "exit_when": {"any": [{"indicator": "rsi", "period": 14, "op": ">", "value": 65}]},
}
_INDICATOR_DSL = validate_spec({
    "base_timeframe": "5m",
    "indicators": [{"id": "r", "fn": "rsi", "period": 14, "timeframe": "5m"}],
    "buy_when": {"all": [{"indicator": "r", "cross": "below", "value": 30}]},
    "exit_when": {"any": [{"indicator": "r", "cross": "above", "value": 55}]},
})


def _bars(closes: list[float], step_min: int = 1) -> list[Bar]:
    return [Bar(ts=_T0 + timedelta(minutes=step_min * i), open=c, high=c, low=c, close=c, volume=1)
            for i, c in enumerate(closes)]


def _oscillating() -> list[float]:
    prices, p = [], 100.0
    for _ in range(6):
        for _ in range(15):
            p *= 0.97
            prices.append(p)
        for _ in range(15):
            p *= 1.03
            prices.append(p)
    return prices


# ---------------- factory contract ----------------
@pytest.mark.parametrize("kind,spec,cls", [
    ("signal_fn", _SIGNAL_FN, SignalFnStrategy),
    ("rule_dsl", _RULE_DSL, RuleDslStrategy),
    ("indicator_dsl", _INDICATOR_DSL, IndicatorDslStrategy),
])
def test_build_strategy_returns_a_strategy_per_kind(kind, spec, cls):
    strat = build_strategy(kind, spec)
    assert isinstance(strat, cls)
    assert isinstance(strat, Strategy)  # satisfies the runtime-checkable protocol


def test_build_strategy_fails_loudly_on_unknown_kind():
    with pytest.raises(ValueError):
        build_strategy("nope", {})


# ---------------- required_timeframes ----------------
def test_single_series_requires_only_the_request_timeframe():
    assert build_strategy("signal_fn", _SIGNAL_FN).required_timeframes("1h") == ["1h"]
    assert build_strategy("rule_dsl", _RULE_DSL).required_timeframes("5m") == ["5m"]


def test_indicator_dsl_requires_the_spec_derived_timeframes():
    spec = validate_spec({
        "base_timeframe": "1d",
        "indicators": [{"id": "a", "fn": "rsi", "period": 14, "timeframe": "1h"},
                       {"id": "b", "fn": "sma", "period": 5, "timeframe": "5m"}],
        "buy_when": {"all": [{"indicator": "a", "op": "<", "value": 30}]}})
    # ignores the request timeframe; derives from the spec
    assert build_strategy("indicator_dsl", spec).required_timeframes("1m") == ["5m", "1h", "1d"]


# ---------------- evaluate equivalence ----------------
@pytest.mark.parametrize("kind,spec,closes", [
    ("signal_fn", _SIGNAL_FN, [100 - i for i in range(40)] + [60 + i * 3 for i in range(20)]),
    ("rule_dsl", _RULE_DSL, [100 - i * 2 for i in range(30)]),
])
def test_single_series_evaluate_matches_free_function(kind, spec, closes):
    bars = _bars(closes)
    strat = build_strategy(kind, spec)
    # one-entry bars-by-tf map ≡ the old bare bar list
    assert strat.evaluate({"1m": bars}) == free_evaluate(kind, spec, bars)


def test_indicator_dsl_evaluate_matches_free_function():
    bars_by_tf = {"5m": _bars(_oscillating(), step_min=5)}
    strat = build_strategy("indicator_dsl", _INDICATOR_DSL)
    assert strat.evaluate(bars_by_tf) == free_evaluate_multi("indicator_dsl", _INDICATOR_DSL, bars_by_tf)


# ---------------- backtest equivalence (same numbers as before) ----------------
def test_single_series_backtest_matches_bare_list_free_function():
    # The load-bearing collapse: a one-entry bars-by-tf map yields the identical metrics the
    # old bare-list `backtest` produced.
    closes = [100.0] * 40 + [111.0] + [111.0 * (1 + 0.012 * k) for k in range(1, 12)]
    bars = _bars(closes)
    cfg = BacktestConfig(cost_bps=0.0)
    got = build_strategy("rule_dsl", _RULE_DSL).backtest({"1m": bars}, cfg)
    expected = free.backtest("rule_dsl", _RULE_DSL, bars, cfg.starting_cash,
                             alloc_pct=cfg.alloc_pct, cost_bps=cfg.cost_bps,
                             exit_config=cfg.exit_config)
    assert got == EquityMetrics(**expected)


def test_indicator_dsl_backtest_matches_backtest_multi():
    bars_by_tf = {"5m": _bars(_oscillating(), step_min=5)}
    cfg = BacktestConfig(cost_bps=0.0)
    got = build_strategy("indicator_dsl", _INDICATOR_DSL).backtest(bars_by_tf, cfg)
    expected = free.backtest_multi("indicator_dsl", _INDICATOR_DSL, bars_by_tf, cfg.starting_cash,
                                   alloc_pct=cfg.alloc_pct, cost_bps=cfg.cost_bps,
                                   exit_config=cfg.exit_config)
    assert got == EquityMetrics(**expected)
    assert got.trades >= 1


def test_backtest_preserves_the_mandatory_stop_default():
    # A bare BacktestConfig carries ExitConfig()'s mandatory 0.08 stop. A momentum entry that
    # then holds through a collapse is cut by that default stop — vs a loose stop that rides it
    # down — proving the mandatory-stop default survives the interface.
    mom = {"buy_when": {"all": [{"indicator": "roc", "period": 5, "op": ">", "value": 0.08}]}}
    closes = [100.0] * 40 + [111.0] + [111.0 * (1 - 0.02 * k) for k in range(1, 25)]
    bars = {"1m": _bars(closes)}
    strat = build_strategy("rule_dsl", mom)
    default_stop = strat.backtest(bars, BacktestConfig())  # mandatory 0.08
    from app.strategies.interface import ExitConfig  # re-exported from backtest
    loose = strat.backtest(bars, BacktestConfig(exit_config=ExitConfig(stop_loss_pct=0.90)))
    assert default_stop.trades == 1 and loose.trades == 1
    assert default_stop.final_equity > loose.final_equity  # the default stop cut the loss
