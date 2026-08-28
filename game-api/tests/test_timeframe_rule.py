"""game-api derives "which timeframes does this spec need" from the ONE shared rule in
`neuromancing_shared.strategy_spec` — it no longer hand-copies `_base_tf`/`_TF_SECONDS`.
Together with trade-api's re-export test, this asserts both services derive identical
timeframes from the same spec."""

from __future__ import annotations

from datetime import datetime, timezone

from neuromancing_shared.strategy_spec import required_timeframes

from app.evolve import tools

_SPEC = {"base_timeframe": "5m", "indicators": [
    {"id": "trend", "fn": "macd", "fast": 12, "slow": 26, "signal": 9, "timeframe": "1h"},
    {"id": "rsi14", "fn": "rsi", "period": 14, "timeframe": "5m"}]}


def test_evolve_tools_no_longer_defines_a_local_timeframe_rule():
    assert not hasattr(tools, "_base_tf")
    assert not hasattr(tools, "_TF_SECONDS")


def test_evolve_reaches_the_shared_rule_by_reexport():
    assert tools.required_timeframes is required_timeframes


def test_walk_forward_windows_derive_from_the_shared_timeframes():
    # The window span is sized from the shallowest of the spec's REQUIRED timeframes, and
    # those timeframes come from the shared rule — same set trade-api would load.
    assert required_timeframes(_SPEC) == ["5m", "1h"]
    w = tools.walk_forward_windows(_SPEC, datetime(2026, 3, 3, tzinfo=timezone.utc))
    assert len(w) == 2 and w[0][1] == datetime(2026, 3, 3, tzinfo=timezone.utc)
