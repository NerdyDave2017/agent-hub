"""Slack OAuth (Install to Workspace) — store bot token in Secrets Manager and mirror in ``Integration``."""

from __future__ import annotations

import base64
import json
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from agent_hub_core.config.settings import get_settings
from agent_hub_core.db.models import Integration
from agent_hub_core.observability.logging import get_logger

from apis.dependencies import DbSession
from services import agents_service, aws_secrets, tenants_service

log = get_logger(__name__)

router = APIRouter()

# Bot scopes for incident alerts (``chat.postMessage``). Add more in Slack app config if needed.
SLACK_BOT_SCOPES = "chat:write"


def _slack_oauth_redirect_uri() -> str:
    s = get_settings()
    base = s.hub_public_url.rstrip("/")
    prefix = s.api_v1_prefix.rstrip("/") or "/api/v1"
    return f"{base}{prefix}/integrations/slack/oauth/callback"


def _slack_secret_name(tenant_id: UUID, agent_id: UUID) -> str:
    s = get_settings()
    safe_app = s.app_name.replace("/", "-")
    return f"{safe_app}/tenant/{tenant_id}/agent/{agent_id}/slack-oauth"


class SlackIntegrationRead(BaseModel):
    """Non-secret Slack connection snapshot for dashboards."""

    connected: bool = Field(default=False, description="True when a Slack integration row exists and is active.")
    connection_status: str | None = None
    team_id: str | None = None
    team_name: str | None = None
    scopes: str | None = None


@router.get("/tenants/{tenant_id}/agents/{agent_id}/integrations/slack", response_model=SlackIntegrationRead)
async def slack_integration_get(
    session: DbSession,
    tenant_id: UUID,
    agent_id: UUID,
) -> SlackIntegrationRead:
    await tenants_service.require_tenant(session, tenant_id)
    await agents_service.require_agent(session, tenant_id, agent_id)
    row = await session.scalar(
        select(Integration).where(
            Integration.tenant_id == tenant_id,
            Integration.agent_id == agent_id,
            Integration.provider == "slack",
        )
    )
    if row is None:
        return SlackIntegrationRead(connected=False)
    cfg = row.provider_config if isinstance(row.provider_config, dict) else {}
    return SlackIntegrationRead(
        connected=row.connection_status == "active",
        connection_status=row.connection_status,
        team_id=cfg.get("team_id") if cfg else None,
        team_name=cfg.get("team_name") if cfg else None,
        scopes=row.scopes,
    )


@router.get("/tenants/{tenant_id}/agents/{agent_id}/integrations/slack/oauth/start")
async def slack_oauth_start(
    session: DbSession,
    tenant_id: UUID,
    agent_id: UUID,
):
    await tenants_service.require_tenant(session, tenant_id)
    await agents_service.require_agent(session, tenant_id, agent_id)
    s = get_settings()
    if not s.slack_oauth_client_id or not s.slack_oauth_client_secret:
        raise HTTPException(status_code=503, detail="Slack OAuth is not configured on the hub")

    state_payload = {"tenant_id": str(tenant_id), "agent_id": str(agent_id)}
    state = base64.urlsafe_b64encode(json.dumps(state_payload).encode()).decode()
    redirect_uri = _slack_oauth_redirect_uri()
    params = {
        "client_id": s.slack_oauth_client_id,
        "scope": SLACK_BOT_SCOPES,
        "redirect_uri": redirect_uri,
        "state": state,
    }
    auth_url = f"https://slack.com/oauth/v2/authorize?{urlencode(params)}"
    return RedirectResponse(auth_url)


@router.get("/integrations/slack/oauth/callback")
async def slack_oauth_callback(
    session: DbSession,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
):
    if error:
        raise HTTPException(status_code=400, detail=f"OAuth error: {error}")
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code or state")
    try:
        state_data = json.loads(base64.urlsafe_b64decode(state.encode()).decode())
        tenant_id = UUID(state_data["tenant_id"])
        agent_id = UUID(state_data["agent_id"])
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid state: {exc}") from exc

    await tenants_service.require_tenant(session, tenant_id)
    await agents_service.require_agent(session, tenant_id, agent_id)

    s = get_settings()
    if not s.slack_oauth_client_id or not s.slack_oauth_client_secret:
        raise HTTPException(status_code=503, detail="Slack OAuth is not configured on the hub")

    redirect_uri = _slack_oauth_redirect_uri()
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            "https://slack.com/api/oauth.v2.access",
            data={
                "client_id": s.slack_oauth_client_id,
                "client_secret": s.slack_oauth_client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            },
        )
    try:
        data: dict[str, Any] = resp.json()
    except Exception as exc:
        log.exception("slack_oauth_invalid_json", status_code=resp.status_code)
        raise HTTPException(status_code=502, detail="Slack token response was not JSON") from exc

    if not data.get("ok"):
        err = data.get("error", "unknown")
        raise HTTPException(status_code=400, detail=f"Slack oauth.v2.access failed: {err}")

    bot_token = data.get("access_token")
    if not bot_token or not isinstance(bot_token, str):
        raise HTTPException(status_code=502, detail="Slack response missing bot access_token")

    team = data.get("team") or {}
    team_id = team.get("id")
    team_name = team.get("name")
    bot_user_id = data.get("bot_user_id")
    scope_raw = data.get("scope") or SLACK_BOT_SCOPES
    scopes_str = scope_raw if isinstance(scope_raw, str) else SLACK_BOT_SCOPES

    secret_body = {
        "bot_token": bot_token,
        "team_id": team_id,
        "team_name": team_name,
        "bot_user_id": bot_user_id,
        "scope": scopes_str,
    }
    secret_name = _slack_secret_name(tenant_id, agent_id)
    secret_arn = await aws_secrets.upsert_secret_string(
        name=secret_name,
        secret_string=json.dumps(secret_body),
    )

    provider_config: dict[str, Any] = {}
    if team_id:
        provider_config["team_id"] = str(team_id)
    if team_name:
        provider_config["team_name"] = str(team_name)
    if bot_user_id:
        provider_config["bot_user_id"] = str(bot_user_id)

    existing = await session.scalar(
        select(Integration).where(
            Integration.tenant_id == tenant_id,
            Integration.agent_id == agent_id,
            Integration.provider == "slack",
        )
    )
    if existing:
        existing.secret_arn = secret_arn
        existing.scopes = scopes_str
        existing.provider_config = provider_config
        existing.connection_status = "active"
    else:
        session.add(
            Integration(
                tenant_id=tenant_id,
                agent_id=agent_id,
                provider="slack",
                scopes=scopes_str,
                secret_arn=secret_arn,
                provider_config=provider_config or None,
                connection_status="active",
            )
        )
    await session.commit()

    dest = f"{s.hub_public_url.rstrip('/')}/?slack=connected=1"
    return RedirectResponse(dest, status_code=302)
