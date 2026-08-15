---
name: new-strategy
description: Scaffold and validate a new deterministic `indicator_dsl` trading strategy for Neuromancing (a catalog YAML), then seed it. Use when the user wants to add a new house strategy, a signal recipe, or a multi-indicator strategy.
---

# Add a new strategy

Neuromancing strategies are **deterministic** and live as YAML in
`trade-api/app/strategies/catalog/NN-name.yaml`. The richest kind is `indicator_dsl`:
named indicators → named states → `buy_when`/`exit_when` rule groups. The grammar is the
single source of truth in `shared/neuromancing_shared/strategy_spec.py` and is
**fail-closed** — unknown names are rejected at validate time.

## Steps

1. **Pick the next catalog number** and create `trade-api/app/strategies/catalog/NN-<slug>.yaml`
   (e.g. `15-my-strategy.yaml`). `NN` just orders the files.

2. **Author the spec** using only the vocabulary below. Structure:
   ```yaml
   name: My Strategy Name          # unique; referenced from seed.py ROSTER by this name
   kind: indicator_dsl
   spec:
     type: momentum                # archetype (see ARCHETYPES)
     base_timeframe: 5m            # the timeframe "now" steps through
     indicators:                   # each: id + fn + params + source + timeframe
       - {id: trend, fn: macd, fast: 12, slow: 26, signal: 9, source: close, timeframe: 1h}
       - {id: mom,   fn: roc,  period: 20, source: close, timeframe: 5m}
       - {id: vol,   fn: atr,  period: 14, timeframe: 5m}
     states:                       # named level (op/value) or crossing (cross/value) tests
       uptrend: {indicator: trend, field: line, op: ">",  value: 0}
       thrust:  {indicator: mom,   cross: above, value: 0.02}
       liquid:  {indicator: vol,   op: ">",  value: 0}
     buy_when:  {all: [uptrend, thrust, liquid]}   # nestable all/any/not over state names
     exit_when: {any: [{indicator: mom, op: "<", value: -0.02}]}  # or inline conditions
     # optional: strength: {...}   # conviction weighting
   ```

3. **Validate before seeding** (fails fast, no DB):
   ```
   cd trade-api && uv run python ../scripts/validate_strategy.py app/strategies/catalog/NN-<slug>.yaml
   ```
   Fix any reported error until it prints `OK`.

4. **Seed it** so it exists in the `trade` schema (idempotent):
   ```
   docker compose exec game-api uv run python -m app.seed
   ```
   (Seeding also runs the catalog validator; a malformed file aborts the seed loudly.)

5. **(Optional) assign it to an agent** — add the strategy's `name` to that agent's
   strategy list in `game-api/app/seed.py` `ROSTER`, then reseed. To hand it to a *new*
   agent instead, use the `new-agent` skill.

## Vocabulary (fail-closed — only these names are allowed)

- **sources**: `close, open, high, low, hlc3, volume`
- **fns**: `sma, ema, rsi, roc, macd, bollinger, bbpercent, atr, vwap`
  - `sma/ema/roc` require `period`; `macd` uses `fast`/`slow`/`signal` (fast < slow).
- **dict-valued fn fields** (use `field:` in a state): `macd` → `line|signal|hist`;
  `bollinger` → `upper|middle|lower|percent`
- **timeframes**: `1m, 5m, 1h, 1d` (a strategy is backtested on its own indicators' tfs)
- **ops**: `< <= > >= == !=`  ·  **crossings** (`cross:`): `above, below`
- **archetypes** (`type:`): `trend, mean_reversion, momentum, breakout, trend_pullback, custom`

## Notes

- States are either a **level** test (`op` + `value`, optional `field`) or a **crossing**
  test (`cross: above|below` + `value`). Rule groups (`all`/`any`/`not`) take state names
  or inline condition dicts.
- Multi-timeframe is as-of aligned with **no lookahead** — a higher-tf bar only counts
  once closed. That's what makes the backtest track record reproducible.
- Legacy `kind: signal_fn` / `rule_dsl` strategies also exist (see `catalog/01-*.yaml`);
  they're validated at seed time, not by this skill's validator.
- Reference examples to copy from: `catalog/12-momentum-trend.yaml` (multi-tf macd+roc+atr),
  `catalog/13-bollinger-mean-reversion.yaml`, `catalog/11-bollinger-breakout.yaml`.
