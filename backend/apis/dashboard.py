"""Tenant overview and per-agent observability (Postgres-backed)."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import StreamingResponse

from agent_hub_core.config.settings import get_settings
from agent_hub_core.db.engine import get_session_factory
from agent_hub_core.schemas.dashboard import (
    AgentOverviewResponse,
    AgentTokenDailyRow,
    IncidentDetailResponse,
    MetricSnapshotRow,
    SafeIncidentRow,
    TenantOverviewResponse,
)

from apis.dependencies import DbSession
from deps.auth import DashboardAuth
from services import dashboard_service

router = APIRouter()


@router.get("/overview", response_model=TenantOverviewResponse)
async def tenant_overview(
    tenant_id: UUID,
    principal: DashboardAuth,
    session: DbSession,
    window_days: int = Query(30, ge=1, le=365),
) -> TenantOverviewResponse:
    return await dashboard_service.get_tenant_overview(session, principal.tenant_id, window_days=window_days)


@router.get("/agents/{agent_id}/overview", response_model=AgentOverviewResponse)
async def agent_overview(
    tenant_id: UUID,
    agent_id: UUID,
    principal: DashboardAuth,
    session: DbSession,
    window_days: int = Query(30, ge=1, le=365),
) -> AgentOverviewResponse:
    return await dashboard_service.get_agent_overview(
        session, principal.tenant_id, agent_id, window_days=window_days
    )


@router.get("/agents/{agent_id}/tokens", response_model=list[AgentTokenDailyRow])
async def agent_token_usage(
    tenant_id: UUID,
    agent_id: UUID,
    principal: DashboardAuth,
    session: DbSession,
    window_days: int = Query(30, ge=1, le=365),
) -> list[AgentTokenDailyRow]:
    return await dashboard_service.list_agent_token_daily(
        session, principal.tenant_id, agent_id, window_days=window_days
    )


@router.get("/agents/{agent_id}/snapshots", response_model=list[MetricSnapshotRow])
async def agent_metric_snapshots(
    tenant_id: UUID,
    agent_id: UUID,
    principal: DashboardAuth,
    session: DbSession,
    limit: int = Query(168, ge=1, le=500),
) -> list[MetricSnapshotRow]:
    """
    Rows from ``metric_snapshots`` for this tenant and the agent's ``agent_type`` (rollup
    is per-type, not per-agent id — multiple agents of the same type share series).
    """
    return await dashboard_service.list_agent_metric_snapshots(
        session, principal.tenant_id, agent_id, limit=limit
    )


@router.get("/agents/{agent_id}/feed")
async def live_decision_feed(
    tenant_id: UUID,
    agent_id: UUID,
    principal: DashboardAuth,
) -> StreamingResponse:
    """SSE: recent ``tool_call_events`` for one agent (~2s poll; see deployment instructions)."""
    tid = principal.tenant_id
    factory = get_session_factory()
    async with factory() as s:
        await dashboard_service.assert_decision_feed_access(s, tid, agent_id)

    async def event_stream() -> Any:
        last_ts = dashboard_service.decision_feed_epoch_start()
        while True:
            async with factory() as session:
                rows = await dashboard_service.list_tool_events_after(session, tid, agent_id, after=last_ts)
            for row in rows:
                if row.created_at and row.created_at > last_ts:
                    last_ts = row.created_at
                payload = dashboard_service.tool_call_event_feed_payload(row)
                yield f"data: {json.dumps(payload)}\n\n"
            await asyncio.sleep(2)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/agents/{agent_id}/incidents", response_model=list[SafeIncidentRow])
async def list_agent_incidents(
    tenant_id: UUID,
    agent_id: UUID,
    principal: DashboardAuth,
    session: DbSession,
    window_days: int = Query(7, ge=1, le=90),
    limit: int = Query(50, ge=1, le=200),
) -> list[SafeIncidentRow]:
    return await dashboard_service.list_agent_incidents_safe(
        session, principal.tenant_id, agent_id, window_days=window_days, limit=limit
    )


@router.get("/agents/{agent_id}/incidents/{incident_id}", response_model=IncidentDetailResponse)
async def get_agent_incident_detail(
    tenant_id: UUID,
    agent_id: UUID,
    incident_id: UUID,
    principal: DashboardAuth,
    session: DbSession,
) -> IncidentDetailResponse:
    settings = get_settings()
    detail = await dashboard_service.get_agent_incident_detail(
        session,
        principal.tenant_id,
        agent_id,
        incident_id,
        langfuse_host=settings.langfuse_host,
    )
    if detail is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="incident not found")
    return detail
