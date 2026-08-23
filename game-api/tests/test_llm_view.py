from app.agents.llm import _MANAGEMENT_CONTRACT, _llm_view, _system_prompt


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


def test_system_prompt_uses_authored_persona_voice():
    # A construct with an authored system_prompt: its voice replaces the generic header,
    # and the fixed MANAGEMENT contract is always appended (non-overridable).
    persona = {
        "display_name": "Molly",
        "thesis": "Ride strength.",
        "voice_style": "terse",
        "risk_temperament": "aggressive",
        "system_prompt": "You are Molly — razorgirl on the grid. Short, lethal bursts.",
    }
    out = _system_prompt(persona)
    assert "razorgirl on the grid" in out          # authored voice present
    assert _MANAGEMENT_CONTRACT in out              # safety contract always appended
    assert "You are Molly, an autonomous trading agent." not in out  # generic header replaced


def test_system_prompt_falls_back_to_generated_header():
    # No authored system_prompt (empty string) -> the generated header is used, plus
    # the fixed contract. Preserves the pre-refactor behavior for un-authored personas.
    persona = {
        "display_name": "Nobody",
        "thesis": "T",
        "voice_style": "V",
        "risk_temperament": "balanced",
        "system_prompt": "",
    }
    out = _system_prompt(persona)
    assert "You are Nobody, an autonomous trading agent." in out
    assert "Thesis: T" in out
    assert _MANAGEMENT_CONTRACT in out
