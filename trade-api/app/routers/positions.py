from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Body, Depends, HTTPException

from ..deps import get_broker
from ..schemas import EquityOut, ExitOut, PositionOut
from ..security import require_service_token

router = APIRouter(prefix="/accounts/{account_id}", tags=["positions"])

# Global (cross-account) endpoint for the position-monitor.
exits_router = APIRouter(prefix="/positions", tags=["exits"])


@exits_router.post(
    "/settle-exits",
    response_model=list[ExitOut],
    dependencies=[Depends(require_service_token)],
)
async def settle_exits(tick_id: str = "monitor", broker=Depends(get_broker)) -> list[ExitOut]:
    exits = await broker.settle_exits(tick_id)
    await broker.session.commit()
    return [ExitOut(**e) for e in exits]


@router.get("/positions", response_model=list[PositionOut])
async def list_positions(account_id: int, broker=Depends(get_broker)) -> list[PositionOut]:
    positions = await broker.list_positions(account_id)
    return [PositionOut.model_validate(p) for p in positions]


@router.post(
    "/mark-to-market",
    response_model=EquityOut,
    dependencies=[Depends(require_service_token)],
)
async def mark_to_market(
    account_id: int,
    marks: dict[str, Decimal] = Body(..., embed=True),
    broker=Depends(get_broker),
) -> EquityOut:
    acct = await broker.get_account(account_id)
    if acct is None:
        raise HTTPException(404, "account not found")
    snap = await broker.mark_to_market(account_id, marks)
    await broker.session.commit()
    return EquityOut(
        account_id=account_id,
        ts=snap.ts,
        cash=snap.cash,
        positions_value=snap.positions_value,
        total_equity=snap.total_equity,
        realized_pnl=snap.realized_pnl,
        unrealized_pnl=snap.unrealized_pnl,
    )
