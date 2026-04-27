"""
Job rows (Postgres mirror of async work) and optional **SQS publish** after commit.

Callers may be HTTP routers or other services. Inputs are **plain values** (no Pydantic
request models); wire JSON still uses ``JobQueueEnvelope`` from ``agent_hub_core.messaging`` (transport shape).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, List
from uuid import UUID

from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from agent_hub_core.config.settings import get_settings
from agent_hub_core.db.models import Agent, Job
from agent_hub_core.domain.enums import JobStatus, JobType
from agent_hub_core.domain.exceptions import JobNotFound
from agent_hub_core.domain.job_payload import assert_safe_job_payload
from agent_hub_core.messaging.envelope import JobQueueEnvelope
from agent_hub_core.messaging.sqs import send_job_envelope
from agent_hub_core.observability.logging import get_logger
from services import agents_service
from services.tenants_service import require_tenant

log = get_logger(__name__)

# Numeric codes only — **not** `http.HTTPStatus`, so this module stays free of HTTP-named types.
# Routers use these for `POST` semantics: **201** first create, **200** idempotent replay (RFC-style).
_STATUS_FIRST_CREATE = 201
_STATUS_IDEMPOTENT_REPLAY = 200


@dataclass(frozen=True)
class CreateJobResult:
    """
    Bundle returned by `create_job_with_publish` so callers get both:

    * **`job`** — the SQLAlchemy `Job` row after commit (and optional SQS publish / status flip).
    * **`status_code`** — **201** when a new row was inserted, **200** when an existing row was
      returned for the same `(tenant_id, idempotency_key)` (client retry / double submit).

    The API layer maps `status_code` onto `Response.status_code` for `POST .../jobs`; non-HTTP
    callers (e.g. another service) can ignore it and only read `job`.
    """

    job: Job
    status_code: int


async def get_job_for_tenant(session: AsyncSession, tenant_id: UUID, job_id: UUID) -> Job:
    """Return the job row scoped to tenant, or raise `JobNotFound`."""
    await require_tenant(session, tenant_id)
    job = await session.get(Job, job_id)
    if job is None or job.tenant_id != tenant_id:
        raise JobNotFound(job_id, tenant_id)
    return job


async def get_all_tenant_jobs(session: AsyncSession, tenant_id: UUID) -> List[Job]:
    """Return all jobs for a tenant."""
    return await session.scalars(select(Job).where(Job.tenant_id == tenant_id))


_DESTROY_JOB_TYPES = frozenset(
    {JobType.agent_destroy.value, JobType.agent_deprovision.value},
)


async def _should_supersede_destroy_idempotency(
    session: AsyncSession,
    existing: Job,
    *,
    tenant_id: UUID,
    agent_id: UUID | None,
) -> bool:
    """
    Allow a new ``agent_destroy`` / ``agent_deprovision`` row to reuse the canonical
    idempotency key when the prior attempt is no longer a safe replay.

    Otherwise a **failed** first delete (common after pause/archived + AWS teardown quirks)
    blocks all retries because ``create_job_with_publish`` would keep returning the same
    terminal job without enqueueing SQS again.
    """
    if agent_id is None or existing.tenant_id != tenant_id:
        return False
    if existing.job_type not in _DESTROY_JOB_TYPES:
        return False
    if existing.status in (JobStatus.failed, JobStatus.dead_lettered):
        return existing.agent_id == agent_id
    if existing.status == JobStatus.succeeded:
        row = await session.get(Agent, agent_id)
        return row is not None
    return False


async def create_job_with_publish(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    job_type: str,
    correlation_id: str,
    agent_id: UUID | None = None,
    idempotency_key: str | None = None,
    payload: dict[str, Any] | None = None,
) -> CreateJobResult:
    """
    Insert (or replay) a job, optionally publish to SQS, return the ORM row + status code.

    * **200** — idempotent replay: existing row for `(tenant_id, idempotency_key)`.
    * **201** — new row inserted.

    Transaction note: commits after initial insert; may **commit** again when moving to `queued`.
    """
    assert_safe_job_payload(payload)

    await require_tenant(session, tenant_id)

    if agent_id is not None:
        await agents_service.assert_agent_belongs_to_tenant(
            session, tenant_id=tenant_id, agent_id=agent_id
        )

    if idempotency_key:
        existing = await session.scalar(
            select(Job).where(
                Job.tenant_id == tenant_id,
                Job.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            if await _should_supersede_destroy_idempotency(
                session, existing, tenant_id=tenant_id, agent_id=agent_id
            ):
                existing.idempotency_key = f"{idempotency_key}:superseded:{existing.id}"
                await session.commit()
                log.info(
                    "job_idempotency_superseded_for_retry",
                    prior_job_id=str(existing.id),
                    tenant_id=str(tenant_id),
                    agent_id=str(agent_id) if agent_id else None,
                    prior_status=existing.status.value,
                )
            else:
                return CreateJobResult(job=existing, status_code=_STATUS_IDEMPOTENT_REPLAY)

    job = Job(
        tenant_id=tenant_id,
        agent_id=agent_id,
        job_type=job_type,
        status=JobStatus.pending,
        correlation_id=correlation_id,
        idempotency_key=idempotency_key,
        payload=payload,
    )
    session.add(job)

    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        if idempotency_key:
            replay = await session.scalar(
                select(Job).where(
                    Job.tenant_id == tenant_id,
                    Job.idempotency_key == idempotency_key,
                )
            )
            if replay is not None:
                return CreateJobResult(job=replay, status_code=_STATUS_IDEMPOTENT_REPLAY)
        raise

    await session.refresh(job)
    out_status = _STATUS_FIRST_CREATE

    settings = get_settings()
    if settings.sqs_queue_url:
        envelope = JobQueueEnvelope.from_committed_job(
            job_id=job.id,
            tenant_id=tenant_id,
            job_type=job.job_type,
            correlation_id=job.correlation_id,
            agent_id=job.agent_id,
            payload=job.payload,
        )
        body_json = json.dumps(envelope.model_dump(mode="json"))
        try:
            message_id = await asyncio.to_thread(
                send_job_envelope,
                settings=settings,
                body_json=body_json,
            )
            log.info(
                "job_enqueued_to_sqs",
                service="hub",
                job_id=str(job.id),
                tenant_id=str(tenant_id),
                correlation_id=job.correlation_id,
                sqs_message_id=message_id,
            )
            job.status = JobStatus.queued
            await session.commit()
            await session.refresh(job)
        except (BotoCoreError, ClientError, OSError, RuntimeError) as exc:
            log.warning(
                "sqs_send_failed_job_left_pending",
                service="hub",
                job_id=str(job.id),
                tenant_id=str(tenant_id),
                correlation_id=job.correlation_id,
                error=str(exc),
            )

    return CreateJobResult(job=job, status_code=out_status)
