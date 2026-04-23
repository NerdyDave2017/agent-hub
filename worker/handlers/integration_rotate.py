"""Integration credential rotation — stub."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from agent_hub_core.db.job_transitions import claim_job_for_worker, complete_job_success
from agent_hub_core.db.models import Job
from agent_hub_core.domain.enums import JobStatus
from agent_hub_core.observability.logging import get_logger

from worker.handlers._idempotency import is_terminal_job
from worker.handlers.base import AbstractJobHandler

log = get_logger(__name__)

_RUNNING = "stub_integration_rotate"
_DONE = "stub_integration_rotate_complete"


class IntegrationRotateHandler(AbstractJobHandler):
    async def execute(self, job: Job, session: AsyncSession) -> None:
        if is_terminal_job(job):
            log.info("integration_rotate_skip_terminal", job_id=str(job.id))
            return

        claimed = await claim_job_for_worker(session, job.id, running_step=_RUNNING)
        await session.refresh(job)
        if is_terminal_job(job):
            return
        if not claimed and job.status != JobStatus.running:
            log.warning(
                "integration_rotate_skip_unexpected_status",
                job_id=str(job.id),
                job_status=job.status.value,
            )
            return

        await complete_job_success(session, job.id, final_step=_DONE)
        await session.refresh(job)
        log.info("integration_rotate_stub_complete", job_id=str(job.id))
