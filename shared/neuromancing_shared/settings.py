"""Base settings shared across services. Services subclass this to add their own."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class BaseServiceSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://neuro:neuro@db:5432/neuromancing"
    database_url_sync: str = "postgresql+psycopg://neuro:neuro@db:5432/neuromancing"
    redis_url: str = "redis://redis:6379/0"

    temporal_host: str = "temporal:7233"
    temporal_namespace: str = "default"
    temporal_task_queue: str = "neuromancing"

    # Price platform (see neuromancing_shared/price_store.py).
    bardata_dir: str = "/data/bars"  # Parquet archive root (shared volume)
    price_hot_len: int = 500  # recent bars kept per (timeframe, symbol) in Redis
    price_timeframes: str = "1m,5m,1h,1d"  # comma-separated
    quote_ttl_s: int = 93600  # ~26h so quotes survive overnight/weekends

    @property
    def timeframes(self) -> list[str]:
        return [t.strip() for t in self.price_timeframes.split(",") if t.strip()]

    # Options v1 — synthetic Black-Scholes table knobs (see neuromancing_shared/options/).
    # vrp_mult/skew calibrated to Alpaca indicative IV (2026-08-18, 7 megacaps, 21–45 DTE,
    # near-money, 3128 contracts) via app.ingest.options_calibrate — re-run to recalibrate.
    # skew is regime-stable (equity put-skew is persistently steep); vrp is regime-dependent
    # (this reflects a calm window — near-term IV only ~5% over realized vol).
    options_risk_free_rate: float = 0.04
    options_div_yield: float = 0.0
    options_vrp_mult: float = 1.05      # implied ≈ realized × this (variance-risk premium)
    options_skew: float = 1.32          # equity put skew (0 = flat)
    options_term: float = 0.0           # term-structure slope (0 = flat; not fit in v1)
    options_expiries: str = "7,14,30,45,60"   # DTE ladder for the synthetic chain
    options_strikes: int = 8            # strikes each side of ATM
    options_strike_step_pct: float = 0.02     # ~2% spacing between strikes

    @property
    def options_expiry_ladder(self) -> list[int]:
        return [int(d.strip()) for d in self.options_expiries.split(",") if d.strip()]
