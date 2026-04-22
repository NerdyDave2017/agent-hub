"""
Alembic environment — wires migrations to the same SQLAlchemy `Base` as `db/models.py`.

How this fits together
-----------------------
1. `target_metadata = Base.metadata` tells Alembic what tables *should* exist.
2. `import db.models` runs your model module so every `class X(Base)` registers on that metadata.
3. `run_migrations_online` uses an **async** engine (`asyncpg`) because the hub will use async
   SQLAlchemy at runtime; Alembic runs DDL inside `run_sync(...)`.

Typical commands (from `backend/`)
----------------------------------
  # Create a new revision after you edit models (compare to DB):
  DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/postgres \\
    alembic revision --autogenerate -m "describe change"

  # Apply all pending migrations:
  DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/postgres \\
    alembic upgrade head

  # One step back (destructive to data that depends on new columns):
  alembic downgrade -1

Configuration
---------------
Uses `core.settings.get_settings().async_database_url` (same as the app): optional
`backend/.env`, env vars, or defaults that match `docker-compose.yml` Postgres.
You may pass `postgresql://` or `postgresql+asyncpg://`; asyncpg is enforced in settings.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from core.settings import get_settings
from db.base import Base

import db.models  # noqa: F401 — side effect: register all models on Base.metadata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    raise RuntimeError(
        "Offline mode is disabled: set DATABASE_URL and run `alembic upgrade head` with Postgres up."
    )


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = get_settings().async_database_url

    connectable = async_engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
