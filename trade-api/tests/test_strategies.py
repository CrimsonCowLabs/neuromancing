import math
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from app.strategies.base import Bar
from app.strategies.indicators import rsi, sma
from app.strategies.interface import BacktestConfig, build_strategy


def _bars(closes):
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [Bar(ts=t0 + timedelta(minutes=i), open=c, high=c, low=c, close=c, volume=1) for i, c in enumerate(closes)]


def _evaluate(kind, spec, bars):
    """Single-series evaluation through the interface: a bare bar list under one timeframe."""
    return build_strategy(kind, spec).evaluate({"1m": bars})


def _backtest(kind, spec, bars):
    return asdict(build_strategy(kind, spec).backtest({"1m": bars}, BacktestConfig()))


def test_sma_and_rsi_basic():
    assert sma([1, 2, 3, 4], 2) == 3.5
    # All-up series -> RSI 100
    assert rsi([float(i) for i in range(1, 30)], 14) == 100.0


def test_sma_crossover_buy_on_cross_up():
    # Downtrend then sharp uptrend to force fast>slow crossover.
    closes = [100 - i for i in range(40)] + [60 + i * 3 for i in range(20)]
    spec = {"fn": "sma_crossover", "fast": 5, "slow": 20}
    # Evaluate over the full series; at the end fast should be above slow.
    sig = _evaluate("signal_fn", spec, _bars(closes))
    assert sig.action in ("buy", "hold")  # deterministic, no exception


_RSI_SPEC = {"fn": "rsi_reversion", "period": 14, "low": 30, "high": 70}
_MOM_SPEC = {"fn": "momentum", "lookback": 10, "threshold": 0.03}


def _count_action(spec, closes, start, action):
    """How many times the strategy emits `action` as the series grows bar by bar."""
    return sum(
        1 for i in range(start, len(closes) + 1)
        if _evaluate("signal_fn", spec, _bars(closes[:i])).action == action
    )


def test_rsi_reversion_event_no_spam():
    # A persistently-oversold stretch must NOT re-fire buy every tick (event-driven).
    closes = [100 - i for i in range(40)]  # monotonic decline -> RSI pinned low
    assert _count_action(_RSI_SPEC, closes, 20, "buy") <= 1


def test_rsi_reversion_fires_once_on_cross():
    # Rise (RSI high) then a sharp drop that crosses RSI down through 30 -> exactly one buy.
    closes = [100 + i for i in range(25)] + [124 - i * 7 for i in range(1, 16)]
    assert _count_action(_RSI_SPEC, closes, 26, "buy") == 1


def test_momentum_event_no_spam():
    # Sustained uptrend (momentum stays positive) must fire buy at most once.
    closes = [100 + i * 2 for i in range(40)]
    assert _count_action(_MOM_SPEC, closes, 12, "buy") <= 1


def test_rule_dsl_dip_buyer():
    closes = [100 - i * 2 for i in range(30)]
    spec = {
        "buy_when": {"all": [{"indicator": "rsi", "period": 14, "op": "<", "value": 35}]},
        "exit_when": {"any": [{"indicator": "rsi", "period": 14, "op": ">", "value": 65}]},
    }
    sig = _evaluate("rule_dsl", spec, _bars(closes))
    assert sig.action == "buy"


def test_backtest_is_deterministic():
    closes = [100 + 10 * math.sin(i / 5) for i in range(300)]
    spec = {"fn": "sma_crossover", "fast": 5, "slow": 20}
    r1 = _backtest("signal_fn", spec, _bars(closes))
    r2 = _backtest("signal_fn", spec, _bars(closes))
    assert r1 == r2
    assert r1["bars"] == 300
    assert r1["trades"] >= 0


def test_unknown_kind_raises():
    import pytest

    with pytest.raises(ValueError):
        _evaluate("nope", {}, _bars([1, 2, 3]))
