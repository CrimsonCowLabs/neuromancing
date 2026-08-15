"""Leaderboard: rank agents by time-weighted-ish return % (equity vs starting),
persist a snapshot, and cache the current standing in Redis for cheap reads + SSE.

Reads trade.account / trade.equity_snapshot cross-schema (same cluster), mapping
account.external_ref -> agent.account_ref.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from neuromancing_shared.redisio import make_redis
from sqlalchemy import select, text

from .config import get_settings
from .db import SessionLocal
from .models.entities import Agent, LeaderboardSnapshot

log = logging.getLogger("neuromancing.leaderboard")
_redis = make_redis(get_settings().redis_url)

_ACCOUNT_SQL = text(
    "SELECT id, starting_equity FROM trade.account WHERE external_ref = :ref"
)
_LATEST_EQUITY_SQL = text(
    "SELECT total_equity FROM trade.equity_snapshot WHERE account_id = :aid "
    "ORDER BY ts DESC LIMIT 1"
)


async def compute_ranking() -> list[dict]:
    async with SessionLocal() as session:
        agents = (await session.execute(select(Agent))).scalars().all()
        rows: list[dict] = []
        for a in agents:
            acct = (await session.execute(_ACCOUNT_SQL, {"ref": a.account_ref})).first()
            if acct is None:
                continue
            account_id, starting = acct[0], float(acct[1] or 0)
            eq_row = (await session.execute(_LATEST_EQUITY_SQL, {"aid": account_id})).first()
            equity = float(eq_row[0]) if eq_row else starting
            ret = ((equity - starting) / starting) if starting else 0.0
            rows.append({
                "agent_id": a.id, "handle": a.handle, "display_name": a.display_name,
                "equity": round(equity, 2), "starting_equity": round(starting, 2),
                "return_pct": round(ret * 100, 4),
            })
    rows.sort(key=lambda r: (r["return_pct"], r["equity"]), reverse=True)
    for i, r in enumerate(rows, start=1):
        r["rank"] = i
    return rows


async def rebuild() -> list[dict]:
    ranking = await compute_ranking()
    now = datetime.now(timezone.utc)
    async with SessionLocal() as session:
        session.add(LeaderboardSnapshot(ts=now, ranking={"rows": ranking}))
        await session.commit()
    try:
        await _redis.set("leaderboard:current", json.dumps({"ts": now.isoformat(), "rows": ranking}))
        if ranking:
            await _redis.zadd("leaderboard:z", {r["handle"]: r["return_pct"] for r in ranking})
        await _redis.publish("leaderboard", json.dumps({"ts": now.isoformat(), "rows": ranking[:20]}))
    except Exception as e:  # noqa: BLE001
        log.warning("leaderboard redis update failed: %s", e)
    return ranking


async def current() -> dict:
    raw = await _redis.get("leaderboard:current")
    if raw:
        return json.loads(raw)
    return {"ts": None, "rows": await compute_ranking()}
