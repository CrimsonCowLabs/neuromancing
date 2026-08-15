"""LLM spend circuit-breaker + observability, backed by Redis.

Enforces per-agent + global DAILY TOKEN budgets (deterministic, needs no pricing)
and records fallback occurrences so a retired model / expired key / bug SCREAMS
instead of silently degrading every agent to the deterministic manager.

Keys (UTC-day scoped, auto-expiring):
  llm:tok:{day}:agent:{handle}   running token total for an agent
  llm:tok:{day}:global           running token total across all agents
  llm:fallback:{day}:{reason}    fallback counter by reason
  llm:fallback:{day}:total       total fallbacks
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from neuromancing_shared.redisio import make_redis

from ..config import get_settings

log = logging.getLogger("neuromancing.llm.budget")
_redis = make_redis(get_settings().redis_url)
_TTL = 60 * 60 * 72  # keep 3 days of counters


def _day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


async def _get_int(key: str) -> int:
    try:
        v = await _redis.get(key)
        return int(v) if v else 0
    except Exception:  # noqa: BLE001
        return 0


async def over_budget(handle: str) -> tuple[bool, str]:
    """Return (True, reason) if the agent or the global daily token budget is
    already exhausted — the caller should then force the deterministic fallback."""
    s = get_settings()
    day = _day()
    agent_used = await _get_int(f"llm:tok:{day}:agent:{handle}")
    if agent_used >= s.llm_daily_token_budget_agent:
        return True, "agent_token_budget_exceeded"
    global_used = await _get_int(f"llm:tok:{day}:global")
    if global_used >= s.llm_daily_token_budget_global:
        return True, "global_token_budget_exceeded"
    return False, ""


async def record_usage(handle: str, tokens_in: int, tokens_out: int) -> None:
    total = int(tokens_in) + int(tokens_out)
    if total <= 0:
        return
    day = _day()
    try:
        pipe = _redis.pipeline()
        for key in (f"llm:tok:{day}:agent:{handle}", f"llm:tok:{day}:global"):
            pipe.incrby(key, total)
            pipe.expire(key, _TTL)
        await pipe.execute()
    except Exception as e:  # noqa: BLE001
        log.warning("failed to record token usage: %s", e)


async def record_fallback(model: str, reason: str) -> None:
    """Count a fallback and log LOUDLY so silent degradation is impossible to miss."""
    day = _day()
    try:
        pipe = _redis.pipeline()
        for key in (f"llm:fallback:{day}:{reason}", f"llm:fallback:{day}:total"):
            pipe.incrby(key, 1)
            pipe.expire(key, _TTL)
        await pipe.execute()
    except Exception as e:  # noqa: BLE001
        log.warning("failed to record fallback: %s", e)
    # "no_api_key" is an expected headless-mode state; everything else is a problem.
    if reason == "no_api_key":
        log.info("LLM fallback (%s) model=%s", reason, model)
    else:
        log.error("LLM FALLBACK reason=%s model=%s — agents are NOT LLM-driven", reason, model)


def estimate_cost_usd(tokens_in: int, tokens_out: int) -> float:
    s = get_settings()
    return round(
        tokens_in / 1_000_000 * s.ollama_price_per_mtok_in
        + tokens_out / 1_000_000 * s.ollama_price_per_mtok_out,
        6,
    )
