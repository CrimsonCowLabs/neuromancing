"""Whitelisted declarative rule DSL — the safe path for user-submitted strategies
(Phase 3). NO code execution: only known indicators and comparison operators.

Spec shape:
  {
    "buy_when":  {"all": [<cond>, ...]}  or  {"any": [<cond>, ...]},
    "exit_when": {"any": [<cond>, ...]}
  }
Condition:
  {"indicator": "rsi", "period": 14, "op": "<", "value": 30}
  {"indicator": "sma", "period": 10, "op": ">", "other_indicator": "sma", "other_period": 30}
"""

from __future__ import annotations

from .base import HOLD, Signal
from .indicators import INDICATORS

_OPS = {
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "==": lambda a, b: a == b,
}


def _indicator_value(name: str, closes: list[float], period: int | None) -> float | None:
    fn = INDICATORS.get(name)
    if fn is None:
        raise ValueError(f"unknown indicator: {name}")
    return fn(closes, int(period)) if period is not None else fn(closes)


def _eval_condition(cond: dict, closes: list[float]) -> bool:
    op = _OPS.get(cond.get("op", ""))
    if op is None:
        raise ValueError(f"unknown op: {cond.get('op')}")
    left = _indicator_value(cond["indicator"], closes, cond.get("period"))
    if left is None:
        return False
    if "other_indicator" in cond:
        right = _indicator_value(cond["other_indicator"], closes, cond.get("other_period"))
        if right is None:
            return False
    else:
        right = float(cond["value"])
    return bool(op(left, right))


def _eval_group(group: dict, closes: list[float]) -> bool:
    if "all" in group:
        return all(_eval_condition(c, closes) for c in group["all"])
    if "any" in group:
        return any(_eval_condition(c, closes) for c in group["any"])
    raise ValueError("group must contain 'all' or 'any'")


def evaluate_dsl(spec: dict, closes: list[float]) -> Signal:
    buy_group = spec.get("buy_when")
    exit_group = spec.get("exit_when")
    if buy_group and _eval_group(buy_group, closes):
        return Signal("buy", 0.6, {"dsl": "buy"})
    if exit_group and _eval_group(exit_group, closes):
        return Signal("exit", 0.6, {"dsl": "exit"})
    return HOLD
