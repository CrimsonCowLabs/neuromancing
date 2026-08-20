from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from app.engine.matching import evaluate_exit

D = Decimal


def test_stop_loss_triggers_at_or_below_level():
    assert evaluate_exit(avg_entry=100, last=95.01, stop_loss_pct=0.05).should_exit is False  # above stop
    assert evaluate_exit(avg_entry=100, last=95, stop_loss_pct=0.05).should_exit  # at the stop
    r = evaluate_exit(avg_entry=100, last=94.99, stop_loss_pct=0.05)
    assert r.should_exit and r.reason == "stop_loss"


def test_take_profit_triggers_above_level():
    assert evaluate_exit(avg_entry=100, last=109, take_profit_pct=0.10).should_exit is False
    r = evaluate_exit(avg_entry=100, last=110, take_profit_pct=0.10)
    assert r.should_exit and r.reason == "take_profit"


def test_trailing_uses_high_water():
    # hw 120, 5% trail -> stop at 114; 113 exits, 115 holds
    assert evaluate_exit(avg_entry=100, last=113, high_water=120, trailing_stop_pct=0.05).should_exit
    assert not evaluate_exit(avg_entry=100, last=115, high_water=120, trailing_stop_pct=0.05).should_exit


def test_trailing_ratchets_monotonically():
    r = evaluate_exit(avg_entry=100, last=130, high_water=120, trailing_stop_pct=0.05)
    assert r.high_water == D("130") and not r.should_exit
    # A lower last never lowers the high-water within a single call vs the passed-in hw.
    r2 = evaluate_exit(avg_entry=100, last=110, high_water=130, trailing_stop_pct=0.05)
    assert r2.high_water == D("130")


def test_tighter_of_fixed_and_trailing_wins():
    # entry 100, fixed stop 90 (10%); hw 120 trailing 5% -> 114. Tighter (higher) = 114 trailing.
    r = evaluate_exit(avg_entry=100, last=113, high_water=120, stop_loss_pct=0.10, trailing_stop_pct=0.05)
    assert r.should_exit and r.reason == "trailing_stop"


def test_no_levels_never_exits():
    assert not evaluate_exit(avg_entry=100, last=1).should_exit


@settings(max_examples=200)
@given(
    entry=st.decimals(min_value=D("1"), max_value=D("10000"), places=2),
    last=st.decimals(min_value=D("0.01"), max_value=D("20000"), places=2),
    sl=st.decimals(min_value=D("0.01"), max_value=D("0.9"), places=2),
)
def test_never_exits_on_stop_above_stop_price(entry, last, sl):
    r = evaluate_exit(avg_entry=entry, last=last, stop_loss_pct=sl)
    if r.should_exit and r.reason == "stop_loss":
        # If it stopped out, price must be at/below the stop level.
        assert last <= entry * (D(1) - sl)


# ── short mirror (qty < 0) ──
def test_short_stop_triggers_at_or_above_level():
    # short entry 100, 5% stop -> stop ABOVE at 105; 104.99 holds, 105 covers
    assert not evaluate_exit(avg_entry=100, last=104.99, qty=-10, stop_loss_pct=0.05).should_exit
    r = evaluate_exit(avg_entry=100, last=105, qty=-10, stop_loss_pct=0.05)
    assert r.should_exit and r.reason == "stop_loss"


def test_short_take_profit_triggers_below_level():
    # short target 10% BELOW at 90; 91 holds, 90 covers for profit
    assert not evaluate_exit(avg_entry=100, last=91, qty=-10, take_profit_pct=0.10).should_exit
    r = evaluate_exit(avg_entry=100, last=90, qty=-10, take_profit_pct=0.10)
    assert r.should_exit and r.reason == "take_profit"


def test_short_trailing_uses_low_water():
    # low-water 80, 5% trail -> stop at 84; 85 covers, 83 holds
    assert evaluate_exit(avg_entry=100, last=85, qty=-10, high_water=80, trailing_stop_pct=0.05).should_exit
    assert not evaluate_exit(avg_entry=100, last=83, qty=-10, high_water=80, trailing_stop_pct=0.05).should_exit
    # ratchets DOWN
    r = evaluate_exit(avg_entry=100, last=70, qty=-10, high_water=80, trailing_stop_pct=0.05)
    assert r.high_water == D("70")
