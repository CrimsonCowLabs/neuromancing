"""Flat, fully-typed proposal models for structured LLM output + a deterministic
compiler to the canonical indicator_dsl grammar.

The canonical `StrategySpec` has a recursive `buy_when`/`exit_when` that is unreliable
as a JSON-schema on Ollama's OpenAI-compat endpoint. So the LLM emits this FLAT model
(one all/any combinator level, typed conditions), and `compile_proposal` maps it to the
canonical spec + validates it fail-closed. No JSON parsing at the LLM boundary — see the
`prefer-pydantic-structured-output` rule.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from neuromancing_shared.strategy_spec import validate_spec


class ProposedIndicator(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(description="short unique id, e.g. 'rsi14'")
    fn: str = Field(description="one of: sma, ema, rsi, roc, macd, bollinger, bbpercent, atr, vwap")
    source: str = "close"  # close|open|high|low|hlc3|volume
    timeframe: str = "5m"   # 1m|5m|1h|1d
    period: int | None = None
    fast: int | None = None
    slow: int | None = None
    signal: int | None = None
    mult: float | None = None


class ProposedCondition(BaseModel):
    model_config = ConfigDict(extra="ignore")
    indicator: str = Field(description="an indicator id declared above")
    field: str | None = Field(default=None, description="for macd(line/signal/hist) or bollinger(upper/middle/lower/percent)")
    op: str | None = Field(default=None, description="zone comparison: < <= > >= == != (mutually exclusive with cross)")
    cross: str | None = Field(default=None, description="'above' or 'below' for a crossing event")
    value: float | None = Field(default=None, description="threshold constant (mutually exclusive with other)")
    other: str | None = Field(default=None, description="compare against another indicator id")
    other_field: str | None = None


class RuleBlock(BaseModel):
    model_config = ConfigDict(extra="ignore")
    combinator: str = "all"  # all | any
    conditions: list[ProposedCondition]


class ProposedStrength(BaseModel):
    model_config = ConfigDict(extra="ignore")
    from_indicator: str
    map_low: float   # indicator value mapped to conviction 0
    map_high: float  # indicator value mapped to conviction 1


class ProposedStrategy(BaseModel):
    model_config = ConfigDict(extra="ignore")
    hypothesis: str = Field(description="one sentence: why this should beat the current strategy")
    type: str = "custom"  # trend|mean_reversion|momentum|breakout|trend_pullback|custom
    base_timeframe: str = "5m"
    indicators: list[ProposedIndicator]
    buy: RuleBlock
    exit: RuleBlock
    strength: ProposedStrength | None = None


class StrategyProposal(BaseModel):
    """The structured object the reasoner returns (never parsed from raw JSON)."""
    model_config = ConfigDict(extra="ignore")
    hypothesis: str
    candidates: list[ProposedStrategy]


def _block(rb: RuleBlock) -> dict:
    conds: list[dict] = []
    for c in rb.conditions:
        cd: dict = {"indicator": c.indicator}
        if c.field:
            cd["field"] = c.field
        if c.cross:
            cd["cross"] = c.cross
        elif c.op:
            cd["op"] = c.op
        if c.other:
            cd["other"] = c.other
            if c.other_field:
                cd["other_field"] = c.other_field
        elif c.value is not None:
            cd["value"] = c.value
        conds.append(cd)
    return {rb.combinator: conds}


def compile_proposal(p: ProposedStrategy) -> dict:
    """Compile a flat ProposedStrategy into the canonical indicator_dsl spec, validated
    fail-closed. Raises ValueError on any invalid structure (caught by the graph)."""
    inds: list[dict] = []
    for i in p.indicators:
        d: dict = {"id": i.id, "fn": i.fn, "source": i.source, "timeframe": i.timeframe}
        for k in ("period", "fast", "slow", "signal", "mult"):
            v = getattr(i, k)
            if v is not None:
                d[k] = v
        inds.append(d)
    spec: dict = {
        "type": p.type,
        "base_timeframe": p.base_timeframe,
        "indicators": inds,
        "buy_when": _block(p.buy),
        "exit_when": _block(p.exit),
    }
    if p.strength is not None:
        spec["strength"] = {"buy": {"from": p.strength.from_indicator,
                                    "map": [p.strength.map_low, p.strength.map_high]}}
    return validate_spec(spec)  # fail-closed → canonical dict
