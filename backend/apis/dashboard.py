"""Tenant overview and per-agent observability (Postgres-backed)."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, func, select, text
from agent_hub_core.config.settings import get_settings
from agent_hub_core.db.engine import get_session_factory
from agent_hub_core.db.models import Agent, Incident, ToolCallEvent
from agent_hub_core.domain.enums import AgentStatus
from agent_hub_core.schemas.dashboard import (
    AgentSummaryRow,
    DailySpendRow,
    IncidentDetailResponse,
    SafeIncidentRow,
    TenantOverviewResponse,
)

from apis.dependencies import DbSession
from services import agents_service, tenants_service

router = APIRouter()


def _utc_cutoff(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


def _safe_incident_api(r: Incident) -> SafeIncidentRow:
    return SafeIncidentRow(
        id=str(r.id),
        incident_type=r.incident_type,
        severity=r.severity,
        summary=r.summary,
        confidence=r.confidence,
        actions_taken=r.actions_taken,
        slack_sent=r.slack_sent,
        created_at=r.created_at.isoformat() if r.created_at else "",
    )


@router.get("/overview", response_model=TenantOverviewResponse)
async def tenant_overview(
    tenant_id: UUID,
    session: DbSession,
    window_days: int = Query(30, ge=1, le=365),
) -> TenantOverviewResponse:
    await tenants_service.require_tenant(session, tenant_id)
    cutoff = _utc_cutoff(window_days)
    cutoff_7d = _utc_cutoff(7)

    total_agents = int(
        await session.scalar(select(func.count()).select_from(Agent).where(Agent.tenant_id == tenant_id)) or 0
    )
    active_agents = int(
        await session.scalar(
            select(func.count())
            .select_from(Agent)
            .where(Agent.tenant_id == tenant_id, Agent.status == AgentStatus.active)
        )
        or 0
    )

    tok = await session.execute(
        select(
            func.coalesce(func.sum(ToolCallEvent.prompt_tokens), 0),
            func.coalesce(func.sum(ToolCallEvent.completion_tokens), 0),
            func.coalesce(func.sum(ToolCallEvent.cost_usd), 0),
        ).where(
            and_(
                ToolCallEvent.tenant_id == tenant_id,
                ToolCallEvent.created_at >= cutoff,
                ToolCallEvent.node_name == "classify",
            )
        )
    )
    row = tok.one()
    p = int(row[0] or 0)
    c = int(row[1] or 0)
    cost_raw = row[2]
    total_cost = float(cost_raw) if cost_raw is not None else 0.0

    daily_rows = await session.execute(
        text(
            """
            SELECT date_trunc('day', created_at) AS day,
                   agent_id::text AS agent_id,
                   COALESCE(SUM(prompt_tokens + completion_tokens), 0)::bigint AS tokens,
                   COALESCE(SUM(cost_usd), 0)::double precision AS cost_usd
            FROM tool_call_events
            WHERE tenant_id = CAST(:tenant_id AS uuid)
              AND created_at >= :cutoff
              AND node_name = 'classify'
            GROUP BY 1, 2
            ORDER BY 1
            """
        ),
        {"tenant_id": str(tenant_id), "cutoff": cutoff},
    )
    daily_spend: list[DailySpendRow] = []
    for dr in daily_rows.mappings():
        day_val = dr["day"]
        if isinstance(day_val, datetime):
            day_s = day_val.date().isoformat()
        else:
            day_s = str(day_val)[:10]
        daily_spend.append(
            DailySpendRow(
                day=day_s,
                agent_id=dr.get("agent_id"),
                tokens=int(dr["tokens"] or 0),
                cost_usd=float(dr["cost_usd"] or 0),
            )
        )

    agents_list = (await session.scalars(select(Agent).where(Agent.tenant_id == tenant_id))).all()
    agent_summaries: list[AgentSummaryRow] = []
    token_expr = ToolCallEvent.prompt_tokens + ToolCallEvent.completion_tokens
    for ag in agents_list:
        st = await session.execute(
            select(
                func.coalesce(func.sum(token_expr), 0),
                func.coalesce(func.sum(ToolCallEvent.cost_usd), 0),
            ).where(
                and_(
                    ToolCallEvent.tenant_id == tenant_id,
                    ToolCallEvent.agent_id == ag.id,
                    ToolCallEvent.created_at >= cutoff_7d,
                    ToolCallEvent.node_name == "classify",
                )
            )
        )
        t7, c7 = st.one()
        agent_summaries.append(
            AgentSummaryRow(
                agent_id=str(ag.id),
                name=ag.name,
                agent_type=ag.agent_type.value,
                status=ag.status.value,
                tokens_7d=int(t7 or 0),
                cost_7d=float(c7 or 0) if c7 is not None else 0.0,
            )
        )

    return TenantOverviewResponse(
        window_days=window_days,
        total_agents=total_agents,
        active_agents=active_agents,
        total_tokens=p + c,
        total_cost_usd=total_cost,
        daily_spend=daily_spend,
        agents=agent_summaries,
    )


@router.get("/agents/{agent_id}/feed")
async def live_decision_feed(tenant_id: UUID, agent_id: UUID) -> StreamingResponse:
    """SSE: recent ``tool_call_events`` for one agent (~2s poll; see deployment instructions)."""
    factory = get_session_factory()
    async with factory() as s:
        await tenants_service.require_tenant(s, tenant_id)
        await agents_service.require_agent(s, tenant_id, agent_id)

    async def event_stream() -> Any:
        last_ts = datetime(1970, 1, 1, tzinfo=timezone.utc)
        while True:
            async with factory() as session:
                rows = (
                    await session.scalars(
                        select(ToolCallEvent)
                        .where(
                            ToolCallEvent.tenant_id == tenant_id,
                            ToolCallEvent.agent_id == agent_id,
                            ToolCallEvent.created_at > last_ts,
                        )
                        .order_by(ToolCallEvent.created_at.asc())
                        .limit(100)
                    )
                ).all()
            for row in rows:
                if row.created_at and row.created_at > last_ts:
                    last_ts = row.created_at
                cost = float(row.cost_usd) if row.cost_usd is not None else None
                payload = {
                    "id": str(row.id),
                    "node_name": row.node_name,
                    "tool_name": row.tool_name,
                    "decision": row.decision,
                    "duration_ms": row.duration_ms,
                    "succeeded": row.succeeded,
                    "error": row.error,
                    "created_at": row.created_at.isoformat() if row.created_at else "",
                    "prompt_tokens": row.prompt_tokens,
                    "completion_tokens": row.completion_tokens,
                    "cost_usd": cost,
                }
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
    session: DbSession,
    window_days: int = Query(7, ge=1, le=90),
    limit: int = Query(50, ge=1, le=200),
) -> list[SafeIncidentRow]:
    await tenants_service.require_tenant(session, tenant_id)
    await agents_service.require_agent(session, tenant_id, agent_id)
    cutoff = _utc_cutoff(window_days)
    rows = (
        await session.scalars(
            select(Incident)
            .where(
                Incident.tenant_id == tenant_id,
                Incident.agent_id == agent_id,
                Incident.created_at >= cutoff,
            )
            .order_by(Incident.created_at.desc())
            .limit(limit)
        )
    ).all()
    return [_safe_incident_api(r) for r in rows]


@router.get("/agents/{agent_id}/incidents/{incident_id}", response_model=IncidentDetailResponse)
async def get_agent_incident_detail(
    tenant_id: UUID,
    agent_id: UUID,
    incident_id: UUID,
    session: DbSession,
) -> IncidentDetailResponse:
    settings = get_settings()
    await tenants_service.require_tenant(session, tenant_id)
    await agents_service.require_agent(session, tenant_id, agent_id)
    inc = await session.scalar(
        select(Incident).where(
            Incident.id == incident_id,
            Incident.tenant_id == tenant_id,
            Incident.agent_id == agent_id,
        )
    )
    if inc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="incident not found")
    base = _safe_incident_api(inc)
    trace_url = None
    if inc.langfuse_trace_id:
        host = settings.langfuse_host.rstrip("/")
        trace_url = f"{host}/trace/{inc.langfuse_trace_id}"
    return IncidentDetailResponse(**base.model_dump(), langfuse_trace_url=trace_url)
