"""Insert a ``Job`` row and optionally publish ``JobQueueEnvelope`` to SQS (worker callers)."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import UUID

from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from agent_hub_core.config.settings import Settings, get_settings
from agent_hub_core.db.models import Job
from agent_hub_core.domain.enums import JobStatus
from agent_hub_core.domain.job_payload import assert_safe_job_payload
from agent_hub_core.messaging.envelope import JobQueueEnvelope
from agent_hub_core.messaging.sqs import send_job_envelope
from agent_hub_core.observability.logging import get_logger

log = get_logger(__name__)


async def enqueue_job(
    session: AsyncSession,
    *,
    settings: Settings,
    tenant_id: UUID,
    agent_id: UUID | None,
    job_type: str,
    correlation_id: str,
    payload: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> Job:
    """
    Persist a new job and optionally send SQS (same semantics as hub ``create_job_with_publish``,
    without tenant existence checks — caller must have validated).
    """
    assert_safe_job_payload(payload)

    if idempotency_key:
        existing = await session.scalar(
            select(Job).where(
                Job.tenant_id == tenant_id,
                Job.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            return existing

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
        await session.refresh(job)
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
                return replay
        raise

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
            await asyncio.to_thread(
                send_job_envelope,
                settings=settings,
                body_json=body_json,
            )
            job.status = JobStatus.queued
            await session.commit()
            await session.refresh(job)
        except (BotoCoreError, ClientError, OSError, RuntimeError) as exc:
            log.warning(
                "worker_enqueue_sqs_failed",
                job_id=str(job.id),
                job_type=job_type,
                error=str(exc),
            )
    return job


async def enqueue_job_default_settings(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    agent_id: UUID | None,
    job_type: str,
    correlation_id: str,
    payload: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> Job:
    return await enqueue_job(
        session,
        settings=get_settings(),
        tenant_id=tenant_id,
        agent_id=agent_id,
        job_type=job_type,
        correlation_id=correlation_id,
        payload=payload,
        idempotency_key=idempotency_key,
    )
