"""Request and response models for async job APIs (hub-side mirror of SQS work)."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agent_hub_core.domain.enums import JobStatus
from agent_hub_core.domain.job_payload import assert_safe_job_payload


class JobCreate(BaseModel):
    """
    Body for enqueueing a new unit of async work.

    The hub persists a `Job` row first (durable metadata), then (in a later step) will
    publish the same `job_id` and `correlation_id` inside an SQS message. Nothing secret
    may appear in `payload`—workers and logs may see it.
    """

    job_type: str = Field(
        min_length=1,
        max_length=64,
        description="Worker routing key; align with `agent_hub_core.domain.enums.JobType` (e.g. agent_provisioning).",
        examples=["agent_provisioning"],
    )
    agent_id: uuid.UUID | None = Field(
        default=None,
        description="Optional target agent; must belong to the same tenant when set.",
    )
    idempotency_key: str | None = Field(
        default=None,
        max_length=128,
        description="Client-supplied key; same (tenant_id, key) returns the existing job instead of inserting twice.",
    )
    payload: dict[str, Any] | None = Field(
        default=None,
        description="Non-secret JSON only. Never put OAuth tokens or raw passwords here.",
    )

    @field_validator("payload")
    @classmethod
    def reject_secret_like_keys(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        assert_safe_job_payload(v)
        return v


class JobRead(BaseModel):
    """Serialized job row returned to API clients (includes hub-generated `id` used as `job_id` in SQS)."""

    model_config = ConfigDict(from_attributes=True, use_enum_values=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    agent_id: uuid.UUID | None
    job_type: str
    status: JobStatus
    correlation_id: str | None
    idempotency_key: str | None
    payload: dict[str, Any] | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
