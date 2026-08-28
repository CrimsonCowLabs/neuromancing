"""trade-api reaches the timeframe rule through a re-export of the ONE home
(`neuromancing_shared.strategy_spec`) — never a hand-kept copy. The rule's own behavior is
unit-tested in the shared package (shared/tests/test_strategy_spec.py); here we only assert
trade-api's public entry points ARE those shared functions."""

from __future__ import annotations

from neuromancing_shared.strategy_spec import base_timeframe, required_timeframes

from app.strategies.composed import base_timeframe as trade_base_tf
from app.strategies.composed import required_timeframes as trade_required_tfs


def test_trade_api_reaches_the_shared_rule_by_reexport():
    # Not a copy: the trade-api symbols ARE the shared functions.
    assert trade_base_tf is base_timeframe
    assert trade_required_tfs is required_timeframes


def test_reexport_derives_the_expected_timeframes():
    spec = {"base_timeframe": "1d", "indicators": [
        {"id": "a", "fn": "rsi", "period": 14, "timeframe": "1h"},
        {"id": "b", "fn": "sma", "period": 5, "timeframe": "5m"}]}
    assert trade_required_tfs(spec) == ["5m", "1h", "1d"]
