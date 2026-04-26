"""
HTTP handlers for **jobs** — observability and (for now) direct create for dev.

Why this module exists (read this once)
---------------------------------------
**Persistence and SQS** live in `services.jobs_service` so other flows (e.g. agent provision)
can enqueue work without duplicating SQL. These routes stay **thin**: parse HTTP, derive
`correlation_id`, call the service, return `JobRead`.

Later you can **restrict** `POST` (or remove it from public OpenAPI) while keeping `GET`
for operators and dashboards.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator
from typing import List
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response, status
from fastapi.responses import StreamingResponse

from apis.dependencies import DbSession
from agent_hub_core.db.engine import get_session_factory
from agent_hub_core.domain.enums import JobStatus
from agent_hub_core.schemas.job import JobCreate, JobRead
from services import jobs_service

router = APIRouter()

_CORRELATION_HEADER = "X-Correlation-ID"


def _job_terminal(status: JobStatus) -> bool:
    return status in (JobStatus.succeeded, JobStatus.failed, JobStatus.dead_lettered)


@router.post("", response_model=JobRead)
async def create_job(
    session: DbSession,
    request: Request,
    response: Response,
    tenant_id: UUID,
    body: JobCreate,
) -> JobRead:
    """
    Create a job row and optionally publish to SQS — see `jobs_service.create_job_with_publish`.

    Prefer calling that service from **domain routes** (e.g. agent deploy) in production;
    this endpoint remains useful for integration tests and manual enqueue during bring-up.
    """
    incoming = request.headers.get(_CORRELATION_HEADER)
    correlation_id = incoming.strip() if incoming and incoming.strip() else str(uuid.uuid4())

    outcome = await jobs_service.create_job_with_publish(
        session,
        tenant_id=tenant_id,
        job_type=body.job_type,
        agent_id=body.agent_id,
        idempotency_key=body.idempotency_key,
        payload=body.payload,
        correlation_id=correlation_id,
    )
    response.status_code = outcome.status_code
    return JobRead.model_validate(outcome.job)


async def _job_status_sse(
    request: Request,
    tenant_id: UUID,
    job_id: UUID,
    *,
    poll_interval_seconds: float,
) -> AsyncIterator[str]:
    """Poll ``jobs`` row; emit when JSON changes; heartbeats keep proxies from closing."""
    factory = get_session_factory()
    last_payload: str | None = None
    heartbeat_s = 20.0
    last_heartbeat = time.monotonic()
    while True:
        if await request.is_disconnected():
            break
        async with factory() as session:
            job = await jobs_service.get_job_for_tenant(session, tenant_id, job_id)
        payload = JobRead.model_validate(job).model_dump_json()
        if payload != last_payload:
            last_payload = payload
            yield f"data: {payload}\n\n"
        now = time.monotonic()
        if now - last_heartbeat >= heartbeat_s:
            last_heartbeat = now
            yield ": heartbeat\n\n"
        if _job_terminal(job.status):
            break
        await asyncio.sleep(poll_interval_seconds)


@router.get(
    "/{job_id}/stream",
    summary="SSE stream of job status until terminal state",
)
async def stream_job_status(
    request: Request,
    tenant_id: UUID,
    job_id: UUID,
    poll_interval_seconds: float = Query(1.0, ge=0.25, le=5.0),
) -> StreamingResponse:
    """
    ``text/event-stream`` of JSON snapshots (``JobRead``) until ``succeeded`` / ``failed`` /
    ``dead_lettered``. Same pattern as agent provisioning SSE: fresh session each poll.
    """
    factory = get_session_factory()
    async with factory() as session:
        await jobs_service.get_job_for_tenant(session, tenant_id, job_id)

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(
        _job_status_sse(
            request, tenant_id, job_id, poll_interval_seconds=poll_interval_seconds
        ),
        media_type="text/event-stream",
        headers=headers,
    )


@router.get("/{job_id}", response_model=JobRead)
async def get_job(session: DbSession, tenant_id: UUID, job_id: UUID) -> JobRead:
    """Fetch a single job for observability / UI polling."""
    job = await jobs_service.get_job_for_tenant(session, tenant_id, job_id)
    return JobRead.model_validate(job)

@router.get("", response_model=List[JobRead])
async def get_all_tenant_jobs(session: DbSession, tenant_id: UUID) -> List[JobRead]:
    """Fetch all jobs for a tenant for observability / UI polling."""
    jobs = await jobs_service.get_all_tenant_jobs(session, tenant_id)
    return [JobRead.model_validate(job) for job in jobs]