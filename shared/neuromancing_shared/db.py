"""Async SQLAlchemy engine/session helpers.

Both services share one Postgres cluster but own separate schemas ("trade" and
"game"). Each service creates its own engine pointed at DATABASE_URL and sets its
schema via the model metadata (see each service's models base).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def make_engine(url: str, *, echo: bool = False) -> AsyncEngine:
    return create_async_engine(url, echo=echo, pool_pre_ping=True)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def session_dependency(
    maker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Generator suitable for wrapping in a FastAPI dependency."""
    async with maker() as session:
        yield session
