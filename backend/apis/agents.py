"""
Agent registry HTTP surface.

`POST` creates the **DB row** then enqueues **`agent_provisioning`** work (see `services.jobs_service`)
so the worker can pull from SQS and reconcile runtime (App Runner / ECS / dev URL).

Lifecycle: `POST .../{id}/pause`, `.../scale-to-zero`, `.../destroy`, `.../deprovision` enqueue jobs
(SQS when configured). Monitor with `GET .../jobs/{job_id}` or `GET .../jobs/{job_id}/stream` (SSE).

Observability: same `X-Correlation-ID` as `POST .../jobs` when you pass it on create.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response, status
from fastapi.responses import StreamingResponse

from apis.dependencies import DbSession
from agent_hub_core.db.engine import get_session_factory
from agent_hub_core.domain.enums import JobType
from agent_hub_core.schemas.agent import AgentCreate, AgentProvisioningStatusRead, AgentRead, AgentUpdate
from agent_hub_core.schemas.job import JobRead
from agent_hub_core.schemas.common import PaginatedMeta, PaginatedResponse
from services import agents_service, jobs_service

router = APIRouter()

_CORRELATION_HEADER = "X-Correlation-ID"


@router.get("", response_model=PaginatedResponse[AgentRead])
async def list_agents(
    session: DbSession,
    tenant_id: UUID,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
) -> PaginatedResponse[AgentRead]:
    rows, total = await agents_service.list_agents_page(
        session, tenant_id=tenant_id, skip=skip, limit=limit
    )
    items = [AgentRead.model_validate(r) for r in rows]
    return PaginatedResponse[AgentRead](
        items=items,
        meta=PaginatedMeta(total=total, skip=skip, limit=limit),
    )


@router.post("", response_model=AgentRead, status_code=status.HTTP_201_CREATED)
async def create_agent(
    session: DbSession,
    request: Request,
    tenant_id: UUID,
    body: AgentCreate,
) -> AgentRead:
    """
    Create the agent row, then enqueue async **agent_provisioning** (DB job + optional SQS).

    The HTTP response is still the **agent**; poll `GET .../jobs/{id}` or add a jobs list on
    the agent later if you want the `job_id` on this response.
    """
    agent = await agents_service.create_agent(
        session, tenant_id, agent_type=body.agent_type, name=body.name
    )

    incoming = request.headers.get(_CORRELATION_HEADER)
    correlation_id = incoming.strip() if incoming and incoming.strip() else str(uuid.uuid4())

    await jobs_service.create_job_with_publish(
        session,
        tenant_id=tenant_id,
        job_type=JobType.agent_provisioning.value,
        correlation_id=correlation_id,
        agent_id=agent.id,
        idempotency_key=f"agent_provisioning:{agent.id}",
        payload=None,
    )

    await session.refresh(agent)
    return AgentRead.model_validate(agent)


@router.get(
    "/{agent_id}/provisioning-status",
    response_model=AgentProvisioningStatusRead,
    summary="Snapshot of agent + latest provisioning job",
)
async def get_agent_provisioning_status(
    session: DbSession,
    tenant_id: UUID,
    agent_id: UUID,
) -> AgentProvisioningStatusRead:
    """Immediate read for UI bootstrap (no blocking)."""
    session.expire_all()
    return await agents_service.get_agent_provisioning_status_read(session, tenant_id, agent_id)


@router.get(
    "/{agent_id}/provisioning-status/long-poll",
    response_model=AgentProvisioningStatusRead,
    summary="Long-poll until provisioning state changes or timeout",
)
async def long_poll_agent_provisioning(
    session: DbSession,
    tenant_id: UUID,
    agent_id: UUID,
    since: datetime | None = Query(
        default=None,
        description=(
            "Omit on first call to return the current snapshot immediately. "
            "On later calls, pass the `watermark` from the last response; the server "
            "blocks until `watermark` advances or `timeout` seconds elapse (then returns current)."
        ),
    ),
    timeout: int = Query(25, ge=1, le=55),
    poll_interval_ms: int = Query(500, ge=100, le=2000),
) -> AgentProvisioningStatusRead:
    """
    Hold the request open while re-querying Postgres on an interval.

    Typical UI flow: `GET .../provisioning-status` once, then loop `long-poll?since=<watermark>`
    from each response until the agent reaches a terminal status or the user navigates away.
    """
    if since is not None and since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    poll_interval_s = poll_interval_ms / 1000.0
    deadline = time.monotonic() + timeout

    while True:
        session.expire_all()
        snap = await agents_service.get_agent_provisioning_status_read(session, tenant_id, agent_id)
        if since is None or snap.watermark > since:
            return snap
        if time.monotonic() >= deadline:
            return snap
        await asyncio.sleep(poll_interval_s)


async def _agent_provisioning_sse(
    request: Request,
    tenant_id: UUID,
    agent_id: UUID,
    *,
    poll_interval_seconds: float,
) -> AsyncIterator[str]:
    """One DB session per tick so the stream survives past the HTTP dependency teardown."""
    factory = get_session_factory()
    last_payload: str | None = None
    heartbeat_s = 20.0
    last_heartbeat = time.monotonic()
    while True:
        if await request.is_disconnected():
            break
        async with factory() as session:
            snap = await agents_service.get_agent_provisioning_status_read(
                session, tenant_id, agent_id
            )
        payload = snap.model_dump_json()
        if payload != last_payload:
            last_payload = payload
            yield f"data: {payload}\n\n"
        now = time.monotonic()
        if now - last_heartbeat >= heartbeat_s:
            last_heartbeat = now
            yield ": heartbeat\n\n"
        await asyncio.sleep(poll_interval_seconds)


@router.get(
    "/{agent_id}/provisioning-status/stream",
    summary="SSE stream of provisioning status",
)
async def stream_agent_provisioning(
    request: Request,
    tenant_id: UUID,
    agent_id: UUID,
    poll_interval_seconds: float = Query(1.0, ge=0.25, le=5.0),
) -> StreamingResponse:
    """
    `text/event-stream` of JSON snapshots (same shape as `AgentProvisioningStatusRead`).

    Opens a fresh DB session on each poll so worker commits are visible without holding
    the request-scoped session for the lifetime of the stream.
    """
    # Touch DB once up front so 404 surfaces as HTTP error instead of first SSE chunk.
    factory = get_session_factory()
    async with factory() as session:
        await agents_service.get_agent_provisioning_status_read(session, tenant_id, agent_id)

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(
        _agent_provisioning_sse(
            request, tenant_id, agent_id, poll_interval_seconds=poll_interval_seconds
        ),
        media_type="text/event-stream",
        headers=headers,
    )


def _correlation_id(request: Request) -> str:
    incoming = request.headers.get(_CORRELATION_HEADER)
    return incoming.strip() if incoming and incoming.strip() else str(uuid.uuid4())


@router.post(
    "/{agent_id}/pause",
    response_model=JobRead,
    status_code=status.HTTP_201_CREATED,
    summary="Enqueue agent_pause (App Runner pause or ECS desiredCount=0)",
)
async def enqueue_agent_pause(
    session: DbSession,
    request: Request,
    response: Response,
    tenant_id: UUID,
    agent_id: UUID,
) -> JobRead:
    outcome = await jobs_service.create_job_with_publish(
        session,
        tenant_id=tenant_id,
        job_type=JobType.agent_pause.value,
        correlation_id=_correlation_id(request),
        agent_id=agent_id,
        idempotency_key=f"agent_pause:{agent_id}",
        payload=None,
    )
    response.status_code = outcome.status_code
    return JobRead.model_validate(outcome.job)


@router.post(
    "/{agent_id}/scale-to-zero",
    response_model=JobRead,
    status_code=status.HTTP_201_CREATED,
    summary="Enqueue deployment_scale_to_zero (same worker handler as agent_pause)",
)
async def enqueue_agent_scale_to_zero(
    session: DbSession,
    request: Request,
    response: Response,
    tenant_id: UUID,
    agent_id: UUID,
) -> JobRead:
    outcome = await jobs_service.create_job_with_publish(
        session,
        tenant_id=tenant_id,
        job_type=JobType.deployment_scale_to_zero.value,
        correlation_id=_correlation_id(request),
        agent_id=agent_id,
        idempotency_key=f"deployment_scale_to_zero:{agent_id}",
        payload=None,
    )
    response.status_code = outcome.status_code
    return JobRead.model_validate(outcome.job)


@router.post(
    "/{agent_id}/destroy",
    response_model=JobRead,
    status_code=status.HTTP_201_CREATED,
    summary="Enqueue agent_destroy (delete runtime; archive agent)",
)
async def enqueue_agent_destroy(
    session: DbSession,
    request: Request,
    response: Response,
    tenant_id: UUID,
    agent_id: UUID,
) -> JobRead:
    outcome = await jobs_service.create_job_with_publish(
        session,
        tenant_id=tenant_id,
        job_type=JobType.agent_destroy.value,
        correlation_id=_correlation_id(request),
        agent_id=agent_id,
        idempotency_key=f"agent_destroy:{agent_id}",
        payload=None,
    )
    response.status_code = outcome.status_code
    return JobRead.model_validate(outcome.job)


@router.post(
    "/{agent_id}/deprovision",
    response_model=JobRead,
    status_code=status.HTTP_201_CREATED,
    summary="Enqueue agent_deprovision (same worker handler as agent_destroy)",
)
async def enqueue_agent_deprovision(
    session: DbSession,
    request: Request,
    response: Response,
    tenant_id: UUID,
    agent_id: UUID,
) -> JobRead:
    outcome = await jobs_service.create_job_with_publish(
        session,
        tenant_id=tenant_id,
        job_type=JobType.agent_deprovision.value,
        correlation_id=_correlation_id(request),
        agent_id=agent_id,
        idempotency_key=f"agent_deprovision:{agent_id}",
        payload=None,
    )
    response.status_code = outcome.status_code
    return JobRead.model_validate(outcome.job)


@router.get("/{agent_id}", response_model=AgentRead)
async def get_agent(session: DbSession, tenant_id: UUID, agent_id: UUID) -> AgentRead:
    agent = await agents_service.get_agent(session, tenant_id, agent_id)
    return AgentRead.model_validate(agent)


@router.patch("/{agent_id}", response_model=AgentRead)
async def update_agent(
    session: DbSession, tenant_id: UUID, agent_id: UUID, body: AgentUpdate
) -> AgentRead:
    agent = await agents_service.update_agent(
        session, tenant_id, agent_id, name=body.name, status=body.status
    )
    return AgentRead.model_validate(agent)
