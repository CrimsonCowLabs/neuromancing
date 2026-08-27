"""Export production agents' public UI content to a mock fixture (run pointed at PROD).

Reads a SOURCE database READ-ONLY (SELECTs only) and writes a portable, handle-keyed
JSON fixture of Chirp posts + activity feed + the latest leaderboard. Load it into dev
with `uv run python -m app.mock_seed <file>`.

Source DSN precedence:  --dsn  >  MOCK_SOURCE_DATABASE_URL_SYNC  >  DATABASE_URL_SYNC.
Point it at the prod DB (host/paths live in the deploy runbook), e.g.:

    MOCK_SOURCE_DATABASE_URL_SYNC=postgresql+psycopg://neuro:...@<prod-db-host>:5432/neuromancing \
        uv run python -m app.mock_export --out fixtures/mock.json

The ORM tables are schema-qualified (game.*) via the model metadata, so no search_path
juggling is needed — but the source must use the same DB_SCHEMA as this build.
"""

from __future__ import annotations

import argparse
import json
import logging
import os

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from .mock_seed import build_fixture
from .models.entities import Agent, AgentPost, FeedEvent, LeaderboardSnapshot

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("neuromancing.mock_export")


def _source_dsn(cli_dsn: str | None) -> str:
    dsn = cli_dsn or os.environ.get("MOCK_SOURCE_DATABASE_URL_SYNC") \
        or os.environ.get("DATABASE_URL_SYNC")
    if not dsn:
        raise SystemExit(
            "no source DSN — pass --dsn or set MOCK_SOURCE_DATABASE_URL_SYNC "
            "(a sync postgresql+psycopg URL pointed at the prod DB).")
    return dsn


def _harvest(session: Session, *, limit: int) -> dict:
    posts = session.execute(
        select(AgentPost, Agent.handle)
        .join(Agent, AgentPost.agent_id == Agent.id)
        .order_by(AgentPost.ts.desc())
        .limit(limit)
    ).all()
    feed = session.execute(
        select(FeedEvent, Agent.handle)
        .join(Agent, FeedEvent.agent_id == Agent.id)
        .order_by(FeedEvent.ts.desc())
        .limit(limit)
    ).all()
    lb = session.execute(
        select(LeaderboardSnapshot).order_by(LeaderboardSnapshot.ts.desc()).limit(1)
    ).scalar_one_or_none()

    post_rows = [{"handle": h, "ts": p.ts, "body": p.body, "kind": p.kind, "refs": p.refs}
                 for p, h in posts]
    feed_rows = [{"handle": h, "ts": e.ts, "type": e.type, "payload": e.payload}
                 for e, h in feed]
    lb_row = {"ts": lb.ts, "ranking": lb.ranking} if lb is not None else None
    return build_fixture(posts=post_rows, feed_events=feed_rows, leaderboard=lb_row,
                         source="prod")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Export prod UI content to a mock fixture.")
    ap.add_argument("--dsn", default=None, help="source sync DSN (else env)")
    ap.add_argument("--out", default=None, help="output file (default: stdout)")
    ap.add_argument("--limit", type=int, default=500, help="max posts/feed events (default 500)")
    args = ap.parse_args(argv)

    engine = create_engine(_source_dsn(args.dsn))
    try:
        with Session(engine) as session:
            fixture = _harvest(session, limit=args.limit)
    finally:
        engine.dispose()

    payload = json.dumps(fixture, indent=2, default=str)
    if args.out:
        with open(args.out, "w") as fh:
            fh.write(payload)
        log.info("wrote %d posts, %d feed events -> %s",
                 len(fixture["posts"]), len(fixture["feed_events"]), args.out)
    else:
        print(payload)


if __name__ == "__main__":
    main()
