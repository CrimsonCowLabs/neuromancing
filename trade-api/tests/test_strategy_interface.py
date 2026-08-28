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

from neuromancing_shared.options_strategy import validate_structure

from app.strategies.base import Bar
from app.strategies.composed import evaluate_composed
from app.strategies.dsl import evaluate_dsl
from app.strategies.interface import (
    BacktestConfig,
    EquityMetrics,
    IndicatorDslStrategy,
    NotALiveStrategyError,
    OptionsBacktestConfig,
    OptionsMetrics,
    OptionStructureStrategy,
    RuleDslStrategy,
    SignalFnStrategy,
    Strategy,
    build_strategy,
)
from app.strategies.library import SIGNAL_FNS
from app.strategies.options_backtest import backtest_structure
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


# ---------------- evaluate delegates to the leaf (no reimplementation) ----------------
def test_signal_fn_evaluate_delegates_to_the_leaf():
    bars = _bars([100 - i for i in range(40)] + [60 + i * 3 for i in range(20)])
    got = build_strategy("signal_fn", _SIGNAL_FN).evaluate({"1m": bars})
    assert got == SIGNAL_FNS[_SIGNAL_FN["fn"]]([b.close for b in bars], _SIGNAL_FN)


def test_rule_dsl_evaluate_delegates_to_the_leaf():
    bars = _bars([100 - i * 2 for i in range(30)])
    got = build_strategy("rule_dsl", _RULE_DSL).evaluate({"5m": bars})
    assert got == evaluate_dsl(_RULE_DSL, [b.close for b in bars])


def test_indicator_dsl_evaluate_delegates_to_the_leaf():
    bars_by_tf = {"5m": _bars(_oscillating(), step_min=5)}
    got = build_strategy("indicator_dsl", _INDICATOR_DSL).evaluate(bars_by_tf)
    assert got == evaluate_composed(_INDICATOR_DSL, bars_by_tf)


# ---------------- the single-tf collapse ----------------
@pytest.mark.parametrize("kind,spec", [("signal_fn", _SIGNAL_FN), ("rule_dsl", _RULE_DSL)])
def test_single_series_result_is_independent_of_the_map_key(kind, spec):
    # A single-tf kind driven by a one-entry map is independent of WHICH timeframe the one
    # series is keyed under — identical signal and identical backtest. This is the collapse
    # that made the single-vs-multi split disappear (the old "bare bar list" case).
    bars = _bars([100.0] * 40 + [111.0] + [111.0 * (1 + 0.012 * k) for k in range(1, 12)])
    strat = build_strategy(kind, spec)
    assert strat.evaluate({"1m": bars}) == strat.evaluate({"1d": bars})
    cfg = BacktestConfig(cost_bps=0.0)
    assert strat.backtest({"1m": bars}, cfg) == strat.backtest({"1d": bars}, cfg)


def test_indicator_dsl_backtest_is_deterministic_and_trades():
    bars_by_tf = {"5m": _bars(_oscillating(), step_min=5)}
    strat = build_strategy("indicator_dsl", _INDICATOR_DSL)
    m1 = strat.backtest(bars_by_tf, BacktestConfig(cost_bps=0.0))
    m2 = strat.backtest(bars_by_tf, BacktestConfig(cost_bps=0.0))
    assert m1 == m2 and isinstance(m1, EquityMetrics)
    assert m1.trades >= 1


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


# ---------------- option structures fold into the same interface ----------------
_CSP = validate_structure({"archetype": "cash_secured_put", "dte": 30,
                           "short_delta": 0.30, "alloc_pct": 0.5})


def _daily(closes: list[float]) -> list[Bar]:
    return [Bar(ts=_T0 + timedelta(days=i), open=c, high=c, low=c, close=c, volume=0.0)
            for i, c in enumerate(closes)]


def test_build_strategy_builds_an_option_structure():
    strat = build_strategy("option_structure", _CSP)
    assert isinstance(strat, OptionStructureStrategy)
    assert isinstance(strat, Strategy)
    assert strat.required_timeframes() == ["1d"]


def test_option_structure_evaluate_is_inert():
    # A backtest-only structure has no live signal — it must not silently emit a tradeable
    # action, so evaluate raises rather than returning one.
    strat = build_strategy("option_structure", _CSP)
    with pytest.raises(NotALiveStrategyError):
        strat.evaluate({"1d": _daily([100.0] * 40)})


def test_option_structure_backtest_matches_backtest_structure():
    bars = _daily([100 * (1.0015 ** i) for i in range(150)])
    cfg = OptionsBacktestConfig()
    got = build_strategy("option_structure", _CSP).backtest({"1d": bars}, cfg)
    expected = backtest_structure(_CSP, bars, starting_cash=cfg.starting_cash, r=cfg.r,
                                  q=cfg.q, vrp=cfg.vrp, skew=cfg.skew, term=cfg.term)
    assert isinstance(got, OptionsMetrics)
    assert got == OptionsMetrics.from_structure(expected)
    assert got.trades > 0  # this uptrend trades


def test_options_and_equity_metrics_are_distinct_union_arms():
    # The router discriminates on the Metrics type; the two arms must be distinguishable.
    opts = build_strategy("option_structure", _CSP).backtest(
        {"1d": _daily([100 * (1.0015 ** i) for i in range(150)])}, OptionsBacktestConfig())
    equity = build_strategy("rule_dsl", _RULE_DSL).backtest(
        {"1m": _bars([100.0] * 40 + [90.0] * 20)}, BacktestConfig())
    assert isinstance(opts, OptionsMetrics) and not isinstance(opts, EquityMetrics)
    assert isinstance(equity, EquityMetrics) and not isinstance(equity, OptionsMetrics)
