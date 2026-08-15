"""Maintenance workflow: mark all accounts to market and rebuild the leaderboard
on a schedule, so equity/ranking stay fresh even for slow-cadence agents."""

from __future__ import annotations

import logging

from temporalio import activity, workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from sqlalchemy import select

    from ..db import SessionLocal
    from ..leaderboard import rebuild
    from ..marks import get_marks
    from ..models.entities import Agent
    from ..trade_client import TradeClient

log = logging.getLogger("neuromancing.maintenance")


@activity.defn
async def mark_all_activity() -> dict:
    trade = TradeClient()
    marked = 0
    async with SessionLocal() as session:
        agents = (await session.execute(select(Agent))).scalars().all()
    for a in agents:
        account = await trade.get_account_by_ref(a.account_ref)
        if account is None:
            continue
        positions = await trade.list_positions(account["id"])
        # Marks fall back to last price_bar close (never $0) — see app/marks.py.
        marks = await get_marks([p["symbol"] for p in positions])
        try:
            await trade.mark_to_market(account["id"], marks)
            marked += 1
        except Exception as e:  # noqa: BLE001
            log.warning("mark failed for %s: %s", a.handle, e)
    ranking = await rebuild()
    return {"marked": marked, "ranked": len(ranking)}


@workflow.defn
class MaintenanceWorkflow:
    @workflow.run
    async def run(self) -> dict:
        from datetime import timedelta

        return await workflow.execute_activity(
            mark_all_activity,
            start_to_close_timeout=timedelta(seconds=120),
            retry_policy=RetryPolicy(maximum_attempts=2),
        )
