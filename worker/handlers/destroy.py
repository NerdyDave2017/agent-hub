"""Destroy / deprovision — best-effort AWS teardown then remove the agent row (cascades integrations)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from agent_hub_core.config.settings import get_settings
from agent_hub_core.db.job_transitions import (
    claim_job_for_worker,
    complete_job_success,
    fail_job_while_running,
)
from agent_hub_core.db.models import Agent, Job
from agent_hub_core.domain.enums import JobStatus
from agent_hub_core.observability.logging import get_logger

from worker.handlers._idempotency import is_terminal_job
from worker.handlers.base import AbstractJobHandler
from worker.handlers.deployment_teardown import teardown_deployments_for_agent

log = get_logger(__name__)

_STEP_RUNNING = "destroy_running"
_STEP_DONE = "destroy_complete"


class AgentDestroyHandler(AbstractJobHandler):
    async def execute(self, job: Job, session: AsyncSession) -> None:
        if is_terminal_job(job):
            log.info("destroy_skip_terminal", job_id=str(job.id))
            return

        settings = get_settings()
        claimed = await claim_job_for_worker(session, job.id, running_step=_STEP_RUNNING)
        await session.refresh(job)
        if is_terminal_job(job):
            return
        if not claimed and job.status != JobStatus.running:
            log.warning(
                "destroy_skip_unexpected_status",
                job_id=str(job.id),
                job_status=job.status.value,
            )
            return

        if job.agent_id is None:
            await fail_job_while_running(session, job.id, message="agent_destroy requires agent_id")
            return

        agent = await session.get(Agent, job.agent_id)
        if agent is None:
            await complete_job_success(session, job.id, final_step=_STEP_DONE)
            await session.refresh(job)
            log.info("destroy_agent_already_removed", job_id=str(job.id))
            return

        agent_id_log = str(agent.id)
        try:
            await teardown_deployments_for_agent(
                session,
                settings,
                agent.id,
                log_job_id=str(job.id),
            )
        except Exception:
            log.exception("destroy_teardown_failed", job_id=str(job.id), agent_id=agent_id_log)
        ag = await session.get(Agent, agent.id)
        if ag is not None:
            await session.delete(ag)
        await session.commit()

        await complete_job_success(session, job.id, final_step=_STEP_DONE)
        await session.refresh(job)
        log.info("destroy_complete", job_id=str(job.id), agent_id=agent_id_log)
