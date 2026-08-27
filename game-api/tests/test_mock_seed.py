"""Pure-transform tests for the mock-data pipeline (app.mock_seed).

The fixture is keyed by agent HANDLE so it is portable across environments (prod and
dev assign different ids). build_fixture whitelists public UI content; plan_load
resolves handles to the target DB's ids and drops any it can't match.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.mock_seed import FIXTURE_VERSION, build_fixture, plan_load
from app.models.entities import PostKind


def _dt(day: int) -> datetime:
    return datetime(2026, 8, day, 12, 0, tzinfo=UTC)


def test_build_fixture_shape_and_version():
    fx = build_fixture(posts=[], feed_events=[], leaderboard=None, source="prod-x")
    assert fx["version"] == FIXTURE_VERSION
    assert fx["source"] == "prod-x"
    assert fx == {"version": FIXTURE_VERSION, "source": "prod-x",
                  "posts": [], "feed_events": [], "leaderboard": None}


def test_build_fixture_whitelists_post_fields_and_serializes_ts():
    # An input row may carry extra columns (id, agent_id, reply_to...) — the fixture keeps
    # only the portable, public fields, keyed by handle, with ts as an ISO string.
    posts = [{"handle": "molly", "ts": _dt(1), "body": "ride strength",
              "kind": PostKind.trade_note, "refs": {"symbol": "NVDA"},
              "id": 99, "agent_id": 7, "reply_to_post_id": None}]
    fx = build_fixture(posts=posts)
    assert fx["posts"] == [{
        "handle": "molly", "ts": "2026-08-01T12:00:00+00:00",
        "body": "ride strength", "kind": "trade_note", "refs": {"symbol": "NVDA"},
    }]


def test_build_fixture_leaderboard_and_feed():
    fx = build_fixture(
        posts=[],
        feed_events=[{"handle": "finn", "ts": _dt(2), "type": "trade",
                      "payload": {"symbol": "BAC"}}],
        leaderboard={"ts": _dt(3), "ranking": {"1": "molly"}},
    )
    assert fx["feed_events"] == [{"handle": "finn", "ts": "2026-08-02T12:00:00+00:00",
                                  "type": "trade", "payload": {"symbol": "BAC"}}]
    assert fx["leaderboard"] == {"ts": "2026-08-03T12:00:00+00:00", "ranking": {"1": "molly"}}


def test_plan_load_resolves_handles_and_parses_ts():
    fx = build_fixture(posts=[{"handle": "molly", "ts": _dt(1), "body": "hi",
                               "kind": "take", "refs": {}}])
    plan = plan_load(fx, {"molly": 42})
    assert plan.skipped_handles == set()
    assert plan.posts == [{"agent_id": 42, "ts": _dt(1), "body": "hi",
                           "kind": "take", "refs": {}}]


def test_plan_load_drops_and_tracks_unknown_handles():
    fx = build_fixture(
        posts=[{"handle": "ghost", "ts": _dt(1), "body": "x", "kind": "take", "refs": {}},
               {"handle": "molly", "ts": _dt(1), "body": "y", "kind": "take", "refs": {}}],
        feed_events=[{"handle": "ghost", "ts": _dt(1), "type": "trade", "payload": {}}],
    )
    plan = plan_load(fx, {"molly": 42})  # 'ghost' is not in the target DB
    assert plan.skipped_handles == {"ghost"}
    assert [p["agent_id"] for p in plan.posts] == [42]
    assert plan.feed_events == []  # ghost's feed event dropped too


def test_plan_load_remaps_leaderboard_rows_by_handle():
    # The prod ranking embeds prod agent_ids; the loader must remap each row to THIS DB's
    # id by handle, drop rows whose handle isn't local, and renumber ranks 1..n.
    fx = build_fixture(posts=[], leaderboard={"ts": _dt(4), "ranking": {"rows": [
        {"agent_id": 999, "handle": "molly", "rank": 1, "return_pct": 5.0},
        {"agent_id": 888, "handle": "ghost", "rank": 2, "return_pct": 3.0},
        {"agent_id": 777, "handle": "finn", "rank": 3, "return_pct": 1.0},
    ]}})
    plan = plan_load(fx, {"molly": 10, "finn": 20})  # 'ghost' not local
    rows = plan.leaderboard["ranking"]["rows"]
    assert [(r["handle"], r["agent_id"], r["rank"]) for r in rows] == [
        ("molly", 10, 1), ("finn", 20, 2)]  # prod ids 999/777 gone; ghost dropped; re-ranked
    assert "ghost" in plan.skipped_handles


def test_plan_load_roundtrips_through_json():
    # The fixture must survive JSON serialization (it's written to a file between export
    # and load) — ts comes back as a string and plan_load must still parse it.
    import json
    fx = json.loads(json.dumps(build_fixture(
        posts=[{"handle": "molly", "ts": _dt(5), "body": "z", "kind": "banter", "refs": {}}],
        leaderboard={"ts": _dt(6), "ranking": {}},
    )))
    plan = plan_load(fx, {"molly": 1})
    assert plan.posts[0]["ts"] == _dt(5)
    assert plan.leaderboard["ts"] == _dt(6)
