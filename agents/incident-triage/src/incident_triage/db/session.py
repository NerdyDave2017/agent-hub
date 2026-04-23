"""SQLAlchemy asyncpg engine + ``async_sessionmaker``; ``init_agent_schema`` creates agent-local tables;
``psycopg_conninfo`` adapts the same DSN for LangGraph’s psycopg checkpointer."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from incident_triage.db.models import LocalBase

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def psycopg_conninfo(url: str) -> str:
    """Normalize DSN for psycopg (drops SQLAlchemy’s ``+asyncpg`` driver prefix)."""
    t = url.strip()
    t = t.replace("postgresql+asyncpg://", "postgresql://", 1)
    if t.startswith("postgres://") and not t.startswith("postgresql://"):
        t = "postgresql://" + t[len("postgres://") :]
    return t


def _to_async_driver_url(url: str) -> str:
    trimmed = url.strip()
    if "+asyncpg" in trimmed:
        return trimmed
    if trimmed.startswith("postgresql://"):
        return trimmed.replace("postgresql://", "postgresql+asyncpg://", 1)
    if trimmed.startswith("postgres://"):
        return trimmed.replace("postgres://", "postgresql+asyncpg://", 1)
    return trimmed


def configure_database(database_url: str, *, pool_size: int = 5, max_overflow: int = 10) -> None:
    global _engine, _session_factory
    if not database_url.strip():
        raise ValueError("database_url must be non-empty")
    if _engine is not None:
        return
    async_url = _to_async_driver_url(database_url)
    _engine = create_async_engine(
        async_url,
        pool_pre_ping=True,
        pool_size=pool_size,
        max_overflow=max_overflow,
    )
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)


def get_engine() -> AsyncEngine:
    if _engine is None:
        raise RuntimeError("Database not configured")
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        raise RuntimeError("Database not configured")
    return _session_factory


@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    async with get_session_factory()() as session:
        yield session


async def init_agent_schema() -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(LocalBase.metadata.create_all)


async def dispose_database() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None
