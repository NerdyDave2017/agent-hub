"""Gmail OAuth + ``users.watch`` — tenant connects mailbox to platform Pub/Sub topic."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from uuid import UUID

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from sqlalchemy import select

from agent_hub_core.config.settings import get_settings
from agent_hub_core.db.models import Integration
from agent_hub_core.observability.logging import get_logger

from apis.dependencies import DbSession
from services import agents_service, aws_secrets, tenants_service

log = get_logger(__name__)

router = APIRouter()

# Hub never calls the agent over HTTP. After OAuth, `integrations.watch_active=True`
# (set below) lets the agent poll loop skip Gmail polling when hub Pub/Sub is active.

GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
]


def _gmail_oauth_redirect_uri() -> str:
    s = get_settings()
    base = s.hub_public_url.rstrip("/")
    prefix = s.api_v1_prefix.rstrip("/") or "/api/v1"
    return f"{base}{prefix}/integrations/gmail/oauth/callback"


def _make_flow():
    from google_auth_oauthlib.flow import Flow

    s = get_settings()
    if not s.gmail_oauth_client_id or not s.gmail_oauth_client_secret:
        raise HTTPException(status_code=503, detail="Gmail OAuth is not configured on the hub")
    redirect_uri = _gmail_oauth_redirect_uri()
    return Flow.from_client_config(
        client_config={
            "web": {
                "client_id": s.gmail_oauth_client_id,
                "client_secret": s.gmail_oauth_client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [redirect_uri],
            }
        },
        scopes=GMAIL_SCOPES,
        redirect_uri=redirect_uri,
    )


def _secret_name(tenant_id: UUID, agent_id: UUID) -> str:
    s = get_settings()
    safe_app = s.app_name.replace("/", "-")
    return f"{safe_app}/tenant/{tenant_id}/agent/{agent_id}/gmail-oauth"


@router.get("/tenants/{tenant_id}/agents/{agent_id}/integrations/gmail/oauth/start")
async def gmail_oauth_start(
    session: DbSession,
    tenant_id: UUID,
    agent_id: UUID,
):
    await tenants_service.require_tenant(session, tenant_id)
    await agents_service.require_agent(session, tenant_id, agent_id)
    flow = _make_flow()
    state_payload = {"tenant_id": str(tenant_id), "agent_id": str(agent_id)}
    state = base64.urlsafe_b64encode(json.dumps(state_payload).encode()).decode()
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=state,
    )
    return RedirectResponse(auth_url)


@router.get("/integrations/gmail/oauth/callback")
async def gmail_oauth_callback(
    session: DbSession,
    code: str = Query(...),
    state: str = Query(...),
    error: str | None = Query(default=None),
):
    if error:
        raise HTTPException(status_code=400, detail=f"OAuth error: {error}")
    try:
        state_data = json.loads(base64.urlsafe_b64decode(state.encode()).decode())
        tenant_id = UUID(state_data["tenant_id"])
        agent_id = UUID(state_data["agent_id"])
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid state: {exc}") from exc

    await tenants_service.require_tenant(session, tenant_id)
    await agents_service.require_agent(session, tenant_id, agent_id)

    flow = _make_flow()
    try:
        flow.fetch_token(code=code)
    except Exception as exc:
        log.exception("gmail_oauth_token_exchange_failed")
        raise HTTPException(status_code=400, detail=f"Token exchange failed: {exc}") from exc

    creds = flow.credentials
    settings = get_settings()

    async with httpx.AsyncClient(timeout=20.0) as client:
        userinfo = await client.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {creds.token}"},
        )
    if userinfo.status_code != 200:
        raise HTTPException(status_code=502, detail="Failed to read Google userinfo")
    email_address = userinfo.json().get("email")
    if not email_address:
        raise HTTPException(status_code=502, detail="Google userinfo missing email")

    secret_body = {
        "access_token": creds.token,
        "refresh_token": creds.refresh_token,
        "client_id": settings.gmail_oauth_client_id,
        "client_secret": settings.gmail_oauth_client_secret,
        "token_uri": "https://oauth2.googleapis.com/token",
        "scopes": list(creds.scopes or GMAIL_SCOPES),
    }
    secret_name = _secret_name(tenant_id, agent_id)
    secret_arn = await aws_secrets.upsert_secret_string(
        name=secret_name,
        secret_string=json.dumps(secret_body),
    )

    topic = (settings.gmail_pubsub_topic or "").strip()
    if not topic:
        raise HTTPException(
            status_code=503,
            detail="GMAIL_PUBSUB_TOPIC is not configured — cannot call users.watch()",
        )

    oauth_creds = Credentials(
        token=creds.token,
        refresh_token=creds.refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.gmail_oauth_client_id,
        client_secret=settings.gmail_oauth_client_secret,
        scopes=list(creds.scopes or GMAIL_SCOPES),
    )
    gmail = build("gmail", "v1", credentials=oauth_creds, cache_discovery=False)
    try:
        watch_response = (
            gmail.users()
            .watch(
                userId="me",
                body={
                    "topicName": topic,
                    "labelIds": ["INBOX"],
                    "labelFilterBehavior": "INCLUDE",
                },
            )
            .execute()
        )
    except Exception as exc:
        log.exception("gmail_watch_failed", tenant_id=str(tenant_id), agent_id=str(agent_id))
        raise HTTPException(
            status_code=502,
            detail=(
                "Gmail users.watch() failed. Ensure the Pub/Sub topic exists and "
                "gmail-api-push@system.gserviceaccount.com can publish to it. "
                f"Error: {exc}"
            ),
        ) from exc

    watch_expires_at = datetime.fromtimestamp(
        int(watch_response["expiration"]) / 1000,
        tz=timezone.utc,
    )
    history_id = str(watch_response.get("historyId") or "")

    existing = await session.scalar(
        select(Integration).where(
            Integration.tenant_id == tenant_id,
            Integration.agent_id == agent_id,
            Integration.provider == "gmail",
        )
    )
    scopes_str = " ".join(GMAIL_SCOPES)
    if existing:
        existing.secret_arn = secret_arn
        existing.email_address = email_address
        existing.last_history_id = history_id
        existing.watch_expires_at = watch_expires_at
        existing.watch_active = False
        existing.watch_resource_id = str(watch_response.get("resourceId") or "")
        existing.connection_status = "active"
        existing.scopes = scopes_str
    else:
        session.add(
            Integration(
                tenant_id=tenant_id,
                agent_id=agent_id,
                provider="gmail",
                scopes=scopes_str,
                secret_arn=secret_arn,
                email_address=email_address,
                last_history_id=history_id,
                watch_expires_at=watch_expires_at,
                watch_active=False,
                watch_resource_id=str(watch_response.get("resourceId") or ""),
                connection_status="active",
            )
        )
    await session.commit()

    dest = f"{settings.hub_public_url.rstrip('/')}/?gmail=connected=1"
    return RedirectResponse(dest, status_code=302)
