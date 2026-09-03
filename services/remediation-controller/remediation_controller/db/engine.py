"""Async engine / session factory.

A small copy of ``rca_agent.db.engine`` / ``incident_correlator.db.engine``
(ADR-019 rationale: ~40 trivial lines, copied rather than cross-imported so the
service does not depend on another service package at runtime).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


class Database:
    def __init__(
        self, url: str, *, echo: bool = False, pool_size: int = 5, max_overflow: int = 5
    ) -> None:
        kwargs: dict[str, object] = {"echo": echo}
        if not url.startswith("sqlite"):
            kwargs.update(pool_size=pool_size, max_overflow=max_overflow, pool_pre_ping=True)
        self._engine: AsyncEngine = create_async_engine(url, **kwargs)
        self._sessionmaker = async_sessionmaker(self._engine, expire_on_commit=False)

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self._sessionmaker() as session:
            yield session

    async def ping(self) -> bool:
        try:
            async with self._engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    async def create_all(self) -> None:
        """Test-only convenience (production uses Alembic migrations)."""

        from remediation_controller.db.models import Base

        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def dispose(self) -> None:
        await self._engine.dispose()
