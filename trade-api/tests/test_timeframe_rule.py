"""The timeframe rule ("which timeframes does this spec need") has exactly one home:
`neuromancing_shared.strategy_spec`. These lock the rule's behavior and assert trade-api
reaches it through a re-export (never a hand-kept copy)."""

from __future__ import annotations

from neuromancing_shared.strategy_spec import base_timeframe, required_timeframes

# trade-api's public entry points for the rule — must BE the shared functions.
from app.strategies.composed import base_timeframe as trade_base_tf
from app.strategies.composed import required_timeframes as trade_required_tfs


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


def test_required_timeframes_is_indicator_tfs_union_base_fastest_first():
    spec = {"base_timeframe": "1d", "indicators": [
        {"id": "a", "fn": "rsi", "period": 14, "timeframe": "1h"},
        {"id": "b", "fn": "sma", "period": 5, "timeframe": "5m"}]}
    # {5m, 1h} ∪ {1d}, ordered fastest→slowest
    assert required_timeframes(spec) == ["5m", "1h", "1d"]


def test_trade_api_reaches_the_shared_rule_by_reexport():
    # Not a copy: the trade-api symbols ARE the shared functions.
    assert trade_base_tf is base_timeframe
    assert trade_required_tfs is required_timeframes
