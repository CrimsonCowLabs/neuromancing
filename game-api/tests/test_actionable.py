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
