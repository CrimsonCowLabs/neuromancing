# Neuromancing — domain context

The single-context domain glossary (see `docs/agents/domain.md`). One entry per term
that a reader must get right to reason about the system; ADRs live in `docs/adr/`.

This file is deliberately started small, seeded by issue #1 (the P&L-spine unification).
Add terms as they earn their place — a name goes here when two parts of the codebase must
agree on its meaning.

## Terms

### Fill-accounting spine
The single authority for turning a fill into cash + a new position + realized P&L:
`apply_fill` in `trade-api/app/engine/portfolio.py`. Signed net-position model (qty > 0
long, < 0 short, 0 flat), pure Decimal, fee-aware, and **no-flip** (a fill that would
close past flat is capped at the held qty). It is property-tested and is the trusted
accounting for **both** live fills (the SimBroker books every real fill through it) **and**
backtests (the harness replays through it). Live and backtest deliberately share this ONE
module so their accounting cannot silently drift — a change to fee handling, short
accounting, or the no-flip rule lands in exactly one place. See ADR-0001.

### Exit settlement
The deterministic stop-loss / take-profit / trailing decision a position carries,
evaluated by `evaluate_exit` in `trade-api/app/engine/matching.py`. Long and short mirror
(short's stop is above entry, target below, trailing off the low); the favorable extreme
since entry is the ratcheted `high_water`. Live positions are settled on the monitor tick,
independently of decision ticks; the backtest replays the same `evaluate_exit`
**exit-first** on each bar (before the strategy signal), so a position a live stop would
have closed cannot be rescued by a later signal on the same bar.

### Backtest (gate-first / rank-faithful)
The strategy harness (`backtest` / `backtest_multi` in
`trade-api/app/strategies/backtest.py`) replays a deterministic strategy over historical
bars through the fill-accounting spine + exit settlement. Its scope is **rank-faithful, not
absolute-P&L-faithful**: it models a mechanism only when its absence would change *which*
strategy the evolution adoption gate picks — fixed-notional sizing, a per-side churn cost,
and exit settlement — and deliberately omits rank-neutral live machinery (slippage tiers,
per-asset-class fees, quote-based fills). The adoption gate (game-api evolution loop) ranks
candidates on this number, so it must reflect what the live engine actually books.

### Risk profile
A construct's (agent's) free-form JSONB discipline in game-api
(`max_position_pct`, `stop_loss_pct`, `take_profit_pct`, `trailing_stop_pct`, …). The
evolution gate threads it into each candidate backtest so a candidate is measured under the
exact sizing + exit discipline it would trade under (`max_position_pct` → the backtest's
`alloc_pct`; the stop/take-profit/trailing → the exit config). Absent keys fall back to the
harness's live-like defaults (0.20 alloc, a mandatory 0.08 stop, 5 bps/side).

### Feed freshness
The age of the freshest **crypto** quote — the system's single answer to "is market data
still flowing?" (`crypto_feed_age` in `game-api/app/ingest/health.py`). Crypto-only on
purpose: crypto trades 24/7, so silence is unambiguous, whereas an equity quote falls silent
every night because the *market closed*, not because anything broke. One signal against one
threshold serves every consumer — the web "market data stale" banner, the leaderboard's
`data_stale`, and trade-api's mark staleness. Presence is not freshness: quotes are cached
~26h, so a dark feed still prices the whole book at frozen numbers.

### Loop liveness
Whether market-ingest's **event loop is still scheduling work**, evidenced by a heartbeat an
ordinary task stamps as it runs. Deliberately a separate signal from feed freshness because
the two have different cures — a stalled feed wants a reconnect, a dead loop wants a restart.
A process can look healthy by every other measure (PID up, container up, quotes still in
Redis) while its loop schedules nothing at all.

### Wedged loop
The failure mode where the ingest process is alive but its event loop has stopped scheduling:
no heartbeat, no writes, and no watchdog, since every in-process supervisor is itself a task
on the frozen loop. It is therefore the one condition nothing in-process can repair, and is
escalated **out of process** — the container healthcheck runs as a separate process, so it
survives the wedge and can restart the container. Reported as `WEDGED`, as distinct from
`STALE` (feed stalled, loop fine). A supervisor must not share a failure domain with what it
supervises.

### Mark staleness
Whether a snapshot's valuation should be trusted (`marks_are_stale` in
`trade-api/app/engine/portfolio.py`, persisted as `EquitySnapshot.is_stale`). Two ways to
lose that trust: a held symbol had no mark at all, or feed freshness says the pricing
pipeline was dark when the snapshot was computed. Notably it is **not** the age of that
snapshot's own marks — equity marks are hours old every night by design, so judging a
valuation on them would flag nearly every out-of-hours snapshot and the flag would stop
carrying information.
