"""Position monitor — the deterministic exit enforcer.

A fast Temporal schedule (~10s) calls trade-api's settle_exits, which closes any
position whose stop-loss / trailing-stop / take-profit level has crossed a FRESH
quote. For each exit we write a feed_event and publish it to SSE so the UI shows
"stop-loss hit on BTC/USD". Runs independent of (and much faster than) the agent
decision cadence. Mirrors maintenance.py.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from temporalio import activity, workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from neuromancing_shared.redisio import make_redis
    from sqlalchemy import select

    from ..config import get_settings
    from ..db import SessionLocal
    from ..evolve import diary
    from ..models.entities import Agent, FeedEvent, FeedEventType
    from ..trade_client import TradeClient

log = logging.getLogger("neuromancing.monitor")


@activity.defn
async def monitor_positions_activity() -> dict:
    tick_id = activity.info().workflow_run_id
    trade = TradeClient()
    exits = await trade.settle_exits(tick_id)
    if not exits:
        return {"exits": 0}

    redis = make_redis(get_settings().redis_url)
    now = datetime.now(timezone.utc)
    async with SessionLocal() as session:
        # Map account_ref -> agent (for feed attribution).
        refs = {e["account_ref"] for e in exits}
        rows = (await session.execute(
            select(Agent).where(Agent.account_ref.in_(refs))
        )).scalars().all()
        by_ref = {a.account_ref: a for a in rows}

        published = []
        for e in exits:
            agent = by_ref.get(e["account_ref"])
            if agent is None:
                continue
            fe = FeedEvent(
                agent_id=agent.id, ts=now, type=FeedEventType.trade,
                payload={"symbol": e["symbol"], "side": "sell", "exit": True,
                         "reason": e["reason"], "price": e["price"], "realized": e["realized"]},
            )
            session.add(fe)
            # Finalize the trade-diary episode with the deterministic exit's real
            # price/reason/realized P&L (best-effort — never break the monitor).
            try:
                await diary.close_open(
                    session, agent.id, e["symbol"], exit_price=float(e["price"]),
                    exit_reason=e["reason"], realized=float(e["realized"]),
                )
            except Exception as ex:  # noqa: BLE001
                log.warning("diary close (monitor) failed for %s: %s", e["symbol"], ex)
            published.append((agent, e))
        await session.commit()

    for agent, e in published:
        try:
            await redis.publish("feed", json.dumps({
                "agent_id": agent.id, "handle": agent.handle,
                "display_name": agent.display_name, "type": "trade",
                "symbol": e["symbol"], "side": "sell", "exit": True,
                "reason": e["reason"], "ts": now.isoformat(),
            }))
        except Exception as ex:  # noqa: BLE001
            log.warning("exit feed publish failed: %s", ex)

    log.info("position-monitor executed %d exits", len(exits))
    return {"exits": len(exits)}


@workflow.defn
class PositionMonitorWorkflow:
    @workflow.run
    async def run(self) -> dict:
        from datetime import timedelta

        return await workflow.execute_activity(
            monitor_positions_activity,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(maximum_attempts=2),
        )
