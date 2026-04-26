"""``agent_provisioning`` — App Runner create/describe, ECS describe, dev URL fallback; ``Deployment`` rows."""

from __future__ import annotations

import asyncio
import time
from uuid import UUID

from botocore.exceptions import ClientError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_hub_core.config.settings import Settings, get_settings
from agent_hub_core.db.job_transitions import (
    claim_job_for_worker,
    complete_job_success,
    fail_job_while_running,
)
from agent_hub_core.db.models import Agent, Deployment, Job
from agent_hub_core.domain.enums import AgentStatus, AgentType, DeploymentStatus, JobStatus
from agent_hub_core.observability.logging import get_logger

from worker.handlers._idempotency import is_terminal_job
from worker.handlers.aws.apprunner_adapter import AppRunnerAdapter
from worker.handlers.aws.client_errors import is_not_found_or_gone
from worker.handlers.aws.ecs import ECSAdapter
from worker.handlers.base import AbstractJobHandler

log = get_logger(__name__)

_STEP_CLAIMED = "provision_running"
_STEP_DONE = "provision_complete"


def _service_url_to_https(url: str) -> str:
    u = url.strip()
    if u.startswith("http://") or u.startswith("https://"):
        return u.rstrip("/")
    return f"https://{u}".rstrip("/")


async def _latest_deployment(session: AsyncSession, agent_id: UUID) -> Deployment | None:
    return await session.scalar(
        select(Deployment)
        .where(Deployment.agent_id == agent_id)
        .order_by(Deployment.created_at.desc())
        .limit(1)
    )


def _fallback_base_url(settings: Settings, agent_type: AgentType) -> str | None:
    if agent_type == AgentType.incident_triage:
        u = (settings.incident_triage_agent_url or "").strip()
        return u or None
    return None


def _app_runner_create_ready(settings: Settings) -> bool:
    return bool(
        (settings.app_runner_create_access_role_arn or "").strip()
        and (settings.app_runner_create_instance_role_arn or "").strip()
        and (settings.app_runner_create_image_identifier or "").strip()
    )


def _resolve_create_image_identifier(settings: Settings, job: Job) -> str | None:
    payload = job.payload or {}
    override = payload.get("image_identifier")
    if isinstance(override, str) and override.strip():
        return override.strip()
    img = (settings.app_runner_create_image_identifier or "").strip()
    return img or None


def _app_runner_service_name(agent_id: UUID) -> str:
    """Unique per agent; App Runner allows up to 40 chars (``ah-`` + UUID)."""
    return f"ah-{agent_id}"


async def _wait_apprunner_running(
    settings: Settings,
    service_arn: str,
    *,
    interval_s: float = 4.0,
    max_wait_s: float = 900.0,
) -> dict:
    """
    Poll until ``DescribeService.Status == RUNNING``.

    While App Runner shows ``OPERATION_IN_PROGRESS`` (normal during deploy), also poll
    ``ListOperations`` so a **FAILED** / **ROLLBACK_*** create surfaces immediately instead
    of waiting for the full timeout.
    """
    deadline = time.monotonic() + max_wait_s
    adapter = AppRunnerAdapter(settings)
    last: dict = {}
    _terminal_ops = frozenset({"FAILED", "ROLLBACK_FAILED", "ROLLBACK_SUCCEEDED"})

    while time.monotonic() < deadline:

        def _ops() -> dict:
            return adapter.list_operations(service_arn)

        try:
            ops_raw = await asyncio.to_thread(_ops)
            op_list = ops_raw.get("OperationSummaryList") if isinstance(ops_raw, dict) else None
            if isinstance(op_list, list) and op_list:
                latest = op_list[0]
                if isinstance(latest, dict):
                    op_status = str(latest.get("Status") or "")
                    if op_status in _terminal_ops:
                        raise RuntimeError(
                            f"App Runner operation {latest.get('Type')!r} ended with {op_status!r}: {latest!r}"
                        )
        except RuntimeError:
            raise
        except Exception:
            # ListOperations is best-effort (permissions / transient API errors).
            pass

        def _desc() -> dict:
            return adapter.describe_service(service_arn)

        raw = await asyncio.to_thread(_desc)
        svc = raw.get("Service") if isinstance(raw, dict) else None
        if not isinstance(svc, dict):
            await asyncio.sleep(interval_s)
            continue
        last = svc
        status = svc.get("Status") or ""
        if status == "RUNNING":
            return svc
        if status in ("CREATE_FAILED", "DELETE_FAILED", "DELETED"):
            raise RuntimeError(f"App Runner service {status}: {svc!r}")
        await asyncio.sleep(interval_s)

    last_status = last.get("Status") if isinstance(last, dict) else None
    last_url = last.get("ServiceUrl") if isinstance(last, dict) else None
    raise TimeoutError(
        f"App Runner did not reach RUNNING within {max_wait_s}s (last Status={last_status!r}, "
        f"ServiceUrl={last_url!r}). "
        "Common causes: container not listening on the health-check port (worker uses 8080 by default), "
        "/health not returning 200, or startup crash — inspect App Runner deployment logs."
    )


class AgentProvisioningHandler(AbstractJobHandler):
    async def execute(self, job: Job, session: AsyncSession) -> None:
        if is_terminal_job(job):
            log.info("provision_skip_terminal", job_id=str(job.id))
            return

        settings = get_settings()
        claimed = await claim_job_for_worker(session, job.id, running_step=_STEP_CLAIMED)
        await session.refresh(job)
        if is_terminal_job(job):
            return
        if not claimed and job.status != JobStatus.running:
            log.warning(
                "provision_skip_unexpected_status",
                job_id=str(job.id),
                job_status=job.status.value,
            )
            return

        if job.agent_id is None:
            await fail_job_while_running(session, job.id, message="agent_provisioning requires agent_id")
            return

        agent = await session.get(Agent, job.agent_id)
        if agent is None:
            await fail_job_while_running(session, job.id, message="agent row missing")
            return

        if agent.status == AgentStatus.archived:
            await fail_job_while_running(session, job.id, message="cannot provision archived agent")
            return

        if agent.status == AgentStatus.draft:
            agent.status = AgentStatus.provisioning
        await session.commit()
        await session.refresh(agent)

        dep = await _latest_deployment(session, agent.id)
        if dep is not None and dep.status == DeploymentStatus.live and dep.base_url:
            agent.status = AgentStatus.active
            await session.commit()
            await complete_job_success(session, job.id, final_step=_STEP_DONE)
            await session.refresh(job)
            log.info("provision_idempotent_live", job_id=str(job.id), agent_id=str(agent.id))
            return

        # --- App Runner: sync URL + status from existing service ARN ---
        if dep is not None and dep.app_runner_arn:
            appr_arn = str(dep.app_runner_arn)
            try:

                def _describe() -> dict:
                    return AppRunnerAdapter(settings).describe_service(appr_arn)

                raw = await asyncio.to_thread(_describe)
            except ClientError as exc:
                if is_not_found_or_gone(exc):
                    await fail_job_while_running(
                        session,
                        job.id,
                        message=f"App Runner service not found: {dep.app_runner_arn}",
                    )
                    return
                log.exception("provision_apprunner_describe_failed", job_id=str(job.id))
                await fail_job_while_running(session, job.id, message=str(exc))
                return
            svc = (raw.get("Service") or {}) if isinstance(raw, dict) else {}
            service_url = svc.get("ServiceUrl")
            if not service_url:
                await fail_job_while_running(
                    session,
                    job.id,
                    message="DescribeService missing ServiceUrl",
                )
                return
            dep.base_url = _service_url_to_https(str(service_url))
            dep.status = DeploymentStatus.live
            agent.status = AgentStatus.active
            await session.commit()
            await complete_job_success(session, job.id, final_step=_STEP_DONE)
            await session.refresh(job)
            log.info("provision_apprunner_ok", job_id=str(job.id), agent_id=str(agent.id))
            return

        # --- ECS: mark live when service exists (URL may be set separately) ---
        if dep is not None and dep.cluster_arn and dep.service_arn:
            ecs_cluster = str(dep.cluster_arn)
            ecs_service = str(dep.service_arn)
            try:

                def _ecs_describe() -> dict:
                    return ECSAdapter(settings).describe_service(cluster=ecs_cluster, service=ecs_service)

                raw = await asyncio.to_thread(_ecs_describe)
            except ClientError as exc:
                log.exception("provision_ecs_describe_failed", job_id=str(job.id))
                await fail_job_while_running(session, job.id, message=str(exc))
                return
            failures = raw.get("failures") or []
            svcs = raw.get("services") or []
            if failures or not svcs:
                await fail_job_while_running(
                    session,
                    job.id,
                    message=f"ECS DescribeServices failed: {failures!r}",
                )
                return
            dep.status = DeploymentStatus.live
            agent.status = AgentStatus.active
            await session.commit()
            await complete_job_success(session, job.id, final_step=_STEP_DONE)
            await session.refresh(job)
            log.info("provision_ecs_ok", job_id=str(job.id), agent_id=str(agent.id))
            return

        # --- App Runner: CreateService (IAM + ECR image from env / Terraform outputs wired into task env) ---
        if (dep is None or not dep.app_runner_arn) and _app_runner_create_ready(settings):
            image = _resolve_create_image_identifier(settings, job)
            if not image:
                await fail_job_while_running(
                    session,
                    job.id,
                    message="App Runner create enabled but image identifier missing (payload.image_identifier or APP_RUNNER_CREATE_IMAGE_IDENTIFIER).",
                )
                return
            service_name = _app_runner_service_name(agent.id)
            access = (settings.app_runner_create_access_role_arn or "").strip()
            inst = (settings.app_runner_create_instance_role_arn or "").strip()
            port = (settings.app_runner_create_port or "8080").strip()
            cpu = (settings.app_runner_create_cpu or "1024").strip()
            memory = (settings.app_runner_create_memory or "2048").strip()
            health_path = (settings.app_runner_create_health_check_path or "/health").strip()
            vpc = (settings.app_runner_create_vpc_connector_arn or "").strip() or None
            asg = (settings.app_runner_create_auto_scaling_configuration_arn or "").strip() or None
            tags = [
                {"Key": "agent_hub_agent_id", "Value": str(agent.id)},
                {"Key": "agent_hub_tenant_id", "Value": str(agent.tenant_id)},
                {"Key": "agent_hub_agent_type", "Value": agent.agent_type.value},
            ]
            runtime_env: dict[str, str] = {
                "AGENT_HUB_AGENT_ID": str(agent.id),
                "AGENT_HUB_TENANT_ID": str(agent.tenant_id),
                "AGENT_HUB_AGENT_TYPE": agent.agent_type.value,
            }
            hub = (settings.hub_public_url or "").strip()
            if hub:
                runtime_env["AGENT_HUB_PUBLIC_URL"] = hub

            try:

                def _create() -> dict:
                    return AppRunnerAdapter(settings).create_service(
                        service_name=service_name,
                        image_identifier=image,
                        access_role_arn=access,
                        instance_role_arn=inst,
                        port=port,
                        cpu=cpu,
                        memory=memory,
                        auto_deployments_enabled=settings.app_runner_create_auto_deployments_enabled,
                        auto_scaling_configuration_arn=asg,
                        vpc_connector_arn=vpc,
                        health_check_path=health_path,
                        tags=tags,
                        runtime_environment_variables=runtime_env,
                    )

                create_raw = await asyncio.to_thread(_create)
            except ClientError as exc:
                log.exception("provision_apprunner_create_failed", job_id=str(job.id))
                await fail_job_while_running(session, job.id, message=str(exc))
                return

            svc0 = create_raw.get("Service") if isinstance(create_raw, dict) else None
            if not isinstance(svc0, dict):
                await fail_job_while_running(
                    session,
                    job.id,
                    message="CreateService returned no Service object",
                )
                return
            service_arn = svc0.get("ServiceArn")
            if not service_arn:
                await fail_job_while_running(
                    session,
                    job.id,
                    message="CreateService response missing ServiceArn",
                )
                return
            arn_str = str(service_arn)
            if dep is None:
                dep = Deployment(
                    agent_id=agent.id,
                    status=DeploymentStatus.pending,
                    app_runner_arn=arn_str,
                )
                session.add(dep)
            else:
                dep.app_runner_arn = arn_str
                if dep.status == DeploymentStatus.pending:
                    pass
            await session.commit()
            await session.refresh(dep)

            try:
                final_svc = await _wait_apprunner_running(settings, arn_str)
            except (TimeoutError, RuntimeError) as exc:
                log.exception("provision_apprunner_wait_failed", job_id=str(job.id))
                await fail_job_while_running(session, job.id, message=str(exc))
                return

            service_url = final_svc.get("ServiceUrl")
            if not service_url:
                await fail_job_while_running(
                    session,
                    job.id,
                    message="App Runner RUNNING but DescribeService missing ServiceUrl",
                )
                return
            dep.base_url = _service_url_to_https(str(service_url))
            dep.status = DeploymentStatus.live
            agent.status = AgentStatus.active
            await session.commit()
            await complete_job_success(session, job.id, final_step=_STEP_DONE)
            await session.refresh(job)
            log.info(
                "provision_apprunner_create_ok",
                job_id=str(job.id),
                agent_id=str(agent.id),
                service_arn=arn_str,
            )
            return

        # --- Local / compose: shared agent HTTP root from settings ---
        base = _fallback_base_url(settings, agent.agent_type)
        if base:
            if dep is None:
                dep = Deployment(
                    agent_id=agent.id,
                    status=DeploymentStatus.live,
                    base_url=base,
                )
                session.add(dep)
            else:
                dep.base_url = base
                dep.status = DeploymentStatus.live
            agent.status = AgentStatus.active
            await session.commit()
            await complete_job_success(session, job.id, final_step=_STEP_DONE)
            await session.refresh(job)
            log.info(
                "provision_shared_url_ok",
                job_id=str(job.id),
                agent_id=str(agent.id),
                agent_type=agent.agent_type.value,
            )
            return

        await fail_job_while_running(
            session,
            job.id,
            message=(
                "No runtime for provisioning: set APP_RUNNER_CREATE_* env vars for CreateService, "
                "or Deployment.app_runner_arn / cluster_arn+service_arn, "
                "or INCIDENT_TRIAGE_AGENT_URL for incident_triage in dev."
            ),
        )
