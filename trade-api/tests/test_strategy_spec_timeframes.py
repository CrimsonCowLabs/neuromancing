"""Unit coverage for the shared timeframe rule (`neuromancing_shared.strategy_spec`).

This rule used to live in four hand-kept copies (three in trade-api, one cross-service in
game-api). It now has ONE home; these tests pin its behavior. trade-api's interface tests
and game-api's evolve tests then assert they derive the SAME timeframes from the same spec,
proving the cross-service copy is gone rather than merely relocated (issue #8)."""

from __future__ import annotations

from neuromancing_shared.strategy_spec import (
    base_timeframe,
    required_timeframes,
    tf_seconds,
)


def test_tf_seconds_orders_and_falls_back():
    assert tf_seconds("1m") < tf_seconds("5m") < tf_seconds("1h") < tf_seconds("1d")
    assert tf_seconds("nonsense") == 60  # unknown → 1m


def test_base_timeframe_defaults_to_fastest_indicator():
    spec = {"indicators": [{"id": "a", "timeframe": "1h"}, {"id": "b", "timeframe": "5m"}]}
    assert base_timeframe(spec) == "5m"  # fastest of the indicator tfs


def test_base_timeframe_explicit_wins():
    spec = {"base_timeframe": "1h", "indicators": [{"id": "b", "timeframe": "5m"}]}
    assert base_timeframe(spec) == "1h"  # explicit overrides fastest-indicator


def test_base_timeframe_falls_back_to_1m_without_indicator_tfs():
    assert base_timeframe({"indicators": [{"id": "a", "fn": "rsi", "period": 14}]}) == "1m"
    assert base_timeframe({}) == "1m"  # tolerates a missing indicators key


def test_required_timeframes_is_indicator_union_base_fastest_first():
    spec = {"base_timeframe": "1d",
            "indicators": [{"id": "a", "timeframe": "1h"}, {"id": "b", "timeframe": "5m"}]}
    assert required_timeframes(spec) == ["5m", "1h", "1d"]  # (5m,1h) ∪ base(1d), ordered
