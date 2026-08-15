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
