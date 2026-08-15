from __future__ import annotations

from fastapi import APIRouter

from ..leaderboard import current

router = APIRouter(tags=["leaderboard"])


@router.get("/leaderboard")
async def get_leaderboard() -> dict:
    return await current()
