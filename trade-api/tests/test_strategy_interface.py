"""The collapse of the whole evaluation surface into one `Strategy` interface (issue #8).

These tests observe the interface itself — the seam the router and the evolution loop now
share: `build_strategy(kind, spec)` and the three questions it answers (required_timeframes,
evaluate, backtest). The behavior-preservation of each *kind* lives in its own suite
(test_backtest_spine / test_composed / test_strategies / test_options_backtest); here we pin
the properties the collapse itself introduces.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.strategies import build_strategy
from app.strategies.base import Bar
from app.strategies.interface import (
    OPTION_STRUCTURE_KIND,
    EquityMetrics,
    OptionsMetrics,
)
from app.strategies.spec import validate_spec

UTC = timezone.utc


def _bars(closes):
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    return [Bar(ts=t0 + timedelta(minutes=i), open=c, high=c, low=c, close=c, volume=1)
            for i, c in enumerate(closes)]


# ── factory ──────────────────────────────────────────────────────────────
def test_build_strategy_unknown_kind_fails_loudly():
    # Rejected at construction, not deep inside a dispatch branch.
    with pytest.raises(ValueError):
        build_strategy("not_a_kind", {})


def test_option_kind_is_superset_not_a_persisted_kind():
    # The options kind exists in the factory vocabulary but is NOT a persisted StrategyKind.
    from app.models import StrategyKind

    assert build_strategy(OPTION_STRUCTURE_KIND, {"archetype": "iron_condor", "dte": 30})
    assert OPTION_STRUCTURE_KIND not in {k.value for k in StrategyKind}


# ── required_timeframes rule ──────────────────────────────────────────────
def test_required_timeframes_indicator_dsl_uses_spec_set():
    spec = validate_spec({
        "base_timeframe": "5m",
        "indicators": [{"id": "t", "fn": "sma", "period": 3, "timeframe": "1h"},
                       {"id": "r", "fn": "rsi", "period": 14, "timeframe": "5m"}],
        "buy_when": {"all": [{"indicator": "r", "op": "<", "value": 30}]}})
    strat = build_strategy("indicator_dsl", spec)
    # indicator tfs ∪ base, fastest-first — and it ignores the request timeframe.
    assert strat.required_timeframes("1d") == ["5m", "1h"]


def test_required_timeframes_single_series_uses_request():
    strat = build_strategy("signal_fn", {"fn": "sma_crossover", "fast": 5, "slow": 20})
    assert strat.required_timeframes("5m") == ["5m"]
    assert strat.required_timeframes(None) == ["1m"]  # default when unset


def test_required_timeframes_options_is_daily():
    strat = build_strategy(OPTION_STRUCTURE_KIND, {"archetype": "iron_condor", "dte": 30})
    assert strat.required_timeframes() == ["1d"]


# ── single-tf kinds accept a one-entry bars-by-tf map ─────────────────────
def test_single_series_one_entry_map_matches_across_timeframe_keys():
    # A single-timeframe kind reads its one series regardless of which tf key holds it — the
    # single-vs-multi split no longer leaks into the caller. Same bars → same signal.
    spec = {"fn": "rsi_reversion", "period": 14, "low": 30, "high": 70}
    bars = _bars([100 + i for i in range(25)] + [124 - i * 7 for i in range(1, 16)])
    strat = build_strategy("signal_fn", spec)
    under_1m = strat.evaluate({"1m": bars})
    under_5m = strat.evaluate({"5m": bars})
    assert under_1m.action == under_5m.action
    assert strat.backtest({"1m": bars}) == strat.backtest({"5m": bars})


# ── options evaluate() is inert by type ───────────────────────────────────
def test_options_evaluate_is_inert():
    # An option structure is backtest-only; its live signal can never be tradeable.
    strat = build_strategy(OPTION_STRUCTURE_KIND, {"archetype": "cash_secured_put", "dte": 30})
    sig = strat.evaluate({"1d": _bars([100.0] * 30)})
    assert sig.action == "hold"


# ── the discriminated Metrics union ───────────────────────────────────────
def test_backtest_returns_tagged_metric_shapes():
    eq = build_strategy("signal_fn", {"fn": "sma_crossover", "fast": 5, "slow": 20}).backtest(
        {"1m": _bars([100 + i for i in range(60)])})
    assert isinstance(eq, EquityMetrics) and eq.tag == "equity"
    assert set(eq.to_dict()) == {"bars", "trades", "win_rate", "total_return",
                                 "max_drawdown", "final_equity"}

    opt = build_strategy(OPTION_STRUCTURE_KIND,
                         {"archetype": "cash_secured_put", "dte": 30, "alloc_pct": 0.5}).backtest(
        {"1d": _bars([100 * (1.001 ** i) for i in range(120)])})
    assert isinstance(opt, OptionsMetrics) and opt.tag == "options"
    assert set(opt.to_dict()) == {"trades", "win_rate", "total_return", "max_drawdown",
                                  "avg_credit", "avg_return_on_risk", "assignment_rate",
                                  "final_equity"}
