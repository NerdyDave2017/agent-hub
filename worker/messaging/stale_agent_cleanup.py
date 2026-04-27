"""Purge agents stuck in ``provisioning`` beyond a configurable age (DB + best-effort AWS)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_hub_core.config.settings import get_settings
from agent_hub_core.db.models import Agent
from agent_hub_core.domain.enums import AgentStatus
from agent_hub_core.observability.logging import get_logger

from worker.handlers.deployment_teardown import teardown_deployments_for_agent

log = get_logger(__name__)


async def run_stale_provisioning_agent_cleanup(session: AsyncSession) -> int:
    """
    For each agent in ``provisioning`` whose ``updated_at`` is older than the configured cutoff,
    tear down deployment AWS resources, delete deployment rows, and delete the agent row.

    Paused agents use ``archived`` + draining deployments, not ``provisioning``, so they are not
    selected here.
    """
    settings = get_settings()
    cutoff = datetime.now(timezone.utc) - timedelta(
        hours=settings.stale_provisioning_agent_max_age_hours
    )
    rows = list(
        (
            await session.scalars(
                select(Agent).where(
                    Agent.status == AgentStatus.provisioning,
                    Agent.updated_at < cutoff,
                )
            )
        ).all()
    )
    removed = 0
    for agent in rows:
        agent_id = agent.id
        try:
            await teardown_deployments_for_agent(
                session,
                settings,
                agent_id,
                log_job_id="stale_agent_cleanup",
            )
            ag = await session.get(Agent, agent_id)
            if ag is not None:
                await session.delete(ag)
            await session.commit()
            removed += 1
            log.info("stale_agent_cleanup_removed", agent_id=str(agent_id))
        except Exception:
            await session.rollback()
            log.exception("stale_agent_cleanup_agent_failed", agent_id=str(agent_id))
    return removed
