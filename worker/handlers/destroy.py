"""Destroy / deprovision — delete App Runner or ECS service; archive agent and remove deployments."""

from __future__ import annotations

import asyncio

from botocore.exceptions import ClientError
from sqlalchemy import delete as sql_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_hub_core.config.settings import get_settings
from agent_hub_core.db.job_transitions import (
    claim_job_for_worker,
    complete_job_success,
    fail_job_while_running,
)
from agent_hub_core.db.models import Agent, Deployment, Job
from agent_hub_core.domain.enums import AgentStatus, JobStatus
from agent_hub_core.observability.logging import get_logger

from worker.handlers._idempotency import is_terminal_job
from worker.handlers.aws.apprunner_adapter import AppRunnerAdapter
from worker.handlers.aws.client_errors import is_not_found_or_gone
from worker.handlers.aws.ecs import ECSAdapter
from worker.handlers.base import AbstractJobHandler

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

        if agent.status == AgentStatus.archived:
            await complete_job_success(session, job.id, final_step=_STEP_DONE)
            await session.refresh(job)
            log.info("destroy_idempotent_archived", job_id=str(job.id), agent_id=str(agent.id))
            return

        deps = list(
            (await session.scalars(select(Deployment).where(Deployment.agent_id == agent.id))).all()
        )

        for dep in deps:
            if dep.app_runner_arn:
                try:

                    def _del_appr(arn: str = str(dep.app_runner_arn)) -> None:
                        AppRunnerAdapter(settings).delete_service(arn)

                    await asyncio.to_thread(_del_appr)
                except ClientError as exc:
                    if not is_not_found_or_gone(exc):
                        log.exception("destroy_apprunner_failed", job_id=str(job.id))
                        await fail_job_while_running(session, job.id, message=str(exc))
                        return
                    log.warning(
                        "destroy_apprunner_already_gone",
                        job_id=str(job.id),
                        arn=dep.app_runner_arn,
                    )
            elif dep.cluster_arn and dep.service_arn:
                try:

                    def _del_ecs(
                        *,
                        cluster: str = str(dep.cluster_arn),
                        service: str = str(dep.service_arn),
                    ) -> None:
                        ECSAdapter(settings).delete_service(cluster=cluster, service=service, force=True)

                    await asyncio.to_thread(_del_ecs)
                except ClientError as exc:
                    if not is_not_found_or_gone(exc):
                        log.exception("destroy_ecs_failed", job_id=str(job.id))
                        await fail_job_while_running(session, job.id, message=str(exc))
                        return

        await session.execute(sql_delete(Deployment).where(Deployment.agent_id == agent.id))
        agent.status = AgentStatus.archived
        await session.commit()

        await complete_job_success(session, job.id, final_step=_STEP_DONE)
        await session.refresh(job)
        log.info("destroy_complete", job_id=str(job.id), agent_id=str(agent.id))
