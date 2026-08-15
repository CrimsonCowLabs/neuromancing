"""Strategy-experiment memory — the deep agent's record of what it has tried, so it
learns across runs (and the ≥24h trigger reads the last run time)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.entities import StrategyExperiment


async def record_experiment(
    session: AsyncSession, agent_id: int, run_id: str, *, hypothesis: str = "",
    candidate_specs: dict | None = None, backtests: dict | None = None,
    incumbent_metrics: dict | None = None, decision: str = "rejected", reason: str = "",
    adopted_strategy_id: int | None = None,
) -> StrategyExperiment:
    row = StrategyExperiment(
        agent_id=agent_id, run_id=run_id, hypothesis=hypothesis or "",
        candidate_specs=candidate_specs or {}, backtests=backtests or {},
        incumbent_metrics=incumbent_metrics or {}, decision=decision, reason=reason or "",
        adopted_strategy_id=adopted_strategy_id,
    )
    session.add(row)
    await session.flush()
    return row


async def recent_experiments(session: AsyncSession, agent_id: int, limit: int = 5) -> list[StrategyExperiment]:
    return list((await session.execute(
        select(StrategyExperiment).where(StrategyExperiment.agent_id == agent_id)
        .order_by(desc(StrategyExperiment.ts)).limit(limit)
    )).scalars().all())


async def last_adopted(session: AsyncSession, agent_id: int) -> StrategyExperiment | None:
    """The most recent adoption — its predicted (backtest) metrics let the next run
    compare prediction vs realized live performance (backtest-bias calibration)."""
    return (await session.execute(
        select(StrategyExperiment).where(
            StrategyExperiment.agent_id == agent_id, StrategyExperiment.decision == "adopted"
        ).order_by(desc(StrategyExperiment.ts)).limit(1)
    )).scalar_one_or_none()


async def experiment_exists(session: AsyncSession, agent_id: int, run_id: str) -> bool:
    """Idempotency guard: has this (agent, cycle run_id) already been processed?
    A Temporal activity retry must not re-adopt / re-evolve an already-done agent."""
    return (await session.execute(
        select(StrategyExperiment.id).where(
            StrategyExperiment.agent_id == agent_id, StrategyExperiment.run_id == run_id
        ).limit(1)
    )).scalar_one_or_none() is not None


async def last_experiment_ts(session: AsyncSession, agent_id: int) -> datetime | None:
    return (await session.execute(
        select(StrategyExperiment.ts).where(StrategyExperiment.agent_id == agent_id)
        .order_by(desc(StrategyExperiment.ts)).limit(1)
    )).scalar_one_or_none()
