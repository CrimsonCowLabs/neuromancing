"""Assemble the per-tick decision context: portfolio, cash, equity, latest quotes,
and this tick's deterministic strategy signals. Pure-ish orchestration over the DB,
trade-api, and Redis — called from a Temporal activity."""

from __future__ import annotations

import json
import logging

from neuromancing_shared.redisio import make_redis
from sqlalchemy import select

from ..config import get_settings
from ..db import SessionLocal
from ..ingest.universe import is_crypto
from ..marks import get_marks
from ..models.entities import Agent, Persona
from ..trade_client import TradeClient

log = logging.getLogger("neuromancing.context")
_redis = make_redis(get_settings().redis_url)

DEFAULT_MAX_POSITION_PCT = 0.20


def actionable_signals(
    signals: dict, positions: dict, position_values: dict, equity: float,
    max_position_pct: float = DEFAULT_MAX_POSITION_PCT,
) -> dict:
    """Keep only signals that would actually change something given holdings — so
    a persistent signal for a position already at its cap doesn't invoke the LLM
    every tick. Mirrors the guardrail's own acceptance so it never suppresses a
    trade the guardrails would allow (buy: room under the concentration cap;
    exit/sell: the position is actually held)."""
    cap = max_position_pct * equity
    out: dict = {}
    for symbol, sig in signals.items():
        action = sig.get("action")
        if action == "buy":
            if position_values.get(symbol, 0.0) < cap:
                out[symbol] = sig
        elif action in ("exit", "sell"):
            if positions.get(symbol, 0) > 0:
                out[symbol] = sig
    return out


async def _last_price(symbol: str) -> float | None:
    raw = await _redis.get(f"quote:{symbol}")
    if not raw:
        return None
    return float(json.loads(raw)["last"])


async def load_agent(agent_id: int) -> dict | None:
    async with SessionLocal() as session:
        agent = await session.get(Agent, agent_id)
        if agent is None:
            return None
        persona = await session.get(Persona, agent.persona_id)
        return {
            "id": agent.id,
            "handle": agent.handle,
            "display_name": agent.display_name,
            "account_ref": agent.account_ref,
            "strategy_ids": list(agent.strategy_ids or []),
            "universe": list(agent.tradable_universe or []),
            "risk_profile": dict(agent.risk_profile or {}),
            "persona": {
                "display_name": agent.display_name,
                "thesis": persona.thesis if persona else "",
                "system_prompt": persona.system_prompt if persona else "",
                "voice_style": persona.voice_style if persona else "",
                "risk_temperament": persona.risk_temperament if persona else "balanced",
                "model_config": dict(persona.model_config_json or {}) if persona else {},
            },
        }


async def build_context(agent: dict, equity_open: bool = True) -> dict | None:
    """Build the LLM/guardrail context dict for one agent tick."""
    trade = TradeClient()
    account = await trade.get_account_by_ref(agent["account_ref"])
    if account is None:
        log.warning("no trade account for agent %s (ref %s)", agent["id"], agent["account_ref"])
        return None
    account_id = account["id"]

    positions_raw = await trade.list_positions(account_id)
    positions = {p["symbol"]: float(p["qty"]) for p in positions_raw}

    universe = agent["universe"]
    symbols = set(universe) | set(positions)
    # Marks fall back to last price_bar close when a Redis quote is missing/expired,
    # so positions are never valued at $0 overnight. See app/marks.py.
    marks = await get_marks(list(symbols))

    equity_info = await trade.mark_to_market(account_id, marks)
    equity = float(equity_info["total_equity"])
    cash = float(account["cash_balance"])

    position_values = {
        s: positions[s] * float(marks[s]) for s in positions if s in marks
    }

    # Deterministic signals for this tick, across the agent's strategies. The
    # agent-level timeframe is only a fallback: an indicator_dsl strategy (TODO #2)
    # carries its own per-indicator timeframes in its spec and the trade-api evaluate
    # endpoint honors those over this value.
    timeframe = agent.get("timeframe") or "1m"
    signals: dict[str, dict] = {}
    for sid in agent["strategy_ids"]:
        try:
            sigs = await trade.evaluate_strategy(
                sid, agent["account_ref"], universe, timeframe=timeframe)
        except Exception as e:  # noqa: BLE001
            log.warning("strategy %s eval failed: %s", sid, e)
            continue
        for s in sigs:
            if s["action"] == "hold":
                continue
            prev = signals.get(s["symbol"])
            if prev is None or s["strength"] > prev["strength"]:
                # Keep which strategy produced the winning signal so the trade diary
                # can attribute the trade to a strategy (deep-agent reflection, #3).
                signals[s["symbol"]] = {"action": s["action"], "strength": s["strength"],
                                        "strategy_id": sid}

    # When the equity market is closed, drop equity signals so mixed agents only
    # consider crypto after hours (equity orders can't fill on stale prices anyway).
    if not equity_open:
        signals = {sym: v for sym, v in signals.items() if is_crypto(sym)}

    # Drop signals that aren't actionable given current holdings, so a persistent
    # signal (already-held position at its cap) doesn't invoke the LLM every tick.
    max_pos_pct = float(agent.get("risk_profile", {}).get("max_position_pct", DEFAULT_MAX_POSITION_PCT))
    signals = actionable_signals(signals, positions, position_values, equity, max_pos_pct)

    return {
        "account_id": account_id,
        "equity": equity,
        "cash": cash,
        "positions": positions,
        "position_values": position_values,
        "signals": signals,
        "universe": universe,
        "marks": marks,
        "equity_open": equity_open,
    }
