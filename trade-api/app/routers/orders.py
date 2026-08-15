from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..deps import get_broker
from ..schemas import FillOut, OrderCreate, OrderOut
from ..security import require_service_token

router = APIRouter(prefix="/accounts/{account_id}/orders", tags=["orders"])


@router.post("", response_model=OrderOut, dependencies=[Depends(require_service_token)])
async def place_order(
    account_id: int, body: OrderCreate, broker=Depends(get_broker)
) -> OrderOut:
    try:
        order = await broker.place_order(account_id, body)
    except ValueError as e:
        await broker.session.rollback()
        raise HTTPException(400, str(e))
    await broker.session.commit()
    return OrderOut.model_validate(order)


@router.get("", response_model=list[OrderOut])
async def list_orders(
    account_id: int, limit: int = 100, offset: int = 0,
    exclude_rejected: bool = False, broker=Depends(get_broker),
) -> list[OrderOut]:
    orders = await broker.list_orders(account_id, limit, offset, exclude_rejected)
    return [OrderOut.model_validate(o) for o in orders]


@router.get("/{order_id}", response_model=OrderOut)
async def get_order(account_id: int, order_id: int, broker=Depends(get_broker)) -> OrderOut:
    order = await broker.get_order(order_id)
    if order is None or order.account_id != account_id:
        raise HTTPException(404, "order not found")
    return OrderOut.model_validate(order)


@router.get("/{order_id}/fills", response_model=list[FillOut])
async def order_fills(
    account_id: int, order_id: int, broker=Depends(get_broker)
) -> list[FillOut]:
    order = await broker.get_order(order_id)
    if order is None or order.account_id != account_id:
        raise HTTPException(404, "order not found")
    await broker.session.refresh(order, ["fills"])
    return [FillOut.model_validate(f) for f in order.fills]


@router.delete("/{order_id}", response_model=OrderOut, dependencies=[Depends(require_service_token)])
async def cancel_order(account_id: int, order_id: int, broker=Depends(get_broker)) -> OrderOut:
    order = await broker.cancel_order(order_id)
    if order is None or order.account_id != account_id:
        raise HTTPException(404, "order not found")
    await broker.session.commit()
    return OrderOut.model_validate(order)
