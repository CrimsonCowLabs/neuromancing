from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_session
from ..ingest.universe import is_crypto
from ..leaderboard import current as leaderboard_current
from ..models.entities import Agent, AgentPost, StrategyExperiment, TradeDiary
from ..reads import equity_curve, get_agent_by_handle, get_persona
from ..trade_client import TradeClient

router = APIRouter(prefix="/agents", tags=["agents"])


def _agent_public(agent: Agent) -> dict:
    return {
        "id": agent.id,
        "handle": agent.handle,
        "display_name": agent.display_name,
        "status": agent.status.value,
        "decision_cadence_s": agent.decision_cadence_s,
        "tradable_universe": agent.tradable_universe,
        "strategy_ids": agent.strategy_ids,
    }


@router.get("")
async def list_agents(session: AsyncSession = Depends(get_session)) -> list[dict]:
    agents = (await session.execute(select(Agent))).scalars().all()
    board = {r["handle"]: r for r in (await leaderboard_current()).get("rows", [])}
    out = []
    for a in agents:
        row = _agent_public(a)
        lb = board.get(a.handle, {})
        row["rank"] = lb.get("rank")
        row["equity"] = lb.get("equity")
        row["return_pct"] = lb.get("return_pct")
        out.append(row)
    out.sort(key=lambda r: (r["rank"] is None, r.get("rank") or 1e9))
    return out


@router.get("/by-id/{agent_id}")
async def get_agent_by_id(agent_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    """Resolve a numeric id → the full profile (which carries `handle`), so the
    id-keyed construct page can then reuse the handle-keyed sub-resources."""
    agent = await session.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(404, "construct not found")
    return await get_agent(agent.handle, session)


@router.get("/{handle}")
async def get_agent(handle: str, session: AsyncSession = Depends(get_session)) -> dict:
    agent = await get_agent_by_handle(session, handle)
    if agent is None:
        raise HTTPException(404, "agent not found")
    persona = await get_persona(session, agent.persona_id)
    trade = TradeClient()
    account = await trade.get_account_by_ref(agent.account_ref)
    positions = await trade.list_positions(account["id"]) if account else []
    board = {r["handle"]: r for r in (await leaderboard_current()).get("rows", [])}
    lb = board.get(handle, {})

    # Resolve the agent's assigned strategies (name/kind/params) for the config view.
    strategy_ids = set(agent.strategy_ids or [])
    all_strategies = await trade.list_strategies()
    strategies = [
        {"name": s["name"], "kind": s["kind"], "spec": s["spec"],
         "owner_type": s.get("owner_type", "house")}
        for s in all_strategies if s["id"] in strategy_ids
    ]

    universe = agent.tradable_universe or []
    trades_crypto = any(is_crypto(sym) for sym in universe)
    pmodel = (persona.model_config_json or {}).get("model") if persona else None

    return {
        **_agent_public(agent),
        "persona": {
            "thesis": persona.thesis if persona else "",
            "voice_style": persona.voice_style if persona else "",
            "risk_temperament": persona.risk_temperament if persona else "",
        },
        "config": {
            "strategies": strategies,
            "universe": universe,
            "trades_crypto": trades_crypto,
            "market_hours": "24/7 (crypto)" if trades_crypto else "equity hours ±30m",
            "decision_cadence_s": agent.decision_cadence_s,
            "risk_profile": agent.risk_profile or {},
            "model": pmodel or get_settings().ollama_model,
        },
        "account": {
            "cash": account["cash_balance"] if account else None,
            "starting_equity": account["starting_equity"] if account else None,
        },
        "positions": positions,
        "rank": lb.get("rank"),
        "equity": lb.get("equity"),
        "return_pct": lb.get("return_pct"),
    }


@router.get("/{handle}/equity")
async def get_equity(handle: str, limit: int = 500, session: AsyncSession = Depends(get_session)) -> list[dict]:
    agent = await get_agent_by_handle(session, handle)
    if agent is None:
        raise HTTPException(404, "agent not found")
    return await equity_curve(session, agent.account_ref, limit)


@router.get("/{handle}/trades")
async def get_trades(
    handle: str, limit: int = 10, offset: int = 0,
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    """Recent trades for the public trader screen. Rejected orders are filtered
    out; paginated (default 10 per page, configurable via `limit`/`offset`)."""
    agent = await get_agent_by_handle(session, handle)
    if agent is None:
        raise HTTPException(404, "agent not found")
    trade = TradeClient()
    account = await trade.get_account_by_ref(agent.account_ref)
    if account is None:
        return []
    return await trade.list_orders(
        account["id"], limit=limit, offset=offset, exclude_rejected=True
    )


@router.get("/{handle}/posts")
async def get_posts(handle: str, limit: int = 50, session: AsyncSession = Depends(get_session)) -> list[dict]:
    agent = await get_agent_by_handle(session, handle)
    if agent is None:
        raise HTTPException(404, "agent not found")
    rows = (
        await session.execute(
            select(AgentPost).where(AgentPost.agent_id == agent.id)
            .order_by(AgentPost.ts.desc()).limit(limit)
        )
    ).scalars().all()
    return [
        {"id": p.id, "ts": p.ts.isoformat(), "body": p.body, "kind": p.kind.value, "refs": p.refs}
        for p in rows
    ]


@router.get("/{handle}/experiments")
async def get_experiments(
    handle: str, limit: int = 10, session: AsyncSession = Depends(get_session)
) -> list[dict]:
    """Recent strategy-evolution experiments for the trader page (TODO #3)."""
    agent = await get_agent_by_handle(session, handle)
    if agent is None:
        raise HTTPException(404, "agent not found")
    rows = (await session.execute(
        select(StrategyExperiment).where(StrategyExperiment.agent_id == agent.id)
        .order_by(StrategyExperiment.ts.desc()).limit(limit)
    )).scalars().all()
    return [
        {"ts": e.ts.isoformat(), "decision": e.decision, "reason": e.reason,
         "hypothesis": e.hypothesis, "backtests": e.backtests,
         "adopted_strategy_id": e.adopted_strategy_id}
        for e in rows
    ]


@router.get("/{handle}/diary")
async def get_diary(
    handle: str, limit: int = 20, session: AsyncSession = Depends(get_session)
) -> list[dict]:
    """Recent closed trade-diary episodes for the trader page (TODO #3)."""
    agent = await get_agent_by_handle(session, handle)
    if agent is None:
        raise HTTPException(404, "agent not found")
    rows = (await session.execute(
        select(TradeDiary).where(TradeDiary.agent_id == agent.id, TradeDiary.status == "closed")
        .order_by(TradeDiary.closed_at.desc()).limit(limit)
    )).scalars().all()
    return [
        {"symbol": d.symbol, "opened_at": d.opened_at.isoformat() if d.opened_at else None,
         "closed_at": d.closed_at.isoformat() if d.closed_at else None,
         "entry_price": float(d.entry_price or 0), "exit_price": float(d.exit_price or 0),
         "exit_reason": d.exit_reason, "realized_pnl": float(d.realized_pnl or 0),
         "return_pct": float(d.return_pct or 0), "outcome": d.outcome,
         "rationale": d.rationale}
        for d in rows
    ]
