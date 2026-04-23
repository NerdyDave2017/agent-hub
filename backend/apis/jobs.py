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

from typing import List
import uuid
from uuid import UUID

from fastapi import APIRouter, Request, Response, status

from apis.dependencies import DbSession
from agent_hub_core.schemas.job import JobCreate, JobRead
from services import jobs_service

router = APIRouter()

_CORRELATION_HEADER = "X-Correlation-ID"


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