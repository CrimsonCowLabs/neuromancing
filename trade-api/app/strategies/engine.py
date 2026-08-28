"""House strategy catalog loader.

The three strategy kinds — **signal_fn** (`library.py`), **rule_dsl** (`dsl.py`), and
**indicator_dsl** (`composed.py` + `spec.py`) — are EVALUATED through the one `Strategy`
interface (`interface.py`); there is no dispatch here. This module owns only catalog
loading, which is orthogonal: the house catalog lives as validated YAML files under
`catalog/` (the single source of truth), loaded by `list_house_strategies()`.
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
