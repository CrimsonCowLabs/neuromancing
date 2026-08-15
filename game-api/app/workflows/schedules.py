"""Register/refresh Temporal Schedules (idempotent).

One Schedule per active agent drives the decision workflow at the agent's cadence;
a maintenance Schedule marks all accounts + rebuilds the leaderboard periodically.
Temporal appends the scheduled time to each action's workflow id, giving the
deterministic once-per-tick id the decision workflow reads as tick_id.

Run: `uv run python -m app.workflows.schedules`
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from sqlalchemy import select
from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleIntervalSpec,
    ScheduleOverlapPolicy,
    SchedulePolicy,
    ScheduleSpec,
)

from app.agents.decision import AgentDecisionWorkflow
from app.agents.maintenance import MaintenanceWorkflow
from app.agents.monitor import PositionMonitorWorkflow
from app.config import get_settings
from app.db import SessionLocal
from app.evolve.workflow import EvolutionWorkflow
from app.models.entities import Agent, AgentStatus

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("neuromancing.schedules")

MAINTENANCE_INTERVAL_S = 15
POSITION_MONITOR_INTERVAL_S = 10


async def _upsert(client: Client, schedule_id: str, schedule: Schedule) -> None:
    try:
        await client.create_schedule(schedule_id, schedule)
        log.info("created schedule %s", schedule_id)
    except ScheduleAlreadyRunningError:
        handle = client.get_schedule_handle(schedule_id)
        await handle.delete()
        await client.create_schedule(schedule_id, schedule)
        log.info("recreated schedule %s", schedule_id)


async def main() -> None:
    settings = get_settings()
    client = await Client.connect(settings.temporal_host, namespace=settings.temporal_namespace)
    tq = settings.temporal_task_queue

    async with SessionLocal() as session:
        agents = (
            await session.execute(select(Agent).where(Agent.status == AgentStatus.active))
        ).scalars().all()
        agent_rows = [(a.id, a.handle, a.decision_cadence_s) for a in agents]

    for agent_id, handle, cadence in agent_rows:
        schedule = Schedule(
            action=ScheduleActionStartWorkflow(
                AgentDecisionWorkflow.run,
                args=[agent_id],
                id=f"agent-{agent_id}:tick",
                task_queue=tq,
            ),
            spec=ScheduleSpec(
                intervals=[ScheduleIntervalSpec(every=timedelta(seconds=max(30, cadence)))]
            ),
            policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP),
        )
        await _upsert(client, f"agent-{agent_id}-decision", schedule)

    maintenance = Schedule(
        action=ScheduleActionStartWorkflow(
            MaintenanceWorkflow.run, id="maintenance", task_queue=tq
        ),
        spec=ScheduleSpec(
            intervals=[ScheduleIntervalSpec(every=timedelta(seconds=MAINTENANCE_INTERVAL_S))]
        ),
        policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP),
    )
    await _upsert(client, "maintenance-refresh", maintenance)

    # Fast deterministic exit enforcement — far quicker than the agent cadence.
    monitor = Schedule(
        action=ScheduleActionStartWorkflow(
            PositionMonitorWorkflow.run, id="position-monitor", task_queue=tq
        ),
        spec=ScheduleSpec(
            intervals=[ScheduleIntervalSpec(every=timedelta(seconds=POSITION_MONITOR_INTERVAL_S))]
        ),
        policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP),
    )
    await _upsert(client, "position-monitor", monitor)

    # Strategy evolution (TODO #3) — fires periodically; the per-agent should_evolve_now
    # gate (post-session + ≥24h) inside the activity decides whether to actually run.
    evolution = Schedule(
        action=ScheduleActionStartWorkflow(
            EvolutionWorkflow.run, id="strategy-evolution", task_queue=tq
        ),
        spec=ScheduleSpec(
            intervals=[ScheduleIntervalSpec(
                every=timedelta(seconds=get_settings().evolution_schedule_seconds))]
        ),
        policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP),
    )
    await _upsert(client, "strategy-evolution", evolution)
    log.info("registered %d agent schedules + maintenance + monitor + evolution", len(agent_rows))


if __name__ == "__main__":
    asyncio.run(main())
