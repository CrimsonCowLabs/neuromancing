"""Live end-to-end decision test. Skipped unless run with `-m live` AND a provider key
is set (see conftest.py). Needs the game DB, Redis, and a reachable trade-api — point it
at a running stack (e.g. macneo). The handle defaults to momentum-mike; override with
LIVE_TEST_HANDLE.

    LLM_API_KEY=... uv run pytest -m live
"""

from __future__ import annotations

import os

import pytest

from app.livetest import run_livetest


@pytest.mark.live
async def test_live_decision_hits_provider():
    handle = os.getenv("LIVE_TEST_HANDLE", "momentum-mike")
    result = await run_livetest(handle, place=False)

    # If there were no actionable signals this tick, the model isn't called — that's a
    # valid outcome (not a provider failure), so treat it as an inconclusive skip.
    if result.get("model") is None:
        pytest.skip(f"no actionable signals for {handle} this tick — rerun during market hours")

    # The provider genuinely answered (run_livetest raises if it fell back).
    assert result["model"] != "deterministic-fallback"
    # Guardrails ran; every approved action is well-formed.
    for action in result["valid"]:
        assert action["type"] in ("order", "close")
        assert action.get("symbol")
    # Dry run must not have placed anything.
    assert result["placed"] == []
