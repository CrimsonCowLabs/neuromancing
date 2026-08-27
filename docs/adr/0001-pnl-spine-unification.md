# ADR-0001: Unify the P&L spine — backtest replays through `apply_fill`

- **Status:** accepted
- **Date:** 2026-08-27
- **Issue:** CrimsonCowLabs/neuromancing#1

## Context

The deterministic strategy engine had **two** implementations of position & P&L
accounting that had silently drifted:

- The **live spine** `apply_fill` (`trade-api/app/engine/portfolio.py`): signed, Decimal,
  fee-aware, no-flip; property-tested; used by the SimBroker for every real fill.
- The **backtest harness** `_Book` (`trade-api/app/strategies/backtest.py`):
  re-implemented the same four signed transitions by hand in float, with its own all-in
  sizing, a single flat cost, and **no exit settlement at all** — it exited only on a
  strategy signal, never on the stop / take-profit / trailing exits a live position carries.

The evolution **adoption gate** promotes a construct's strategy to live trading based on the
backtest number. It was therefore optimizing a figure produced by a parallel accounting
that did not match what the live engine books — most importantly, it never saw a position
get stopped out. (The performance digest's predicted-vs-realized calibration was built
specifically to paper over this drift.)

## Decision

Make the backtest **replay through the live spine**. `apply_fill` becomes the single
accounting authority for both live fills and backtests; `_Book`'s hand-rolled transition
math is deleted. `_Book` shrinks to a thin adapter owning only what the spine does not:
sizing, per-side cost, the equity curve, and the **exit replay** — which calls the same
`evaluate_exit` a live position receives, **exit-first** on each bar.

Scope is **gate-first / rank-faithful**, not absolute-P&L-faithful: a mechanism is included
only when its absence changes *which* strategy wins.

- **Sizing:** fixed notional = `alloc_pct × starting_cash` (default 0.20), non-compounding;
  a long falls back to available cash in a deep drawdown.
- **Cost:** flat per-side `cost_bps` (default 5) handed to the spine as `fees`.
- **Exit replay:** `evaluate_exit` with `stop_loss_pct` (mandatory 0.08 floor),
  `take_profit_pct`, `trailing_stop_pct`; shorts mirror via the qty sign.
- **Win counting:** driven by the spine's `realized_pnl > 0`.
- **Numeric type:** Decimal for accounting; indicators stay float.
- The evolution gate threads each construct's **risk profile** into the backtest so a
  candidate is measured under the discipline it would trade under.

**Explicitly out of scope:** live slippage tiers / per-asset-class fees / quote-based fills
(rank-neutral), full guardrail sizing (cancels out of the gate's relative comparison), and a
conformance test pinning two implementations (superseded — there is now one implementation).

## Consequences

- Drift between live and backtest accounting becomes **structurally impossible**: one module
  to fix a P&L bug in, one place to test it.
- The gate now ranks candidates on the accounting the engine actually runs, including
  stop-outs the old backtest never saw.

### Required follow-up (not part of this change)

Bounded sizing (~⅕ of the old all-in) plus real stop-outs will **sharply lower absolute
backtest returns**. The adoption gate's thresholds (`GateConfig`:
`evolution_return_margin` / `evolution_min_trades` / `evolution_max_dd`) were tuned against
the old, optimistic numbers, so **adoption may stall until they are recalibrated**. Land the
accounting change first, observe the new distribution in production, then retune — do not
tune blind. Tracked as a separate follow-up.
