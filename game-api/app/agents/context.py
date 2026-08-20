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
    """Keep only signals that would actually change something given holdings — so a
    persistent signal for a position already at its cap doesn't invoke the LLM every
    tick. Mirrors the guardrail's acceptance (signed positions): buy/short need room
    under the concentration cap on that side; exit/sell needs a held long; cover needs
    a held short."""
    cap = max_position_pct * equity
    out: dict = {}
    for symbol, sig in signals.items():
        action = sig.get("action")
        qty = positions.get(symbol, 0)
        val = position_values.get(symbol, 0.0)
        if action == "buy":
            if val < cap:  # room to add to a long
                out[symbol] = sig
        elif action == "short":
            if abs(min(0.0, val)) < cap:  # room to add to a short
                out[symbol] = sig
        elif action in ("exit", "sell"):
            if qty > 0:  # a held long to close
                out[symbol] = sig
        elif action == "cover":
            if qty < 0:  # a held short to close
                out[symbol] = sig
    return out


def _trim_features(features: dict | None) -> dict:
    """Keep only compact scalar indicator features (rounded floats), dropping any
    lists/nested dicts, so per-symbol `sources` stay cheap to serialize to the LLM."""
    if not features:
        return {}
    out: dict = {}
    for k, v in features.items():
        if isinstance(v, bool) or isinstance(v, int) or isinstance(v, str):
            out[k] = v
        elif isinstance(v, float):
            out[k] = round(v, 4)
        # skip lists/dicts — bound the payload
    return out


def merge_signals(evaluations: list[tuple[int, list[dict]]]) -> tuple[dict, dict]:
    """Aggregate per-strategy signals per symbol. Returns (primary, sources):

    - `primary[symbol]` = the strength-max winner `{action, strength, strategy_id}`
      (unchanged shape, so actionable_signals/guardrails/the fallback are untouched).
    - `sources[symbol]` = every contributing strategy's signal
      `{strategy_id, action, strength, features}` — so the LLM can see indicator
      features and disagreement instead of a single collapsed action. Pure.
    """
    primary: dict[str, dict] = {}
    sources: dict[str, list[dict]] = {}
    for sid, sigs in evaluations:
        for s in sigs:
            if s.get("action") == "hold":
                continue
            sym = s["symbol"]
            sources.setdefault(sym, []).append({
                "strategy_id": sid, "action": s["action"],
                "strength": s["strength"], "features": _trim_features(s.get("features")),
            })
            prev = primary.get(sym)
            if prev is None or s["strength"] > prev["strength"]:
                primary[sym] = {"action": s["action"], "strength": s["strength"],
                                "strategy_id": sid}
    return primary, sources


def enrich_actionable(signals: dict, sources: dict) -> dict:
    """Attach each *actionable* signal's per-strategy `sources` + a `conflict` flag
    (strategies disagree on the action). Only actionable symbols are enriched, so the
    richer payload stays bounded even with a wide universe. Mutates and returns."""
    for sym, sig in signals.items():
        srcs = sources.get(sym, [])
        sig["sources"] = srcs
        sig["conflict"] = len({s["action"] for s in srcs}) > 1
    return signals


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
    buying_power = float(account.get("buying_power", cash))

    position_values = {
        s: positions[s] * float(marks[s]) for s in positions if s in marks
    }

    # Deterministic signals for this tick, across the agent's strategies. The
    # agent-level timeframe is only a fallback: an indicator_dsl strategy (TODO #2)
    # carries its own per-indicator timeframes in its spec and the trade-api evaluate
    # endpoint honors those over this value.
    timeframe = agent.get("timeframe") or "1m"
    evaluations: list[tuple[int, list[dict]]] = []
    for sid in agent["strategy_ids"]:
        try:
            sigs = await trade.evaluate_strategy(
                sid, agent["account_ref"], universe, timeframe=timeframe)
        except Exception as e:  # noqa: BLE001
            log.warning("strategy %s eval failed: %s", sid, e)
            continue
        evaluations.append((sid, sigs))
    # Primary = strength-max winner per symbol (drives actionable/guardrails/fallback);
    # sources = every strategy's signal per symbol, surfaced to the LLM so features and
    # cross-strategy disagreement aren't silently collapsed away (#2).
    signals, sources = merge_signals(evaluations)

    # When the equity market is closed, drop equity signals so mixed agents only
    # consider crypto after hours (equity orders can't fill on stale prices anyway).
    if not equity_open:
        signals = {sym: v for sym, v in signals.items() if is_crypto(sym)}

    # Drop signals that aren't actionable given current holdings, so a persistent
    # signal (already-held position at its cap) doesn't invoke the LLM every tick.
    max_pos_pct = float(agent.get("risk_profile", {}).get("max_position_pct", DEFAULT_MAX_POSITION_PCT))
    signals = actionable_signals(signals, positions, position_values, equity, max_pos_pct)
    # Enrich only the actionable survivors with their per-strategy sources + conflict.
    enrich_actionable(signals, sources)

    return {
        "account_id": account_id,
        "equity": equity,
        "cash": cash,
        "buying_power": buying_power,
        "positions": positions,
        "position_values": position_values,
        "signals": signals,
        "universe": universe,
        "marks": marks,
        "equity_open": equity_open,
    }
