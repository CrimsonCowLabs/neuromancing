"""When may an agent evolve? — pure trigger logic (unit-tested).

Gate = (a trading session just ended for this agent) AND (≥24h since last run) AND
(enough diary history). Equity agents fire after today's session close; crypto-only
agents fire once/day at a configured UTC boundary. The activity gathers the inputs
(market_hours, diary count, last-experiment ts) and calls this pure function.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class TriggerConfig:
    min_hours_between: int
    min_diary_episodes: int
    crypto_utc_hour: int


def should_evolve_now(
    *, now: datetime, last_ts: datetime | None, closed_episodes: int,
    is_crypto_only: bool, session_closed_today: bool, cfg: TriggerConfig,
) -> tuple[bool, str]:
    if last_ts is not None:
        if last_ts.tzinfo is None:
            last_ts = last_ts.replace(tzinfo=now.tzinfo)
        if (now - last_ts) < timedelta(hours=cfg.min_hours_between):
            return False, "within cooldown (<24h since last run)"
    if closed_episodes < cfg.min_diary_episodes:
        return False, f"cold start ({closed_episodes} < {cfg.min_diary_episodes} episodes)"
    if is_crypto_only:
        if now.hour != cfg.crypto_utc_hour:
            return False, "not the crypto daily evolution hour"
    elif not session_closed_today:
        return False, "equity session not closed yet"
    return True, "ok"
