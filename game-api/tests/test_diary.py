"""Trade-diary unit coverage: close math + reflect aggregates (pure, no DB)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.evolve import diary
from app.models.entities import TradeDiary

UTC = timezone.utc


def _episode(symbol="AAPL", entry=100.0, qty=10.0, opened=None):
    return TradeDiary(
        agent_id=1, symbol=symbol, status="open",
        entry_price=Decimal(str(entry)), qty=Decimal(str(qty)),
        notional=Decimal(str(entry * qty)),
        opened_at=opened or datetime(2026, 3, 2, 14, 0, tzinfo=UTC),
    )


async def test_close_open_computes_pnl(monkeypatch):
    row = _episode(entry=100.0, qty=10.0)

    async def fake_open_row(session, agent_id, symbol):
        return row

    monkeypatch.setattr(diary, "_open_row", fake_open_row)
    closed_at = datetime(2026, 3, 2, 15, 0, tzinfo=UTC)
    out = await diary.close_open(None, 1, "AAPL", exit_price=110.0,
                                 exit_reason="take", closed_at=closed_at)
    assert out is row
    assert row.status == "closed"
    assert float(row.realized_pnl) == pytest.approx(100.0)  # (110-100)*10
    assert float(row.return_pct) == pytest.approx(0.1)
    assert row.outcome == "win"
    assert row.exit_reason == "take"
    assert row.holding_secs == 3600


async def test_close_open_loss(monkeypatch):
    row = _episode(entry=100.0, qty=5.0)
    monkeypatch.setattr(diary, "_open_row", lambda *a, **k: _async(row))
    out = await diary.close_open(None, 1, "AAPL", exit_price=90.0, exit_reason="stop",
                                 closed_at=datetime(2026, 3, 2, 14, 30, tzinfo=UTC))
    assert out.outcome == "loss" and float(out.realized_pnl) == pytest.approx(-50.0)


async def test_close_open_no_open_is_noop(monkeypatch):
    monkeypatch.setattr(diary, "_open_row", lambda *a, **k: _async(None))
    assert await diary.close_open(None, 1, "ZZZ", exit_price=1.0, exit_reason="signal") is None


def _async(v):
    async def _a():
        return v
    return _a()


def test_aggregates():
    def closed(sym, ret, reason, hold=600):
        r = _episode(symbol=sym)
        r.status = "closed"
        r.return_pct = Decimal(str(ret))
        r.realized_pnl = Decimal(str(ret * 1000))
        r.outcome = "win" if ret > 0 else ("loss" if ret < 0 else "flat")
        r.exit_reason = reason
        r.holding_secs = hold
        return r

    rows = [closed("AAPL", 0.05, "take"), closed("AAPL", -0.02, "stop"),
            closed("NVDA", 0.10, "take")]
    agg = diary.aggregates(rows)
    assert agg["episodes"] == 3
    assert agg["win_rate"] == pytest.approx(2 / 3, abs=0.01)
    assert agg["by_symbol"]["AAPL"]["n"] == 2
    assert agg["by_exit_reason"] == {"take": 2, "stop": 1}
    assert agg["best"]["symbol"] == "NVDA"
    assert agg["worst"]["symbol"] == "AAPL"


def test_aggregates_empty():
    assert diary.aggregates([])["episodes"] == 0
