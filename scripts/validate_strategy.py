#!/usr/bin/env python3
"""Validate an `indicator_dsl` strategy catalog YAML against the shared grammar WITHOUT
touching the DB — so a newly authored strategy fails fast before you seed it.

Run inside a service env that has `neuromancing_shared` (either service works), e.g.
from the repo root:

    cd trade-api && uv run python ../scripts/validate_strategy.py \
        app/strategies/catalog/15-my-strategy.yaml

Exits 0 if the spec is valid, 1 on any grammar violation, 2 on usage/env errors.
"""

from __future__ import annotations

import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: validate_strategy.py <catalog.yaml>", file=sys.stderr)
        return 2
    try:
        import yaml
        from neuromancing_shared.strategy_spec import validate_spec
    except ImportError as e:
        print(f"import error ({e}) — run inside a service env, e.g.\n"
              "  cd trade-api && uv run python ../scripts/validate_strategy.py <file>",
              file=sys.stderr)
        return 2

    path = Path(argv[1])
    doc = yaml.safe_load(path.read_text())
    name, kind, spec = doc.get("name"), doc.get("kind"), doc.get("spec")
    if kind != "indicator_dsl":
        print(f"{path}: kind={kind!r} — this validator only checks indicator_dsl specs "
              "(signal_fn / rule_dsl specs are validated at seed time).")
        return 0
    try:
        validate_spec(spec)
    except Exception as e:  # validate_spec raises on any grammar violation  # noqa: BLE001
        print(f"INVALID: {name}: {e}", file=sys.stderr)
        return 1
    print(f"OK: {name} — valid indicator_dsl spec.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
