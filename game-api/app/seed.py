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

# Per-agent tradable universes are *curated subsets* of the ~150-symbol ingest
# universe (ingest fetches prices for all of it; each persona trades a coherent
# handful). Sector-diverse baskets keep the personas distinct and the LLM context
# small. All tickers below are members of ingest/symbols/diversified.txt.
_MOMENTUM = ["NVDA", "AMD", "TSLA", "AVGO", "META", "QCOM"]
_DEFENSIVE = ["KO", "PG", "JNJ", "WMT", "XOM", "PFE", "MRK", "VZ"]
_TREND = ["AAPL", "MSFT", "SPY", "QQQ", "JPM", "CAT", "HON", "GE"]
_VALUE = ["BAC", "WFC", "CVX", "HD", "LOW", "UNH", "ABBV", "CSCO", "TGT", "F"]
_DIVERSIFIED = ["AAPL", "JPM", "UNH", "XOM", "CAT", "LIN", "NEE", "PLD", "AMZN", "GOOGL"]

# (handle, display_name, thesis, voice, risk_temperament, [strategy names],
#  universe, cadence_seconds)
# Cadence rationale: crypto agents run 24/7, so they're slower to respect the
# daily token budget; equity-only agents sleep outside market hours (see
# market_hours), so they can afford to be snappier during their ~7h window.
ROSTER = [
    # Each agent runs its original strategy PLUS one thematically-matched
    # indicator_dsl (multi-indicator) strategy — see trade-api/app/strategies/catalog.
    ("momentum-mike", "Momentum Mike",
     "Ride strength. Winners keep winning until they don't.",
     "punchy, hype, lots of momentum metaphors", "aggressive",
     ["20-bar Momentum", "Momentum Breakout (Trend-Filtered)"],
     _MOMENTUM + DEFAULT_CRYPTO, 90),  # crypto 24/7
    ("contrarian-cara", "Contrarian Cara",
     "Buy fear, sell greed. Mean reversion pays the patient.",
     "wry, contrarian, calm", "cautious",
     ["RSI Mean Reversion", "Bollinger Mean Reversion"],
     _DEFENSIVE, 90),  # equity-only, sleeps off-hours
    ("crossover-cole", "Crossover Cole",
     "Trends are friends. Follow the moving averages.",
     "measured, systematic, trend-follower", "balanced",
     ["SMA 10/30 Crossover", "MACD Trend + RSI Pullback"],
     _TREND + DEFAULT_CRYPTO, 120),  # crypto 24/7
    ("dip-buyer-dana", "Dip Buyer Dana",
     "Great businesses on sale. Buy the oversold dips.",
     "value-investor, folksy, long-term", "cautious",
     ["RSI-DSL Dip Buyer", "Trend Dip Buyer"], _VALUE, 180),  # equity-only, patient
    ("diversified-dex", "Diversified Dex",
     "Many small edges beat one big bet.",
     "quant, understated, risk-aware", "balanced",
     ["20-bar Momentum", "RSI Mean Reversion", "Bollinger Breakout + VWAP"],
     _DIVERSIFIED + DEFAULT_CRYPTO, 120),  # crypto 24/7
]


async def main() -> None:
    trade = TradeClient()
    strategies = await trade.seed_strategies()
    by_name = {s["name"]: s["id"] for s in strategies}
    log.info("seeded %d house strategies", len(strategies))

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
                agent.strategy_ids = strat_ids
                agent.tradable_universe = universe
                agent.decision_cadence_s = cadence
        await session.commit()
    log.info("seeded %d agents", len(ROSTER))


if __name__ == "__main__":
    asyncio.run(main())
