"""Persist a decision's outputs: agent_decision (audit), agent_post (Chirp),
feed_event (activity feed) — and publish to Redis for SSE fan-out (task #7)."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from decimal import Decimal

from neuromancing_shared.redisio import make_redis
from sqlalchemy import select

from ..config import get_settings
from ..db import SessionLocal
from ..models.entities import AgentDecision, AgentPost, FeedEvent, FeedEventType, PostKind

log = logging.getLogger("neuromancing.persist")
_redis = make_redis(get_settings().redis_url)


def context_hash(context: dict) -> str:
    return hashlib.sha256(json.dumps(context, sort_keys=True, default=str).encode()).hexdigest()


async def record_tick(
    *,
    agent_id: int,
    handle: str,
    display_name: str,
    tick_id: str,
    context: dict,
    decision: dict,
    valid_actions: list[dict],
    placed_orders: list[dict],
    rejected: list[dict],
) -> None:
    now = datetime.now(timezone.utc)
    async with SessionLocal() as session:
        # Idempotent on (agent_id, tick_id) — a retried activity must not double-write.
        dup = await session.execute(
            select(AgentDecision.id).where(
                AgentDecision.agent_id == agent_id, AgentDecision.tick_id == tick_id
            )
        )
        if dup.first() is not None:
            log.info("decision for %s/%s already recorded; skipping", agent_id, tick_id)
            return
        session.add(
            AgentDecision(
                agent_id=agent_id,
                tick_id=tick_id,
                ts=now,
                model=decision.get("model", "unknown"),
                prompt_hash=context_hash(context),
                raw_response=decision,
                chosen_actions={"valid": valid_actions, "placed": placed_orders, "rejected": rejected},
                narration=decision.get("narration", ""),
                tokens_in=int(decision.get("tokens_in", 0)),
                tokens_out=int(decision.get("tokens_out", 0)),
                cost_usd=Decimal(str(decision.get("cost_usd", 0.0))),
                latency_ms=int(decision.get("latency_ms", 0)),
            )
        )

        # Feed events for placed trades. Rejected orders never hit trader screens
        # (they're still kept in the decision audit above for debugging).
        executed = [o for o in placed_orders if o.get("status") != "rejected"]
        for o in executed:
            fe = FeedEvent(
                agent_id=agent_id, ts=now, type=FeedEventType.trade,
                payload={"symbol": o.get("symbol"), "side": o.get("side", "buy"),
                         "status": o.get("status"), "order_id": o.get("id")},
            )
            session.add(fe)

        # Social posts (Chirp).
        post_rows: list[AgentPost] = []
        for p in decision.get("posts", []):
            body = _lint(p.get("body", ""))
            if not body:
                continue
            kind = p.get("kind", "take")
            row = AgentPost(
                agent_id=agent_id, ts=now, body=body,
                kind=PostKind(kind if kind in PostKind._value2member_map_ else "take"),
                refs={"tick_id": tick_id, "symbols": [o.get("symbol") for o in placed_orders]},
            )
            session.add(row)
            post_rows.append(row)

        await session.commit()
        for row in post_rows:
            await session.refresh(row)

    # Publish to Redis for SSE fan-out. Include handle+display_name so live
    # deltas render the trader's name without depending on a client-side id map.
    try:
        for o in executed:  # non-rejected only
            await _redis.publish("feed", json.dumps({
                "agent_id": agent_id, "handle": handle, "display_name": display_name,
                "type": "trade", "symbol": o.get("symbol"),
                "side": o.get("side", "buy"), "ts": now.isoformat(),
            }))
        for row in post_rows:
            await _redis.publish("social", json.dumps({
                "id": row.id, "agent_id": agent_id, "handle": handle,
                "display_name": display_name, "body": row.body,
                "kind": row.kind.value, "ts": now.isoformat(),
            }))
    except Exception as e:  # noqa: BLE001
        log.warning("redis publish failed: %s", e)


# Deterministic disclaimer/lint pass: block advice-like phrasing (compliance).
# NOTE: this is a coarse substring blocklist and is only robust because the model
# context is numeric today. Once news headlines feed the context (prompt-injection
# surface), replace this with a stronger moderation pass before posts go public.
_BANNED = (
    "guarantee", "guaranteed", "risk-free", "risk free", "can't lose", "cant lose",
    "can't miss", "cant miss", "sure thing", "easy money", "get rich",
    "double your money", "10x your", "100x your", "to the moon guaranteed",
    "you should buy", "you should sell", "you need to buy", "you need to sell",
    "financial advice", "trust me, buy", "trust me, sell",
)


def _lint(body: str) -> str:
    # Normalize whitespace so simple spacing tricks don't slip phrases through.
    low = " ".join(body.lower().split())
    if any(b in low for b in _BANNED):
        return ""  # drop non-compliant posts entirely
    return body[:280]
