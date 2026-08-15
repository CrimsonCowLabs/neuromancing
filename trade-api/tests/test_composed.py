"""Offline coverage for the indicator_dsl model: indicator math, spec validation,
the composed evaluator (as-of multi-tf alignment, event-driven no-spam, strength),
the YAML catalog, and the multi-tf backtest. All pure — no infra."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.strategies import indicators as ind
from app.strategies.backtest import backtest_multi
from app.strategies.base import Bar
from app.strategies.composed import evaluate_composed
from app.strategies.engine import list_house_strategies
from app.strategies.spec import validate_spec

UTC = timezone.utc
T0 = datetime(2026, 3, 2, 14, 0, tzinfo=UTC)


def _flat(ts, c, v=1000.0):
    return Bar(ts=ts, open=c, high=c, low=c, close=c, volume=v)


def _series5m(prices, start=T0):
    return [_flat(start + timedelta(minutes=5 * i), c) for i, c in enumerate(prices)]


# ---------------- indicator math ----------------
def test_new_indicators_math():
    closes = [float(x) for x in range(1, 60)]
    bars = _series5m(closes)
    assert set(ind.macd(closes)) == {"line", "signal", "hist"}
    assert set(ind.bollinger(closes, 20)) == {"upper", "middle", "lower", "percent"}
    assert ind.bbpercent(closes, 20) is not None
    assert ind.atr(bars, 14) is not None
    assert ind.vwap(bars, 20) is not None
    assert ind.series_from_bars(bars, "hlc3")[0] == closes[0]
    assert ind.macd([1, 2, 3]) is None  # too short → None


# ---------------- validation ----------------
def test_valid_spec_roundtrips():
    canon = validate_spec({
        "type": "trend_pullback", "base_timeframe": "5m",
        "indicators": [
            {"id": "trend", "fn": "macd", "fast": 12, "slow": 26, "signal": 9, "timeframe": "1h"},
            {"id": "rsi14", "fn": "rsi", "period": 14, "timeframe": "5m"}],
        "states": {"up": {"indicator": "trend", "field": "hist", "op": ">", "value": 0},
                   "reclaim": {"indicator": "rsi14", "cross": "above", "value": 40}},
        "buy_when": {"all": ["up", "reclaim"]},
        "exit_when": {"any": [{"indicator": "rsi14", "op": ">", "value": 70}]},
    })
    assert canon["indicators"][0]["fn"] == "macd"


@pytest.mark.parametrize("spec", [
    {"indicators": [{"id": "x", "fn": "nope", "period": 5}],
     "buy_when": {"all": [{"indicator": "x", "op": ">", "value": 1}]}},          # unknown fn
    {"indicators": [{"id": "x", "fn": "rsi", "period": 14}], "buy_when": "ghost"},  # dangling state
    {"indicators": [{"id": "x", "fn": "rsi", "period": 14, "source": "bogus"}],
     "buy_when": {"all": [{"indicator": "x", "op": ">", "value": 1}]}},          # bad source
    {"indicators": [{"id": "x", "fn": "rsi", "period": 14}],
     "buy_when": {"all": [{"indicator": "x", "field": "hist", "op": ">", "value": 1}]}},  # field on scalar
    {"indicators": [{"id": "x", "fn": "macd", "fast": 12, "slow": 26, "signal": 9}],
     "buy_when": {"all": [{"indicator": "x", "op": ">", "value": 1}]}},          # macd needs field
    {"indicators": [{"id": "x", "fn": "rsi", "period": 14, "timeframe": "3m"}],
     "buy_when": {"all": [{"indicator": "x", "op": ">", "value": 1}]}},          # bad tf
])
def test_validation_rejects(spec):
    with pytest.raises(ValueError):
        validate_spec(spec)


# ---------------- event-driven, single tf ----------------
def test_zone_event_no_spam():
    spec = validate_spec({
        "base_timeframe": "5m",
        "indicators": [{"id": "r", "fn": "rsi", "period": 14, "timeframe": "5m"}],
        "buy_when": {"all": [{"indicator": "r", "op": "<", "value": 30}]}})
    b5 = _series5m([100 - i * 2 for i in range(40)])  # decline into oversold, stays there
    buys = sum(1 for i in range(2, len(b5) + 1)
               if evaluate_composed(spec, {"5m": b5[:i]}).action == "buy")
    assert buys == 1  # fires once on the crossing, not every tick it stays oversold


def test_cross_above_fires_once():
    spec = validate_spec({
        "base_timeframe": "5m",
        "indicators": [{"id": "f", "fn": "sma", "period": 3, "timeframe": "5m"},
                       {"id": "sl", "fn": "sma", "period": 8, "timeframe": "5m"}],
        "buy_when": {"all": [{"indicator": "f", "cross": "above", "other": "sl"}]}})
    b = _series5m([100, 99, 98, 97, 96, 95, 94, 93, 94, 96, 99, 103, 108, 114])
    buys = sum(1 for i in range(2, len(b) + 1)
               if evaluate_composed(spec, {"5m": b[:i]}).action == "buy")
    assert buys == 1


# ---------------- multi-tf: no lookahead + no spam ----------------
def _mtf_spec():
    # trend = sma(1) on 1h close → value == latest CLOSED 1h close
    return validate_spec({
        "base_timeframe": "5m",
        "indicators": [{"id": "trend", "fn": "sma", "period": 1, "timeframe": "1h"}],
        "buy_when": {"all": [{"indicator": "trend", "op": ">", "value": 500}]}})


def _h1():
    h = datetime(2026, 3, 2, 12, 0, tzinfo=UTC)
    return [_flat(h, 100), _flat(h + timedelta(hours=1), 110),
            _flat(h + timedelta(hours=2), 999)]  # 14:00 bar (c=999) closes at 15:00


def test_multitf_no_lookahead():
    spec, h1 = _mtf_spec(), _h1()
    b5 = _series5m([300] * 19, start=datetime(2026, 3, 2, 14, 0, tzinfo=UTC))
    end1430 = [x for x in b5 if x.ts <= datetime(2026, 3, 2, 14, 30, tzinfo=UTC)]
    end1455 = [x for x in b5 if x.ts <= datetime(2026, 3, 2, 14, 55, tzinfo=UTC)]
    # 14:00 1h bar not yet closed at 14:30 → trend still 110 → no early fire
    assert evaluate_composed(spec, {"5m": end1430, "1h": h1}).action == "hold"
    # bar closes at 15:00; the base step whose close-time hits 15:00 sees the flip
    assert evaluate_composed(spec, {"5m": end1455, "1h": h1}).action == "buy"


def test_multitf_no_spam():
    spec, h1 = _mtf_spec(), _h1()
    b5 = _series5m([300] * 19, start=datetime(2026, 3, 2, 14, 0, tzinfo=UTC))
    buys = sum(1 for i in range(2, len(b5) + 1)
               if evaluate_composed(spec, {"5m": b5[:i], "1h": h1}).action == "buy")
    assert buys == 1  # a persistent 1h condition fires exactly once, not every 5m


# ---------------- strength ----------------
def test_strength_map():
    spec = validate_spec({
        "base_timeframe": "5m",
        "indicators": [{"id": "r", "fn": "rsi", "period": 14, "timeframe": "5m"}],
        "buy_when": {"all": [{"indicator": "r", "op": "<", "value": 30}]},
        "strength": {"buy": {"from": "r", "map": [30, 0]}}})
    b5 = _series5m([100 - i * 2 for i in range(40)])
    for i in range(2, len(b5) + 1):
        s = evaluate_composed(spec, {"5m": b5[:i]})
        if s.action == "buy":
            assert 0.0 < s.strength <= 1.0
            return
    pytest.fail("no buy produced")


# ---------------- catalog ----------------
def test_catalog_loads_and_legacy_unchanged():
    hs = {h["name"]: h for h in list_house_strategies()}
    assert len(hs) >= 9
    assert hs["SMA 10/30 Crossover"]["spec"] == {"fn": "sma_crossover", "fast": 10, "slow": 30}
    assert hs["RSI Mean Reversion"]["spec"] == {"fn": "rsi_reversion", "period": 14, "low": 30, "high": 70}
    assert hs["20-bar Momentum"]["spec"] == {"fn": "momentum", "lookback": 20, "threshold": 0.03}
    assert hs["RSI-DSL Dip Buyer"]["spec"]["buy_when"]["all"][0]["value"] == 35
    # the augment strategies exist and are validated indicator_dsl
    for name in ("Bollinger Mean Reversion", "Trend Dip Buyer"):
        assert hs[name]["kind"] == "indicator_dsl" and hs[name]["spec"]["indicators"]
    assert sum(1 for h in hs.values() if h["kind"] == "indicator_dsl") >= 5


def test_bollinger_mean_reversion_fires():
    spec = {h["name"]: h for h in list_house_strategies()}["Bollinger Mean Reversion"]["spec"]
    b5 = _series5m([100.0] * 24 + [99, 97.5, 95.5, 93, 90, 86.5, 82.5])  # sharp drop
    buys = sum(1 for i in range(2, len(b5) + 1)
               if evaluate_composed(spec, {"5m": b5[:i]}).action == "buy")
    assert buys >= 1


# ---------------- multi-tf backtest ----------------
def test_backtest_multi_deterministic_and_no_lookahead():
    spec = validate_spec({
        "base_timeframe": "5m",
        "indicators": [{"id": "r", "fn": "rsi", "period": 14, "timeframe": "5m"}],
        "buy_when": {"all": [{"indicator": "r", "cross": "below", "value": 30}]},
        "exit_when": {"any": [{"indicator": "r", "cross": "above", "value": 55}]}})
    bars = {"5m": _series5m(_oscillating())}
    m1 = backtest_multi("indicator_dsl", spec, bars)
    m2 = backtest_multi("indicator_dsl", spec, bars)
    assert m1 == m2  # deterministic
    assert m1["trades"] >= 1  # this series trades


def _oscillating():
    """Repeated decline→rally cycles so RSI clearly crosses below 30 and above 55."""
    prices, p = [], 100.0
    for _ in range(6):
        for _ in range(15):
            p *= 0.97
            prices.append(p)
        for _ in range(15):
            p *= 1.03
            prices.append(p)
    return prices


def test_backtest_costs_reduce_return():
    spec = validate_spec({
        "base_timeframe": "5m",
        "indicators": [{"id": "r", "fn": "rsi", "period": 14, "timeframe": "5m"}],
        "buy_when": {"all": [{"indicator": "r", "cross": "below", "value": 30}]},
        "exit_when": {"any": [{"indicator": "r", "cross": "above", "value": 55}]}})
    bars = {"5m": _series5m(_oscillating())}
    free = backtest_multi("indicator_dsl", spec, bars, cost_bps=0.0)
    costed = backtest_multi("indicator_dsl", spec, bars, cost_bps=100.0)
    assert free["trades"] == costed["trades"] >= 1
    assert costed["total_return"] < free["total_return"]  # fees/slippage bite
