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
from neuromancing_shared.options_strategy import validate_structure

from ..config import get_settings
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
from ..strategies import OPTION_STRUCTURE_KIND, build_strategy, list_house_strategies
from ..strategies.backtest import DEFAULT_ALLOC_PCT, DEFAULT_COST_BPS, ExitConfig
from ..strategies.data import load_bars
from ..strategies.interface import BacktestConfig
from ..strategies.interface import Strategy as StrategyInterface
from ..strategies.library import SIGNAL_FNS
from ..strategies.options_backtest import RV_WINDOW
from ..strategies.spec import validate_spec

router = APIRouter(prefix="/strategies", tags=["strategies"])


def _bt_config(body: AdhocBacktestRequest | BacktestRequest) -> BacktestConfig:
    """Build the one backtest `config` from a request's optional sizing/cost/exit fields,
    applying the harness defaults for anything omitted."""
    return BacktestConfig(
        starting_cash=body.starting_cash,
        alloc_pct=body.alloc_pct if body.alloc_pct is not None else DEFAULT_ALLOC_PCT,
        cost_bps=body.cost_bps if body.cost_bps is not None else DEFAULT_COST_BPS,
        exit_config=ExitConfig(
            stop_loss_pct=body.stop_loss_pct,
            take_profit_pct=body.take_profit_pct,
            trailing_stop_pct=body.trailing_stop_pct,
        ),
    )


async def _load_bars_by_tf(
    session: AsyncSession, strat: StrategyInterface, symbol: str, limit: int, *,
    request_timeframe: str | None = None, window=None,
) -> dict[str, list]:
    """The one bar loader for every endpoint: ask the strategy which timeframes it needs,
    then load each (deduped; each hot-cache served). Replaces the three copy-pasted
    per-timeframe loops + `kind ==` branches the router used to carry."""
    bars_by_tf: dict[str, list] = {}
    for tf in strat.required_timeframes(request_timeframe):
        bb = await load_bars(session, symbol, tf, limit, window=window)
        if bb:
            bars_by_tf[tf] = bb
    return bars_by_tf


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
    row = await session.get(Strategy, strategy_id)
    if row is None:
        raise HTTPException(404, "strategy not found")

    # One interface: indicator_dsl carries its own (possibly multiple) timeframes; those win
    # over the request's; single-series kinds use the request timeframe — the strategy owns
    # that rule now, so the router no longer branches on `kind`.
    strat = build_strategy(row.kind.value, row.spec)

    out: list[SignalOut] = []
    for symbol in body.symbols:
        bars_by_tf = await _load_bars_by_tf(
            session, strat, symbol, body.lookback, request_timeframe=body.timeframe)
        if not bars_by_tf:
            continue
        sig = strat.evaluate(bars_by_tf)
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
    strat = build_strategy(body.kind, spec)
    # Ad-hoc single-series kinds default to 1m (they carry no request timeframe);
    # indicator_dsl ignores it and uses its own spec timeframes.
    bars_by_tf = await _load_bars_by_tf(
        session, strat, body.symbol, 5000, request_timeframe="1m", window=window)
    if not bars_by_tf:
        raise HTTPException(400, "no bars for symbol/window")
    metrics = strat.backtest(bars_by_tf, _bt_config(body))
    return BacktestResult(symbol=body.symbol, **metrics.to_dict())


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
    strat = build_strategy(OPTION_STRUCTURE_KIND, spec)
    config = BacktestConfig(
        starting_cash=body.starting_cash, r=s.options_risk_free_rate, q=s.options_div_yield,
        vrp=s.options_vrp_mult, skew=s.options_skew, term=s.options_term)
    window = (body.window.start, body.window.end) if body.window else None

    # Cross-underlying aggregation stays a ROUTER concern: the interface is per-series (one
    # structure, one underlying's bars, one result); the router loops and aggregates.
    per: list[dict] = []
    for u in body.underlyings:
        bars = await load_bars(session, u.upper(), "1d", 5000, window=window)
        if len(bars) < RV_WINDOW + 5:
            continue
        m = strat.backtest({"1d": bars}, config).to_dict()
        m["underlying"] = u.upper()
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
    row = await session.get(Strategy, strategy_id)
    if row is None:
        raise HTTPException(404, "strategy not found")
    strat = build_strategy(row.kind.value, row.spec)
    bars_by_tf = await _load_bars_by_tf(
        session, strat, body.symbol, body.limit, request_timeframe=body.timeframe)
    if not bars_by_tf:
        raise HTTPException(400, "no bars for symbol")
    metrics = strat.backtest(bars_by_tf, _bt_config(body))
    return BacktestResult(symbol=body.symbol, **metrics.to_dict())
