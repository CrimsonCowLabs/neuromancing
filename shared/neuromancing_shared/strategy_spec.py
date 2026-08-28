"""Shared, validated schema + vocabulary for the `indicator_dsl` strategy kind.

This is the single source of truth for the strategy grammar, used by BOTH services:
- trade-api validates specs on create/backtest and evaluates them.
- game-api's deep-agent (TODO #3) emits candidate strategies as structured Pydantic
  output and validates them here before backtesting.

A strategy is: named **indicators** (each with an OHLCV `source` and its own
`timeframe`), named **states** (a level/zone or a crossing condition), and
`buy_when`/`exit_when` **rule groups** (nestable all/any/not over state names or
inline conditions), plus an optional `strength` map and a `type` archetype.

`validate_spec(dict)` is the fail-closed gate: it rejects unknown fns/ops/sources/
fields, dangling references, bad params, and out-of-range timeframes, and returns the
canonical dict. NO code execution — only whitelisted names.

The vocabulary constants below are the canonical grammar; trade-api's `indicators.py`
imports them so its fn *implementations* can never drift from the grammar.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator

# --- Vocabulary (the grammar contract) ------------------------------------
SOURCES = ("close", "open", "high", "low", "hlc3", "volume")
KNOWN_FNS = ("sma", "ema", "rsi", "roc", "macd", "bollinger", "bbpercent", "atr", "vwap")
# fn -> valid `field` selectors for its dict-valued output
DICT_FIELDS = {
    "macd": ("line", "signal", "hist"),
    "bollinger": ("upper", "middle", "lower", "percent"),
}
# Must track neuromancing_shared PRICE_TIMEFRAMES (kept literal so this stays
# pure/offline-testable).
ALLOWED_TIMEFRAMES = ("1m", "5m", "1h", "1d")
OPS = ("<", "<=", ">", ">=", "==", "!=")
CROSS = ("above", "below")
ARCHETYPES = ("trend", "mean_reversion", "momentum", "breakout", "trend_pullback", "custom")

# Seconds per timeframe bar — the ordering key over a set of timeframes (fastest first)
# and the as-of window width the evaluator uses. The one home for "which timeframes does
# this spec need"; both services derive base/required timeframes from here so the rule is
# never kept aligned by hand.
_TF_SECONDS = {"1m": 60, "5m": 300, "1h": 3600, "1d": 86400}


def tf_seconds(tf: str) -> int:
    """Seconds in one bar of `tf`; the ordering key for a timeframe set. Unknown → 60."""
    return _TF_SECONDS.get(tf, 60)


def base_timeframe(spec: dict) -> str:
    """The evaluation cadence: explicit `base_timeframe`, else the fastest indicator tf."""
    ind_tfs = [i.get("timeframe") for i in spec.get("indicators", []) if i.get("timeframe")]
    return spec.get("base_timeframe") or (min(ind_tfs, key=tf_seconds) if ind_tfs else "1m")


def required_timeframes(spec: dict) -> list[str]:
    """All distinct timeframes a spec needs loaded (indicator tfs ∪ base), fastest first."""
    tfs = {i.get("timeframe") for i in spec.get("indicators", []) if i.get("timeframe")}
    tfs.add(base_timeframe(spec))
    return sorted(tfs, key=tf_seconds)


class IndicatorSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    fn: str
    source: str = "close"
    timeframe: str | None = None
    period: int | None = None
    fast: int | None = None
    slow: int | None = None
    signal: int | None = None
    mult: float | None = None

    @model_validator(mode="after")
    def _check(self) -> IndicatorSpec:
        if self.fn not in KNOWN_FNS:
            raise ValueError(f"unknown indicator fn: {self.fn}")
        if self.source not in SOURCES:
            raise ValueError(f"unknown source: {self.source}")
        if self.timeframe is not None and self.timeframe not in ALLOWED_TIMEFRAMES:
            raise ValueError(f"timeframe {self.timeframe} not in {ALLOWED_TIMEFRAMES}")
        for p in ("period", "fast", "slow", "signal"):
            v = getattr(self, p)
            if v is not None and v <= 0:
                raise ValueError(f"{self.id}.{p} must be positive")
        if self.fn == "macd" and self.fast and self.slow and self.fast >= self.slow:
            raise ValueError(f"{self.id}: macd fast must be < slow")
        if self.fn in ("sma", "ema", "roc") and not self.period:
            raise ValueError(f"{self.id}: {self.fn} requires a period")
        return self


def _is_group(node: Any) -> bool:
    return isinstance(node, dict) and any(k in node for k in ("all", "any", "not"))


class StrategySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str | None = None
    base_timeframe: str | None = None
    indicators: list[IndicatorSpec]
    states: dict[str, dict] = {}
    buy_when: dict | None = None
    exit_when: dict | None = None
    short_when: dict | None = None   # open a short (sell-to-open)
    cover_when: dict | None = None   # close a short (buy-to-close)
    strength: dict | None = None

    @model_validator(mode="after")
    def _check(self) -> StrategySpec:
        ids = [i.id for i in self.indicators]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate indicator id")
        if not ids:
            raise ValueError("at least one indicator required")
        by_id = {i.id: i for i in self.indicators}
        if self.type is not None and self.type not in ARCHETYPES:
            raise ValueError(f"unknown type: {self.type}")
        if self.base_timeframe and self.base_timeframe not in ALLOWED_TIMEFRAMES:
            raise ValueError(f"base_timeframe not in {ALLOWED_TIMEFRAMES}")

        def check_cond(cond: dict) -> None:
            if not isinstance(cond, dict) or "indicator" not in cond:
                raise ValueError(f"condition needs an 'indicator': {cond}")
            iid = cond["indicator"]
            if iid not in by_id:
                raise ValueError(f"condition references unknown indicator: {iid}")
            fn = by_id[iid].fn
            field = cond.get("field")
            if fn in DICT_FIELDS:
                if field not in DICT_FIELDS[fn]:
                    raise ValueError(f"{iid} ({fn}) needs field in {DICT_FIELDS[fn]}, got {field}")
            elif field is not None:
                raise ValueError(f"{iid} ({fn}) is scalar; no 'field' allowed")
            has_op, has_cross = "op" in cond, "cross" in cond
            if has_op == has_cross:
                raise ValueError(f"condition needs exactly one of op/cross: {cond}")
            if has_op and cond["op"] not in OPS:
                raise ValueError(f"unknown op: {cond['op']}")
            if has_cross and cond["cross"] not in CROSS:
                raise ValueError(f"unknown cross: {cond['cross']}")
            has_val, has_other = "value" in cond, "other" in cond
            if has_val == has_other:
                raise ValueError(f"condition needs exactly one of value/other: {cond}")
            if has_other:
                other = cond["other"]
                if other not in by_id:
                    raise ValueError(f"condition references unknown indicator: {other}")
                ofn = by_id[other].fn
                ofield = cond.get("other_field")
                if ofn in DICT_FIELDS and ofield not in DICT_FIELDS[ofn]:
                    raise ValueError(f"other {other} needs other_field in {DICT_FIELDS[ofn]}")
            allowed = {"indicator", "field", "op", "cross", "value", "other", "other_field"}
            extra = set(cond) - allowed
            if extra:
                raise ValueError(f"unknown condition keys: {extra}")

        for name, cond in self.states.items():
            check_cond(cond)

        def check_group(node: Any) -> None:
            if isinstance(node, str):
                if node not in self.states:
                    raise ValueError(f"rule references unknown state: {node}")
                return
            if not isinstance(node, dict):
                raise ValueError(f"rule node must be a state name or dict: {node}")
            if _is_group(node):
                keys = [k for k in ("all", "any", "not") if k in node]
                if len(keys) != 1 or len(node) != 1:
                    raise ValueError(f"group must have exactly one of all/any/not: {node}")
                key = keys[0]
                if key == "not":
                    check_group(node["not"])
                else:
                    items = node[key]
                    if not isinstance(items, list) or not items:
                        raise ValueError(f"'{key}' must be a non-empty list")
                    for it in items:
                        check_group(it)
            else:
                check_cond(node)  # inline condition

        if not any((self.buy_when, self.exit_when, self.short_when, self.cover_when)):
            raise ValueError("strategy needs at least one of buy/exit/short/cover_when")
        for group in (self.buy_when, self.exit_when, self.short_when, self.cover_when):
            if group:
                check_group(group)

        if self.strength is not None:
            for side, sp in self.strength.items():
                if side not in ("buy", "exit", "short", "cover"):
                    raise ValueError(f"strength side must be buy/exit/short/cover: {side}")
                if not isinstance(sp, dict) or sp.get("from") not in by_id:
                    raise ValueError(f"strength.{side}.from must reference an indicator")
                mp = sp.get("map")
                if not (isinstance(mp, (list, tuple)) and len(mp) == 2):
                    raise ValueError(f"strength.{side}.map must be [a, b]")
        return self


def validate_spec(spec: dict) -> dict:
    """Validate an indicator_dsl spec dict; return the canonical dict to store.
    Raises ValueError (fail-closed) on any problem."""
    model = StrategySpec.model_validate(spec)
    return model.model_dump(exclude_none=True)
