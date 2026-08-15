"""Temporal worker entrypoint.

Hosts the durable workflows + activities: per-agent decision, maintenance
(mark-to-market + leaderboard). Run: `uv run python -m app.workflows.worker`
"""

from __future__ import annotations

import asyncio
import logging

from temporalio.client import Client
from temporalio.worker import Worker

from app.agents.decision import AgentDecisionWorkflow, apply_activity, decide_activity
from app.agents.maintenance import MaintenanceWorkflow, mark_all_activity
from app.agents.monitor import PositionMonitorWorkflow, monitor_positions_activity
from app.config import get_settings
from app.evolve.workflow import EvolutionWorkflow, run_evolution_cycle

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("neuromancing.worker")


async def main() -> None:
    settings = get_settings()
    client = await Client.connect(
        settings.temporal_host, namespace=settings.temporal_namespace
    )
    log.info("connected to Temporal at %s", settings.temporal_host)

    worker = Worker(
        client,
        task_queue=settings.temporal_task_queue,
        workflows=[AgentDecisionWorkflow, MaintenanceWorkflow, PositionMonitorWorkflow,
                   EvolutionWorkflow],
        activities=[decide_activity, apply_activity, mark_all_activity,
                    monitor_positions_activity, run_evolution_cycle],
    )
    log.info("worker running on task queue %s", settings.temporal_task_queue)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
