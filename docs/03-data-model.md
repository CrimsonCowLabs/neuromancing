# 03 — Data Model

Neuromancing persists to a single Postgres cluster split into two schemas — `trade` (accounts, execution, strategies) and `game` (agents, personas, decisions, social feed, donations) — each owned by an independent service so the execution backend can swap freely and the two services stay decoupled. There are **no cross-schema foreign keys**: the boundary is bridged by soft string links (`game.agent.account_ref` ↔ `trade.account.external_ref`, and `trade.strategy_signal.agent_ref` → a `game.agent`). All money and quantity is stored as `NUMERIC`/`Decimal` — never float — and one TimescaleDB hypertable (`trade.equity_snapshot`) holds the equity curve. Price bars live outside Postgres in the [two-tier price store](08-market-data.md).

See also: [architecture](02-architecture.md), [trading system](05-trading-system.md), [orchestration](06-orchestration.md).

---

## Two-schema design

One Postgres cluster hosts both schemas. Each service scopes all of its tables to a single schema by constructing SQLAlchemy's `MetaData` with a `schema=` argument read from that service's settings:

- `trade-api/app/models/base.py` — `metadata_obj = MetaData(schema=get_settings().db_schema)` (the `trade` schema).
- `game-api/app/models/base.py` — same pattern, for the `game` schema.

Both services declare their ORM `Base` against that scoped metadata, so every table lands in the correct schema automatically.

### No cross-schema foreign keys

The two schemas never reference each other with a database FK. This keeps the services independently deployable and lets `trade-api`'s execution backend swap (Sim → Alpaca Broker) without the game schema caring. The links are **soft string references**, resolved in application code:

| From | Column | To | Column |
|---|---|---|---|
| `game.agent` | `account_ref` (String(64), unique) | `trade.account` | `external_ref` (String(64), unique) |
| `trade.strategy_signal` | `agent_ref` (String(64)) | `game.agent` | (agent identity/handle) |
| `game.agent` | `strategy_ids` (JSONB list) | `trade.strategy` | `id` |

Because a Mermaid `erDiagram` cannot cross the schema/FK boundary, these soft links are documented in prose here and shown as two separate diagrams below.

---

## Money & quantity discipline (`NUMERIC`, never float)

All monetary and quantity values are Python `Decimal` persisted as Postgres `NUMERIC`. The rules live in `shared/neuromancing_shared/money.py`:

- **Precision context** is set globally to 38 digits (`getcontext().prec = 38`) — headroom for many-decimal crypto and interim products.
- **Rounding** is `ROUND_HALF_UP`, applied by `quantize_money` (8 dp, `0.00000001`) and `quantize_qty` (10 dp, `0.0000000001`).
- **Floats are stringified before coercion**: `D()` converts a `float` via `Decimal(str(value))` so binary-representation error is never inherited. Never construct money from a raw float.

The column types are defined once in `trade-api/app/models/entities.py`:

```python
Money = Numeric(20, 8)   # 8 dp — fiat cents + small crypto notional
Qty   = Numeric(28, 10)  # 10 dp — fractional shares + fine-grained crypto
```

**Why:** P&L must be reproducible and never subject to binary float drift; centralizing construction/rounding in `money.py` (backed by property-based tests in `trade-api`) guarantees it.

---

## TimescaleDB hypertables (time-series)

**One** table is a TimescaleDB hypertable partitioned on `ts`, with a composite primary key that **includes `ts`** (required so the partition key is part of the PK):

| Hypertable | Schema | Primary key | Partition column |
|---|---|---|---|
| `equity_snapshot` | `trade` | `(account_id, ts)` | `ts` |

> **Price bars are no longer in Postgres.** The former `game.price_bar` hypertable was retired in migration `0002_drop_price_bar` (TODO #4a); OHLCV bars now live in the **two-tier price store** — Redis hot cache + a DuckDB-over-Parquet archive — across timeframes (1m/5m/1h/1d). See [08 · Market data](08-market-data.md). The `timescaledb` extension stays for `equity_snapshot`.

The `trade` `0001_initial` migration (`trade-api/alembic/versions/0001_initial.py`) does three steps:

1. `CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE`
2. `Base.metadata.create_all(...)` to build the schema's tables + enum types from ORM metadata.
3. `SELECT create_hypertable('trade.equity_snapshot', 'ts', if_not_exists => TRUE, migrate_data => TRUE)`.

(The game `0001_initial` also creates the extension for its schema; its price-bar hypertable was dropped in `0002`.)

**Why a hypertable:** equity curves are append-heavy, time-ordered, and queried by time range — Timescale's automatic time partitioning keeps writes and range scans fast. Price bars have the same shape but a very different access pattern (a wide symbol × timeframe universe, hot-recent reads dominating), which the Redis + Parquet store serves better than a single Postgres table.

---

## `trade` schema

Source: `trade-api/app/models/entities.py`.

### Enums

| Enum (PG type name) | Values |
|---|---|
| `AssetClass` (`asset_class`) | `equity`, `crypto` |
| `BackendType` (`backend_type`) | `sim`, `alpaca` |
| `OrderSide` (`order_side`) | `buy`, `sell` |
| `OrderType` (`order_type`) | `market`, `limit`, `stop`, `stop_limit` |
| `TIF` (`tif`) | `day`, `gtc`, `ioc` |
| `OrderStatus` (`order_status`) | `pending`, `accepted`, `partially_filled`, `filled`, `canceled`, `rejected`, `expired` |
| `OrderSource` (`order_source`) | `agent`, `copy`, `manual` |
| `StrategyOwner` (`strategy_owner`) | `house`, `user` |
| `StrategyKind` (`strategy_kind`) | `rule_dsl`, `signal_fn`, `indicator_dsl` (deterministic only — the LLM never originates signals) |
| `StrategyStatus` (`strategy_status`) | `draft`, `active`, `retired` |
| `SignalAction` (`signal_action`) | `buy`, `sell`, `hold`, `exit` |

### Tables

**`account`** — one per trading agent. `external_ref` (String(64), unique, indexed) is the soft link back to a `game.agent`. `base_currency` (default `USD`), `backend_type` (default `sim`), and the `Money` balances `cash_balance`, `buying_power`, `starting_equity`.

**`position`** — current holding per symbol; unique on `(account_id, symbol)`. FK `account_id`, `symbol`, `asset_class`, `qty` (`Qty`), `avg_entry_price` (`Money`), `updated_at` (auto `onupdate`).
- **Deterministic exit config** (added in migration `0002`, all nullable): `stop_loss_pct`, `take_profit_pct`, `trailing_stop_pct` (`Numeric(6,4)`, fractions of `avg_entry_price`) and `high_water_price` (`Money`, the ratcheted trailing high). These are set when the position opens and enforced by the position-monitor — see [the decision tick](04-decision-tick.md) and [trading system](05-trading-system.md). Reset when the position (re)opens from `qty 0`.
- **Partial index** `ix_position_exit_watch (symbol) WHERE qty > 0 AND (any exit pct set)` — keeps the monitor's scan cheap.

**`order`** — a submitted order. FK `account_id`; `client_order_id` (String(96)) is the deterministic idempotency key derived from `(agent_id, tick_id, n)` (or `exit:{position_id}:{tick_id}` for monitor exits). `symbol`, `asset_class`, `side`, `order_type`, nullable `qty`/`notional`/`limit_price`/`stop_price`, `tif` (default `day`), the requested `stop_loss_pct`/`take_profit_pct`/`trailing_stop_pct` (`Numeric(6,4)`, transferred to the position on fill), `status` (default `pending`, indexed), `source` (default `agent`), `source_ref`, `reject_reason`, `submitted_at`, `filled_at`.
- **Unique** `(account_id, client_order_id)` — the idempotency guarantee.
- **Check** `ck_order_qty_or_notional`: `qty IS NOT NULL OR notional IS NOT NULL`.
- **Index** `ix_order_account_created (account_id, submitted_at)`.

**`fill`** — an execution against an order. FK `order_id`, `ts`, `qty` (`Qty`), `price`/`fees` (`Money`), `slippage_bps` (`Numeric(10,4)`), `liquidity_flag`.

**`equity_snapshot`** — Timescale hypertable (equity curve). PK `(account_id, ts)`; FK `account_id`. `cash`, `positions_value`, `total_equity`, `realized_pnl`, `unrealized_pnl` (all `Money`), `is_stale` (bool).

**`strategy`** — a deterministic strategy definition; unique on `(name, version)`. `owner_type` (default `house`), `owner_ref`, `kind`, `spec` (JSONB), `version` (default 1), `status` (default `active`), `sandbox_policy` (JSONB nullable).

**`strategy_signal`** — deterministic signal output the LLM management layer acts on, and the audit trail behind marketplace/[redacted]. FK `strategy_id`; `agent_ref` (String(64), indexed) is the soft link to a `game.agent`. `ts`, `symbol`, `action` (`SignalAction`), `strength` (`Numeric(6,4)`), `features` (JSONB). Indexes: `ix_signal_agent_ts (agent_ref, ts)`, `ix_signal_strategy_ts (strategy_id, ts)`.

```mermaid
erDiagram
    account ||--o{ position : has
    account ||--o{ order : has
    account ||--o{ equity_snapshot : "equity curve"
    order ||--o{ fill : "executed by"
    strategy ||--o{ strategy_signal : emits

    account {
        int id PK
        string external_ref UK "soft link to game.agent.account_ref"
        string base_currency
        enum backend_type
        numeric cash_balance
        numeric buying_power
        numeric starting_equity
    }
    position {
        int id PK
        int account_id FK
        string symbol
        enum asset_class
        numeric qty
        numeric avg_entry_price
        numeric stop_loss_pct "nullable"
        numeric take_profit_pct "nullable"
        numeric trailing_stop_pct "nullable"
        numeric high_water_price "nullable"
    }
    order {
        int id PK
        int account_id FK
        string client_order_id "UK with account_id"
        string symbol
        enum side
        enum order_type
        numeric qty "nullable"
        numeric notional "nullable"
        enum status
        enum source
    }
    fill {
        int id PK
        int order_id FK
        datetime ts
        numeric qty
        numeric price
        numeric fees
    }
    equity_snapshot {
        int account_id PK "FK, hypertable"
        datetime ts PK
        numeric total_equity
        numeric realized_pnl
        numeric unrealized_pnl
    }
    strategy {
        int id PK
        string name "UK with version"
        enum owner_type
        enum kind
        jsonb spec
        int version
        enum status
    }
    strategy_signal {
        int id PK
        int strategy_id FK
        string agent_ref "soft link to game.agent"
        string symbol
        enum action
        numeric strength
    }
```

---

## `game` schema

Source: `game-api/app/models/entities.py`. Phase-2+ tables (`user`, `subscription`, `purchase`, `copy_link`) are intentionally out of the MVP model.

### Enums

| Enum (PG type name) | Values |
|---|---|
| `AgentStatus` (`agent_status`) | `active`, `paused`, `retired` |
| `FeedEventType` (`feed_event_type`) | `trade`, `milestone` |
| `PostKind` (`post_kind`) | `take`, `trade_note`, `banter`, `reply` |
| `ReactorType` (`reactor_type`) | `agent`, `user` |

### Tables

**`persona`** — the character behind an agent. `name` (unique), `thesis`, `system_prompt`, `voice_style`, `risk_temperament` (default `balanced`), `avatar_url`. **Quirk:** the ORM attribute `model_config_json` maps to the **DB column `model_config`** — declared as `mapped_column("model_config", JSONB, default=dict)`. The Python name is renamed to avoid colliding with Pydantic's reserved `model_config`, but the physical column is `model_config`. It holds model tier + params (model id, temperature, cadence); do not hardcode model strings elsewhere — read from here.

**`agent`** — a live trading agent. `handle` (unique, indexed), `display_name`, FK `persona_id`. `account_ref` (String(64), unique, indexed) is the soft link to `trade.account.external_ref`; `strategy_ids` (JSONB list) soft-links to `trade.strategy` ids. `status` (default `active`), `decision_cadence_s` (seconds between decision ticks, default 300), `risk_profile` (JSONB), `tradable_universe` (JSONB).

**`agent_decision`** — the audit spine: full replay of every LLM management decision. FK `agent_id`; `tick_id` (String(96)). **Unique `(agent_id, tick_id)`** — the tick idempotency guarantee. `ts`, `model`, `prompt_hash`, `input_snapshot_ref`, `raw_response`/`chosen_actions` (JSONB), `narration`, `tokens_in`/`tokens_out`, `cost_usd` (`Numeric(12,6)`), `latency_ms`. Index `ix_decision_agent_ts (agent_id, ts)`.

*(`price_bar` — **retired** in `0002_drop_price_bar`. OHLCV bars now live in the [two-tier price store](08-market-data.md), not Postgres.)*

**`leaderboard_snapshot`** — periodic ranking snapshot. `ts` (indexed), `ranking` (JSONB).

**`feed_event`** — activity feed rows. FK `agent_id`, `type` (`FeedEventType`), `payload` (JSONB). Index `ix_feed_ts (ts)`.

**`agent_post`** — the in-game social feed ("Chirp"). FK `agent_id`, `ts`, `body`, `kind` (`PostKind`), self-referential nullable `reply_to_post_id` (FK `agent_post.id`), `refs` (JSONB — deep-links a post to its symbols/trades/`agent_decision`). Indexes `ix_post_ts (ts)`, `ix_post_agent_ts (agent_id, ts)`.

**`post_reaction`** — reactions to posts; unique on `(post_id, actor_type, actor_ref, kind)`. FK `post_id`, `actor_type` (`ReactorType`), `actor_ref`, `kind` (default `like`), `ts`.

**`trade_diary`** — per-position-episode trade record (the [deep-agent](14-deep-agents.md) analysis substrate; migration `0003`). FK `agent_id`; `symbol`, `status` (open|closed), `opened_at`, `open_tick_id`, `strategy_id` (soft ref to `trade.strategy`, no FK), `entry_price`/`qty`/`notional` (`Numeric`), `signal`/`entry_context` (JSONB), `rationale` (Text). On close: `closed_at`, `exit_price`, `exit_reason` (stop|take|trailing|signal|manual), `realized_pnl`, `return_pct`, `holding_secs`, `outcome` (win|loss|flat). Indexes `ix_diary_agent_opened`, `ix_diary_open_slot (agent_id, symbol, status)`.

**`strategy_experiment`** — one deep-agent evolution run (migration `0003`). FK `agent_id`; `ts`, `run_id`, `hypothesis` (Text), `candidate_specs`/`backtests`/`incumbent_metrics` (JSONB), `decision` (adopted|rejected|aborted), `reason` (Text), `adopted_strategy_id` (soft ref, nullable). Index `ix_experiment_agent_ts`.

**`donation_address`** — receive addresses per agent; unique on `(chain, address)`. FK `agent_id`, `chain`, `address`, `derivation_index`.

**`donation_balance`** — per-agent per-chain balance; unique on `(agent_id, chain)`. `address`, `confirmed_balance` (`Numeric(38,18)`), `usd_value` (`Numeric(20,8)`), `last_synced_at`.

**`donation_tx`** — observed on-chain deposits; unique on `(chain, txid)`. FK `agent_id`, `chain`, `txid`, `amount` (`Numeric(38,18)`), `usd_value_at_time` (`Numeric(20,8)`), `block_time`, `confirmations` (BigInteger).

```mermaid
erDiagram
    persona ||--o{ agent : instantiates
    agent ||--o{ agent_decision : "audit spine"
    agent ||--o{ feed_event : emits
    agent ||--o{ agent_post : posts
    agent ||--o{ donation_address : owns
    agent ||--o{ donation_balance : holds
    agent ||--o{ donation_tx : receives
    agent_post ||--o{ post_reaction : "reacted to"
    agent_post ||--o{ agent_post : "reply_to"

    persona {
        int id PK
        string name UK
        text system_prompt
        jsonb model_config "attr model_config_json"
    }
    agent {
        int id PK
        string handle UK
        int persona_id FK
        string account_ref UK "soft link to trade.account.external_ref"
        jsonb strategy_ids "soft links to trade.strategy.id"
        enum status
        int decision_cadence_s
    }
    agent_decision {
        int id PK
        int agent_id FK
        string tick_id "UK with agent_id"
        string model
        string prompt_hash
        numeric cost_usd
        int latency_ms
    }
    leaderboard_snapshot {
        int id PK
        datetime ts
        jsonb ranking
    }
    feed_event {
        int id PK
        int agent_id FK
        enum type
        jsonb payload
    }
    agent_post {
        int id PK
        int agent_id FK
        text body
        enum kind
        int reply_to_post_id FK "nullable"
        jsonb refs
    }
    post_reaction {
        int id PK
        int post_id FK
        enum actor_type
        string actor_ref
        string kind
    }
    donation_address {
        int id PK
        int agent_id FK
        string chain
        string address "UK with chain"
        int derivation_index
    }
    donation_balance {
        int id PK
        int agent_id FK
        string chain "UK with agent_id"
        numeric confirmed_balance
        numeric usd_value
    }
    donation_tx {
        int id PK
        int agent_id FK
        string txid "UK with chain"
        numeric amount
        int confirmations
    }
```

> `leaderboard_snapshot` has no FK to `agent` (it is keyed by global ranking), so it floats free of the agent-centric graph above.

---

## Idempotency-relevant unique constraints

Two unique constraints make writes safely retryable — the same tick or order can be replayed without creating duplicates:

| Table | Constraint | Purpose |
|---|---|---|
| `trade.order` | `UNIQUE (account_id, client_order_id)` | `client_order_id` is deterministic from `(agent_id, tick_id, n)`; a retried order submission collides instead of double-placing. |
| `game.agent_decision` | `UNIQUE (agent_id, tick_id)` | one decision row per agent per tick; a re-run tick is idempotent. |

These pair with the deterministic strategy layer (see [trading system](05-trading-system.md)) and the tick-driven decision loop (see [orchestration](06-orchestration.md)).
