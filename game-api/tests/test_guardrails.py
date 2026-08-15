from app.agents.guardrails import GuardrailContext, validate


def _ctx(**kw):
    base = dict(
        equity=10000.0, cash=10000.0, positions={}, position_values={},
        signals={}, universe=["AAPL", "MSFT", "BTC/USD"],
    )
    base.update(kw)
    return GuardrailContext(**base)


def test_buy_requires_signal():
    ctx = _ctx(signals={})  # no signal
    r = validate([{"type": "order", "symbol": "AAPL", "side": "buy", "notional": 500}], ctx)
    assert not r.valid
    assert "not backed by a current signal" in r.rejected[0]["reason"]


def test_buy_with_signal_ok_and_capped():
    ctx = _ctx(signals={"AAPL": "buy"})
    r = validate([{"type": "order", "symbol": "AAPL", "side": "buy", "notional": 999999}], ctx)
    assert len(r.valid) == 1
    # per-tick cap is 15% of 10000 = 1500
    assert r.valid[0]["notional"] <= 1500.0


def test_symbol_outside_universe_rejected():
    ctx = _ctx(signals={"TSLA": "buy"}, universe=["AAPL"])
    r = validate([{"type": "order", "symbol": "TSLA", "side": "buy", "notional": 500}], ctx)
    assert not r.valid
    assert "not in tradable universe" in r.rejected[0]["reason"]


def test_close_requires_position():
    ctx = _ctx(positions={})
    r = validate([{"type": "close", "symbol": "AAPL"}], ctx)
    assert not r.valid


def test_close_with_position_ok():
    ctx = _ctx(positions={"AAPL": 10.0}, position_values={"AAPL": 2000.0})
    r = validate([{"type": "close", "symbol": "AAPL", "rationale": "take profit"}], ctx)
    assert len(r.valid) == 1
    assert r.valid[0]["type"] == "close"


def test_equity_blocked_when_market_closed():
    ctx = _ctx(signals={"AAPL": "buy"}, universe=["AAPL", "BTC/USD"], equity_open=False)
    r = validate([{"type": "order", "symbol": "AAPL", "side": "buy", "notional": 500}], ctx)
    assert not r.valid
    assert "equity market closed" in r.rejected[0]["reason"]


def test_crypto_allowed_when_equity_market_closed():
    ctx = _ctx(signals={"BTC/USD": "buy"}, universe=["AAPL", "BTC/USD"], equity_open=False)
    r = validate([{"type": "order", "symbol": "BTC/USD", "side": "buy", "notional": 500}], ctx)
    assert len(r.valid) == 1


def test_equity_close_blocked_when_market_closed():
    ctx = _ctx(positions={"AAPL": 10.0}, position_values={"AAPL": 2000.0},
               universe=["AAPL", "BTC/USD"], equity_open=False)
    r = validate([{"type": "close", "symbol": "AAPL"}], ctx)
    assert not r.valid
    assert "equity market closed" in r.rejected[0]["reason"]


def test_concentration_cap_blocks_overweight_add():
    # Already at 20% max position value in AAPL -> no room to add.
    ctx = _ctx(signals={"AAPL": "buy"}, positions={"AAPL": 1.0},
               position_values={"AAPL": 2000.0})  # 20% of 10000
    r = validate([{"type": "order", "symbol": "AAPL", "side": "buy", "notional": 500}], ctx)
    assert not r.valid
    assert "concentration" in r.rejected[0]["reason"]
