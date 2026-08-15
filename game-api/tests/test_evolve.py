"""Offline coverage for the deep-agent deterministic pieces: adoption gate, trigger
gate, proposal compile/validation, and walk-forward windows. No LLM, no DB."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.evolve.gate import GateConfig, should_adopt
from app.evolve.proposal import ProposedStrategy, compile_proposal
from app.evolve.tools import walk_forward_windows
from app.evolve.trigger import TriggerConfig, should_evolve_now

UTC = timezone.utc
GC = GateConfig(return_margin=0.02, min_trades=5, max_dd=0.4)


def _m(ret, trades=8, dd=0.2):
    return {"total_return": ret, "trades": trades, "max_drawdown": dd}


# ---------------- adoption gate ----------------
def test_gate_adopts_when_better_both_windows():
    inc = {"w1": _m(0.01), "w2": _m(0.0)}
    cand = {"w1": _m(0.06), "w2": _m(0.05)}
    ok, _ = should_adopt(inc, cand, GC)
    assert ok


@pytest.mark.parametrize("cand,why", [
    ({"w1": _m(0.06), "w2": _m(0.001)}, "one-window"),   # no edge on w2
    ({"w1": _m(0.09, trades=2), "w2": _m(0.09)}, "few-trades"),
    ({"w1": _m(0.09, dd=0.6), "w2": _m(0.09)}, "drawdown"),
])
def test_gate_rejects(cand, why):
    inc = {"w1": _m(0.0), "w2": _m(0.0)}
    ok, _ = should_adopt(inc, cand, GC)
    assert not ok


# ---------------- trigger gate ----------------
TC = TriggerConfig(min_hours_between=24, min_diary_episodes=10, crypto_utc_hour=0)
NOW = datetime(2026, 3, 3, 21, 30, tzinfo=UTC)


def test_trigger_cooldown():
    ok, why = should_evolve_now(now=NOW, last_ts=NOW - timedelta(hours=5), closed_episodes=50,
                                is_crypto_only=False, session_closed_today=True, cfg=TC)
    assert not ok and "cooldown" in why


def test_trigger_cold_start():
    ok, why = should_evolve_now(now=NOW, last_ts=None, closed_episodes=3,
                                is_crypto_only=False, session_closed_today=True, cfg=TC)
    assert not ok and "cold start" in why


def test_trigger_equity_needs_session_close():
    ok, _ = should_evolve_now(now=NOW, last_ts=None, closed_episodes=20,
                              is_crypto_only=False, session_closed_today=False, cfg=TC)
    assert not ok


def test_trigger_equity_ok_after_close():
    ok, _ = should_evolve_now(now=NOW, last_ts=None, closed_episodes=20,
                              is_crypto_only=False, session_closed_today=True, cfg=TC)
    assert ok


def test_trigger_crypto_daily_boundary():
    at_hour = NOW.replace(hour=0)
    ok, _ = should_evolve_now(now=at_hour, last_ts=None, closed_episodes=20,
                              is_crypto_only=True, session_closed_today=False, cfg=TC)
    assert ok
    off, _ = should_evolve_now(now=NOW, last_ts=None, closed_episodes=20,
                               is_crypto_only=True, session_closed_today=False, cfg=TC)
    assert not off


# ---------------- proposal compile / validate ----------------
def test_compile_valid_proposal():
    p = ProposedStrategy.model_validate({
        "hypothesis": "trend + dip", "type": "trend_pullback", "base_timeframe": "5m",
        "indicators": [
            {"id": "trend", "fn": "macd", "fast": 12, "slow": 26, "signal": 9, "timeframe": "1h"},
            {"id": "rsi14", "fn": "rsi", "period": 14, "timeframe": "5m"}],
        "buy": {"combinator": "all", "conditions": [
            {"indicator": "trend", "field": "hist", "op": ">", "value": 0},
            {"indicator": "rsi14", "cross": "above", "value": 40}]},
        "exit": {"combinator": "any", "conditions": [{"indicator": "rsi14", "op": ">", "value": 70}]},
        "strength": {"from_indicator": "rsi14", "map_low": 40, "map_high": 20},
    })
    spec = compile_proposal(p)
    assert spec["buy_when"]["all"][1]["cross"] == "above"
    assert spec["strength"]["buy"]["from"] == "rsi14"


def test_compile_rejects_unknown_fn():
    p = ProposedStrategy.model_validate({
        "hypothesis": "x", "indicators": [{"id": "a", "fn": "bogus", "period": 5}],
        "buy": {"combinator": "all", "conditions": [{"indicator": "a", "op": ">", "value": 1}]},
        "exit": {"combinator": "any", "conditions": [{"indicator": "a", "op": "<", "value": 1}]},
    })
    with pytest.raises(ValueError):
        compile_proposal(p)


# ---------------- walk-forward windows ----------------
def test_walk_forward_windows_nonoverlapping_and_tf_scaled():
    now = datetime(2026, 3, 3, tzinfo=UTC)
    spec_5m = {"base_timeframe": "5m", "indicators": [{"id": "r", "fn": "rsi", "period": 14, "timeframe": "5m"}]}
    w = walk_forward_windows(spec_5m, now)
    assert len(w) == 2
    (s1, e1), (s2, e2) = w
    assert e2 <= s1          # non-overlapping (w2 strictly before w1)
    assert e1 == now
    # 1h strategy gets deeper (longer) windows than a 5m one
    spec_1h = {"base_timeframe": "1h", "indicators": [{"id": "t", "fn": "sma", "period": 20, "timeframe": "1h"}]}
    w_1h = walk_forward_windows(spec_1h, now)
    assert (w_1h[0][1] - w_1h[0][0]) > (e1 - s1)
