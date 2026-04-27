"""Best-effort AWS teardown and removal of ``Deployment`` rows for an agent."""

from __future__ import annotations

import asyncio
from uuid import UUID

from botocore.exceptions import ClientError
from sqlalchemy import delete as sql_delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_hub_core.config.settings import Settings
from agent_hub_core.db.models import Deployment
from agent_hub_core.observability.logging import get_logger

from worker.handlers.aws.apprunner_adapter import AppRunnerAdapter
from worker.handlers.aws.client_errors import is_not_found_or_gone
from worker.handlers.aws.ecs import ECSAdapter

log = get_logger(__name__)


async def teardown_deployments_for_agent(
    session: AsyncSession,
    settings: Settings,
    agent_id: UUID,
    *,
    log_job_id: str | None = None,
) -> None:
    """
    Delete App Runner / ECS resources for each deployment, then delete deployment rows.

    Does not commit or modify ``Agent``. Swallows **not found** AWS errors; logs other AWS errors.
    """
    deps = list(
        (await session.scalars(select(Deployment).where(Deployment.agent_id == agent_id))).all()
    )
    extra = {"job_id": log_job_id} if log_job_id else {}
    for dep in deps:
        if dep.app_runner_arn:
            try:

                def _del_appr(arn: str = str(dep.app_runner_arn)) -> None:
                    AppRunnerAdapter(settings).delete_service(arn)

                await asyncio.to_thread(_del_appr)
            except ClientError as exc:
                if is_not_found_or_gone(exc):
                    log.warning(
                        "deployment_teardown_apprunner_gone",
                        agent_id=str(agent_id),
                        arn=dep.app_runner_arn,
                        **extra,
                    )
                else:
                    log.warning(
                        "deployment_teardown_apprunner_failed",
                        agent_id=str(agent_id),
                        error=str(exc),
                        **extra,
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
                if is_not_found_or_gone(exc):
                    log.warning(
                        "deployment_teardown_ecs_gone",
                        agent_id=str(agent_id),
                        **extra,
                    )
                else:
                    log.warning(
                        "deployment_teardown_ecs_failed",
                        agent_id=str(agent_id),
                        error=str(exc),
                        **extra,
                    )
    await session.execute(sql_delete(Deployment).where(Deployment.agent_id == agent_id))
