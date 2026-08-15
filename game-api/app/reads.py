"""Shared read helpers for the public API (agent resolution + equity curve)."""

from __future__ import annotations

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .models.entities import Agent, Persona

_EQUITY_CURVE_SQL = text(
    """
    SELECT es.ts, es.total_equity, es.realized_pnl, es.unrealized_pnl
    FROM trade.equity_snapshot es
    JOIN trade.account a ON a.id = es.account_id
    WHERE a.external_ref = :ref
    ORDER BY es.ts DESC
    LIMIT :limit
    """
)


async def get_agent_by_handle(session: AsyncSession, handle: str) -> Agent | None:
    res = await session.execute(select(Agent).where(Agent.handle == handle))
    return res.scalar_one_or_none()


async def get_persona(session: AsyncSession, persona_id: int) -> Persona | None:
    return await session.get(Persona, persona_id)


async def equity_curve(session: AsyncSession, account_ref: str, limit: int = 500) -> list[dict]:
    res = await session.execute(_EQUITY_CURVE_SQL, {"ref": account_ref, "limit": limit})
    rows = [
        {"ts": r["ts"].isoformat(), "equity": float(r["total_equity"]),
         "realized_pnl": float(r["realized_pnl"]), "unrealized_pnl": float(r["unrealized_pnl"])}
        for r in res.mappings().all()
    ]
    rows.reverse()
    return rows
