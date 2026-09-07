"""Async engine and session factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import Base

log = get_logger(__name__)

engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    future=True,
    # SQLite holds a short write lock; without this, a WebSocket turn writing
    # history while an HTTP request reads it raises "database is locked".
    connect_args={"timeout": 30} if settings.database_url.startswith("sqlite") else {},
)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def init_db() -> None:
    settings.ensure_dirs()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def dispose_db() -> None:
    """Best-effort cleanup on the way out.

    Shutdown must not raise. By the time this runs the loop may already be
    closing — Starlette's TestClient tears its anyio portal down before the
    lifespan finishes, and aiosqlite then cannot close its connections. There
    is nothing left to dispose in that case, and turning a clean exit into a
    failure served nobody: it made the whole backend suite exit non-zero with
    every test passing.

    Narrow on purpose. Only the closed-loop case is tolerated; a real disposal
    fault still propagates.
    """
    try:
        await engine.dispose()
    except RuntimeError as exc:
        if "event loop is closed" not in str(exc).lower():
            raise
        log.debug("engine disposal skipped: the loop had already closed")


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency."""
    async with SessionLocal() as session:
        yield session


@asynccontextmanager
async def db_session() -> AsyncIterator[AsyncSession]:
    """For WebSocket handlers and background work, which have no DI container."""
    async with SessionLocal() as session:
        yield session
