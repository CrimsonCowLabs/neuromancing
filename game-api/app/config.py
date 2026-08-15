from __future__ import annotations

from functools import lru_cache

from neuromancing_shared.settings import BaseServiceSettings


class Settings(BaseServiceSettings):
    # DB schema this service owns.
    db_schema: str = "game"

    # Internal seam to trade-api.
    trade_api_url: str = "http://trade-api:8000"
    trade_api_service_token: str = "change-me-privileged-token"
    # Read-scoped token the thin Next.js BFF is allowed to present to game-api.
    game_api_public_token: str = "change-me-read-token"

    # Alpaca (market data at MVP).
    alpaca_api_key: str = ""
    alpaca_api_secret: str = ""
    alpaca_data_feed: str = "iex"

    # Price platform (TODO #4a). Active equity universe name -> ingest/symbols/*.txt.
    price_universe: str = "diversified"
    # Per-timeframe backfill depth in days ("tf:days,..."). IEX free history is
    # limited, so 1m depth is modest; deep multi-year history is a #3/#4b batch.
    price_backfill_days: str = "1m:5,5m:20,1h:120,1d:365"
    # Per-timeframe live poll cadence in seconds ("tf:seconds,...").
    price_poll_seconds: str = "1m:60,5m:300,1h:3600,1d:86400"
    # Parquet durability flush + light gap-fill cadence (seconds).
    price_flush_seconds: int = 300
    price_gapfill_seconds: int = 1800
    # Max symbols per batched Alpaca bars request.
    price_batch_size: int = 100

    def _tf_map(self, raw: str) -> dict[str, int]:
        out: dict[str, int] = {}
        for part in raw.split(","):
            part = part.strip()
            if not part or ":" not in part:
                continue
            tf, val = part.split(":", 1)
            out[tf.strip()] = int(val)
        return out

    @property
    def backfill_days(self) -> dict[str, int]:
        return self._tf_map(self.price_backfill_days)

    @property
    def poll_seconds(self) -> dict[str, int]:
        return self._tf_map(self.price_poll_seconds)

    # Ollama Cloud (LLM management layer). We never use Anthropic.
    # Accessed via the OpenAI-COMPATIBLE endpoint (…/v1) — the pattern proven in
    # the hl-froggy trading system. OLLAMA_HOST carries the /v1 base URL.
    ollama_host: str = "https://ollama.com/v1"
    ollama_api_key: str = ""
    ollama_model: str = "deepseek-v4-flash:0731"
    # Hard timeout per LLM call — a stuck Ollama Cloud request hung hl-froggy's
    # loop for 22h without one. MUST stay < the decide-activity timeout (75s) so
    # the openai call fails before Temporal times out the activity (no zombie call).
    ollama_timeout_s: float = 45.0
    # Cap concurrent Ollama calls in a worker (backpressure vs 429s).
    ollama_max_concurrency: int = 4
    # Optional USD pricing for cost tracking (per 1M tokens). Leave 0 if unknown —
    # the enforced circuit-breaker is token-based, so it works without pricing.
    ollama_price_per_mtok_in: float = 0.0
    ollama_price_per_mtok_out: float = 0.0
    # Daily token budgets (the enforced circuit-breaker). Over budget -> forced
    # fallback + loud log, so runaway/retry-storm spend is bounded.
    llm_daily_token_budget_agent: int = 2_000_000
    llm_daily_token_budget_global: int = 20_000_000

    # ---- Deep agents / strategy evolution (TODO #3) ----
    evolution_enabled: bool = False  # off until reasoning model + keys are set
    # Strong reasoning model for design/critique; empty -> falls back to ollama_model.
    ollama_reasoning_model: str = ""
    ollama_reasoning_timeout_s: float = 120.0  # runs in the heartbeating evolve activity
    llm_daily_token_budget_evolution: int = 5_000_000
    evolution_schedule_seconds: int = 3600  # gate fires hourly; the ≥24h guard spaces runs
    evolution_min_hours_between: int = 24
    evolution_crypto_utc_hour: int = 0  # daily boundary for 24/7 (crypto-only) agents
    evolution_min_diary_episodes: int = 10  # cold-start guard
    evolution_min_trades: int = 5  # min backtest trades to trust a candidate
    evolution_return_margin: float = 0.02  # candidate must beat incumbent by this
    evolution_max_dd: float = 0.40  # reject candidates over this max drawdown
    evolution_backtest_symbols: int = 6  # sample cap per run

    @property
    def reasoning_model(self) -> str:
        return self.ollama_reasoning_model or self.ollama_model
    # Advisory USD budgets (surfaced for dashboards).
    llm_daily_budget_usd: float = 25.0
    llm_agent_daily_budget_usd: float = 2.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
