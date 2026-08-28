"""The one strategy-evaluation surface (issue #8).

A caller stops importing seven free functions and branching on `kind`. It builds one
`Strategy` from `(kind, spec)` via `build_strategy` and asks it three questions:

    strat   = build_strategy(kind, spec)      # one factory, one vocabulary of kinds
    tfs     = strat.required_timeframes(req_tf)
    bars    = load(tfs)                        # caller still owns I/O
    sig     = strat.evaluate(bars)            # live signal
    metrics = strat.backtest(bars, config)    # historical replay

Every kind — `signal_fn`, `rule_dsl`, `indicator_dsl`, and the backtest-only option
structure — implements this same interface. The single-vs-multi split disappears: every
strategy speaks **bars-keyed-by-timeframe**, and a single-timeframe kind simply collapses a
one-entry map internally. Each class wraps the *existing, unchanged* internals (the DSL
evaluators, the composed engine, the P&L-spine replay, the options pricer) — this is a pure
surface/shape collapse, not a behavior change.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field

from neuromancing_shared.strategy_spec import (
    base_timeframe,
    required_timeframes as _spec_required_timeframes,
)

from .backtest import (
    DEFAULT_ALLOC_PCT,
    DEFAULT_COST_BPS,
    WARMUP,
    ExitConfig,
    replay,
)
from .base import HOLD, Bar, Signal
from .composed import evaluate_composed
from .dsl import evaluate_dsl
from .library import SIGNAL_FNS
from .options_backtest import backtest_structure

OPTION_STRUCTURE_KIND = "option_structure"  # ad-hoc, backtest-only; NOT a persisted StrategyKind

_EQUITY_DEFAULT_CASH = 10_000.0
_OPTIONS_DEFAULT_CASH = 100_000.0


# ── config + result types ────────────────────────────────────────────────
@dataclass(frozen=True)
class BacktestConfig:
    """One backtest knob-bag carried through the single `Strategy.backtest` signature.
    Equity kinds read the sizing/cost/exit knobs; the options kind reads the pricing knobs;
    `starting_cash` is shared (None → each kind's own historical default). Keeping both knob
    families in one object stops a third family from reintroducing a second backtest surface."""

    starting_cash: float | None = None
    # equity: sizing / cost / exit
    alloc_pct: float = DEFAULT_ALLOC_PCT
    cost_bps: float = DEFAULT_COST_BPS
    exit_config: ExitConfig = field(default_factory=ExitConfig)
    # options: pricing. These bare-config defaults MIRROR the options pricer's own signature
    # defaults (`open_structure`/`mtm_pnl_per_contract`/`backtest_structure`), preserved so a
    # bare `BacktestConfig()` reproduces the old free-function numbers. They are NOT the
    # production source of truth: the options endpoint always overrides them from `Settings`
    # (options_vrp_mult/options_skew/…), so this default set only ever surfaces in unit tests.
    r: float = 0.04
    q: float = 0.0
    vrp: float = 1.15
    skew: float = 0.35
    term: float = 0.0


@dataclass(frozen=True)
class EquityMetrics:
    """Track record of an equity (long/flat OR short/flat) replay."""

    bars: int
    trades: int
    win_rate: float
    total_return: float
    max_drawdown: float
    final_equity: float
    tag: str = "equity"

    def to_dict(self) -> dict:
        return _metric_fields(self)


@dataclass(frozen=True)
class OptionsMetrics:
    """Track record of a defined-risk option-structure replay."""

    trades: int
    win_rate: float
    total_return: float
    max_drawdown: float
    avg_credit: float
    avg_return_on_risk: float
    assignment_rate: float
    final_equity: float
    tag: str = "options"

    def to_dict(self) -> dict:
        return _metric_fields(self)


# A backtest returns one of two shapes; the `tag` discriminant lets a consumer serialize to
# the correct response model (BacktestResult vs OptionsBacktestResult) without guessing.
Metrics = EquityMetrics | OptionsMetrics


def _metric_fields(m: Metrics) -> dict:
    """The metric's fields WITHOUT the `tag` discriminant — the exact dict the old free
    functions returned, so `SomeResult(**metrics.to_dict())` matches the pre-collapse splat."""
    return {k: v for k, v in asdict(m).items() if k != "tag"}


def _cash(config: BacktestConfig, default: float) -> float:
    return config.starting_cash if config.starting_cash is not None else default


def _only_series(bars_by_tf: dict[str, list[Bar]]) -> list[Bar]:
    """The single series of a single-timeframe kind's one-entry bars-by-tf map."""
    return next(iter(bars_by_tf.values()), [])


def _equity_replay(n_bars: int, price_at, action_at, config: BacktestConfig) -> EquityMetrics:
    """Book an equity (long/flat OR short/flat) replay through the P&L spine and wrap the
    result. Shared by every equity kind so the single- and multi-timeframe paths are booked
    identically — only `n_bars`, `price_at`, and `action_at` differ between them."""
    m = replay(n_bars, price_at, action_at,
               starting_cash=_cash(config, _EQUITY_DEFAULT_CASH),
               alloc_pct=config.alloc_pct, cost_bps=config.cost_bps,
               exit_config=config.exit_config)
    return EquityMetrics(**m)


# ── the interface ────────────────────────────────────────────────────────
class Strategy(ABC):
    """The whole evaluation surface behind three questions: what data do you need, what's
    your signal on it, and how would you have done on this history."""

    @abstractmethod
    def required_timeframes(self, request_timeframe: str | None = None) -> list[str]:
        """The distinct timeframes this strategy needs loaded. indicator_dsl derives its own
        set from the spec; single-series kinds use `[request_timeframe]`; options uses ['1d']."""

    @abstractmethod
    def evaluate(self, bars_by_tf: dict[str, list[Bar]]) -> Signal:
        """The live signal on bars keyed by timeframe."""

    @abstractmethod
    def backtest(self, bars_by_tf: dict[str, list[Bar]],
                 config: BacktestConfig | None = None) -> Metrics:
        """The historical track record on bars keyed by timeframe."""


class _SingleSeriesStrategy(Strategy):
    """Base for the close-only, single-price-series kinds (signal_fn / rule_dsl). Speaks the
    universal bars-by-timeframe input by collapsing the one-entry map to its single series,
    then replays through the exact same P&L spine as the multi-tf path."""

    def __init__(self, spec: dict):
        self.spec = spec

    def required_timeframes(self, request_timeframe: str | None = None) -> list[str]:
        return [request_timeframe or "1m"]

    def _signal(self, bars: list[Bar]) -> Signal:  # kind-specific
        raise NotImplementedError

    def evaluate(self, bars_by_tf: dict[str, list[Bar]]) -> Signal:
        return self._signal(_only_series(bars_by_tf))

    def backtest(self, bars_by_tf: dict[str, list[Bar]],
                 config: BacktestConfig | None = None) -> EquityMetrics:
        config = config or BacktestConfig()
        bars = _only_series(bars_by_tf)

        def action_at(i: int) -> str | None:
            return self._signal(bars[: i + 1]).action if i + 1 >= WARMUP else None

        return _equity_replay(len(bars), lambda i: bars[i].close, action_at, config)


class SignalFnStrategy(_SingleSeriesStrategy):
    """`signal_fn` — a named hardcoded function from `library.py`, event-driven."""

    def _signal(self, bars: list[Bar]) -> Signal:
        closes = [b.close for b in bars]
        fn = SIGNAL_FNS.get(self.spec.get("fn", ""))
        if fn is None:
            raise ValueError(f"unknown signal_fn: {self.spec.get('fn')}")
        return fn(closes, self.spec)


class RuleDslStrategy(_SingleSeriesStrategy):
    """`rule_dsl` — the legacy declarative evaluator (`dsl.py`), single-tf, close-only."""

    def _signal(self, bars: list[Bar]) -> Signal:
        return evaluate_dsl(self.spec, [b.close for b in bars])


class IndicatorDslStrategy(Strategy):
    """`indicator_dsl` — the validated, multi-timeframe, indicator-state model. Steps 'now'
    through the BASE timeframe only; higher-tf series are passed whole and the composed
    evaluator's as-of filter drops any bar not yet closed, so there is NO lookahead."""

    def __init__(self, spec: dict):
        self.spec = spec

    def required_timeframes(self, request_timeframe: str | None = None) -> list[str]:
        return _spec_required_timeframes(self.spec)  # spec's own tfs win over the request's

    def evaluate(self, bars_by_tf: dict[str, list[Bar]]) -> Signal:
        return evaluate_composed(self.spec, bars_by_tf)

    def backtest(self, bars_by_tf: dict[str, list[Bar]],
                 config: BacktestConfig | None = None) -> EquityMetrics:
        config = config or BacktestConfig()
        base_tf = base_timeframe(self.spec)
        base = bars_by_tf.get(base_tf) or []
        higher = {tf: bb for tf, bb in bars_by_tf.items() if tf != base_tf}

        def action_at(i: int) -> str | None:
            if i + 1 < WARMUP:
                return None
            window = {base_tf: base[: i + 1], **higher}  # higher tfs filtered as-of by composed
            return evaluate_composed(self.spec, window).action

        return _equity_replay(len(base), lambda i: base[i].close, action_at, config)


class OptionStructureStrategy(Strategy):
    """A defined-risk option structure — backtest-only. `evaluate()` is inert BY TYPE: an
    option structure is never traded live in the game, so it can never emit a live signal;
    it always returns HOLD. `backtest()` returns the options-metric shape."""

    def __init__(self, spec: dict):
        self.spec = spec

    def required_timeframes(self, request_timeframe: str | None = None) -> list[str]:
        return ["1d"]  # options are marked on daily underlying bars

    def evaluate(self, bars_by_tf: dict[str, list[Bar]]) -> Signal:
        return HOLD  # inert — options are backtest-only, never a live signal

    def backtest(self, bars_by_tf: dict[str, list[Bar]],
                 config: BacktestConfig | None = None) -> OptionsMetrics:
        config = config or BacktestConfig()
        bars = _only_series(bars_by_tf)
        m = backtest_structure(self.spec, bars,
                               starting_cash=_cash(config, _OPTIONS_DEFAULT_CASH),
                               r=config.r, q=config.q, vrp=config.vrp,
                               skew=config.skew, term=config.term)
        return OptionsMetrics(**m)


_BUILDERS = {
    "signal_fn": SignalFnStrategy,
    "rule_dsl": RuleDslStrategy,
    "indicator_dsl": IndicatorDslStrategy,
    OPTION_STRUCTURE_KIND: OptionStructureStrategy,
}


def build_strategy(kind: str, spec: dict) -> Strategy:
    """The one factory for the whole evaluation surface. `kind` is a SUPERSET of the
    persisted `StrategyKind` enum — it adds `option_structure` for the ad-hoc, backtest-only
    options path (never a DB enum value). An unknown kind fails loudly HERE, at construction,
    rather than deep inside a dispatch branch."""
    try:
        cls = _BUILDERS[kind]
    except KeyError:
        raise ValueError(f"unknown strategy kind: {kind}")
    return cls(spec)
