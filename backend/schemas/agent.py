"""Request and response models for agent registry APIs."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from domain.enums import AgentStatus, AgentType, JobStatus


class AgentCreate(BaseModel):
    agent_type: AgentType = Field(description="Registered agent implementation")
    name: str = Field(min_length=1, max_length=255)
    # image_repo: str | None = Field(default=None, max_length=512)
    # image_tag: str | None = Field(default=None, max_length=128)


class AgentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    status: AgentStatus | None = None
    # image_repo: str | None = Field(default=None, max_length=512)
    # image_tag: str | None = Field(default=None, max_length=128)


class AgentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, use_enum_values=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    agent_type: AgentType
    name: str
    status: AgentStatus
    # image_repo: str | None
    # image_tag: str | None
    created_at: datetime
    updated_at: datetime


class AgentProvisioningJobSummary(BaseModel):
    """Latest `agent_provisioning` job for an agent (hub + worker mirror)."""

    model_config = ConfigDict(from_attributes=True, use_enum_values=True)

    id: uuid.UUID
    status: JobStatus
    job_step: str | None = None
    correlation_id: str | None = None
    error_message: str | None = None
    updated_at: datetime


class AgentProvisioningStatusRead(BaseModel):
    """Single payload for UI polling / SSE — agent row plus latest provisioning job."""

    model_config = ConfigDict(from_attributes=True, use_enum_values=True)

    agent_id: uuid.UUID
    tenant_id: uuid.UUID
    agent_status: AgentStatus
    agent_name: str
    job: AgentProvisioningJobSummary | None
    watermark: datetime = Field(
        description="Monotonic cursor: max(agent.updated_at, job.updated_at). Pass as `since` to long-poll."
    )
