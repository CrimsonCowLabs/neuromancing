from __future__ import annotations

from functools import lru_cache

from neuromancing_shared.settings import BaseServiceSettings


class Settings(BaseServiceSettings):
    # Privileged token required for order-mutating endpoints. The thin BFF must
    # never hold this — only server-side game-api/temporal-worker callers do.
    trade_api_service_token: str = "change-me-privileged-token"
    # DB schema this service owns.
    db_schema: str = "trade"
    # Reg-T-style collateral a short position reserves against buying power
    # (entry_price * |qty| * this). 1.5 ≈ 150% initial margin.
    short_collateral_mult: float = 1.5


@lru_cache
def get_settings() -> Settings:
    return Settings()
