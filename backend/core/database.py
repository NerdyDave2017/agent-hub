"""
Async SQLAlchemy engine and session factory for request-scoped database work.

Flow
----
1. `get_engine()` lazily builds one shared `AsyncEngine` (connection pool).
2. `get_session_factory()` wraps it in an `async_sessionmaker` (how new `AsyncSession`s are born).
3. `get_db()` is what FastAPI will later `Depends()` on: open session → `yield` → close.

Commit policy
-------------
`get_db` does **not** auto-commit. Your route or service calls `await session.commit()` after
a successful unit of work, and `await session.rollback()` on errors (or let the session
context exit without commit). This keeps transaction boundaries obvious in application code.

Shutdown
--------
Call `await dispose_engine()` from app lifespan on process exit so pool connections close cleanly.
"""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from core.settings import Settings, get_settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine(settings: Settings | None = None) -> AsyncEngine:
    """Return the shared async engine, creating it on first use."""
    global _engine
    if _engine is None:
        resolved = settings or get_settings()
        _engine = create_async_engine(
            resolved.async_database_url,
            pool_pre_ping=True,
        )
    return _engine


def get_session_factory(settings: Settings | None = None) -> async_sessionmaker[AsyncSession]:
    """Return the session factory bound to the shared engine."""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(settings),
            expire_on_commit=False,
        )
    return _session_factory


async def get_db() -> AsyncIterator[AsyncSession]:
    """Yield a session for one logical request; close when the caller is done."""
    factory = get_session_factory()
    async with factory() as session:
        yield session


async def dispose_engine() -> None:
    """Dispose pool and drop cached engine/session factory (e.g. on shutdown or in tests)."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None
