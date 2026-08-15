from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from neuromancing_shared.redisio import make_redis

from .brokers import SimBroker
from .config import get_settings
from .db import get_session

_redis = make_redis(get_settings().redis_url)


def get_redis():
    return _redis


async def get_broker(
    session: AsyncSession = Depends(get_session),
) -> AsyncIterator[SimBroker]:
    yield SimBroker(session, _redis)
