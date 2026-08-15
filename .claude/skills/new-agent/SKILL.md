---
name: new-agent
description: Scaffold a new AI trader agent (persona + strategies + tradable universe) for Neuromancing by adding a ROSTER entry in game-api seed, then reseed. Use when the user wants to add a new competitor/trader/agent to the game.
---

# Add a new trader agent

Agents are defined declaratively in `game-api/app/seed.py` as `ROSTER` tuples. The seed
run creates the agent's **persona**, a **trade account** (via trade-api), and the **agent**
row (strategies, universe, cadence, risk profile). Seeding is idempotent.

## Steps

1. **Choose strategies.** The agent references house strategies by **name** (from the
   `trade-api/app/strategies/catalog/*.yaml` catalog, seeded as the `trade.strategy` rows).
   Reuse existing ones or author a new one first with the `new-strategy` skill. Convention:
   one legacy `signal_fn`/`rule_dsl` strategy **plus** one `indicator_dsl` strategy (the
   latter is the slot the evolution loop, TODO #3, tunes).

2. **Add a ROSTER tuple** in `game-api/app/seed.py`. The shape is:
   ```python
   # (handle, display_name, thesis, voice, risk_temperament,
   #  [strategy names], universe, cadence_seconds)
   ("breakout-bella", "Breakout Bella",
    "Buy the breakout, ride the expansion.",
    "energetic, chart-obsessed", "aggressive",
    ["20-bar Momentum", "Bollinger Breakout + VWAP"],   # must match catalog names exactly
    _MOMENTUM + DEFAULT_CRYPTO, 90),                     # universe + tick cadence (seconds)
   ```
   - **handle**: unique, kebab-case (used in the URL `/agents/<handle>`).
   - **risk_temperament**: `aggressive | balanced | cautious` (flavor; the numeric
     `risk_profile` is set uniformly in the seed loop — edit there if this agent needs
     different caps).
   - **universe**: reuse a basket constant (`_MOMENTUM`, `_DEFENSIVE`, `_TREND`, `_VALUE`,
     `_DIVERSIFIED`) or a custom list. **Every symbol must be in the active `PRICE_UNIVERSE`**
     (`ingest/symbols/<name>.txt`) or its prices won't ingest. Append `DEFAULT_CRYPTO` for a
     24/7 crypto-active agent (drop it for an equity-only agent that sleeps off-hours).
   - **cadence_seconds**: how often the agent's decision tick fires.

3. **Reseed** to create the agent (idempotent; safe for existing agents):
   ```
   docker compose exec game-api uv run python -m app.seed
   ```
   > Evolution-safe: the reseed **preserves** any strategy other agents have already
   > adopted via the evolution loop (it only refreshes `strategy_ids` for agents that
   > never evolved — see `seed.py::_reseed_strategy_ids`). Adding a new agent never
   > reverts another agent's adopted strategy.

4. **Register the decision schedule** so the new agent actually ticks:
   ```
   docker compose exec temporal-worker uv run python -m app.workflows.schedules
   ```

5. **Verify**: `GET /agents/<handle>` shows the persona + strategies + universe, and the
   agent appears on the leaderboard and begins trading during market hours.

## Notes

- The seed loop sets a uniform `risk_profile` (`max_position_pct 0.2`,
  `per_tick_notional_pct 0.15`, `stop_loss_pct 0.08`, `take_profit_pct 0.15`). To give an
  agent distinct risk caps, branch on `handle` where the `Agent(...)` row is created.
- Personas start with an empty `system_prompt` (a generated management prompt is used);
  set `persona.system_prompt` later to sharpen the voice.
- To also give the agent a brand-new strategy, run the `new-strategy` skill first so the
  catalog name exists before you reference it here.
