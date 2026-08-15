from app.agents.context import (
    _trim_features,
    enrich_actionable,
    merge_signals,
)


def test_merge_picks_strength_max_primary():
    evals = [
        (1, [{"symbol": "AAPL", "action": "buy", "strength": 0.6, "features": {"rsi": 28.0}}]),
        (2, [{"symbol": "AAPL", "action": "buy", "strength": 0.9, "features": {"rsi": 20.0}}]),
    ]
    primary, sources = merge_signals(evals)
    assert primary["AAPL"] == {"action": "buy", "strength": 0.9, "strategy_id": 2}
    # Both strategies preserved as sources — nothing collapsed away.
    assert len(sources["AAPL"]) == 2
    assert {s["strategy_id"] for s in sources["AAPL"]} == {1, 2}


def test_merge_keeps_opposing_actions_in_sources():
    evals = [
        (1, [{"symbol": "MSFT", "action": "sell", "strength": 0.6, "features": {}}]),
        (2, [{"symbol": "MSFT", "action": "buy", "strength": 0.7, "features": {}}]),
    ]
    primary, sources = merge_signals(evals)
    assert primary["MSFT"]["action"] == "buy"  # strength-max still decides the primary
    actions = {s["action"] for s in sources["MSFT"]}
    assert actions == {"buy", "sell"}  # the opposing signal is NOT discarded


def test_merge_skips_hold():
    evals = [(1, [{"symbol": "NVDA", "action": "hold", "strength": 0.5, "features": {}}])]
    primary, sources = merge_signals(evals)
    assert primary == {} and sources == {}


def test_enrich_sets_conflict_flag():
    signals = {"MSFT": {"action": "buy", "strength": 0.7, "strategy_id": 2}}
    sources = {"MSFT": [{"strategy_id": 1, "action": "sell", "strength": 0.6, "features": {}},
                        {"strategy_id": 2, "action": "buy", "strength": 0.7, "features": {}}]}
    enrich_actionable(signals, sources)
    assert signals["MSFT"]["conflict"] is True
    assert len(signals["MSFT"]["sources"]) == 2


def test_enrich_no_conflict_when_aligned():
    signals = {"AAPL": {"action": "buy", "strength": 0.9, "strategy_id": 2}}
    sources = {"AAPL": [{"strategy_id": 1, "action": "buy", "strength": 0.6, "features": {}},
                        {"strategy_id": 2, "action": "buy", "strength": 0.9, "features": {}}]}
    enrich_actionable(signals, sources)
    assert signals["AAPL"]["conflict"] is False


def test_trim_features_bounds_payload():
    feats = {"rsi": 27.123456, "cross": True, "n": 3, "label": "oversold",
             "series": [1, 2, 3], "nested": {"a": 1}}
    out = _trim_features(feats)
    assert out == {"rsi": 27.1235, "cross": True, "n": 3, "label": "oversold"}
    # lists/dicts dropped to keep the LLM payload compact
    assert "series" not in out and "nested" not in out
