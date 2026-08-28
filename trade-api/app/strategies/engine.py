"""House strategy catalog loader.

The strategy *evaluation* surface (turn a spec into a signal or a track record) lives
behind the one `Strategy` interface in `interface.py` (`build_strategy`). This module is
now just the catalog: the house strategies live as validated YAML files under `catalog/`
(the single source of truth), loaded by `list_house_strategies()`.

Three kinds are wrapped by the interface:
- **signal_fn** — named hardcoded functions (`library.py`), event-driven.
- **rule_dsl** — the legacy declarative evaluator (`dsl.py`), single-tf, close-only.
- **indicator_dsl** — the YAML-authored, validated, multi-timeframe, indicator-state
  model (`composed.py` + `spec.py`). This is the configurable strategy grammar.
"""

from __future__ import annotations

import pathlib

import yaml

from .spec import validate_spec

_CATALOG_DIR = pathlib.Path(__file__).parent / "catalog"


def list_house_strategies() -> list[dict]:
    """Load the house catalog from `catalog/*.yaml`, validating indicator_dsl specs.
    A malformed catalog file fails loudly (seed aborts) rather than shipping."""
    out: list[dict] = []
    for path in sorted(_CATALOG_DIR.glob("*.yaml")):
        doc = yaml.safe_load(path.read_text())
        if not doc or "name" not in doc or "kind" not in doc:
            raise ValueError(f"catalog {path.name}: missing name/kind")
        spec = doc.get("spec", {})
        if doc["kind"] == "indicator_dsl":
            spec = validate_spec(spec)  # fail-closed
        out.append({"name": doc["name"], "kind": doc["kind"], "spec": spec})
    if not out:
        raise ValueError(f"no house strategies found under {_CATALOG_DIR}")
    return out
