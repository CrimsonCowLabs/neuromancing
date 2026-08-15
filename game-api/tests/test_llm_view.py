from app.agents.llm import _llm_view


def _ctx():
    # A wide universe with marks for many symbols, but only a couple held / signaled.
    return {
        "account_id": 1,
        "equity": 10000.0,
        "cash": 5000.0,
        "positions": {"AAPL": 10},
        "position_values": {"AAPL": 2000.0},
        "signals": {"MSFT": {"action": "buy", "strength": 0.7, "strategy_id": 3}},
        "universe": [f"SYM{i}" for i in range(200)],
        "marks": {"AAPL": "200", "MSFT": "300", "SYM1": "1", "SYM2": "2"},
        "equity_open": True,
    }


def test_llm_view_drops_universe_and_unrelated_marks():
    view = _llm_view(_ctx())
    # The full universe list is not serialized to the model.
    assert "universe" not in view
    # Marks are limited to held + signaled symbols only.
    assert set(view["marks"]) == {"AAPL", "MSFT"}


def test_llm_view_keeps_reasoning_fields():
    view = _llm_view(_ctx())
    for k in ("equity", "cash", "positions", "position_values", "signals", "equity_open"):
        assert k in view
    assert view["signals"]["MSFT"]["action"] == "buy"
