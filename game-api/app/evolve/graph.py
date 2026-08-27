"""The LangGraph evolution reasoning graph: reflect → propose → backtest → decide,
with a bounded refine loop (the agent learns from backtest feedback). Structured
output only (Pydantic `StrategyProposal`); a deterministic heuristic proposer backs
the dry-run / no-key / structured-output-failure path so the pipeline never blocks.

The graph REASONS and returns a decision; the side-effecting adoption (persist +
reassign strategy_ids) happens in the workflow after the graph, keeping it idempotent.
"""

from __future__ import annotations

import copy
import json
import logging
from typing import Annotated, Any, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from neuromancing_shared.strategy_spec import validate_spec

from . import budget, llm, tools
from .gate import GateConfig, should_adopt
from .proposal import StrategyProposal, compile_proposal

log = logging.getLogger("neuromancing.evolve.graph")
MAX_REFINE = 1

_SYSTEM = (
    "You are a quantitative strategy researcher improving ONE deterministic trading "
    "strategy for a trading-game agent. You design candidate strategies in a fixed "
    "indicator grammar. Propose 1-3 candidates that are meaningfully DIFFERENT from the "
    "current strategy and grounded in the performance summary. Only use the provided "
    "indicator functions, sources, timeframes and operators. Return them via the "
    "structured schema — do not write prose."
)


class EvolutionState(TypedDict, total=False):
    run_id: str
    agent_id: int
    universe: list
    incumbent_spec: dict
    digest: dict
    hypothesis: str
    candidates: list          # canonical validated specs
    backtests: dict           # candidate index -> {w1,w2}
    incumbent_metrics: dict
    best_idx: int
    decision: str
    reason: str
    adopted_spec: dict | None
    refine_count: int
    dry_run: bool
    messages: Annotated[list, add_messages]


def _period_variants(incumbent: dict) -> list[dict]:
    out: list[dict] = []
    # Nudge each indicator's period ± a step, and shift buy-condition thresholds ±20%.
    for delta in (2, -2, 4):
        cand = copy.deepcopy(incumbent)
        bumped = False
        for ind in cand.get("indicators", []):
            if isinstance(ind.get("period"), int) and ind["period"] + delta > 1:
                ind["period"] += delta
                bumped = True
        if bumped:
            out.append(cand)
    for scale in (1.1, 0.9):
        cand = copy.deepcopy(incumbent)
        touched = False
        for grp in (cand.get("buy_when"), cand.get("exit_when")):
            for key in ("all", "any"):
                for c in (grp or {}).get(key, []):
                    if isinstance(c, dict) and isinstance(c.get("value"), (int, float)):
                        c["value"] = round(c["value"] * scale, 4)
                        touched = True
        if touched:
            out.append(cand)
    return out


def _heuristic_candidates(incumbent: dict | None) -> list[dict]:
    """Deterministic fallback proposer (dry-run / no key / structured-output failure):
    generate several varied nudges of the incumbent so the pipeline is exercisable and
    can occasionally clear the gate. Returns validated canonical specs ([] if none)."""
    if not incumbent:
        return []
    out: list[dict] = []
    for cand in _period_variants(incumbent):
        try:
            out.append(validate_spec(cand))
        except Exception:  # noqa: BLE001
            continue
    return out


async def _record_usage(resp) -> None:
    """Best-effort: bill LLM tokens to the separate evolution budget."""
    try:
        um = getattr(resp, "usage_metadata", None) or {}
        await budget.record_usage(um.get("input_tokens", 0), um.get("output_tokens", 0))
    except Exception:  # noqa: BLE001
        pass


async def _flash_narrative(digest: dict, incumbent: dict | None) -> str:
    """Flash compaction: turn the deterministic aggregates into a short qualitative
    read (real multi-model routing + context compaction)."""
    sys = ("You summarize a trading agent's recent performance in 2-3 plain sentences: "
           "what's working, what's not, and one concrete idea to try. No preamble.")
    human = json.dumps({"performance": digest,
                        "current_strategy_type": (incumbent or {}).get("type")}, default=str)
    resp = await llm.flash().ainvoke([SystemMessage(content=sys), HumanMessage(content=human)])
    await _record_usage(resp)
    return (getattr(resp, "content", "") or "").strip()


async def _propose_llm(digest, incumbent, feedback) -> tuple[str, list[dict]]:
    """Structured-output proposal via the reasoner. Returns (hypothesis, [canonical specs])."""
    model = llm.reasoner().with_structured_output(
        StrategyProposal, method="function_calling", include_raw=True)
    human = json.dumps({
        "performance_summary": digest,
        "current_strategy": incumbent,
        "indicator_grammar": tools.indicator_vocabulary(),
        "backtest_feedback": feedback or "none yet",
        "instructions": "Propose 1-3 improved candidate strategies as structured objects. "
                        "Make them meaningfully different from the current strategy and "
                        "grounded in the performance summary; only use the listed fns/sources/"
                        "timeframes/operators.",
    }, default=str)
    out = await model.ainvoke([SystemMessage(content=_SYSTEM), HumanMessage(content=human)])
    await _record_usage(out.get("raw"))
    proposal: StrategyProposal | None = out.get("parsed")
    if proposal is None:
        raise ValueError(f"structured output failed to parse: {out.get('parsing_error')}")
    specs: list[dict] = []
    for cand in proposal.candidates:
        try:
            specs.append(compile_proposal(cand))  # validates fail-closed
        except Exception as e:  # noqa: BLE001 — drop invalid candidates
            log.info("dropped invalid candidate: %s", e)
    return proposal.hypothesis, specs


def build_graph(ctx: dict, checkpointer):
    """ctx: {session_factory, trade, settings, now, backtest_symbols, cfg}."""

    async def reflect(state: EvolutionState) -> dict:
        async with ctx["session_factory"]() as session:
            digest = await tools.gather_performance(session, state["agent_id"])
        # Flash compaction (real multi-model routing) — a short qualitative read over the
        # deterministic aggregates. Skipped in dry-run / no-key (aggregates alone suffice).
        if not state.get("dry_run") and llm.has_key():
            try:
                digest["narrative"] = await _flash_narrative(digest, state.get("incumbent_spec"))
            except Exception as e:  # noqa: BLE001
                log.info("flash narrative skipped: %s", e)
        return {"digest": digest}

    async def propose(state: EvolutionState) -> dict:
        incumbent = state.get("incumbent_spec")
        feedback = None
        if state.get("backtests"):
            feedback = {"incumbent": state.get("incumbent_metrics"),
                        "candidates": state.get("backtests")}
        if state.get("dry_run") or not llm.has_key():
            cands = _heuristic_candidates(incumbent)
            return {"candidates": cands, "hypothesis": "heuristic parameter nudge (dry-run)",
                    "refine_count": state.get("refine_count", 0) + 1}
        try:
            hypothesis, cands = await _propose_llm(state["digest"], incumbent, feedback)
        except Exception as e:  # noqa: BLE001 — structured output unsupported/failed → heuristic
            log.warning("reasoner propose failed (%s); using heuristic", e)
            cands = _heuristic_candidates(incumbent)
            hypothesis = "heuristic (reasoner unavailable)"
        return {"candidates": cands, "hypothesis": hypothesis,
                "refine_count": state.get("refine_count", 0) + 1}

    async def backtest(state: EvolutionState) -> dict:
        cands = state.get("candidates") or []
        if not cands:
            return {"backtests": {}, "best_idx": -1}
        syms = list(state["universe"])[: ctx["backtest_symbols"]]
        now = ctx["now"]
        rp = ctx.get("risk_profile")  # measure every candidate under the construct's own discipline
        inc_metrics = state.get("incumbent_metrics")
        if inc_metrics is None and state.get("incumbent_spec"):
            inc_metrics = await tools.backtest_candidate(
                ctx["trade"], state["incumbent_spec"], syms, now, risk_profile=rp)
        bt: dict = {}
        best_idx, best_score = -1, -1e9
        for i, spec in enumerate(cands):
            m = await tools.backtest_candidate(ctx["trade"], spec, syms, now, risk_profile=rp)
            bt[str(i)] = m
            score = float(m.get("w1", {}).get("total_return", -9)) + float(m.get("w2", {}).get("total_return", -9))
            if score > best_score:
                best_idx, best_score = i, score
        return {"backtests": bt, "best_idx": best_idx, "incumbent_metrics": inc_metrics or {}}

    async def decide(state: EvolutionState) -> dict:
        idx = state.get("best_idx", -1)
        if idx < 0:
            return {"decision": "rejected", "reason": "no valid candidates", "adopted_spec": None}
        cand_metrics = state["backtests"][str(idx)]
        adopt, reason = should_adopt(state.get("incumbent_metrics") or {}, cand_metrics, ctx["cfg"])
        if adopt:
            return {"decision": "adopted", "reason": reason,
                    "adopted_spec": state["candidates"][idx]}
        return {"decision": "rejected", "reason": reason, "adopted_spec": None}

    def route_after_decide(state: EvolutionState) -> str:
        if state.get("decision") == "adopted":
            return "done"
        if state.get("refine_count", 0) <= MAX_REFINE and not state.get("dry_run") and llm.has_key():
            return "refine"
        return "done"

    g = StateGraph(EvolutionState)
    g.add_node("reflect", reflect)
    g.add_node("propose", propose)
    g.add_node("backtest", backtest)
    g.add_node("decide", decide)
    g.add_edge(START, "reflect")
    g.add_edge("reflect", "propose")
    g.add_edge("propose", "backtest")
    g.add_edge("backtest", "decide")
    g.add_conditional_edges("decide", route_after_decide, {"refine": "propose", "done": END})
    return g.compile(checkpointer=checkpointer)
