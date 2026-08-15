from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..deps import get_broker
from ..schemas import AccountCreate, AccountOut
from ..security import require_service_token

router = APIRouter(prefix="/accounts", tags=["accounts"])


@router.post("", response_model=AccountOut, dependencies=[Depends(require_service_token)])
async def create_account(body: AccountCreate, broker=Depends(get_broker)) -> AccountOut:
    existing = await broker.get_account_by_ref(body.external_ref)
    if existing is not None:
        await broker.session.commit()
        return AccountOut.model_validate(existing)
    acct = await broker.create_account(
        body.external_ref, body.starting_cash, body.base_currency
    )
    await broker.session.commit()
    return AccountOut.model_validate(acct)


@router.get("/{account_id}", response_model=AccountOut)
async def get_account(account_id: int, broker=Depends(get_broker)) -> AccountOut:
    acct = await broker.get_account(account_id)
    if acct is None:
        raise HTTPException(404, "account not found")
    return AccountOut.model_validate(acct)


@router.get("/by-ref/{external_ref}", response_model=AccountOut)
async def get_account_by_ref(external_ref: str, broker=Depends(get_broker)) -> AccountOut:
    acct = await broker.get_account_by_ref(external_ref)
    if acct is None:
        raise HTTPException(404, "account not found")
    return AccountOut.model_validate(acct)
