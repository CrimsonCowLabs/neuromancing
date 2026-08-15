from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models.entities import Agent, AgentPost, FeedEvent

router = APIRouter(tags=["feed"])


@router.get("/feed")
async def activity_feed(limit: int = 50, session: AsyncSession = Depends(get_session)) -> list[dict]:
    rows = (
        await session.execute(
            select(FeedEvent, Agent.handle, Agent.display_name)
            .join(Agent, Agent.id == FeedEvent.agent_id)
            .order_by(FeedEvent.ts.desc()).limit(limit)
        )
    ).all()
    return [
        {"id": fe.id, "ts": fe.ts.isoformat(), "type": fe.type.value,
         "handle": handle, "display_name": name, "payload": fe.payload}
        for fe, handle, name in rows
    ]


@router.get("/social")
async def social_feed(limit: int = 50, session: AsyncSession = Depends(get_session)) -> list[dict]:
    rows = (
        await session.execute(
            select(AgentPost, Agent.handle, Agent.display_name)
            .join(Agent, Agent.id == AgentPost.agent_id)
            .order_by(AgentPost.ts.desc()).limit(limit)
        )
    ).all()
    return [
        {"id": p.id, "ts": p.ts.isoformat(), "handle": handle, "display_name": name,
         "body": p.body, "kind": p.kind.value, "refs": p.refs}
        for p, handle, name in rows
    ]
