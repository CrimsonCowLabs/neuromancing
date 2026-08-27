"""Mock-data pipeline — populate a DEV database with real production UI content
(Chirp posts, activity feed, leaderboard) so the site/systems can be smoke-tested
WITHOUT running the LLM (dev sets LLM_ENABLED=false; see the dev-env spec, issue #14).

Two pure transforms + a thin, idempotent loader:

  - `build_fixture(...)`  ORM/dict rows -> a portable JSON fixture. Keyed by agent
    HANDLE (portable — prod and dev assign different ids) and whitelisted to public,
    money-blind UI fields only.
  - `plan_load(fixture, handle_to_agent_id)` -> a LoadPlan resolved against THIS DB's
    agent ids; handles with no local agent are dropped and reported.

Layer it on top of the normal roster seed:

    uv run python -m app.seed            # personas + agents + strategies (dev ids)
    uv run python -m app.mock_seed FILE  # prod content, mapped onto those dev agents

Produce FILE by pointing the exporter at prod:  `uv run python -m app.mock_export`.
"""

from __future__ import annotations

import argparse
import asyncio
import enum
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("neuromancing.mock_seed")

FIXTURE_VERSION = 1


# ---- pure transforms (unit-tested; no DB, no I/O) --------------------------------

def _val(x):
    """Enum -> its string value; anything else passes through (test dicts use plain
    strings, ORM rows carry the Enum)."""
    return x.value if isinstance(x, enum.Enum) else x


def _iso(ts) -> str:
    return ts.isoformat() if isinstance(ts, datetime) else str(ts)


def _parse_ts(v):
    return v if isinstance(v, datetime) else datetime.fromisoformat(v)


def _clean_post(p: dict) -> dict:
    return {
        "handle": p["handle"],
        "ts": _iso(p["ts"]),
        "body": p.get("body", ""),
        "kind": _val(p.get("kind", "take")),
        "refs": p.get("refs") or {},
    }


def _clean_feed(e: dict) -> dict:
    return {
        "handle": e["handle"],
        "ts": _iso(e["ts"]),
        "type": _val(e.get("type", "trade")),
        "payload": e.get("payload") or {},
    }


def _clean_leaderboard(lb: dict | None) -> dict | None:
    if not lb:
        return None
    return {"ts": _iso(lb["ts"]), "ranking": lb.get("ranking") or {}}


def build_fixture(posts, feed_events=None, leaderboard=None, *, source="prod") -> dict:
    """Whitelist public UI content into a portable, handle-keyed fixture."""
    return {
        "version": FIXTURE_VERSION,
        "source": source,
        "posts": [_clean_post(p) for p in posts],
        "feed_events": [_clean_feed(e) for e in (feed_events or [])],
        "leaderboard": _clean_leaderboard(leaderboard),
    }


@dataclass
class LoadPlan:
    posts: list[dict]
    feed_events: list[dict]
    leaderboard: dict | None
    skipped_handles: set[str]


def plan_load(fixture: dict, handle_to_agent_id: dict[str, int]) -> LoadPlan:
    """Resolve a fixture's handles to THIS DB's agent ids; drop + report unknowns."""
    skipped: set[str] = set()

    def _resolve(rows, extra):
        out = []
        for r in rows:
            aid = handle_to_agent_id.get(r["handle"])
            if aid is None:
                skipped.add(r["handle"])
                continue
            out.append({"agent_id": aid, "ts": _parse_ts(r["ts"]), **extra(r)})
        return out

    posts = _resolve(fixture.get("posts", []),
                     lambda r: {"body": r.get("body", ""), "kind": r["kind"],
                                "refs": r.get("refs") or {}})
    feed_events = _resolve(fixture.get("feed_events", []),
                           lambda r: {"type": r["type"], "payload": r.get("payload") or {}})

    # The leaderboard ranking embeds a PROD agent_id per row (see leaderboard.compute_ranking);
    # remap each row by its handle to THIS DB's id, drop rows whose handle isn't local, and
    # renumber ranks so a partial roster still ranks 1..n. Never copy prod ids verbatim.
    lb = fixture.get("leaderboard")
    leaderboard = None
    if lb:
        rows = []
        for row in (lb.get("ranking") or {}).get("rows", []):
            aid = handle_to_agent_id.get(row.get("handle"))
            if aid is None:
                if row.get("handle"):
                    skipped.add(row["handle"])
                continue
            rows.append({**row, "agent_id": aid})
        for i, row in enumerate(rows, start=1):
            row["rank"] = i
        leaderboard = {"ts": _parse_ts(lb["ts"]), "ranking": {"rows": rows}}
    return LoadPlan(posts=posts, feed_events=feed_events,
                    leaderboard=leaderboard, skipped_handles=skipped)


# ---- loader (thin DB I/O; run in dev) --------------------------------------------

async def _apply(session, plan: LoadPlan, *, replace: bool) -> bool:
    """Insert the plan into the current DB. Idempotent: no-op if Chirp already has rows
    unless `replace` (which first clears the content tables this loader owns). Returns
    True if it wrote anything."""
    from sqlalchemy import delete, func, select

    from .models.entities import (
        AgentPost,
        FeedEvent,
        FeedEventType,
        LeaderboardSnapshot,
        PostKind,
    )

    existing = await session.scalar(select(func.count()).select_from(AgentPost))
    if existing and not replace:
        log.info("agent_post already has %d rows; skipping (pass --replace to reload)", existing)
        return False
    if replace:
        # Only the content tables this loader owns — never agents/personas/trade data.
        await session.execute(delete(FeedEvent))
        await session.execute(delete(LeaderboardSnapshot))
        await session.execute(delete(AgentPost))

    for p in plan.posts:
        session.add(AgentPost(agent_id=p["agent_id"], ts=p["ts"], body=p["body"],
                              kind=PostKind(p["kind"]), refs=p["refs"]))
    for e in plan.feed_events:
        session.add(FeedEvent(agent_id=e["agent_id"], ts=e["ts"],
                              type=FeedEventType(e["type"]), payload=e["payload"]))
    if plan.leaderboard:
        session.add(LeaderboardSnapshot(ts=plan.leaderboard["ts"],
                                        ranking=plan.leaderboard["ranking"]))
    await session.commit()
    return True


def _parse_args(argv=None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Load a prod-harvested mock fixture into dev.")
    ap.add_argument("fixture", help="path to the JSON fixture (from app.mock_export)")
    ap.add_argument("--replace", action="store_true",
                    help="clear existing Chirp/feed/leaderboard rows first")
    ap.add_argument("--force", action="store_true",
                    help="load even when LLM_ENABLED=true (bypass the prod guard)")
    return ap.parse_args(argv)


async def main(argv=None) -> None:
    from sqlalchemy import select

    from .config import get_settings
    from .db import SessionLocal
    from .models.entities import Agent

    args = _parse_args(argv)
    # Guard: this writes fabricated content — refuse to run against a live (LLM-on)
    # environment, which is almost certainly production, unless explicitly forced.
    if get_settings().llm_enabled and not args.force:
        raise SystemExit(
            "refusing to load mock data with LLM_ENABLED=true (looks like prod). "
            "Mock data is for dev; pass --force only if you are certain.")

    fixture = json.loads(Path(args.fixture).read_text())
    if fixture.get("version") != FIXTURE_VERSION:
        raise SystemExit(f"fixture version {fixture.get('version')} != {FIXTURE_VERSION}")

    async with SessionLocal() as session:
        agents = (await session.scalars(select(Agent))).all()
        handle_to_id = {a.handle: a.id for a in agents}
        plan = plan_load(fixture, handle_to_id)
        if plan.skipped_handles:
            log.warning("skipped %d handle(s) with no local agent (run app.seed first?): %s",
                        len(plan.skipped_handles), sorted(plan.skipped_handles))
        wrote = await _apply(session, plan, replace=args.replace)

    log.info("mock-seed %s: %d posts, %d feed events, leaderboard=%s (source=%s)",
             "loaded" if wrote else "skipped",
             len(plan.posts), len(plan.feed_events), bool(plan.leaderboard),
             fixture.get("source"))


if __name__ == "__main__":
    asyncio.run(main())
