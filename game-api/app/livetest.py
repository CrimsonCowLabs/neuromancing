"""On-demand LIVE test: run ONE real agent decision end-to-end against the configured
LLM provider (the Phase-C seam), so you can verify decisions actually execute with a
real key. This is NOT part of the offline unit suite — it needs a provider key plus the
game DB, Redis, and a reachable trade-api.

    uv run python -m app.livetest momentum-mike           # dry — no orders placed
    uv run python -m app.livetest momentum-mike --place    # actually place the orders

It fails LOUDLY if the LLM fell back to the deterministic manager (no key / budget
tripped / API error), so a success always means the provider genuinely answered and its
actions survived the guardrails. Mirrors decision.decide_activity/apply_activity but runs
inline (no Temporal), and defaults to dry so a live run never mutates the deployment.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging

from sqlalchemy import select

from . import config
from .agents import guardrails, llm, market_hours
from .agents.context import build_context, load_agent
from .agents.decision import DEFAULT_STOP_LOSS_PCT
from .db import SessionLocal
from .ingest.universe import is_crypto
from .llm import provider
from .models.entities import Agent
from .trade_client import TradeClient

log = logging.getLogger("neuromancing.livetest")


class LiveTestError(RuntimeError):
    """A precondition failed or the provider was not genuinely exercised."""


async def _resolve_agent_id(handle: str) -> int:
    async with SessionLocal() as session:
        row = (
            await session.execute(select(Agent).where(Agent.handle == handle))
        ).scalar_one_or_none()
    if row is None:
        raise LiveTestError(f"no agent with handle {handle!r} — seed the roster first")
    return row.id


async def run_livetest(handle: str, place: bool = False) -> dict:
    """Drive one real decision. Returns a summary dict; raises LiveTestError on any
    precondition failure or if the LLM fell back (provider not exercised)."""
    pc = provider.resolve_provider("manage")
    if not pc.api_key:
        raise LiveTestError(
            "no LLM key resolved — set LLM_API_KEY (or OLLAMA_API_KEY for the ollama "
            f"provider). provider={pc.provider} base_url={pc.base_url}")

    agent_id = await _resolve_agent_id(handle)
    agent = await load_agent(agent_id)
    equity_open = await market_hours.equity_active()
    context = await build_context(agent, equity_open=equity_open)
    if context is None:
        raise LiveTestError("context unavailable — is trade-api reachable / account seeded?")
    if not context["signals"]:
        log.warning("no actionable signals this tick (equity_open=%s) — the model isn't "
                    "invoked when nothing is actionable; try during market hours", equity_open)
        return {"handle": handle, "provider": pc.provider, "model": None,
                "signals": 0, "note": "no actionable signals", "placed": []}

    decision = await llm.manage(context, agent["persona"], agent["handle"])
    model = decision.get("model")
    if model == llm.FALLBACK_MODEL:
        raise LiveTestError(
            "LLM fell back to the deterministic manager — the provider was NOT "
            "exercised (likely budget tripped or an API error). Check budget headroom "
            f"and the provider config (provider={pc.provider}, base_url={pc.base_url}).")

    risk = agent.get("risk_profile", {})
    gctx = guardrails.GuardrailContext(
        equity=context["equity"], cash=context["cash"], positions=context["positions"],
        position_values=context["position_values"],
        signals={s: v["action"] for s, v in context["signals"].items()},
        universe=context["universe"], equity_open=context.get("equity_open", True),
        max_position_pct=float(risk.get("max_position_pct", guardrails.DEFAULT_MAX_POSITION_PCT)),
        per_tick_notional_pct=float(risk.get("per_tick_notional_pct", guardrails.DEFAULT_PER_TICK_NOTIONAL_PCT)),
    )
    gresult = guardrails.validate(decision.get("actions", []), gctx)

    placed: list[dict] = []
    if place and gresult.valid:
        placed = await _place(context, agent, gresult.valid)

    return {
        "handle": handle, "provider": pc.provider, "model": model,
        "signals": len(context["signals"]),
        "actions": decision.get("actions", []),
        "valid": gresult.valid, "rejected": gresult.rejected,
        "posts": decision.get("posts", []),
        "tokens_in": decision.get("tokens_in"), "tokens_out": decision.get("tokens_out"),
        "placed": placed, "dry": not place,
    }


async def _place(context: dict, agent: dict, valid: list[dict]) -> list[dict]:
    """Place the guardrail-approved orders (opt-in). Minimal mirror of apply_activity's
    order construction — used only when --place is passed."""
    trade = TradeClient()
    account_id = context["account_id"]
    placed: list[dict] = []
    for idx, action in enumerate(valid):
        symbol = action["symbol"]
        asset_class = "crypto" if is_crypto(symbol) else "equity"
        coid = f"livetest#{idx}"
        if action["type"] == "close":
            qty = context["positions"].get(symbol, 0)
            if qty <= 0:
                continue
            order = {"symbol": symbol, "side": "sell", "order_type": "market",
                     "qty": str(qty), "asset_class": asset_class,
                     "client_order_id": coid, "source": "livetest", "source_ref": "livetest"}
        else:
            order = {"symbol": symbol, "side": action["side"], "order_type": "market",
                     "asset_class": asset_class, "client_order_id": coid,
                     "source": "livetest", "source_ref": "livetest"}
            if action.get("notional") is not None:
                order["notional"] = str(action["notional"])
            else:
                order["qty"] = str(action.get("qty"))
            if action["side"] == "buy":
                sl = action.get("stop_loss_pct") or agent.get("risk_profile", {}).get("stop_loss_pct") or DEFAULT_STOP_LOSS_PCT
                order["stop_loss_pct"] = str(sl)
        try:
            placed.append(await trade.place_order(account_id, order))
        except Exception as e:  # noqa: BLE001
            log.warning("place_order %s failed: %s", symbol, e)
    return placed


def main() -> None:
    ap = argparse.ArgumentParser(description="Run one live agent decision end-to-end.")
    ap.add_argument("handle", help="agent handle, e.g. momentum-mike")
    ap.add_argument("--place", action="store_true",
                    help="actually place the approved orders (default: dry, no orders)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)

    try:
        result = asyncio.run(run_livetest(args.handle, place=args.place))
    except LiveTestError as e:
        log.error("LIVE TEST FAILED: %s", e)
        raise SystemExit(1)
    print(json.dumps(result, indent=2, default=str))
    pc = config.get_settings()  # noqa: F841 — surfaced above already
    log.info("live test OK: provider answered (model=%s), %d valid action(s), %s",
             result.get("model"), len(result.get("valid", [])),
             "placed" if args.place else "DRY (no orders placed)")


if __name__ == "__main__":
    main()
