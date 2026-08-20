"""Seed the roster: house strategies (via trade-api), personas, accounts, and agents.

Idempotent — safe to re-run. Run: `uv run python -m app.seed`
Then register schedules with: `uv run python -m app.workflows.schedules`
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select

from .db import SessionLocal
from .ingest.universe import DEFAULT_CRYPTO
from .models.entities import Agent, Persona
from .trade_client import TradeClient

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("neuromancing.seed")

STARTING_CASH = 100_000.0

# Per-agent tradable universes are *curated, persona-themed subsets* of the ingest
# universe (ingest fetches prices for all of it; each persona trades a coherent slice).
# The guardrail (agents/guardrails.py) hard-caps trading to these lists, so they set
# the breadth of each agent's opportunity set. They're ~30 symbols each (widened from
# the original ~10) — the LLM's per-tick token cost stays bounded because the context
# only serializes marks for held + signaled symbols, not the whole universe (see
# agents/llm.py::_llm_view). NOTE: every ticker below is a member of
# ingest/symbols/diversified.txt, so they price under BOTH PRICE_UNIVERSE=diversified
# and the russell1000 superset. If you add names outside diversified.txt, make sure
# the active PRICE_UNIVERSE ingests them or their marks/signals will be missing.
_MOMENTUM = [  # high-beta growth / semis / momentum leaders
    "NVDA", "AMD", "TSLA", "AVGO", "META", "QCOM", "MU", "AMAT", "NOW", "INTU",
    "ADBE", "CRM", "ORCL", "AMZN", "GOOGL", "NFLX", "NKE", "BKNG", "MAR", "CMG",
    "ISRG", "LLY", "AXP", "GS", "MS", "COF", "GM", "DIS",
]
_DEFENSIVE = [  # staples / healthcare / utilities / telecom — low-beta mean reversion
    "KO", "PG", "JNJ", "WMT", "PFE", "MRK", "VZ", "T", "PEP", "COST", "MDLZ", "CL",
    "MO", "PM", "KMB", "GIS", "SYY", "KHC", "ABBV", "ABT", "BMY", "AMGN", "MDT",
    "CVS", "GILD", "DUK", "SO", "D", "AEP", "EXC", "XEL", "PEG", "NEE",
]
_TREND = [  # liquid trending large caps + broad ETFs + industrials
    "AAPL", "MSFT", "SPY", "QQQ", "IWM", "DIA", "JPM", "BAC", "GS", "CAT", "HON",
    "GE", "UNP", "RTX", "LMT", "DE", "UPS", "FDX", "BA", "ETN", "EMR", "CSX",
    "NSC", "MMM", "LIN", "XOM", "CVX", "COP", "NEE", "AMT",
]
_VALUE = [  # financials / energy / cyclicals / dividend payers — oversold dips
    "BAC", "WFC", "C", "USB", "PNC", "COF", "CVX", "XOM", "COP", "SLB", "EOG",
    "MPC", "PSX", "VLO", "OXY", "HD", "LOW", "TGT", "F", "GM", "CVS", "PFE", "MRK",
    "ABBV", "MO", "PM", "T", "VZ", "KMI", "WMB", "DOW", "DD", "NUE", "FCX",
]
_DIVERSIFIED = [  # broad multi-sector spread + ETFs
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "JPM", "BAC", "GS", "UNH",
    "JNJ", "LLY", "ABBV", "XOM", "CVX", "CAT", "HON", "GE", "LIN", "APD", "NEE",
    "DUK", "PLD", "AMT", "WMT", "PG", "KO", "COST", "HD", "MCD", "DIS", "VZ",
    "SPY", "QQQ",
]

# (handle, display_name, thesis, voice, risk_temperament, [strategy names],
#  universe, cadence_seconds)
# Cadence rationale: crypto agents run 24/7, so they're slower to respect the
# daily token budget; equity-only agents sleep outside market hours (see
# market_hours), so they can afford to be snappier during their ~7h window.
ROSTER = [
    # The constructs — five autonomous AIs on the grid, named for the Sprawl.
    # Each runs its legacy strategy PLUS one thematically-matched indicator_dsl
    # (multi-indicator) strategy — see trade-api/app/strategies/catalog.
    ("molly", "Molly",
     "Ride strength. Winners keep winning until they don't.",
     "terse, lethal, street-samurai cool", "aggressive",
     ["20-bar Momentum", "Momentum Breakout (Trend-Filtered)"],
     _MOMENTUM + DEFAULT_CRYPTO, 90),  # crypto 24/7
    ("riviera", "Riviera",
     "Buy fear, sell greed — and short the crowd's euphoria. Perception is the opening.",
     "sardonic, silky, plays on perception", "cautious",
     ["Bollinger Mean Reversion", "Bollinger Fade (Short)"],
     _DEFENSIVE, 90),  # equity-only, sleeps off-hours; the only construct that shorts
    ("armitage", "Armitage",
     "Trends are the mission. Follow the signal, hold the line.",
     "clipped, military, systematic", "balanced",
     ["SMA 10/30 Crossover", "MACD Trend + RSI Pullback"],
     _TREND + DEFAULT_CRYPTO, 120),  # crypto 24/7
    ("finn", "Finn",
     "Everything's worth something. Buy the good stuff when it's marked down.",
     "dry, streetwise, knows a bargain", "cautious",
     ["RSI-DSL Dip Buyer", "Trend Dip Buyer"], _VALUE, 180),  # equity-only, patient
    ("wintermute", "Wintermute",
     "Many small edges, assembled into one. No single bet decides the game.",
     "cold, patient, calculating", "balanced",
     ["20-bar Momentum", "RSI Mean Reversion", "Bollinger Breakout + VWAP"],
     _DIVERSIFIED + DEFAULT_CRYPTO, 120),  # crypto 24/7
]


def _reseed_strategy_ids(
    current_ids: list[int], seed_ids: list[int], owner_type_by_id: dict[int, str]
) -> list[int]:
    """Evolution-safe strategy_ids for a reseed of an EXISTING agent.

    If the agent has adopted a self-evolved strategy (owner_type='user', written by
    the deep-agent evolution loop, TODO #3), its current lineage is preserved
    untouched — a reseed must never revert an adoption (re-adding the retired house
    slot would also give it two indicator_dsl strategies and break the evolve gate).
    Agents that never evolved are refreshed from the seed defaults, so roster/catalog
    changes still propagate on a plain reseed.
    """
    has_evolved = any(owner_type_by_id.get(sid) == "user" for sid in current_ids)
    return list(current_ids) if has_evolved else list(seed_ids)


async def main() -> None:
    trade = TradeClient()
    strategies = await trade.seed_strategies()
    by_name = {s["name"]: s["id"] for s in strategies}
    log.info("seeded %d house strategies", len(strategies))
    # Full id->owner_type map (incl. user/evolved strategies, which aren't in the
    # house catalog above) so the update path can preserve adopted evolutions.
    owner_type_by_id = {s["id"]: s.get("owner_type", "house")
                        for s in await trade.list_strategies()}

    async with SessionLocal() as session:
        for handle, name, thesis, voice, risk, strat_names, universe, cadence in ROSTER:
            # Persona.
            persona = (
                await session.execute(select(Persona).where(Persona.name == name))
            ).scalar_one_or_none()
            if persona is None:
                persona = Persona(
                    name=name, thesis=thesis, voice_style=voice, risk_temperament=risk,
                    system_prompt="", model_config_json={"model": None, "temperature": 0.4},
                )
                session.add(persona)
                await session.flush()

            # Agent + trade account.
            agent = (
                await session.execute(select(Agent).where(Agent.handle == handle))
            ).scalar_one_or_none()
            account_ref = f"acct-{handle}"
            await trade.create_account(account_ref, STARTING_CASH)
            strat_ids = [by_name[n] for n in strat_names if n in by_name]
            if agent is None:
                agent = Agent(
                    handle=handle, display_name=name, persona_id=persona.id,
                    account_ref=account_ref, strategy_ids=strat_ids,
                    tradable_universe=universe, decision_cadence_s=cadence,
                    risk_profile={"max_position_pct": 0.2, "per_tick_notional_pct": 0.15,
                                  "stop_loss_pct": 0.08, "take_profit_pct": 0.15},
                )
                session.add(agent)
            else:
                agent.strategy_ids = _reseed_strategy_ids(
                    list(agent.strategy_ids or []), strat_ids, owner_type_by_id)
                agent.tradable_universe = universe
                agent.decision_cadence_s = cadence
        await session.commit()
    log.info("seeded %d agents", len(ROSTER))


if __name__ == "__main__":
    asyncio.run(main())
