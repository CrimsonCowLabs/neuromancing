from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from neuromancing_shared.db import make_engine, make_sessionmaker

from .config import get_settings

_settings = get_settings()
engine = make_engine(_settings.database_url)
SessionLocal = make_sessionmaker(engine)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
