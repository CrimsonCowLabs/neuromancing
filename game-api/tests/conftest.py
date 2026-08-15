"""Pytest config for game-api.

`live`-marked tests call a real LLM provider (and need the game DB / Redis / trade-api).
They are OPT-IN — collected only when you explicitly select them (`-m live`) AND a
provider key is resolvable — so the default offline suite and CI never touch the network.
"""

from __future__ import annotations

import pytest


def _has_provider_key() -> bool:
    try:
        from app.llm import provider
        return provider.has_key()
    except Exception:  # noqa: BLE001 — never let import issues break collection
        return False


def pytest_collection_modifyitems(config, items):
    selected = config.getoption("-m")  # e.g. "live"
    key = _has_provider_key()
    for item in items:
        if "live" not in item.keywords:
            continue
        if "live" not in selected:
            item.add_marker(pytest.mark.skip(reason="live test — select with `-m live`"))
        elif not key:
            item.add_marker(pytest.mark.skip(
                reason="live test — no LLM key resolved (set LLM_API_KEY/OLLAMA_API_KEY)"))
