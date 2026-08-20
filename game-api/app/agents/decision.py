"""The per-agent decision loop as a Temporal workflow + activities.

Pipeline: strategy-eval + context (build_context) -> LLM management (Ollama Cloud
or fallback) -> deterministic guardrails -> place orders via trade-api -> persist
audit + social posts + feed events. Deterministic workflow IDs give once-per-tick
semantics; each activity retries independently.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from temporalio import activity, workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from . import guardrails, llm, market_hours
    from .context import build_context, load_agent
    from .guardrails import GuardrailContext
    from .persist import record_tick
    from ..db import SessionLocal
    from ..evolve import diary
    from ..ingest.universe import is_crypto
    from ..marks import get_marks
    from ..trade_client import TradeClient

log = logging.getLogger("neuromancing.decision")

# Applied when a buy carries no stop-loss (from the LLM, risk_profile, or fallback).
DEFAULT_STOP_LOSS_PCT = 0.08


def _trades_crypto(agent: dict) -> bool:
    return any(is_crypto(s) for s in agent.get("universe", []))


async def _holds_crypto(account_ref: str) -> bool:
    """Defensive: an equity-only agent normally can't hold crypto, but honor the
    'hold or trade' rule in case its universe was reconfigured while holding a bag."""
    trade = TradeClient()
    acct = await trade.get_account_by_ref(account_ref)
    if acct is None:
        return False
    positions = await trade.list_positions(acct["id"])
    return any(is_crypto(p["symbol"]) and float(p["qty"]) > 0 for p in positions)


async def _should_sleep(agent: dict) -> bool:
    """Equity-only agents sleep outside [open-30m, close+30m]; anything that trades
    or holds crypto runs 24/7."""
    if _trades_crypto(agent):
        return False
    if await market_hours.equity_active():
        return False
    return not await _holds_crypto(agent["account_ref"])


# ---------------- Activities ----------------
@activity.defn
async def decide_activity(agent_id: int, tick_id: str) -> dict | None:
    agent = await load_agent(agent_id)
    if agent is None:
        return None
    # Market-hours sleep: skip the tick entirely (no LLM, no trades) when an
    # equity-only agent is outside the trading window.
    if await _should_sleep(agent):
        log.info("agent %s sleeping — equity market closed", agent["handle"])
        return None
    # Mixed agents stay awake for crypto but must not attempt equity after hours.
    equity_open = await market_hours.equity_active()
    context = await build_context(agent, equity_open=equity_open)
    if context is None:
        return None
    # Nothing actionable this tick -> don't call the LLM and don't post. This is
    # what keeps the feed to real activity and cuts idle (esp. after-hours) LLM use.
    if not context["signals"]:
        return None
    decision = await llm.manage(context, agent["persona"], agent["handle"])
    return {"agent_id": agent_id, "tick_id": tick_id, "agent": agent,
            "context": context, "decision": decision}


async def _diary_reconcile(agent_id, tick_id, account_id, context, valid_actions, placed, trade):
    """Reconcile the trade diary against post-tick positions: OPEN an episode for a
    buy that left the symbol held (avg entry from the position), CLOSE on a sell/close
    that returned the symbol to flat (exit ≈ current mark)."""
    if not placed:
        return
    action_by_sym = {a["symbol"]: a for a in valid_actions}
    try:
        positions = await trade.list_positions(account_id)
    except Exception:  # noqa: BLE001
        positions = []
    pos_by_sym = {p["symbol"]: p for p in positions}

    async def _exit_price(p) -> float | None:
        """Actual fill price for a closed order (exact), falling back to the mark."""
        try:
            fills = await trade.order_fills(account_id, p["id"])
            if fills:
                return float(fills[-1]["price"])
        except Exception:  # noqa: BLE001
            pass
        mk = (await get_marks([p["symbol"]])).get(p["symbol"])
        return float(mk) if mk is not None else None

    async with SessionLocal() as session:
        for p in placed:
            sym, intent = p["symbol"], p.get("intent")
            try:
                pos = pos_by_sym.get(sym)
                qty = float(pos.get("qty", 0)) if pos else 0.0
                held = pos is not None and qty != 0  # either sign
                if intent in ("buy", "short") and held:
                    # Open (or average) an episode — signed qty (short is negative).
                    entry = float(pos.get("avg_entry_price") or pos.get("avg_entry") or 0)
                    if entry <= 0:
                        continue
                    act = action_by_sym.get(sym, {})
                    sig = context["signals"].get(sym, {})
                    await diary.open_or_update(
                        session, agent_id, sym, entry_price=entry, qty=qty, tick_id=tick_id,
                        strategy_id=sig.get("strategy_id"), notional=entry * abs(qty), signal=sig,
                        rationale=act.get("rationale", ""),
                        entry_context={"equity": context.get("equity")},
                    )
                elif intent in ("close", "sell", "cover") and not held:
                    # Closed back to flat (a long sold or a short covered).
                    px = await _exit_price(p)
                    if px is not None:
                        await diary.close_open(session, agent_id, sym,
                                               exit_price=px, exit_reason="signal")
            except Exception as e:  # noqa: BLE001
                log.warning("diary reconcile %s: %s", sym, e)
        await session.commit()


@activity.defn
async def apply_activity(payload: dict) -> dict:
    agent_id = payload["agent_id"]
    tick_id = payload["tick_id"]
    context = payload["context"]
    decision = payload["decision"]
    agent = payload.get("agent", {})

    risk = agent.get("risk_profile", {})
    gctx = GuardrailContext(
        equity=context["equity"],
        cash=context["cash"],
        buying_power=float(context.get("buying_power", context["cash"])),
        positions=context["positions"],
        position_values=context["position_values"],
        signals={s: v["action"] for s, v in context["signals"].items()},
        universe=context["universe"],
        equity_open=context.get("equity_open", True),
        max_position_pct=float(risk.get("max_position_pct", guardrails.DEFAULT_MAX_POSITION_PCT)),
        per_tick_notional_pct=float(risk.get("per_tick_notional_pct", guardrails.DEFAULT_PER_TICK_NOTIONAL_PCT)),
    )
    gresult = guardrails.validate(decision.get("actions", []), gctx)

    trade = TradeClient()
    account_id = context["account_id"]
    placed: list[dict] = []
    failed: list[dict] = []

    for idx, action in enumerate(gresult.valid):
        symbol = action["symbol"]
        asset_class = "crypto" if is_crypto(symbol) else "equity"
        coid = f"{tick_id}#{idx}"
        pos_qty = float(context["positions"].get(symbol, 0))
        side = action.get("side")

        if action["type"] == "close" or side in ("sell", "cover"):
            # Close/cover: resolve direction from the held sign (long→sell, short→buy),
            # reduce_only so trade-api can't flip on a stale close.
            if pos_qty == 0:
                continue
            close_side = "sell" if pos_qty > 0 else "buy"
            order = {"symbol": symbol, "side": close_side, "order_type": "market",
                     "qty": str(abs(pos_qty)), "asset_class": asset_class, "reduce_only": True,
                     "client_order_id": coid, "source": "agent", "source_ref": tick_id}
        else:  # open: buy (long) or short (sell-to-open)
            order = {"symbol": symbol, "side": "sell" if side == "short" else "buy",
                     "order_type": "market", "asset_class": asset_class,
                     "client_order_id": coid, "source": "agent", "source_ref": tick_id}
            if action.get("notional") is not None:
                order["notional"] = str(action["notional"])
            else:
                order["qty"] = str(action.get("qty"))
            # Mandatory stop FLOOR on every open (long AND short) so nothing is ever
            # left unprotected — for a short this bounds the otherwise-unbounded loss.
            risk_p = agent.get("risk_profile", {})
            sl = action.get("stop_loss_pct") or risk_p.get("stop_loss_pct") or DEFAULT_STOP_LOSS_PCT
            order["stop_loss_pct"] = str(sl)
            tp = action.get("take_profit_pct") or risk_p.get("take_profit_pct")
            if tp:
                order["take_profit_pct"] = str(tp)
            trail = action.get("trailing_stop_pct") or risk_p.get("trailing_stop_pct")
            if trail:
                order["trailing_stop_pct"] = str(trail)
        try:
            res = await trade.place_order(account_id, order)
            placed.append({**res, "symbol": symbol, "side": order["side"], "intent": side or "close"})
        except Exception as e:  # noqa: BLE001 — one bad order can't kill the tick
            failed.append({"action": action, "error": str(e)})

    await record_tick(
        agent_id=agent_id, handle=agent.get("handle", f"agent-{agent_id}"),
        display_name=agent.get("display_name", f"Agent {agent_id}"),
        tick_id=tick_id, context=context, decision=decision,
        valid_actions=gresult.valid, placed_orders=placed,
        rejected=gresult.rejected + failed,
    )

    # Trade diary: open episodes for filled buys, close on LLM sells/closes to flat.
    # Best-effort — a diary write must never break the tick.
    try:
        await _diary_reconcile(agent_id, tick_id, account_id, context, gresult.valid, placed, trade)
    except Exception as e:  # noqa: BLE001
        log.warning("diary reconcile failed (tick %s): %s", tick_id, e)
    return {"placed": len(placed), "rejected": len(gresult.rejected) + len(failed),
            "posts": len(decision.get("posts", []))}


# ---------------- Workflow ----------------
@workflow.defn
class AgentDecisionWorkflow:
    @workflow.run
    async def run(self, agent_id: int) -> dict:
        tick_id = workflow.info().workflow_id

        # decide includes the LLM call (openai timeout 45s < this 75s so the call
        # fails cleanly before Temporal times the activity out — no zombie call).
        # Fewer attempts here: a retry re-runs strategy eval + LLM (cost + dup
        # signals), and manage() already never raises on LLM failure.
        decision = await workflow.execute_activity(
            decide_activity,
            args=[agent_id, tick_id],
            start_to_close_timeout=timedelta(seconds=75),
            retry_policy=RetryPolicy(maximum_attempts=2),
        )
        if decision is None:
            return {"status": "skipped", "agent_id": agent_id}

        # apply is idempotent (deterministic client_order_id) so retrying is safe.
        result = await workflow.execute_activity(
            apply_activity,
            args=[decision],
            start_to_close_timeout=timedelta(seconds=60),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
        return {"status": "ok", "agent_id": agent_id, **result}
