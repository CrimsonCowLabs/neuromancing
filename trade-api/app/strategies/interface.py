"""The one strategy-evaluation surface.

A caller stops importing free functions and branching on `kind`: it builds one
`Strategy` from `(kind, spec)` via `build_strategy` and asks it three questions —
`required_timeframes`, `evaluate`, `backtest`. Every kind speaks bars-keyed-by-timeframe;
single-series kinds (`signal_fn`, `rule_dsl`) collapse the one-entry map internally, so the
single-vs-multi split disappears from the caller's view.

Each concrete class wraps the *existing, unchanged* internals — `SIGNAL_FNS` /
`evaluate_dsl` / `evaluate_composed` for signals, and the shared `_replay` accounting loop
(which books every fill through the live P&L spine and replays `evaluate_exit`) for
backtests. No evaluation logic is reimplemented here; this module only unifies the shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from neuromancing_shared.strategy_spec import base_timeframe, required_timeframes

from .backtest import (
    _WARMUP,
    DEFAULT_ALLOC_PCT,
    DEFAULT_COST_BPS,
    ExitConfig,
    _replay,
)
from .base import Bar, Signal
from .composed import evaluate_composed
from .dsl import evaluate_dsl
from .library import SIGNAL_FNS


# --- config + metrics ------------------------------------------------------
@dataclass(frozen=True)
class BacktestConfig:
    """Equity backtest knobs: fixed-notional sizing, per-side cost, and the exit discipline
    replayed on each bar. Defaults mirror the harness's live-like defaults."""
    starting_cash: float = 10000.0
    alloc_pct: float = DEFAULT_ALLOC_PCT
    cost_bps: float = DEFAULT_COST_BPS
    exit_config: ExitConfig = field(default_factory=ExitConfig)


@dataclass(frozen=True)
class EquityMetrics:
    """Long/flat-or-short/flat backtest metrics — the result shape for every signal kind.
    Fields match `BacktestResult` (minus `symbol`) so the router serializes by unpacking."""
    bars: int
    trades: int
    win_rate: float
    total_return: float
    max_drawdown: float
    final_equity: float

    @classmethod
    def from_replay(cls, m: dict) -> EquityMetrics:
        return cls(**m)


# --- interface -------------------------------------------------------------
@runtime_checkable
class Strategy(Protocol):
    """One deterministic strategy over one instrument's bars. `evaluate` gives the live
    signal; `backtest` replays a track record. Both take bars keyed by timeframe."""

    def required_timeframes(self, request_timeframe: str | None = None) -> list[str]: ...

    def evaluate(self, bars_by_tf: dict[str, list[Bar]]) -> Signal: ...

    def backtest(self, bars_by_tf: dict[str, list[Bar]], config: BacktestConfig) -> EquityMetrics: ...


def _only_series(bars_by_tf: dict[str, list[Bar]]) -> list[Bar]:
    """The single bar series of a single-timeframe kind, whatever timeframe it's keyed by."""
    return next(iter(bars_by_tf.values())) if bars_by_tf else []


class _SingleSeriesStrategy:
    """Base for the single-timeframe, close-only kinds (`signal_fn`, `rule_dsl`). They
    require exactly the request timeframe and evaluate one bar series; the one-entry
    bars-by-tf map is collapsed internally so they share the multi-tf caller shape."""

    def __init__(self, spec: dict):
        self.spec = spec

    def required_timeframes(self, request_timeframe: str | None = None) -> list[str]:
        return [request_timeframe] if request_timeframe is not None else []

    def _signal(self, bars: list[Bar]) -> Signal:
        raise NotImplementedError

    def evaluate(self, bars_by_tf: dict[str, list[Bar]]) -> Signal:
        return self._signal(_only_series(bars_by_tf))

    def backtest(self, bars_by_tf: dict[str, list[Bar]], config: BacktestConfig) -> EquityMetrics:
        bars = _only_series(bars_by_tf)

        def action_at(i: int) -> str | None:
            return self._signal(bars[: i + 1]).action if i + 1 >= _WARMUP else None

        m = _replay(len(bars), lambda i: bars[i].close, action_at,
                    starting_cash=config.starting_cash, alloc_pct=config.alloc_pct,
                    cost_bps=config.cost_bps, exit_config=config.exit_config)
        return EquityMetrics.from_replay(m)


class SignalFnStrategy(_SingleSeriesStrategy):
    """A named hardcoded signal function (`library.py`), event-driven over closes."""

    def _signal(self, bars: list[Bar]) -> Signal:
        fn = SIGNAL_FNS.get(self.spec.get("fn", ""))
        if fn is None:
            raise ValueError(f"unknown signal_fn: {self.spec.get('fn')}")
        return fn([b.close for b in bars], self.spec)


class RuleDslStrategy(_SingleSeriesStrategy):
    """The legacy declarative rule evaluator (`dsl.py`), single-tf, close-only."""

    def _signal(self, bars: list[Bar]) -> Signal:
        return evaluate_dsl(self.spec, [b.close for b in bars])


class IndicatorDslStrategy:
    """The YAML-authored, validated, multi-timeframe indicator-state model (`composed.py`).
    Requires the spec-derived timeframe set (the shared rule); steps 'now' through the base
    timeframe while higher timeframes are filtered as-of by the composed evaluator (no
    lookahead) — the exact behavior of the former `backtest_multi`."""

    def __init__(self, spec: dict):
        self.spec = spec

    def required_timeframes(self, request_timeframe: str | None = None) -> list[str]:
        return required_timeframes(self.spec)

    def evaluate(self, bars_by_tf: dict[str, list[Bar]]) -> Signal:
        return evaluate_composed(self.spec, bars_by_tf)

    def backtest(self, bars_by_tf: dict[str, list[Bar]], config: BacktestConfig) -> EquityMetrics:
        base_tf = base_timeframe(self.spec)
        base = bars_by_tf.get(base_tf) or []
        higher = {tf: bb for tf, bb in bars_by_tf.items() if tf != base_tf}

        def action_at(i: int) -> str | None:
            if i + 1 < _WARMUP:
                return None
            window = {base_tf: base[: i + 1], **higher}  # higher tfs filtered as-of by composed
            return evaluate_composed(self.spec, window).action

        m = _replay(len(base), lambda i: base[i].close, action_at,
                    starting_cash=config.starting_cash, alloc_pct=config.alloc_pct,
                    cost_bps=config.cost_bps, exit_config=config.exit_config)
        return EquityMetrics.from_replay(m)


_SIGNAL_KINDS = {
    "signal_fn": SignalFnStrategy,
    "rule_dsl": RuleDslStrategy,
    "indicator_dsl": IndicatorDslStrategy,
}


def build_strategy(kind: str, spec: dict) -> Strategy:
    """Build the one `Strategy` for `(kind, spec)`. Fails loudly at construction on an
    unknown kind — a caller never silently gets a no-op strategy."""
    cls = _SIGNAL_KINDS.get(kind)
    if cls is None:
        raise ValueError(f"unknown strategy kind: {kind}")
    return cls(spec)
