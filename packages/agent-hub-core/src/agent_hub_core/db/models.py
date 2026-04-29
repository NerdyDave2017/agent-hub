"""
Postgres tables for the hub, expressed as SQLAlchemy 2.0 ORM classes.

What this file is
------------------
Each class below maps to one database table (`__tablename__`). Columns are declared
with `Mapped[...]` and `mapped_column(...)` so types stay explicit in Python.

What SQLAlchemy is doing for you
-------------------------------
- **ORM**: you work with Python objects; SQLAlchemy turns that into SQL for Postgres.
- **DDL source of truth (for now)**: the shapes here are what we will later turn into
  real `CREATE TABLE` statements—typically via **Alembic** migrations (next step).

What we are *not* doing in this file yet
----------------------------------------
- No FastAPI routes—only table shapes and relationships. Runtime DB access lives in
  `agent_hub_core.db.engine`; configuration in `agent_hub_core.config.settings`.

Migrations
----------
Schema changes are applied with **Alembic** under `agent_hub_core/migrations/` (see `env.py` and
`versions/`). Each revision should stay aligned with these model classes.

Alternatives (so you know the tradeoffs)
---------------------------------------
1. **Raw SQL files** (`schema.sql`): simplest mentally, no ORM; you duplicate types in
   Python by hand and keep SQL + code in sync yourself.
2. **SQLAlchemy Core only** (Table objects, no classes): middle ground; still code-first
   DDL but less “object oriented” than ORM.
3. **This approach (ORM models)**: one Python definition drives both your mental model
   and, with Alembic, migrations—common in FastAPI services.

Enums
-----
String enums live in **`agent_hub_core.domain.enums`** (shared with schemas and services). This file
imports them for SQLAlchemy `Enum(...)` columns. We store values as short strings in
Postgres (`native_enum=False`) so the database stays easy to read and migrate.

Relationships
---------------
`relationship(...)` tells SQLAlchemy how rows link *after* you load them in Python. It
does not replace foreign keys—you still need `ForeignKey(...)` on the owning column.
`cascade="all, delete-orphan"` means “if a parent row is deleted, delete its children,”
which matches how we want tenants/agents to clean up dependent rows in dev.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from agent_hub_core.db.base import Base
from agent_hub_core.domain.enums import AgentStatus, AgentType, DeploymentStatus, JobStatus


class Tenant(Base):
    """A customer / organization using the hub."""

    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)  # human-facing org name
    slug: Mapped[str] = mapped_column(
        String(128), unique=True, index=True, nullable=False
    )  # stable URL/key; lowercase, no spaces (enforce in API layer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )  # bumped on any column change when using default ORM update patterns

    users: Mapped[list["User"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")
    agents: Mapped[list["Agent"]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan"
    )
    integrations: Mapped[list["Integration"]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan"
    )
    jobs: Mapped[list["Job"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")


class User(Base):
    """A person belonging to one tenant."""

    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("tenant_id", "email", name="uq_users_tenant_email"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)  # unique per tenant (see __table_args__)
    display_name: Mapped[str | None] = mapped_column(String(255))  # optional; shown in UI
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=true()
    )  # soft-disable login without deleting the row
    password_hash: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )  # bcrypt hash; null for Google-auth users or until operator sets password
    auth_provider: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'password'")
    )  # "password" or "google"; controls which login flow is valid
    google_sub: Mapped[str | None] = mapped_column(
        String(255), nullable=True, unique=True
    )  # Google account 'sub' claim — globally unique, used to match returning Google users
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    tenant: Mapped[Tenant] = relationship(back_populates="users")


class Agent(Base):
    """A deployable agent record (capstone focuses on type `incident_triage`)."""

    __tablename__ = "agents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    agent_type: Mapped[AgentType] = mapped_column(
        Enum(AgentType, name="agent_type", native_enum=False, length=32),
        nullable=False,
        server_default=AgentType.incident_triage.value,
        index=True,
    )  # stored as VARCHAR; capstone uses incident_triage
    name: Mapped[str] = mapped_column(String(255), nullable=False)  # tenant-chosen label (not unique globally)
    status: Mapped[AgentStatus] = mapped_column(
        Enum(AgentStatus, name="agent_status", native_enum=False, length=32),
        nullable=False,
        server_default=AgentStatus.draft.value,
    )  # registry lifecycle; pair with Deployment.status for infra
    image_repo: Mapped[str | None] = mapped_column(
        String(512)
    )  # ECR repository URI or name; null until you pin an image for deploy
    image_tag: Mapped[str | None] = mapped_column(String(128))  # e.g. git sha or semver; null until pinned
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    tenant: Mapped[Tenant] = relationship(back_populates="agents")
    deployments: Mapped[list["Deployment"]] = relationship(
        back_populates="agent", cascade="all, delete-orphan"
    )
    integrations: Mapped[list["Integration"]] = relationship(
        back_populates="agent", cascade="all, delete-orphan"
    )
    jobs: Mapped[list["Job"]] = relationship(back_populates="agent")


class Deployment(Base):
    """Where an agent runs (ECS/App Runner) and how clients reach it."""

    __tablename__ = "deployments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[DeploymentStatus] = mapped_column(
        Enum(DeploymentStatus, name="deployment_status", native_enum=False, length=32),
        nullable=False,
        server_default=DeploymentStatus.pending.value,
    )
    cluster_arn: Mapped[str | None] = mapped_column(
        String(512)
    )  # ECS cluster ARN when running on Fargate/EC2; null if using App Runner only
    service_arn: Mapped[str | None] = mapped_column(
        String(512)
    )  # ECS service ARN; use with cluster_arn for API calls
    app_runner_arn: Mapped[str | None] = mapped_column(
        String(512)
    )  # optional alternate runtime; typically only one of ECS vs App Runner is set
    base_url: Mapped[str | None] = mapped_column(
        Text
    )  # public or internal HTTP root for the agent (used after status=live)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    agent: Mapped[Agent] = relationship(back_populates="deployments")


class Integration(Base):
    """OAuth or tool connection metadata; secrets live in AWS Secrets Manager, not here."""

    __tablename__ = "integrations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), index=True
    )  # null = tenant-wide OAuth; set when the credential is scoped to one agent
    provider: Mapped[str] = mapped_column(String(64), nullable=False, index=True)  # e.g. slack, github, google
    scopes: Mapped[str | None] = mapped_column(
        Text
    )  # optional: space-separated scopes or JSON string—pick one convention in API docs
    secret_arn: Mapped[str] = mapped_column(
        String(512), nullable=False
    )  # Secrets Manager ARN only; never store refresh tokens in this column
    provider_config: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB
    )  # non-secret metadata (workspace ids, webhook ids); keep tokens out
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Gmail Pub/Sub + users.watch() (hub: integrations_gmail / webhooks_gmail; see docs/plan.md)
    email_address: Mapped[str | None] = mapped_column(String(320), index=True)
    last_history_id: Mapped[str | None] = mapped_column(String(64))
    watch_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    watch_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    watch_resource_id: Mapped[str | None] = mapped_column(String(128))
    connection_status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'pending'")
    )  # pending | active | error | revoked
    tenant: Mapped[Tenant] = relationship(back_populates="integrations")
    agent: Mapped[Agent | None] = relationship(back_populates="integrations")


class Job(Base):
    """Async work queued via SQS; this row is the durable hub-side mirror."""

    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_jobs_tenant_idempotency"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), index=True
    )  # optional target; SET NULL keeps history if agent row is deleted
    job_type: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True
    )  # string discriminator for worker routing; align values with `agent_hub_core.domain.enums.JobType`
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="job_status", native_enum=False, length=32),
        nullable=False,
        server_default=JobStatus.pending.value,
    )
    job_step: Mapped[str | None] = mapped_column(String(128)) # Logs states from job actions
    correlation_id: Mapped[str | None] = mapped_column(
        String(128), index=True
    )  # mirrors X-Correlation-ID / request_id for log tracing across hub → SQS → worker
    idempotency_key: Mapped[str | None] = mapped_column(
        String(128)
    )  # client-supplied key; duplicate (tenant_id, key) returns same job—see __table_args__
    payload: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB
    )  # safe, non-secret JSON only (no OAuth tokens); echoed in SQS body in prod
    error_message: Mapped[str | None] = mapped_column(
        Text
    )  # last worker error string; null when status is non-terminal or succeeded
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    tenant: Mapped[Tenant] = relationship(back_populates="jobs")
    agent: Mapped[Agent | None] = relationship(back_populates="jobs")


class MetricSnapshot(Base):
    """Pre-aggregated KPI JSON per tenant/agent/window for dashboard reads."""

    __tablename__ = "metric_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "agent_type",
            "window_start",
            name="uq_metric_snapshots_tenant_agent_window",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    agent_type: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True
    )  # same string family as Agent.agent_type (e.g. incident_triage) for rollup grouping
    window_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )  # inclusive bucket start (UTC); unique with tenant_id + agent_type
    window_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )  # optional exclusive end; null if the row represents a single instant snapshot
    metrics: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )  # flexible KPI bag: run_count, error_rate, hours_saved_estimate, etc.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ToolCallEvent(Base):
    """Per-node / tool spans written by the incident-triage agent (observability feed)."""

    __tablename__ = "tool_call_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), index=True
    )
    trace_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    message_id: Mapped[str | None] = mapped_column(String(255), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    node_name: Mapped[str | None] = mapped_column(String(64))
    tool_name: Mapped[str | None] = mapped_column(String(128))
    decision: Mapped[str | None] = mapped_column(String(128))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    succeeded: Mapped[bool | None] = mapped_column(Boolean)
    error: Mapped[str | None] = mapped_column(Text)
    llm_model: Mapped[str | None] = mapped_column("model", String(128), nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[float | None] = mapped_column(Numeric(10, 8))


class Incident(Base):
    """Canonical incident row written by the triage agent; hub dashboards read this table."""

    __tablename__ = "incidents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    message_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    langfuse_trace_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    incident_type: Mapped[str | None] = mapped_column(String(64))
    severity: Mapped[str | None] = mapped_column(String(32))
    summary: Mapped[str | None] = mapped_column(String(512))
    confidence: Mapped[float | None] = mapped_column(Float)
    actions_taken: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    slack_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    slack_ts: Mapped[str | None] = mapped_column(String(64))
    raw_subject: Mapped[str | None] = mapped_column(String(1024))
    raw_sender: Mapped[str | None] = mapped_column(String(512))
