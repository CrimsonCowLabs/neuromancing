"""Strategy dispatch + house strategy catalog.

Three kinds:
- **signal_fn** — named hardcoded functions (`library.py`), event-driven.
- **rule_dsl** — the legacy declarative evaluator (`dsl.py`), single-tf, close-only.
- **indicator_dsl** — the YAML-authored, validated, multi-timeframe, indicator-state
  model (`composed.py` + `spec.py`). This is the configurable strategy grammar.

The house catalog lives as validated YAML files under `catalog/` (the single source
of truth), loaded by `list_house_strategies()`.
"""

from __future__ import annotations

import pathlib

import yaml

from .base import Bar, Signal
from .composed import base_timeframe, evaluate_composed
from .dsl import evaluate_dsl
from .library import SIGNAL_FNS
from .spec import validate_spec

_CATALOG_DIR = pathlib.Path(__file__).parent / "catalog"


def evaluate(kind: str, spec: dict, bars: list[Bar]) -> Signal:
    """Single-series evaluation. For `indicator_dsl` this wraps `bars` under the
    strategy's base timeframe — correct for single-tf strategies (and single-tf
    backtests); the multi-tf live path uses `evaluate_multi`."""
    if kind == "indicator_dsl":
        return evaluate_composed(spec, {base_timeframe(spec): bars})
    closes = [b.close for b in bars]
    if kind == "signal_fn":
        fn = SIGNAL_FNS.get(spec.get("fn", ""))
        if fn is None:
            raise ValueError(f"unknown signal_fn: {spec.get('fn')}")
        return fn(closes, spec)
    if kind == "rule_dsl":
        return evaluate_dsl(spec, closes)
    raise ValueError(f"unknown strategy kind: {kind}")


def evaluate_multi(kind: str, spec: dict, bars_by_tf: dict[str, list[Bar]]) -> Signal:
    """Multi-timeframe evaluation. `indicator_dsl` uses the composed engine over all
    timeframes; single-series kinds fall back to their base/only series."""
    if kind == "indicator_dsl":
        return evaluate_composed(spec, bars_by_tf)
    tf = spec.get("timeframe") or base_timeframe(spec)
    bars = bars_by_tf.get(tf) or (next(iter(bars_by_tf.values())) if bars_by_tf else [])
    return evaluate(kind, spec, bars)


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
