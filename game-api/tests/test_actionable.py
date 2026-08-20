from app.agents.context import actionable_signals

EQUITY = 10000.0
CAP_PCT = 0.20  # cap = $2000 per position


def test_buy_kept_when_room():
    sig = {"AAPL": {"action": "buy", "strength": 0.8}}
    out = actionable_signals(sig, positions={}, position_values={}, equity=EQUITY, max_position_pct=CAP_PCT)
    assert "AAPL" in out


def test_buy_dropped_when_at_cap():
    sig = {"AAPL": {"action": "buy", "strength": 0.8}}
    out = actionable_signals(
        sig, positions={"AAPL": 10}, position_values={"AAPL": 2000.0}, equity=EQUITY, max_position_pct=CAP_PCT
    )
    assert out == {}  # already at 20% cap -> persistent buy is not actionable


def test_buy_kept_when_below_cap():
    sig = {"AAPL": {"action": "buy", "strength": 0.8}}
    out = actionable_signals(
        sig, positions={"AAPL": 5}, position_values={"AAPL": 1000.0}, equity=EQUITY, max_position_pct=CAP_PCT
    )
    assert "AAPL" in out  # still room to add


def test_exit_requires_holding():
    sig = {"AAPL": {"action": "exit", "strength": 1.0}, "MSFT": {"action": "exit", "strength": 1.0}}
    out = actionable_signals(
        sig, positions={"AAPL": 10}, position_values={"AAPL": 1500.0}, equity=EQUITY, max_position_pct=CAP_PCT
    )
    assert "AAPL" in out and "MSFT" not in out  # only the held one is actionable


def test_all_persistent_at_cap_yields_empty():
    # The observed case: strategy keeps signaling buy on a position already at cap.
    sig = {"AAPL": {"action": "buy", "strength": 0.9}}
    out = actionable_signals(
        sig, positions={"AAPL": 10}, position_values={"AAPL": 2500.0}, equity=EQUITY, max_position_pct=CAP_PCT
    )
    assert out == {}  # -> idle-skip -> no LLM call


# ── short/cover actionability (signed positions) ──
def test_short_kept_when_room_and_cover_requires_short():
    from app.agents.context import actionable_signals
    sig = {"AAPL": {"action": "short", "strength": 0.7}}
    # room on the short side (no existing short)
    out = actionable_signals(sig, positions={}, position_values={}, equity=10000.0, max_position_pct=0.2)
    assert "AAPL" in out
    # cover only when actually short
    cov = {"AAPL": {"action": "cover", "strength": 1.0}}
    assert actionable_signals(cov, positions={"AAPL": -5}, position_values={"AAPL": -1000.0}, equity=10000.0) != {}
    assert actionable_signals(cov, positions={}, position_values={}, equity=10000.0) == {}


def test_short_dropped_at_short_cap():
    from app.agents.context import actionable_signals
    sig = {"AAPL": {"action": "short", "strength": 0.7}}
    # already short 20% of equity (cap) -> not actionable
    out = actionable_signals(sig, positions={"AAPL": -10}, position_values={"AAPL": -2000.0},
                             equity=10000.0, max_position_pct=0.2)
    assert out == {}
