"""Unit coverage for the one home of the timeframe rule — `base_timeframe` /
`required_timeframes` / `tf_seconds` in `neuromancing_shared.strategy_spec`. Both services
derive their timeframes from here (trade-api and game-api each assert they reach these by
re-export in their own suites)."""

from __future__ import annotations

from neuromancing_shared.strategy_spec import (
    base_timeframe,
    required_timeframes,
    tf_seconds,
)


def test_base_timeframe_defaults_to_fastest_indicator():
    spec = {"indicators": [
        {"id": "a", "fn": "rsi", "period": 14, "timeframe": "1h"},
        {"id": "b", "fn": "sma", "period": 5, "timeframe": "5m"}]}
    assert base_timeframe(spec) == "5m"  # 5m is faster than 1h


def test_explicit_base_timeframe_wins_over_indicators():
    spec = {"base_timeframe": "1d", "indicators": [
        {"id": "a", "fn": "rsi", "period": 14, "timeframe": "5m"}]}
    assert base_timeframe(spec) == "1d"


def test_base_timeframe_falls_back_to_1m_without_indicator_tfs():
    assert base_timeframe({"indicators": [{"id": "a", "fn": "rsi", "period": 14}]}) == "1m"
    assert base_timeframe({}) == "1m"


def test_required_timeframes_is_indicator_tfs_union_base_fastest_first():
    spec = {"base_timeframe": "1d", "indicators": [
        {"id": "a", "fn": "rsi", "period": 14, "timeframe": "1h"},
        {"id": "b", "fn": "sma", "period": 5, "timeframe": "5m"}]}
    # {5m, 1h} ∪ {1d}, ordered fastest→slowest
    assert required_timeframes(spec) == ["5m", "1h", "1d"]


def test_tf_seconds_orders_the_timeframes_and_defaults_unknown():
    assert tf_seconds("1m") < tf_seconds("5m") < tf_seconds("1h") < tf_seconds("1d")
    assert tf_seconds("bogus") == 60
