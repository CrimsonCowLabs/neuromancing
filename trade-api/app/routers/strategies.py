from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from neuromancing_shared.options_strategy import validate_structure
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_session
from ..models import (
    SignalAction,
    Strategy,
    StrategyKind,
    StrategyOwner,
    StrategySignal,
    StrategyStatus,
)
from ..schemas import (
    AdhocBacktestRequest,
    BacktestRequest,
    BacktestResult,
    EvaluateRequest,
    OptionsBacktestRequest,
    OptionsBacktestResult,
    SignalOut,
    StrategyCreate,
    StrategyOut,
    StrategyStatusUpdate,
)
from ..security import require_service_token
from ..strategies.data import load_bars
from ..strategies.engine import list_house_strategies
from ..strategies.interface import (
    BacktestConfig,
    EquityMetrics,
    ExitConfig,
    OptionsBacktestConfig,
    build_strategy,
)
from ..strategies.library import SIGNAL_FNS
from ..strategies.options_backtest import RV_WINDOW
from ..strategies.spec import validate_spec

router = APIRouter(prefix="/strategies", tags=["strategies"])


def _bt_config(body: AdhocBacktestRequest | BacktestRequest) -> BacktestConfig:
    """Turn a backtest request's optional sizing/cost/exit fields into the one backtest
    `config`, applying the harness's live-like defaults for anything omitted."""
    knobs = {}
    if body.alloc_pct is not None:
        knobs["alloc_pct"] = body.alloc_pct
    if body.cost_bps is not None:
        knobs["cost_bps"] = body.cost_bps
    return BacktestConfig(
        starting_cash=body.starting_cash,
        exit_config=ExitConfig(
            stop_loss_pct=body.stop_loss_pct,
            take_profit_pct=body.take_profit_pct,
            trailing_stop_pct=body.trailing_stop_pct,
        ),
        **knobs,
    )


async def _load_by_tf(
    session: AsyncSession, symbol: str, tfs: list[str], limit: int, *, window=None
) -> dict[str, list]:
    """The one per-timeframe bar loader driven by `strat.required_timeframes(...)`. Each
    (deduped) timeframe is served from its hot cache; empty series are dropped."""
    out: dict[str, list] = {}
    for tf in tfs:
        bb = await load_bars(session, symbol, tf, limit, window=window)
        if bb:
            out[tf] = bb
    return out


def _equity_result(symbol: str, metrics: EquityMetrics) -> BacktestResult:
    """Serialize the equity arm of the Metrics union to the HTTP response model."""
    return BacktestResult(
        symbol=symbol, bars=metrics.bars, trades=metrics.trades, win_rate=metrics.win_rate,
        total_return=metrics.total_return, max_drawdown=metrics.max_drawdown,
        final_equity=metrics.final_equity,
    )


def _validate_for_kind(kind: str, spec: dict) -> dict:
    """Fail-closed spec validation on create (closes the old blind-store gap).
    Returns the canonical spec to persist; raises ValueError on any problem."""
    if kind == "indicator_dsl":
        return validate_spec(spec)
    if kind == "signal_fn":
        if spec.get("fn") not in SIGNAL_FNS:
            raise ValueError(f"unknown signal_fn: {spec.get('fn')}")
    elif kind == "rule_dsl":
        if not (isinstance(spec.get("buy_when"), dict) or isinstance(spec.get("exit_when"), dict)):
            raise ValueError("rule_dsl needs a buy_when and/or exit_when group")
    return spec


@router.post("", response_model=StrategyOut, dependencies=[Depends(require_service_token)])
async def create_strategy(
    body: StrategyCreate, session: AsyncSession = Depends(get_session)
) -> StrategyOut:
    try:
        spec = _validate_for_kind(body.kind, body.spec)
    except ValueError as e:
        raise HTTPException(422, f"invalid strategy spec: {e}")
    strat = Strategy(
        name=body.name,
        kind=StrategyKind(body.kind),
        spec=spec,
        owner_type=StrategyOwner(body.owner_type),
        owner_ref=body.owner_ref,
        version=body.version,
        status=StrategyStatus(body.status),
    )
    session.add(strat)
    await session.commit()
    return StrategyOut.model_validate(strat)


@router.post(
    "/{strategy_id}/status", response_model=StrategyOut,
    dependencies=[Depends(require_service_token)],
)
async def set_strategy_status(
    strategy_id: int, body: StrategyStatusUpdate, session: AsyncSession = Depends(get_session)
) -> StrategyOut:
    """Transition a strategy's lifecycle status (draft/active/retired) — used by the
    deep agent to retire a superseded evolved strategy (TODO #3)."""
    strat = await session.get(Strategy, strategy_id)
    if strat is None:
        raise HTTPException(404, "strategy not found")
    try:
        strat.status = StrategyStatus(body.status)
    except ValueError:
        raise HTTPException(422, f"invalid status: {body.status}")
    await session.commit()
    return StrategyOut.model_validate(strat)


@router.post("/seed", response_model=list[StrategyOut], dependencies=[Depends(require_service_token)])
async def seed_house_strategies(session: AsyncSession = Depends(get_session)) -> list[StrategyOut]:
    """Idempotently create the house strategy catalog."""
    out: list[Strategy] = []
    for defn in list_house_strategies():
        existing = await session.execute(select(Strategy).where(Strategy.name == defn["name"]))
        strat = existing.scalar_one_or_none()
        if strat is None:
            strat = Strategy(
                name=defn["name"],
                kind=StrategyKind(defn["kind"]),
                spec=defn["spec"],
                owner_type=StrategyOwner.house,
            )
            session.add(strat)
        out.append(strat)
    await session.commit()
    return [StrategyOut.model_validate(s) for s in out]


@router.get("", response_model=list[StrategyOut])
async def list_strategies(session: AsyncSession = Depends(get_session)) -> list[StrategyOut]:
    res = await session.execute(select(Strategy).order_by(Strategy.id))
    return [StrategyOut.model_validate(s) for s in res.scalars().all()]


@router.get("/{strategy_id}", response_model=StrategyOut)
async def get_strategy(strategy_id: int, session: AsyncSession = Depends(get_session)) -> StrategyOut:
    strat = await session.get(Strategy, strategy_id)
    if strat is None:
        raise HTTPException(404, "strategy not found")
    return StrategyOut.model_validate(strat)


@router.post(
    "/{strategy_id}/evaluate",
    response_model=list[SignalOut],
    dependencies=[Depends(require_service_token)],
)
async def evaluate_strategy(
    strategy_id: int, body: EvaluateRequest, session: AsyncSession = Depends(get_session)
) -> list[SignalOut]:
    """Run a deterministic strategy over recent bars for each symbol, optionally
    persisting strategy_signal rows. This is what the game-api decision loop calls."""
    strat = await session.get(Strategy, strategy_id)
    if strat is None:
        raise HTTPException(404, "strategy not found")

    # One interface: indicator_dsl derives its own (possibly multiple) timeframes; single
    # kinds require the request timeframe. Both then load → evaluate the same way.
    strategy = build_strategy(strat.kind.value, strat.spec)
    tfs = strategy.required_timeframes(request_timeframe=body.timeframe)

    out: list[SignalOut] = []
    for symbol in body.symbols:
        bars_by_tf = await _load_by_tf(session, symbol, tfs, body.lookback)
        if not bars_by_tf:
            continue
        sig = strategy.evaluate(bars_by_tf)
        out.append(
            SignalOut(symbol=symbol, action=sig.action, strength=sig.strength, features=sig.features)
        )
        if body.persist and sig.action != "hold":
            session.add(
                StrategySignal(
                    strategy_id=strategy_id,
                    agent_ref=body.agent_ref,
                    symbol=symbol,
                    action=SignalAction(sig.action),
                    strength=Decimal(str(round(sig.strength, 4))),
                    features=sig.features,
                )
            )
    if body.persist:
        await session.commit()
    return out


@router.post("/backtest", response_model=BacktestResult,
             dependencies=[Depends(require_service_token)])
async def backtest_adhoc(
    body: AdhocBacktestRequest, session: AsyncSession = Depends(get_session)
) -> BacktestResult:
    """Backtest an ad-hoc spec WITHOUT persisting it (deep-agent iteration, TODO #3).
    Validates the spec fail-closed, then backtests on the spec's own timeframes over an
    optional calendar `window` (walk-forward)."""
    try:
        spec = _validate_for_kind(body.kind, body.spec)
    except ValueError as e:
        raise HTTPException(422, f"invalid strategy spec: {e}")
    window = (body.window.start, body.window.end) if body.window else None
    strategy = build_strategy(body.kind, spec)
    # single-series kinds backtest on "1m"; indicator_dsl derives its own timeframes.
    tfs = strategy.required_timeframes(request_timeframe="1m")
    bars_by_tf = await _load_by_tf(session, body.symbol, tfs, 5000, window=window)
    if not bars_by_tf:
        raise HTTPException(400, "no bars for symbol/window")
    metrics = strategy.backtest(bars_by_tf, _bt_config(body))
    return _equity_result(body.symbol, metrics)


@router.post("/options-backtest", response_model=OptionsBacktestResult,
             dependencies=[Depends(require_service_token)])
async def options_backtest_adhoc(
    body: OptionsBacktestRequest, session: AsyncSession = Depends(get_session)
) -> OptionsBacktestResult:
    """Backtest a defined-risk option structure (v1) over one or more underlyings using the
    synthetic Black-Scholes chain (backtest-only; nothing is persisted or traded live)."""
    try:
        spec = validate_structure(body.structure)
    except ValueError as e:
        raise HTTPException(422, f"invalid option structure: {e}")
    s = get_settings()
    config = OptionsBacktestConfig(
        starting_cash=body.starting_cash, r=s.options_risk_free_rate, q=s.options_div_yield,
        vrp=s.options_vrp_mult, skew=s.options_skew, term=s.options_term)
    window = (body.window.start, body.window.end) if body.window else None

    # The interface is per-series (one structure, one underlying's daily bars); cross-underlying
    # aggregation stays a router concern.
    strategy = build_strategy("option_structure", spec)
    tfs = strategy.required_timeframes()
    per: list[dict] = []
    for u in body.underlyings:
        bars_by_tf = await _load_by_tf(session, u.upper(), tfs, 5000, window=window)
        if len(bars_by_tf.get("1d", [])) < RV_WINDOW + 5:
            continue
        metrics = strategy.backtest(bars_by_tf, config)
        m = {"trades": metrics.trades, "win_rate": metrics.win_rate,
             "total_return": metrics.total_return, "max_drawdown": metrics.max_drawdown,
             "avg_credit": metrics.avg_credit, "avg_return_on_risk": metrics.avg_return_on_risk,
             "assignment_rate": metrics.assignment_rate, "final_equity": metrics.final_equity,
             "underlying": u.upper()}
        per.append(m)
    if not per:
        raise HTTPException(400, "no daily bars for any requested underlying")

    tot = sum(p["trades"] for p in per) or 1
    return OptionsBacktestResult(
        archetype=spec["archetype"],
        underlyings=[p["underlying"] for p in per],
        trades=sum(p["trades"] for p in per),
        win_rate=round(sum(p["win_rate"] * p["trades"] for p in per) / tot, 4),
        total_return=round(sum(p["total_return"] for p in per) / len(per), 6),
        max_drawdown=round(max(p["max_drawdown"] for p in per), 6),
        avg_credit=round(sum(p["avg_credit"] * p["trades"] for p in per) / tot, 2),
        avg_return_on_risk=round(sum(p["avg_return_on_risk"] * p["trades"] for p in per) / tot, 4),
        assignment_rate=round(sum(p["assignment_rate"] * p["trades"] for p in per) / tot, 4),
        per_underlying=per,
    )


@router.post("/{strategy_id}/backtest", response_model=BacktestResult,
             dependencies=[Depends(require_service_token)])
async def backtest_strategy(
    strategy_id: int, body: BacktestRequest, session: AsyncSession = Depends(get_session)
) -> BacktestResult:
    strat = await session.get(Strategy, strategy_id)
    if strat is None:
        raise HTTPException(404, "strategy not found")
    strategy = build_strategy(strat.kind.value, strat.spec)
    tfs = strategy.required_timeframes(request_timeframe=body.timeframe)
    bars_by_tf = await _load_by_tf(session, body.symbol, tfs, body.limit)
    if not bars_by_tf:
        raise HTTPException(400, "no bars for symbol")
    metrics = strategy.backtest(bars_by_tf, _bt_config(body))
    return _equity_result(body.symbol, metrics)
