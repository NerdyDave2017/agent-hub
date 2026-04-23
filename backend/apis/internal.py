"""Agent-to-hub read APIs (Bearer ``INTERNAL_SERVICE_TOKEN``)."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_hub_core.config.settings import get_settings
from agent_hub_core.db.engine import get_db
from agent_hub_core.db.models import Incident, Tenant

from services import tenants_service


def require_internal_auth(request: Request) -> None:
    settings = get_settings()
    expected = (settings.internal_service_token or "").strip()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="INTERNAL_SERVICE_TOKEN not configured",
        )
    auth = (request.headers.get("Authorization") or request.headers.get("authorization") or "").strip()
    if auth != f"Bearer {expected}":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid bearer token")


router = APIRouter(dependencies=[Depends(require_internal_auth)])


@router.get("/tenants/{tenant_id}")
async def get_internal_tenant(
    tenant_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    tenant = await tenants_service.require_tenant(session, tenant_id)
    return {
        "id": str(tenant.id),
        "name": tenant.name,
        "slug": tenant.slug,
        "sla_tier": None,
        "contact_email": None,
        "slack_channel": None,
        "plan": None,
    }


def _safe_incident_for_internal_list(r: Incident) -> dict:
    """Fields safe for agent LLM context — no PII (see ``deployment-and-dashboard-instructions``)."""
    return {
        "id": str(r.id),
        "incident_type": r.incident_type,
        "severity": r.severity,
        "summary": r.summary,
        "confidence": r.confidence,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "slack_sent": r.slack_sent,
        "actions_taken": r.actions_taken,
    }


@router.get("/tenants/{tenant_id}/incidents/recent")
async def list_recent_incidents(
    tenant_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db)],
    limit: int = 5,
) -> list[dict]:
    await tenants_service.require_tenant(session, tenant_id)
    lim = max(1, min(limit, 50))
    rows = (
        await session.scalars(
            select(Incident)
            .where(Incident.tenant_id == tenant_id)
            .order_by(Incident.created_at.desc())
            .limit(lim)
        )
    ).all()
    return [_safe_incident_for_internal_list(r) for r in rows]
