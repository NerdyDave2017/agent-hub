"""POST incident-triage agent ``/api/v1/runs`` for one Gmail ``message_id``."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import httpx

from agent_hub_core.config.settings import get_settings
from agent_hub_core.db.job_transitions import claim_job_for_worker, complete_job_success, fail_job_while_running
from agent_hub_core.db.models import Deployment, Job
from agent_hub_core.domain.enums import DeploymentStatus, JobStatus
from agent_hub_core.observability.logging import get_logger

from worker.handlers._idempotency import is_terminal_job
from worker.handlers.base import AbstractJobHandler

log = get_logger(__name__)

_RUNNING = "gmail_process_message_running"
_DONE = "gmail_process_message_complete"


class GmailProcessMessageHandler(AbstractJobHandler):
    async def execute(self, job: Job, session: AsyncSession) -> None:
        if is_terminal_job(job):
            return

        claimed = await claim_job_for_worker(session, job.id, running_step=_RUNNING)
        await session.refresh(job)
        if is_terminal_job(job):
            return
        if not claimed and job.status != JobStatus.running:
            log.warning(
                "gmail_process_skip_status",
                job_id=str(job.id),
                job_status=job.status.value,
            )
            return

        payload = job.payload or {}
        message_id = payload.get("message_id")
        if not message_id or not isinstance(message_id, str):
            await fail_job_while_running(session, job.id, message="missing message_id")
            return

        settings = get_settings()
        base_url: str | None = None
        if job.agent_id is not None:
            row = await session.scalar(
                select(Deployment)
                .where(
                    Deployment.agent_id == job.agent_id,
                    Deployment.status == DeploymentStatus.live,
                )
                .order_by(Deployment.created_at.desc())
                .limit(1)
            )
            if row and row.base_url:
                base_url = str(row.base_url).rstrip("/")

        if not base_url:
            base_url = (settings.incident_triage_agent_url or "").strip().rstrip("/")
        if not base_url:
            await fail_job_while_running(
                session,
                job.id,
                message="no agent base_url (set Deployment.base_url or INCIDENT_TRIAGE_AGENT_URL)",
            )
            return

        url = f"{base_url}/api/v1/runs"
        cid = job.correlation_id or str(job.id)
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    url,
                    json={"message_id": message_id},
                    headers={"X-Correlation-ID": cid},
                )
            if resp.status_code not in (200, 201, 202):
                await fail_job_while_running(
                    session,
                    job.id,
                    message=f"agent HTTP {resp.status_code}: {resp.text[:500]}",
                )
                return
        except Exception as exc:
            log.exception("gmail_process_agent_call_failed", job_id=str(job.id))
            await fail_job_while_running(session, job.id, message=str(exc))
            return

        await complete_job_success(session, job.id, final_step=_DONE)
        await session.refresh(job)
        log.info(
            "gmail_process_message_complete",
            job_id=str(job.id),
            message_id=message_id,
            agent_url=url,
        )
