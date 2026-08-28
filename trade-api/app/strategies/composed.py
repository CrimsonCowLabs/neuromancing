"""Evaluator for the `indicator_dsl` strategy kind — multi-timeframe, event-driven.

Pure/sync so it stays fully backtestable. Given a validated spec + a bar series per
timeframe, it produces one Signal. Two correctness invariants:

- **As-of alignment (no lookahead).** For an indicator on timeframe `T`, only bars
  that have CLOSED as of the evaluation instant are used: `bar_open_ts + T <= cutoff`.
  Alpaca stamps bars at OPEN, so a 1h bar opened 14:00 is usable only from 15:00.
- **Event-driven at base resolution.** The whole buy/exit group is evaluated at the
  current base step and the previous one; a signal fires only on the group's
  false→true transition. A higher-tf condition therefore contributes exactly one
  transition (its value only changes when its bar closes) — no per-base-step spam.

See docs/05-trading-system.md and the plan for the full model.
"""

from __future__ import annotations

from typing import Any

from neuromancing_shared.strategy_spec import base_timeframe, tf_seconds as _tf_sec

from .base import HOLD, Signal
from .indicators import compute_indicator, scalar_value

_OPS = {
    "<": lambda a, b: a < b, "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b, ">=": lambda a, b: a >= b,
    "==": lambda a, b: a == b, "!=": lambda a, b: a != b,
}


def _epoch(ts) -> float:
    return ts.timestamp()


def evaluate_composed(spec: dict, bars_by_tf: dict[str, list]) -> Signal:
    inds = {i["id"]: i for i in spec["indicators"]}
    base_tf = base_timeframe(spec)
    base = bars_by_tf.get(base_tf) or []
    if len(base) < 2:  # need current + previous base step for a transition
        return HOLD
    bsec = _tf_sec(base_tf)

    # Evaluation instants: close-time of the latest base bar (now), the previous, and
    # the one before (prev2 — needed so a cross's "previous" is defined at step 1).
    cutoffs: list[float | None] = []
    for k in range(3):
        cutoffs.append(_epoch(base[-1 - k].ts) + bsec if len(base) >= k + 1 else None)

    raw: dict[tuple[str, int], Any] = {}
    for iid, ind in inds.items():
        tf = ind.get("timeframe") or base_tf
        ibars = bars_by_tf.get(tf) or []
        tsec = _tf_sec(tf)
        for k in range(3):
            c = cutoffs[k]
            if c is None:
                raw[(iid, k)] = None
                continue
            window = [b for b in ibars if _epoch(b.ts) + tsec <= c]  # as-of: closed bars only
            raw[(iid, k)] = compute_indicator(ind, window) if window else None

    states = spec.get("states", {})

    def scal(cond: dict, k: int) -> float | None:
        return scalar_value(raw.get((cond["indicator"], k)), cond.get("field"))

    def threshold(cond: dict, k: int) -> float | None:
        if "other" in cond:
            return scalar_value(raw.get((cond["other"], k)), cond.get("other_field"))
        return float(cond["value"])

    def cond_true(cond: dict, k: int) -> bool:
        now = scal(cond, k)
        if now is None:
            return False
        if "cross" in cond:
            if k + 1 >= 3:  # no defined "previous" for the crossing
                return False
            prev = scal(cond, k + 1)
            x_now, x_prev = threshold(cond, k), threshold(cond, k + 1)
            if prev is None or x_now is None or x_prev is None:
                return False
            if cond["cross"] == "above":
                return prev <= x_prev and now > x_now
            return prev >= x_prev and now < x_now
        x = threshold(cond, k)
        return x is not None and _OPS[cond["op"]](now, x)

    def node_true(node: Any, k: int) -> bool:
        if isinstance(node, str):
            return cond_true(states[node], k)
        if "all" in node:
            return all(node_true(x, k) for x in node["all"])
        if "any" in node:
            return any(node_true(x, k) for x in node["any"])
        if "not" in node:
            return not node_true(node["not"], k)
        return cond_true(node, k)  # inline condition

    def fired(group: dict) -> bool:
        return node_true(group, 0) and not node_true(group, 1)

    feats = {iid: raw.get((iid, 0)) for iid in inds}
    # Precedence: long entry, long exit, short cover (close), short entry. Long-only
    # strategies (no short/cover_when) behave exactly as before.
    for group_key, action in (("buy_when", "buy"), ("exit_when", "exit"),
                              ("cover_when", "cover"), ("short_when", "short")):
        group = spec.get(group_key)
        if group and fired(group):
            return Signal(action, _strength(spec, action, raw), {"dsl": action, **feats})
    return HOLD


def _strength(spec: dict, side: str, raw: dict) -> float:
    sp = (spec.get("strength") or {}).get(side)
    if not sp:
        return 0.6
    v = scalar_value(raw.get((sp["from"], 0)), sp.get("field"))
    if v is None:
        return 0.6
    a, b = float(sp["map"][0]), float(sp["map"][1])
    if a == b:
        return 0.6
    return max(0.0, min(1.0, (v - a) / (b - a)))
