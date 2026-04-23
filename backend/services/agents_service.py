"""
Agent persistence scoped to a tenant.

Routers pass `tenant_id` on every call; this module enforces tenant existence via
`tenants_service.require_tenant`.

Raises **`agent_hub_core.domain.exceptions`** — no HTTP/FastAPI types here. No Pydantic request models —
pass plain values (and ``agent_hub_core.domain.enums`` types) from routers after validation.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_hub_core.db.models import Agent, Job
from agent_hub_core.domain.enums import AgentStatus, AgentType, JobType
from agent_hub_core.domain.exceptions import AgentNotFound
from agent_hub_core.schemas.agent import AgentProvisioningJobSummary, AgentProvisioningStatusRead
from services.tenants_service import require_tenant


async def require_agent(session: AsyncSession, tenant_id: UUID, agent_id: UUID) -> Agent:
    """Return the agent row if it belongs to `tenant_id`, else raise `AgentNotFound`."""
    agent = await session.get(Agent, agent_id)
    if agent is None or agent.tenant_id != tenant_id:
        raise AgentNotFound(agent_id, tenant_id)
    return agent


async def assert_agent_belongs_to_tenant(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    agent_id: UUID,
) -> None:
    """
    Fail fast when `agent_id` does not exist or is not under `tenant_id`.

    Used before inserting jobs that reference an agent.
    """
    await require_agent(session, tenant_id, agent_id)


async def list_agents_page(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    skip: int,
    limit: int,
) -> tuple[list[Agent], int]:
    """Return `(rows, total_count)` for agents under the tenant."""
    await require_tenant(session, tenant_id)
    total = int(
        await session.scalar(
            select(func.count()).select_from(Agent).where(Agent.tenant_id == tenant_id)
        )
        or 0
    )
    rows = await session.scalars(
        select(Agent)
        .where(Agent.tenant_id == tenant_id)
        .order_by(Agent.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    return list(rows.all()), total


async def create_agent(
    session: AsyncSession,
    tenant_id: UUID,
    *,
    agent_type: AgentType,
    name: str,
) -> Agent:
    """Insert an agent for the tenant; **commits**."""
    await require_tenant(session, tenant_id)
    agent = Agent(
        tenant_id=tenant_id,
        agent_type=agent_type,
        name=name,
    )
    session.add(agent)
    await session.commit()
    await session.refresh(agent)
    return agent


async def get_agent(session: AsyncSession, tenant_id: UUID, agent_id: UUID) -> Agent:
    """Load one agent under the tenant."""
    await require_tenant(session, tenant_id)
    return await require_agent(session, tenant_id, agent_id)


async def get_agent_provisioning_status_read(
    session: AsyncSession,
    tenant_id: UUID,
    agent_id: UUID,
) -> AgentProvisioningStatusRead:
    """
    Latest agent row plus newest `agent_provisioning` job for that agent (if any).

    Callers doing tight poll loops should `session.expire_all()` before each call so
    picks up commits from the worker on fresh connections / identity map.
    """
    agent = await get_agent(session, tenant_id, agent_id)
    job = await session.scalar(
        select(Job)
        .where(
            Job.tenant_id == tenant_id,
            Job.agent_id == agent_id,
            Job.job_type == JobType.agent_provisioning.value,
        )
        .order_by(Job.created_at.desc())
        .limit(1)
    )
    job_summary = AgentProvisioningJobSummary.model_validate(job) if job is not None else None
    watermark = agent.updated_at
    if job is not None and job.updated_at > watermark:
        watermark = job.updated_at
    return AgentProvisioningStatusRead(
        agent_id=agent.id,
        tenant_id=agent.tenant_id,
        agent_status=agent.status,
        agent_name=agent.name,
        job=job_summary,
        watermark=watermark,
    )


async def update_agent(
    session: AsyncSession,
    tenant_id: UUID,
    agent_id: UUID,
    *,
    name: str | None = None,
    status: AgentStatus | None = None,
) -> Agent:
    """Partial update; **commits**."""
    await require_tenant(session, tenant_id)
    agent = await require_agent(session, tenant_id, agent_id)
    if name is not None:
        agent.name = name
    if status is not None:
        agent.status = status
    await session.commit()
    await session.refresh(agent)
    return agent
