"""ORM models on ``LocalBase`` (not hub tables): ``ProcessedEmail`` dedup ledger for the graph."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class LocalBase(DeclarativeBase):
    """Declarative base for tables created by this agent (not hub Alembic models)."""


class ProcessedEmail(LocalBase):
    """One row per processed Gmail id so retries/pollers do not re-run the graph."""

    __tablename__ = "processed_emails"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    message_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
