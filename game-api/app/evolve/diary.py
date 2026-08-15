"""Trade diary — per-position-episode records (open-from-flat → close-to-flat).

The primary substrate the deep agent's reflect step analyzes. Written best-effort
from the decision apply path (open) and the exit monitor + LLM-close path (close);
read + aggregated by evolution. Kept always, independent of evolution being enabled.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.entities import TradeDiary


async def _open_row(session: AsyncSession, agent_id: int, symbol: str) -> TradeDiary | None:
    return (await session.execute(
        select(TradeDiary).where(
            TradeDiary.agent_id == agent_id,
            TradeDiary.symbol == symbol,
            TradeDiary.status == "open",
        )
    )).scalar_one_or_none()


async def open_or_update(
    session: AsyncSession, agent_id: int, symbol: str, *, entry_price: float, qty: float,
    tick_id: str | None = None, strategy_id: int | None = None, notional: float | None = None,
    signal: dict | None = None, rationale: str = "", entry_context: dict | None = None,
) -> TradeDiary:
    """Open a new episode from flat, or update (average) an existing open one."""
    row = await _open_row(session, agent_id, symbol)
    notional = notional if notional is not None else entry_price * qty
    if row is None:
        row = TradeDiary(
            agent_id=agent_id, symbol=symbol, status="open", open_tick_id=tick_id,
            strategy_id=strategy_id, entry_price=Decimal(str(entry_price)),
            qty=Decimal(str(qty)), notional=Decimal(str(notional)),
            signal=signal or {}, rationale=rationale or "", entry_context=entry_context or {},
        )
        session.add(row)
    else:  # averaging into the existing episode
        row.entry_price = Decimal(str(entry_price))
        row.qty = Decimal(str(qty))
        row.notional = Decimal(str(notional))
    return row


async def close_open(
    session: AsyncSession, agent_id: int, symbol: str, *, exit_price: float,
    exit_reason: str, realized: float | None = None, closed_at: datetime | None = None,
) -> TradeDiary | None:
    """Finalize the open episode for (agent, symbol). No-op if none is open."""
    row = await _open_row(session, agent_id, symbol)
    if row is None:
        return None
    closed_at = closed_at or datetime.now(timezone.utc)
    entry, qty = float(row.entry_price), float(row.qty)
    realized = float(realized) if realized is not None else (exit_price - entry) * qty
    opened = row.opened_at
    if opened.tzinfo is None:
        opened = opened.replace(tzinfo=timezone.utc)
    row.status = "closed"
    row.closed_at = closed_at
    row.exit_price = Decimal(str(exit_price))
    row.exit_reason = exit_reason
    row.realized_pnl = Decimal(str(round(realized, 8)))
    row.return_pct = round((exit_price / entry - 1.0), 6) if entry else 0.0
    row.holding_secs = int((closed_at - opened).total_seconds())
    row.outcome = "win" if realized > 0 else ("loss" if realized < 0 else "flat")
    return row


async def read_closed(session: AsyncSession, agent_id: int, since: datetime) -> list[TradeDiary]:
    return list((await session.execute(
        select(TradeDiary).where(
            TradeDiary.agent_id == agent_id,
            TradeDiary.status == "closed",
            TradeDiary.closed_at >= since,
        ).order_by(TradeDiary.closed_at)
    )).scalars().all())


async def count_closed(session: AsyncSession, agent_id: int) -> int:
    from sqlalchemy import func

    return int((await session.execute(
        select(func.count()).select_from(TradeDiary).where(
            TradeDiary.agent_id == agent_id, TradeDiary.status == "closed"
        )
    )).scalar_one())


def aggregates(rows: list[TradeDiary]) -> dict:
    """Deterministic performance summary the reflect step feeds to the LLM (grounded
    numbers, not prose). Pure — safe to unit-test."""
    closed = [r for r in rows if r.outcome]
    n = len(closed)
    if not n:
        return {"episodes": 0}
    rets = [float(r.return_pct or 0) for r in closed]
    wins = sum(1 for r in closed if r.outcome == "win")
    by_symbol: dict[str, dict] = {}
    by_reason: dict[str, int] = {}
    for r in closed:
        s = by_symbol.setdefault(r.symbol, {"n": 0, "ret": 0.0, "wins": 0})
        s["n"] += 1
        s["ret"] += float(r.return_pct or 0)
        s["wins"] += 1 if r.outcome == "win" else 0
        by_reason[r.exit_reason or "?"] = by_reason.get(r.exit_reason or "?", 0) + 1
    for s in by_symbol.values():
        s["avg_ret"] = round(s["ret"] / s["n"], 5)
    holdings = [r.holding_secs for r in closed if r.holding_secs is not None]
    best = max(closed, key=lambda r: float(r.return_pct or 0))
    worst = min(closed, key=lambda r: float(r.return_pct or 0))
    return {
        "episodes": n,
        "win_rate": round(wins / n, 3),
        "avg_return": round(sum(rets) / n, 5),
        "total_realized": round(sum(float(r.realized_pnl or 0) for r in closed), 2),
        "avg_holding_secs": int(sum(holdings) / len(holdings)) if holdings else None,
        "by_symbol": {k: {"n": v["n"], "avg_ret": v["avg_ret"],
                          "win_rate": round(v["wins"] / v["n"], 3)}
                      for k, v in by_symbol.items()},
        "by_exit_reason": by_reason,
        "best": {"symbol": best.symbol, "return_pct": float(best.return_pct or 0)},
        "worst": {"symbol": worst.symbol, "return_pct": float(worst.return_pct or 0)},
    }
