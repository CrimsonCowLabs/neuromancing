# 12 · Decisions

An ADR-style record of the non-obvious choices in Neuromancing and the reasoning (and, where relevant, the incident) behind each. If you're wondering "why is it done *this* way?", it's probably here.

---

### D1 · Deterministic strategies generate signals; the LLM only manages

**Why.** The strategies are the deterministic core — they must be reproducible and backtestable, which an LLM is not. Making the LLM the *manager* (sizing, skip/close, risk, persona commentary) rather than the *signal source* gives shareable AI personalities while keeping correctness deterministic. Two agents can run the same strategy and differ only by persona.
**Consequence.** A guardrail (D8) mechanically enforces "buy must be backed by a current signal," so the model can't invent trades even if it tries. See [07](07-agent-brain.md).

### D2 · Temporal for orchestration (not arq/Celery)

**Why.** The correctness bar is exactly-once trading intent, resumable-after-crash game loop, and full auditability — Temporal's sweet spot. Deterministic workflow IDs give once-per-tick semantics for free; the alternative would push retry/idempotency/observability back onto us.
**Cost.** Operating a Temporal cluster (or paying for Cloud). Accepted deliberately. See [04](04-decision-tick.md), [06](06-orchestration.md).

### D3 · Internal simulation engine + a pluggable Broker seam

**Why.** A game needs *many* agents. Real brokerage accounts (Alpaca's Trading API caps at 3 paper + 1 live) don't scale and carry regulatory overhead. So we fill simulated orders against **real Alpaca prices** through a `Broker` interface whose `SimBroker` implementation can later be swapped for an `AlpacaBroker` — without game-api changing. Unlimited agents, no per-account cost, and a clean path to a real brokerage adapter later. See [05](05-trading-system.md).

### D4 · Ollama Cloud via the OpenAI-compatible endpoint, never Anthropic

**Why.** Project rule: never Anthropic with an API key. The proven pattern (from the `hl-froggy` system) is Ollama Cloud's **OpenAI-compatible endpoint** (`/v1`) through the `openai` SDK — our tool schema is already OpenAI function-calling shaped.
**Hard-won details.** A **mandatory per-call timeout** (45s): hl-froggy once froze for 22 hours on a stuck call with no timeout. Timeout must stay under the decide-activity timeout so the LLM fails before Temporal does (no zombie call). And **read model names from config, never hardcode** — we shipped `deepseek-v3.2:cloud`, which had been *retired* (HTTP 410); every decision silently fell back until the fallback counters/logs (D9) surfaced it. Now on `deepseek-v4-flash:0731`. See [07](07-agent-brain.md).

### D5 · SSE, not WebSockets, for realtime

**Why.** The spectator UI is server→client only (leaderboard, trade feed, Chirp). SSE is one-directional, auto-reconnecting, CDN-friendlier, and lets each web process hold **one Redis subscription for all clients** instead of per-client sockets. WebSockets are reserved for a later interactive phase. See [09](09-realtime.md).

### D6 · Decimal everywhere; NUMERIC in Postgres, never float

**Why.** Money math cannot inherit binary float drift. All money/qty are `Decimal` (`shared/neuromancing_shared/money.py`, precision 38, `ROUND_HALF_UP`, floats stringified on the way in) persisted as `NUMERIC`. The matching engine and portfolio math are property-tested for cash conservation. Indicators, by contrast, *are* float — signals aren't money. See [03](03-data-model.md), [05](05-trading-system.md).

### D7 · One Postgres cluster, two schemas, soft cross-service links

**Why.** The two services stay independent (each owns/migrates its own schema: `trade`, `game`) while sharing one cluster for operational simplicity. Cross-service references are **soft string refs** (`account_ref` ↔ `external_ref`), never cross-schema foreign keys, so the boundary could physically split later.
**Consequence.** One explicit cross-schema read remains, behind one helper (game-api reads `trade.equity_snapshot` for leaderboard/equity curves). The former trade→game read of `game.price_bar` is gone — bars moved to the shared `price_store` (Redis hot + Parquet), so trade-api no longer touches the game schema. All *mutation* of trade state from game-api goes through the HTTP seam with the service token, never a direct DB write. See [02](02-architecture.md).

### D8 · Guardrails as a pure, deterministic layer between model and execution

**Why.** An LLM will eventually propose an absurd or oversized order (or be nudged there by prompt injection via future news inputs). A pure, unit-tested `guardrails.validate` caps the blast radius: signal-backed buys only, concentration/notional caps, universe, and an equity-hours block. `trade-api`'s SimBroker adds an independent second layer (buying power, oversized sell, stale price) — defense in depth. See [07](07-agent-brain.md).

### D9 · Budget circuit-breaker + loud fallback observability

**Why.** The LLM is the only expensive per-tick component, and its failures are *silent* by nature (it just falls back to the deterministic manager). So: daily **token** budgets (per-agent 2M, global 20M) in Redis force the fallback when exceeded, and every non-expected fallback logs at **ERROR** ("agents are NOT LLM-driven") and bumps a counter. This came directly out of an adversarial review that noted the original silent-fallback would hide a retired model / expired key indefinitely (D4). See [07](07-agent-brain.md).

### D10 · Market-hours sleep + idle-skip

**Why.** Several orthogonal ways to make the system quiet and cheap — the model should only run when there's something genuinely new to manage:
- **Sleep** — equity-only agents are dormant outside `[open−30m, close+30m]` (Alpaca calendar, DST/holiday-aware, **fail-open** so a calendar hiccup never sleeps them forever). Crypto/mixed agents run 24/7 but drop equity signals after hours.
- **Idle-skip** — if a tick produces no signals, the LLM is never called and no post is written.
- **Actionability filter** *(added after observing the LLM run every tick)* — the idle-skip alone wasn't enough: two house strategies (RSI, momentum) were **state-based** and re-emitted the same signal every tick while their condition held, so an agent already at its position cap still had a non-empty `signals` set and kept invoking the LLM (decisions ≫ trades). Two fixes together: **(A)** `context.actionable_signals` drops signals that wouldn't change anything given holdings (buy with no cap room; exit with no position) — general, covers every strategy including the state-based `rule_dsl`; and **(B)** the RSI/momentum `signal_fn`s were made **event-driven** (fire only on the transition into the condition, like the crossover already did), removing the spam at the source. A uses the same `max_position_pct` as the guardrails (now wired from `agent.risk_profile`) so it never suppresses a valid trade.

Together these cut LLM cost dramatically and keep the Chirp feed to real activity rather than "no signal, holding" noise. See [04](04-decision-tick.md) and [05](05-trading-system.md).

### D11 · Graceful degradation: synthetic feed + deterministic fallback

**Why.** The whole game must run before any keys exist. With no `ALPACA_API_KEY`, `market-ingest` runs a seeded synthetic random-walk feed; with no `OLLAMA_API_KEY`, the LLM manager runs its deterministic fallback. Drop the keys into `.env` and it upgrades to live data + LLM management with no code change. See [08](08-market-data.md).

### D12 · Rejected orders are filtered from trader screens (and blocked at the source)

**Why.** Rejected orders (mostly equity attempts on stale prices after hours) cluttered the leaderboard/feed and were pure noise. They're now filtered from every trader screen (kept only in the decision audit for debugging). Better still, the equity-hours guardrail (D8) rejects those orders *before they're placed*, so after-hours mixed agents produce **zero** rejected equity rows rather than hiding them. See [06](06-orchestration.md), [10](10-web.md).

### D13 · Money-blind BFF

**Why.** The Next.js tier is internet-facing and, historically, the biggest attack surface (recent Next.js RCE/authz CVEs; npm supply-chain worms). It holds only a **read-scoped** token; the privileged order token lives only in server-side game-api/worker. A full compromise of the Node tier can't move funds. Reinforced by patched Next 16, `npm ci --ignore-scripts`, and SSRF-locked image config. See [10](10-web.md), [02](02-architecture.md).

### D14 · uv for Python

**Why.** `uv` for all Python dependency/venv management — committed lockfiles, `uv sync --frozen` for reproducible installs, `uv run` for commands. Each service has its own `pyproject.toml` + `uv.lock`; the shared package is a path dependency. `.env` is host-local and never committed (see `.env.example`).

### D15 · Deep agents: LangGraph *inside* Temporal, structured output, gated autonomy

**Why.** Making agents evolve their own strategies raised three non-obvious choices. **(1) LangGraph reconciled with Temporal, not competing:** the reasoning graph runs *inside* a heartbeating Temporal activity — Temporal owns scheduling + outer retry, LangGraph's Postgres checkpointer owns fine-grained step-resume — so a restart doesn't re-pay for expensive LLM/backtest steps, honoring the "Temporal for durability" rule (D3). **(2) Pydantic structured output, never JSON parsing:** the reasoner emits a flat, fully-typed `ProposedStrategy` via `with_structured_output(..., method="function_calling")` compiled to the canonical grammar — a recursive schema is unreliable on Ollama's OpenAI-compat endpoint, and hand-parsed JSON is fragile. **(3) Autonomous but gated:** the model *proposes*, a deterministic gate *decides* (out-of-sample, two walk-forward windows, margin/min-trades/drawdown), and the execution guardrails (D8) are untouched — so evolution can change *what* signals fire but never the risk ceiling, and any error/over-budget path is a no-op. Evolution is also silent on the social feed (no chirp about strategy changes). See [14 · Deep agents](14-deep-agents.md).

---

### A lesson: never mark a position at $0 for a missing quote

A trader appeared to lose ~75% in a day. It hadn't — it was a **mark-to-market artifact**. Redis quotes had a 1-hour TTL, so after the equity market closed the equity quotes expired; `positions_value` skipped any symbol without a mark and valued those held positions at **$0**, cratering displayed equity (the snapshot was even correctly flagged `is_stale`, but the leaderboard still showed the crater). Cash and positions were intact; realized P&L was tiny; no fake fills (the matcher's 90s staleness check protects fills). **The fix:** `app/marks.py::get_marks` now falls back to the last bar close via `price_store.get_last_bar` (Redis-first, Parquet on miss) when a quote is missing/expired (plus a 26h quote TTL as backstop), so a position is *always* valued at a real last-known price. **Takeaway:** a missing input must degrade to the last good value, never to zero — especially in a valuation path. See [08](08-market-data.md).

### A lesson worth its own entry: the synthetic→real basis contamination

When the live Alpaca feed was switched on, the leaderboard showed **+467%** returns. Not a bug — the *basis* was contaminated: positions had been opened at **synthetic** random-walk prices (synthetic AMD had wandered to ~$17) and were suddenly marked at **real** Alpaca prices (~$165), a ~9× phantom gain. The accounting was correct; the state was polluted. The fix — and the reason `game-api/app/reset.py` exists — is a clean wipe + reseed whenever the price feed's basis changes. **Takeaway:** switching a valuation source under open positions is a data-integrity event; reset, don't reconcile. See [06](06-orchestration.md).
