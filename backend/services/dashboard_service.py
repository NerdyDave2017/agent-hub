"""
Dashboard read models — Postgres aggregations and incident shaping for observability APIs.

Routers stay thin: dependency injection, status codes, and streaming wrappers only.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from agent_hub_core.db.models import Agent, Incident, MetricSnapshot, ToolCallEvent
from agent_hub_core.domain.enums import AgentStatus
from agent_hub_core.schemas.dashboard import (
    AgentOverviewResponse,
    AgentSummaryRow,
    AgentTokenDailyRow,
    DailySpendRow,
    IncidentDetailResponse,
    MetricSnapshotRow,
    SafeIncidentRow,
    TenantOverviewResponse,
)

from services import agents_service, tenants_service


def decision_feed_epoch_start() -> datetime:
    """Initial cursor for SSE so the first poll returns only new rows."""
    return datetime(1970, 1, 1, tzinfo=timezone.utc)


async def assert_decision_feed_access(
    session: AsyncSession,
    tenant_id: UUID,
    agent_id: UUID,
) -> None:
    await tenants_service.require_tenant(session, tenant_id)
    await agents_service.require_agent(session, tenant_id, agent_id)


def utc_cutoff(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


def safe_incident_row(r: Incident) -> SafeIncidentRow:
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


def _day_string(day_val: Any) -> str:
    if isinstance(day_val, datetime):
        return day_val.date().isoformat()
    return str(day_val)[:10]


async def get_tenant_overview(
    session: AsyncSession,
    tenant_id: UUID,
    *,
    window_days: int,
) -> TenantOverviewResponse:
    await tenants_service.require_tenant(session, tenant_id)
    cutoff = utc_cutoff(window_days)
    cutoff_7d = utc_cutoff(7)

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
        daily_spend.append(
            DailySpendRow(
                day=_day_string(dr["day"]),
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


async def get_agent_overview(
    session: AsyncSession,
    tenant_id: UUID,
    agent_id: UUID,
    *,
    window_days: int,
) -> AgentOverviewResponse:
    await tenants_service.require_tenant(session, tenant_id)
    ag = await agents_service.require_agent(session, tenant_id, agent_id)
    cutoff = utc_cutoff(window_days)
    tok = await session.execute(
        select(
            func.coalesce(func.sum(ToolCallEvent.prompt_tokens), 0),
            func.coalesce(func.sum(ToolCallEvent.completion_tokens), 0),
            func.coalesce(func.sum(ToolCallEvent.cost_usd), 0),
        ).where(
            and_(
                ToolCallEvent.tenant_id == tenant_id,
                ToolCallEvent.agent_id == agent_id,
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
    incidents_count = int(
        await session.scalar(
            select(func.count())
            .select_from(Incident)
            .where(
                Incident.tenant_id == tenant_id,
                Incident.agent_id == agent_id,
                Incident.created_at >= cutoff,
            )
        )
        or 0
    )
    return AgentOverviewResponse(
        agent_id=str(ag.id),
        name=ag.name,
        agent_type=ag.agent_type.value,
        status=ag.status.value,
        window_days=window_days,
        total_tokens=p + c,
        total_cost_usd=total_cost,
        incidents_count=incidents_count,
    )


async def list_agent_token_daily(
    session: AsyncSession,
    tenant_id: UUID,
    agent_id: UUID,
    *,
    window_days: int,
) -> list[AgentTokenDailyRow]:
    await tenants_service.require_tenant(session, tenant_id)
    await agents_service.require_agent(session, tenant_id, agent_id)
    cutoff = utc_cutoff(window_days)
    daily_rows = await session.execute(
        text(
            """
            SELECT date_trunc('day', created_at) AS day,
                   COALESCE(SUM(prompt_tokens + completion_tokens), 0)::bigint AS tokens,
                   COALESCE(SUM(cost_usd), 0)::double precision AS cost_usd
            FROM tool_call_events
            WHERE tenant_id = CAST(:tenant_id AS uuid)
              AND agent_id = CAST(:agent_id AS uuid)
              AND created_at >= :cutoff
              AND node_name = 'classify'
            GROUP BY 1
            ORDER BY 1
            """
        ),
        {"tenant_id": str(tenant_id), "agent_id": str(agent_id), "cutoff": cutoff},
    )
    out: list[AgentTokenDailyRow] = []
    for dr in daily_rows.mappings():
        out.append(
            AgentTokenDailyRow(
                day=_day_string(dr["day"]),
                tokens=int(dr["tokens"] or 0),
                cost_usd=float(dr["cost_usd"] or 0),
            )
        )
    return out


async def list_agent_metric_snapshots(
    session: AsyncSession,
    tenant_id: UUID,
    agent_id: UUID,
    *,
    limit: int,
) -> list[MetricSnapshotRow]:
    await tenants_service.require_tenant(session, tenant_id)
    ag = await agents_service.require_agent(session, tenant_id, agent_id)
    agent_type_str = ag.agent_type.value
    rows = (
        await session.scalars(
            select(MetricSnapshot)
            .where(MetricSnapshot.tenant_id == tenant_id, MetricSnapshot.agent_type == agent_type_str)
            .order_by(MetricSnapshot.window_start.desc())
            .limit(limit)
        )
    ).all()
    result: list[MetricSnapshotRow] = []
    for r in rows:
        result.append(
            MetricSnapshotRow(
                agent_type=r.agent_type,
                window_start=r.window_start.isoformat() if r.window_start else "",
                window_end=r.window_end.isoformat() if r.window_end else None,
                metrics=dict(r.metrics or {}),
            )
        )
    return result


async def list_tool_events_after(
    session: AsyncSession,
    tenant_id: UUID,
    agent_id: UUID,
    *,
    after: datetime,
) -> list[ToolCallEvent]:
    """Recent tool call rows strictly after ``after`` (ascending by ``created_at``)."""
    return list(
        (
            await session.scalars(
                select(ToolCallEvent)
                .where(
                    ToolCallEvent.tenant_id == tenant_id,
                    ToolCallEvent.agent_id == agent_id,
                    ToolCallEvent.created_at > after,
                )
                .order_by(ToolCallEvent.created_at.asc())
                .limit(100)
            )
        ).all()
    )


def tool_call_event_feed_payload(row: ToolCallEvent) -> dict[str, Any]:
    cost = float(row.cost_usd) if row.cost_usd is not None else None
    return {
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


async def list_agent_incidents_safe(
    session: AsyncSession,
    tenant_id: UUID,
    agent_id: UUID,
    *,
    window_days: int,
    limit: int,
) -> list[SafeIncidentRow]:
    await tenants_service.require_tenant(session, tenant_id)
    await agents_service.require_agent(session, tenant_id, agent_id)
    cutoff = utc_cutoff(window_days)
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
    return [safe_incident_row(r) for r in rows]


async def get_agent_incident_detail(
    session: AsyncSession,
    tenant_id: UUID,
    agent_id: UUID,
    incident_id: UUID,
    *,
    langfuse_host: str,
) -> IncidentDetailResponse | None:
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
        return None
    base = safe_incident_row(inc)
    trace_url = None
    if inc.langfuse_trace_id:
        host = langfuse_host.rstrip("/")
        trace_url = f"{host}/trace/{inc.langfuse_trace_id}"
    return IncidentDetailResponse(**base.model_dump(), langfuse_trace_url=trace_url)
