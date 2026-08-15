"""Separate daily token budget for the (expensive) evolution reasoner, so it can
never starve the trading loop's budget. Redis counter, UTC-day scoped, fail-open."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from neuromancing_shared.redisio import make_redis

from ..config import get_settings

log = logging.getLogger("neuromancing.evolve.budget")
_TTL_S = 60 * 60 * 72


def _day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _redis():
    return make_redis(get_settings().redis_url)


async def over_budget() -> tuple[bool, str]:
    s = get_settings()
    try:
        spent = int(await _redis().get(f"llm:evo:{_day()}") or 0)
    except Exception:  # noqa: BLE001 — fail open (never block on Redis error)
        return False, ""
    if spent >= s.llm_daily_token_budget_evolution:
        return True, "evolution_token_budget_exceeded"
    return False, ""


async def record_usage(tokens_in: int, tokens_out: int) -> None:
    try:
        r = _redis()
        key = f"llm:evo:{_day()}"
        await r.incrby(key, int(tokens_in) + int(tokens_out))
        await r.expire(key, _TTL_S)
    except Exception as e:  # noqa: BLE001
        log.warning("evolution budget record failed: %s", e)
