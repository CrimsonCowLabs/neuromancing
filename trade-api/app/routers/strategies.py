from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
    SignalOut,
    StrategyCreate,
    StrategyOut,
    StrategyStatusUpdate,
)
from ..security import require_service_token
from ..strategies import evaluate
from ..strategies.backtest import backtest, backtest_multi
from ..strategies.composed import required_timeframes
from ..strategies.data import load_bars
from ..strategies.engine import evaluate_multi, list_house_strategies
from ..strategies.library import SIGNAL_FNS
from ..strategies.spec import validate_spec

router = APIRouter(prefix="/strategies", tags=["strategies"])


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

    kind = strat.kind.value
    # indicator_dsl carries its own (possibly multiple) timeframes; those win over
    # the request's timeframe. Single-series kinds use the request timeframe.
    tfs = required_timeframes(strat.spec) if kind == "indicator_dsl" else None

    out: list[SignalOut] = []
    for symbol in body.symbols:
        if kind == "indicator_dsl":
            bars_by_tf = {}
            for tf in tfs:  # deduped; each hot-cache served
                bb = await load_bars(session, symbol, tf, body.lookback)
                if bb:
                    bars_by_tf[tf] = bb
            if not bars_by_tf:
                continue
            sig = evaluate_multi(kind, strat.spec, bars_by_tf)
        else:
            bars = await load_bars(session, symbol, body.timeframe, body.lookback)
            if not bars:
                continue
            sig = evaluate(kind, strat.spec, bars)
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
    if body.kind == "indicator_dsl":
        bars_by_tf = {}
        for tf in required_timeframes(spec):
            bb = await load_bars(session, body.symbol, tf, 5000, window=window)
            if bb:
                bars_by_tf[tf] = bb
        if not bars_by_tf:
            raise HTTPException(400, "no bars for symbol/window")
        metrics = backtest_multi(body.kind, spec, bars_by_tf, body.starting_cash)
    else:
        bars = await load_bars(session, body.symbol, "1m", 5000, window=window)
        if not bars:
            raise HTTPException(400, "no bars for symbol/window")
        metrics = backtest(body.kind, spec, bars, body.starting_cash)
    return BacktestResult(symbol=body.symbol, **metrics)


@router.post("/{strategy_id}/backtest", response_model=BacktestResult,
             dependencies=[Depends(require_service_token)])
async def backtest_strategy(
    strategy_id: int, body: BacktestRequest, session: AsyncSession = Depends(get_session)
) -> BacktestResult:
    strat = await session.get(Strategy, strategy_id)
    if strat is None:
        raise HTTPException(404, "strategy not found")
    if strat.kind.value == "indicator_dsl":
        bars_by_tf = {}
        for tf in required_timeframes(strat.spec):
            bb = await load_bars(session, body.symbol, tf, body.limit)
            if bb:
                bars_by_tf[tf] = bb
        if not bars_by_tf:
            raise HTTPException(400, "no bars for symbol")
        metrics = backtest_multi(strat.kind.value, strat.spec, bars_by_tf, body.starting_cash)
    else:
        bars = await load_bars(session, body.symbol, body.timeframe, body.limit)
        if not bars:
            raise HTTPException(400, "no bars for symbol")
        metrics = backtest(strat.kind.value, strat.spec, bars, body.starting_cash)
    return BacktestResult(symbol=body.symbol, **metrics)
