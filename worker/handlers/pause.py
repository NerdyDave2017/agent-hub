"""Pause / scale-to-zero — App Runner ``pause_service`` or ECS ``desiredCount=0``; DB fallback."""

from __future__ import annotations

import asyncio

from botocore.exceptions import ClientError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_hub_core.config.settings import get_settings
from agent_hub_core.db.job_transitions import (
    claim_job_for_worker,
    complete_job_success,
    fail_job_while_running,
)
from agent_hub_core.db.models import Agent, Deployment, Job
from agent_hub_core.domain.enums import AgentStatus, DeploymentStatus, JobStatus
from agent_hub_core.observability.logging import get_logger

from worker.handlers._idempotency import is_terminal_job
from worker.handlers.aws.apprunner_adapter import AppRunnerAdapter
from worker.handlers.aws.client_errors import is_not_found_or_gone
from worker.handlers.aws.ecs import ECSAdapter
from worker.handlers.base import AbstractJobHandler

log = get_logger(__name__)

_STEP_RUNNING = "pause_running"
_STEP_DONE = "pause_complete"


class AgentPauseHandler(AbstractJobHandler):
    async def execute(self, job: Job, session: AsyncSession) -> None:
        if is_terminal_job(job):
            log.info("pause_skip_terminal", job_id=str(job.id))
            return

        settings = get_settings()
        claimed = await claim_job_for_worker(session, job.id, running_step=_STEP_RUNNING)
        await session.refresh(job)
        if is_terminal_job(job):
            return
        if not claimed and job.status != JobStatus.running:
            log.warning(
                "pause_skip_unexpected_status",
                job_id=str(job.id),
                job_status=job.status.value,
            )
            return

        if job.agent_id is None:
            await fail_job_while_running(session, job.id, message="agent_pause requires agent_id")
            return

        agent = await session.get(Agent, job.agent_id)
        if agent is None:
            await fail_job_while_running(session, job.id, message="agent row missing")
            return

        deps = list(
            (
                await session.scalars(
                    select(Deployment).where(
                        Deployment.agent_id == job.agent_id,
                        Deployment.status == DeploymentStatus.live,
                    )
                )
            ).all()
        )
        if not deps:
            await complete_job_success(session, job.id, final_step=_STEP_DONE)
            await session.refresh(job)
            log.info("pause_no_live_deployment", job_id=str(job.id), agent_id=str(job.agent_id))
            return

        for dep in deps:
            if dep.app_runner_arn:
                try:

                    def _pause(arn: str = str(dep.app_runner_arn)) -> None:
                        AppRunnerAdapter(settings).pause_service(arn)

                    await asyncio.to_thread(_pause)
                except ClientError as exc:
                    if is_not_found_or_gone(exc):
                        log.warning(
                            "pause_apprunner_already_gone",
                            job_id=str(job.id),
                            arn=dep.app_runner_arn,
                        )
                    else:
                        log.exception("pause_apprunner_failed", job_id=str(job.id))
                        await fail_job_while_running(session, job.id, message=str(exc))
                        return
                dep.status = DeploymentStatus.draining
            elif dep.cluster_arn and dep.service_arn:
                try:

                    def _scale(
                        *,
                        cluster: str = str(dep.cluster_arn),
                        service: str = str(dep.service_arn),
                    ) -> None:
                        ECSAdapter(settings).update_service_desired_count(
                            cluster=cluster, service=service, desired=0
                        )

                    await asyncio.to_thread(_scale)
                except ClientError as exc:
                    log.exception("pause_ecs_failed", job_id=str(job.id))
                    await fail_job_while_running(session, job.id, message=str(exc))
                    return
                dep.status = DeploymentStatus.draining
            else:
                # No AWS binding (shared dev URL) or unknown — logical pause so workers skip this deployment.
                dep.status = DeploymentStatus.draining

        agent.status = AgentStatus.archived
        await session.commit()
        await complete_job_success(session, job.id, final_step=_STEP_DONE)
        await session.refresh(job)
        log.info("pause_complete", job_id=str(job.id), agent_id=str(job.agent_id))
