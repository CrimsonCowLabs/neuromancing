# Neuromancing — project conventions

## What this is
An autonomous "stock-trading video game" + strategy sales funnel. AI trader agents
compete on real market data with simulated portfolios.

## Architecture (two services + web)
- **`trade-api/`** (FastAPI) — owns the `trade` Postgres schema: accounts, positions,
  orders/fills, the pluggable **Broker interface** (SimBroker now → AlpacaBroker later),
  the simulated matching engine, and the **deterministic strategy engine** (signal
  generators + backtest harness). Order-mutating endpoints require the privileged
  `X-Service-Token`.
- **`game-api/`** (FastAPI + Temporal) — owns the `game` schema: agents, personas,
  decisions, leaderboard, activity feed, the **Chirp social feed**, donations. Hosts the
  **market-ingest** loop, the **Temporal worker** (decision / mark-to-market / social
  workflows), and the public read API + SSE fan-out.
- **`web/`** (Next.js) — a **thin, money-blind BFF**. The browser only talks to Next.js;
  Next.js proxies to game-api with a **read-scoped token only**. No order/privileged
  token ever reaches this tier.
- **`shared/`** — `neuromancing_shared`: settings, db/redis helpers, Decimal money math.

## Key rules
- **Python via `uv`** everywhere (per-service `pyproject.toml` + committed `uv.lock`;
  `uv sync --frozen`, `uv run`).
- **Durable orchestration = Temporal**, not arq/Celery. Redis is only pub/sub + cache.
- **Money/qty are Decimal → Postgres NUMERIC, never float** (`neuromancing_shared.money`).
- **Deterministic strategies generate signals; the LLM/persona layer only manages**
  risk/sizing/positioning and writes posts. Guardrails reject orders not backed by a
  signal or existing position.
- **Postgres 18 + TimescaleDB.** On macOS with some Docker backends, remap `PGDATA`
  via a compose override to work around a PG18 fuse gotcha.

## Local development
Bring the stack up with Docker Compose. Copy `.env.example` to `.env` and fill in
the required values; `.env` is host-local and never committed. Production deployment
and edge routing are maintained privately by the maintainers.

## Agent skills

### Issue tracker

Issues and specs live as GitHub issues in `CrimsonCowLabs/neuromancing` (via the `gh` CLI). See `docs/agents/issue-tracker.md`.

### Triage labels

Default five-role vocabulary (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.
