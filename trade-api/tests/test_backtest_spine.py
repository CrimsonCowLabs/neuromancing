"""Behavioral coverage for the unified P&L-spine backtest (issue #1).

Every assertion observes EXTERNAL behavior through the one `Strategy.backtest` seam — the
returned metrics for a spec + bar series + params — never `_Book`'s internals or the
sequence of spine calls. The local `_bt` helper routes every case through
`build_strategy(kind, spec).backtest(bars_by_tf, BacktestConfig(...))`, wrapping a bare
single-tf bar list under one timeframe so single- and multi-tf kinds share the same shape.
The pattern throughout: hold the spec + bars fixed and vary ONLY the exit/sizing knobs, so a
difference in the metrics isolates the exact mechanism under test.

The long cases use a momentum entry (`roc > 8%`) with NO exit condition, so the strategy
opens once and then only ever holds — the ONLY way out is exit settlement. That makes a
stop / take-profit / trailing exit the sole cause of any close, cleanly separable. A
single upward jump (not a sustained ramp) triggers the entry so momentum has faded by the
time the exit fires, avoiding a same-bar re-entry.

`apply_fill` and `evaluate_exit` are consumed here, not re-tested — they have their own
property tests (test_portfolio / test_exits)."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from app.strategies.base import Bar
from app.strategies.interface import BacktestConfig, ExitConfig, build_strategy
from app.strategies.spec import validate_spec

UTC = timezone.utc
_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _bt(kind, spec, bars, starting_cash=10000.0, *, alloc_pct=None, cost_bps=None,
        exit_config=None):
    """Run a backtest through the interface, returning metrics as a dict. A bare bar list is
    wrapped under one timeframe; a bars-by-tf map is passed through. Omitted knobs fall back
    to `BacktestConfig`'s live-like defaults (0.20 alloc, 5 bps/side, mandatory 0.08 stop)."""
    knobs = {}
    if alloc_pct is not None:
        knobs["alloc_pct"] = alloc_pct
    if cost_bps is not None:
        knobs["cost_bps"] = cost_bps
    cfg = BacktestConfig(starting_cash=starting_cash, exit_config=exit_config or ExitConfig(),
                         **knobs)
    bars_by_tf = bars if isinstance(bars, dict) else {"1m": bars}
    return asdict(build_strategy(kind, spec).backtest(bars_by_tf, cfg))


# Momentum entry, no signal exit → settlement is the only exit. Opens one long on the
# upward-momentum crossing, then holds.
_MOM = {"buy_when": {"all": [{"indicator": "roc", "period": 5, "op": ">", "value": 0.08}]}}
_FLAT = [100.0] * 40          # warmup
_JUMP = 111.0                 # single +11% bar → one roc>8% entry at ~111


def _bars(closes: list[float]) -> list[Bar]:
    return [Bar(ts=_T0 + timedelta(minutes=i), open=c, high=c, low=c, close=c, volume=1)
            for i, c in enumerate(closes)]


def _mom(after: list[float]) -> list[Bar]:
    """Warmup + a single jump entry (~111) + a bespoke post-entry price path."""
    return _bars(_FLAT + [_JUMP] + after)


# ── exit settlement: the load-bearing part of the change ──
def test_tight_stop_cuts_a_long_loss_vs_a_loose_stop():
    # Enter, then collapse. A tight stop settles near entry; a loose stop rides it down.
    collapse = [_JUMP * (1 - 0.02 * k) for k in range(1, 25)]
    bars = _mom(collapse)
    tight = _bt("rule_dsl", _MOM, bars, exit_config=ExitConfig(stop_loss_pct=0.03))
    loose = _bt("rule_dsl", _MOM, bars, exit_config=ExitConfig(stop_loss_pct=0.90))
    assert tight["trades"] == 1 and loose["trades"] == 1
    assert tight["final_equity"] > loose["final_equity"]  # the stop cut the loss


def test_take_profit_banks_a_gain_that_is_otherwise_given_back():
    # Drift up past the target, then round-trip back below entry. With a take-profit the
    # trade banks a win at the target; without one it's held through and ends underwater.
    drift_up = [_JUMP * (1 + 0.012 * k) for k in range(1, 12)]        # gentle rise past +8%
    fall = [drift_up[-1] * (1 - 0.02 * k) for k in range(1, 25)]      # back below entry
    bars = _mom(drift_up + fall)
    tp = _bt("rule_dsl", _MOM, bars, cost_bps=0.0, exit_config=ExitConfig(take_profit_pct=0.08))
    no_tp = _bt("rule_dsl", _MOM, bars, cost_bps=0.0, exit_config=ExitConfig())
    assert tp["trades"] == 1 and tp["win_rate"] == 1.0 and tp["final_equity"] > 10000.0
    assert no_tp["win_rate"] == 0.0 and no_tp["final_equity"] < 10000.0
    assert tp["final_equity"] > no_tp["final_equity"]


def test_trailing_stop_banks_more_of_a_run_than_a_fixed_stop_alone():
    # Big run up, then a pullback that's shallow vs entry but past the trailing width. A
    # trailing stop exits near the high (a win); a fixed stop never triggers (price stays
    # above entry) so the run is given back with the position still open.
    rise = [_JUMP * (1 + 0.03 * k) for k in range(1, 10)]            # strong run
    pull = [rise[-1] * (1 - 0.012 * k) for k in range(1, 6)]         # ~6% pullback, still > entry
    bars = _mom(rise + pull)
    trail = _bt("rule_dsl", _MOM, bars, cost_bps=0.0, exit_config=ExitConfig(trailing_stop_pct=0.05))
    fixed = _bt("rule_dsl", _MOM, bars, cost_bps=0.0, exit_config=ExitConfig(stop_loss_pct=0.08))
    assert trail["trades"] == 1 and trail["win_rate"] == 1.0
    assert trail["final_equity"] > fixed["final_equity"]  # trailing captured the run


# ── shorts mirror (indicator_dsl → backtest_multi) ──
_SHORT_SPEC = validate_spec({
    "base_timeframe": "5m",
    "indicators": [{"id": "r", "fn": "rsi", "period": 14, "timeframe": "5m"}],
    "short_when": {"all": [{"indicator": "r", "cross": "above", "value": 65}]},
    "cover_when": {"any": [{"indicator": "r", "cross": "below", "value": 35}]},
})


def _bars5m(closes: list[float]) -> list[Bar]:
    return [Bar(ts=_T0 + timedelta(minutes=5 * i), open=c, high=c, low=c, close=c, volume=1)
            for i, c in enumerate(closes)]


def test_short_stop_fires_above_entry():
    # A dip (RSI low) then a sustained rally that crosses RSI up through 65 → opens a short,
    # then keeps climbing so the short's ABOVE-entry stop is what settles it.
    closes = [100 - i * 0.5 for i in range(40)] + [80 + i * 2 for i in range(40)]
    bars = {"5m": _bars5m(closes)}
    tight = _bt("indicator_dsl", _SHORT_SPEC, bars, exit_config=ExitConfig(stop_loss_pct=0.03))
    loose = _bt("indicator_dsl", _SHORT_SPEC, bars, exit_config=ExitConfig(stop_loss_pct=0.90))
    assert tight["trades"] >= 1  # the short opened
    # A short loses as price rises; covering early (tight, above-entry stop) beats riding it.
    assert tight["final_equity"] > loose["final_equity"]


# ── fixed-notional sizing ──
def _clean_winning_long() -> list[Bar]:
    # One momentum entry, gentle climb to a +10% take-profit: a single winning long.
    return _mom([_JUMP * (1 + 0.012 * k) for k in range(1, 12)])


def test_return_scales_linearly_with_alloc_pct():
    bars = _clean_winning_long()
    r02 = _bt("rule_dsl", _MOM, bars, cost_bps=0.0, alloc_pct=0.20,
                   exit_config=ExitConfig(take_profit_pct=0.10))
    r04 = _bt("rule_dsl", _MOM, bars, cost_bps=0.0, alloc_pct=0.40,
                   exit_config=ExitConfig(take_profit_pct=0.10))
    assert r02["trades"] == r04["trades"] == 1
    assert abs(r04["total_return"] - 2 * r02["total_return"]) < 1e-6  # notional ∝ alloc_pct


def test_return_is_independent_of_starting_cash():
    # Sizing is a fraction of STARTING cash (non-compounding), so the same % move yields the
    # same total_return regardless of absolute account size.
    bars = _clean_winning_long()
    small = _bt("rule_dsl", _MOM, bars, 10_000.0, cost_bps=0.0,
                     exit_config=ExitConfig(take_profit_pct=0.10))
    big = _bt("rule_dsl", _MOM, bars, 1_000_000.0, cost_bps=0.0,
                   exit_config=ExitConfig(take_profit_pct=0.10))
    assert abs(small["total_return"] - big["total_return"]) < 1e-6


def test_long_sizing_falls_back_to_available_cash():
    # With alloc_pct >= 1 the target notional always exceeds cash, so a long is capped at
    # available cash. Two different oversized allocs must then book identically (both bound
    # by cash) and the account is never driven negative by an unaffordable position.
    collapse = [_JUMP * (1 - 0.02 * k) for k in range(1, 25)]
    bars = _mom(collapse)
    a1 = _bt("rule_dsl", _MOM, bars, cost_bps=10.0, alloc_pct=1.0)
    a3 = _bt("rule_dsl", _MOM, bars, cost_bps=10.0, alloc_pct=3.0)
    assert a1 == a3                # both bound by cash → identical books
    assert a1["final_equity"] > 0  # never booked a position it couldn't afford


# ── churn cost ──
def test_per_side_cost_reduces_return():
    # Same entry/exit; only the per-side cost differs → the costed run returns less.
    bars = _clean_winning_long()
    free = _bt("rule_dsl", _MOM, bars, cost_bps=0.0, exit_config=ExitConfig(take_profit_pct=0.10))
    costed = _bt("rule_dsl", _MOM, bars, cost_bps=50.0, exit_config=ExitConfig(take_profit_pct=0.10))
    assert free["trades"] == costed["trades"] == 1
    assert costed["total_return"] < free["total_return"]


# ── multi-tf path shares the accounting + exit replay ──
def test_multi_tf_honors_exit_settlement_like_single_tf():
    # The single- and multi-tf paths run the SAME _replay: a tight stop must settle a
    # multi-tf long exactly as it does a single-tf one.
    spec = validate_spec({
        "base_timeframe": "5m",
        "indicators": [{"id": "r", "fn": "rsi", "period": 14, "timeframe": "5m"}],
        "buy_when": {"all": [{"indicator": "r", "cross": "below", "value": 35}]},
    })
    # rally (RSI high) then a long decline: RSI crosses down through 35 → buy, then the
    # decline continues so the stop settles the exit.
    closes = [80 + i for i in range(30)] + [110 - i * 2 for i in range(40)]
    bars = {"5m": _bars5m(closes)}
    tight = _bt("indicator_dsl", spec, bars, exit_config=ExitConfig(stop_loss_pct=0.03))
    loose = _bt("indicator_dsl", spec, bars, exit_config=ExitConfig(stop_loss_pct=0.90))
    assert tight["trades"] >= 1
    assert tight["final_equity"] > loose["final_equity"]  # the stop settled the exit
