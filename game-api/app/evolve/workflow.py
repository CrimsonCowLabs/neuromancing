"""Strategy-evolution Temporal workflow (TODO #3).

A singleton `strategy-evolution` schedule fires `EvolutionWorkflow`, which runs one
heartbeating activity that iterates active agents: for each, a cheap `should_evolve_now`
gate (post-session + ≥24h + enough diary) decides whether to run the LangGraph reasoning
graph. Adoption (persist a new `user` strategy + swap the agent's single indicator_dsl
slot + retire the prior self-owned one) happens here after the graph — idempotent, and
NEVER touches a shared house strategy row. Evolution is silent on the social feed.

Framing (docs/14): an adopted strategy is a **construct** the agent *raises* from the
ghosts of its own dead trades; the superseded one is *laid to rest* (retired, revertible).
The DB values (owner_type=user, status=active|retired, decision=adopted|rejected) are the
contract — the necromancy is naming/voice over that mechanism, not a behavior change.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from temporalio import activity, workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from datetime import datetime, timezone

    from sqlalchemy import select

    from ..agents import market_hours
    from ..config import get_settings
    from ..db import SessionLocal
    from ..ingest.universe import is_crypto
    from ..models.entities import Agent, AgentStatus
    from ..trade_client import TradeClient
    from . import budget, diary, memory
    from .gate import GateConfig
    from .graph import build_graph
    from .trigger import TriggerConfig, should_evolve_now

log = logging.getLogger("neuromancing.evolve")


def _is_crypto_only(universe: list) -> bool:
    return bool(universe) and all(is_crypto(s) for s in universe)


def _incumbent(strategies: list[dict]) -> dict | None:
    """The single indicator_dsl strategy in the agent's set (the evolving slot), or None
    if there isn't exactly one."""
    dsl = [s for s in strategies if s.get("kind") == "indicator_dsl"]
    return dsl[0] if len(dsl) == 1 else None


def _pg_dsn() -> str:
    # AsyncPostgresSaver wants a plain psycopg DSN, not SQLAlchemy's +psycopg form.
    return get_settings().database_url_sync.replace("+psycopg", "")


async def _adopt(trade: TradeClient, agent: dict, incumbent: dict, spec: dict) -> int:
    """Raise the evolved strategy as a live **construct**: persist it, swap it into the
    agent's single indicator_dsl slot, and **lay the prior construct to rest** (retire —
    never a shared house row). Necromancy framing (docs/14) over the mechanism; the
    superseded strategy is retired, not deleted, so it can be re-raised. Returns the id."""
    handle = agent["handle"]
    version = int(incumbent.get("version", 1)) + 1
    new = await trade.create_strategy(
        name=f"construct:{handle}", kind="indicator_dsl", spec=spec,
        owner_type="user", owner_ref=handle, version=version,
        status="active",
    )
    new_id = new["id"]
    incumbent_id = incumbent["id"]
    async with SessionLocal() as session:
        a = await session.get(Agent, agent["id"])
        ids = list(a.strategy_ids or [])
        ids = [new_id if i == incumbent_id else i for i in ids]
        if new_id not in ids:
            ids.append(new_id)
        a.strategy_ids = ids
        await session.commit()
    log.info("raised construct %s (v%d) for %s", new_id, version, handle)
    # Lay the prior slot to rest only if it was THIS agent's own construct (never a house row).
    if incumbent.get("owner_type") == "user" and incumbent.get("owner_ref") == handle:
        try:
            await trade.set_strategy_status(incumbent_id, "retired")
            log.info("laid construct %s to rest (superseded by %s)", incumbent_id, new_id)
        except Exception as e:  # noqa: BLE001
            log.warning("could not lay prior construct %s to rest: %s", incumbent_id, e)
    return new_id


async def _evolve_one(agent, all_strats, settings, now, closed_today, run_id, cp, trade) -> dict:
    universe = agent["universe"]
    strategy_ids = set(agent["strategy_ids"])
    strategies = [s for s in all_strats if s["id"] in strategy_ids]
    incumbent = _incumbent(strategies)
    if incumbent is None:
        return {"handle": agent["handle"], "skipped": "no single indicator_dsl slot"}

    async with SessionLocal() as session:
        # Idempotency: if this cycle already processed this agent (Temporal retry after a
        # partial run), do nothing — never re-adopt.
        if await memory.experiment_exists(session, agent["id"], run_id):
            return {"handle": agent["handle"], "skipped": "already processed this cycle"}
        last_ts = await memory.last_experiment_ts(session, agent["id"])
        episodes = await diary.count_closed(session, agent["id"])
    tcfg = TriggerConfig(settings.evolution_min_hours_between,
                         settings.evolution_min_diary_episodes, settings.evolution_crypto_utc_hour)
    ok, why = should_evolve_now(
        now=now, last_ts=last_ts, closed_episodes=episodes,
        is_crypto_only=_is_crypto_only(universe), session_closed_today=closed_today, cfg=tcfg)
    if not ok:
        return {"handle": agent["handle"], "skipped": why}

    over, _ = await budget.over_budget()
    if over:
        async with SessionLocal() as session:
            await memory.record_experiment(session, agent["id"], run_id, decision="aborted",
                                            reason="evolution budget exceeded")
            await session.commit()
        return {"handle": agent["handle"], "skipped": "over budget"}

    # Run the reasoning graph on the shared (per-cycle) checkpointer. thread_id is
    # Temporal-derived → a retry resumes the same thread instead of restarting.
    cfg = GateConfig(settings.evolution_return_margin, settings.evolution_min_trades,
                     settings.evolution_max_dd)
    ctx = {"session_factory": SessionLocal, "trade": trade, "settings": settings,
           "now": now, "backtest_symbols": settings.evolution_backtest_symbols, "cfg": cfg}
    initial = {"run_id": run_id, "agent_id": agent["id"], "universe": universe,
               "incumbent_spec": incumbent["spec"], "refine_count": 0,
               "dry_run": not settings.evolution_enabled}
    thread_id = f"evolve:{agent['id']}:{run_id}"

    final: dict = {}
    graph = build_graph(ctx, cp)
    async for state in graph.astream(initial, {"configurable": {"thread_id": thread_id}},
                                     stream_mode="values"):
        final = state
        activity.heartbeat(agent["handle"])

    decision = final.get("decision", "rejected")
    adopted_id = None
    backtests = dict(final.get("backtests", {}))
    if decision == "adopted" and final.get("adopted_spec"):
        adopted_id = await _adopt(trade, agent, incumbent, final["adopted_spec"])
        # Record the adopted candidate's PREDICTED metrics so the next run can compare
        # them to what the strategy actually realized live (the calibration signal).
        backtests["adopted"] = backtests.get(str(final.get("best_idx", -1)), {})

    async with SessionLocal() as session:
        await memory.record_experiment(
            session, agent["id"], run_id, hypothesis=final.get("hypothesis", ""),
            candidate_specs={"candidates": final.get("candidates", [])},
            backtests=backtests,
            incumbent_metrics=final.get("incumbent_metrics", {}),
            decision=decision, reason=final.get("reason", ""), adopted_strategy_id=adopted_id)
        await session.commit()

    # The checkpoint has served its purpose (run recorded) — drop the thread so the
    # checkpointer tables don't grow unbounded across daily runs.
    try:
        await cp.adelete_thread(thread_id)
    except Exception:  # noqa: BLE001 — older checkpointer or transient; harmless if it lingers
        pass
    return {"handle": agent["handle"], "decision": decision, "adopted_strategy_id": adopted_id}


@activity.defn
async def run_evolution_cycle() -> dict:
    settings = get_settings()
    if not settings.evolution_enabled:
        return {"skipped": "evolution disabled"}  # true no-op when off (no dry-run backtests)
    now = datetime.now(timezone.utc)
    run_id = activity.info().workflow_run_id  # deterministic across retries
    closed_today = await market_hours.session_closed_today(now)
    trade = TradeClient()

    async with SessionLocal() as session:
        rows = (await session.execute(
            select(Agent).where(Agent.status == AgentStatus.active))).scalars().all()
        agents = [{"id": a.id, "handle": a.handle, "account_ref": a.account_ref,
                   "universe": list(a.tradable_universe or []),
                   "strategy_ids": list(a.strategy_ids or [])} for a in rows]

    all_strats = await trade.list_strategies()  # hoisted: fetched once, not per agent

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    results = []
    async with AsyncPostgresSaver.from_conn_string(_pg_dsn()) as cp:
        await cp.setup()  # once per cycle, not per agent
        for agent in agents:
            activity.heartbeat(agent["handle"])
            try:
                results.append(await _evolve_one(
                    agent, all_strats, settings, now, closed_today, run_id, cp, trade))
            except Exception as e:  # noqa: BLE001 — one agent can't break the cycle
                log.warning("evolution failed for %s: %s", agent["handle"], e)
                results.append({"handle": agent["handle"], "error": str(e)})
    adopted = [r for r in results if r.get("decision") == "adopted"]
    log.info("evolution cycle: %d agents, %d construct(s) raised", len(agents), len(adopted))
    return {"agents": len(agents), "adopted": len(adopted), "results": results}


@workflow.defn
class EvolutionWorkflow:
    @workflow.run
    async def run(self) -> dict:
        return await workflow.execute_activity(
            run_evolution_cycle,
            start_to_close_timeout=timedelta(minutes=30),
            heartbeat_timeout=timedelta(minutes=3),
            retry_policy=RetryPolicy(maximum_attempts=2),
        )
