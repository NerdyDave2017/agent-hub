"""Gmail OAuth + ``users.watch`` — tenant connects mailbox to platform Pub/Sub topic."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from uuid import UUID

import httpx
from botocore.exceptions import ClientError
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from agent_hub_core.config.settings import get_settings
from agent_hub_core.db.models import Integration
from agent_hub_core.observability.logging import get_logger

from apis.dependencies import DbSession
from services import agents_service, aws_secrets, tenants_service

log = get_logger(__name__)

router = APIRouter()


def _oauth_code_metrics(code: str | None) -> dict[str, int | bool]:
    if not code:
        return {"oauth_code_present": False}
    return {"oauth_code_present": True, "oauth_code_length": len(code)}

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
    override = (s.google_oauth_redirect_uri or "").strip()
    if override:
        return override.rstrip("/")
    base = s.hub_public_url.rstrip("/")
    prefix = (s.api_v1_prefix or "/api/v1").strip() or "/api/v1"
    if not prefix.startswith("/"):
        prefix = "/" + prefix
    prefix = prefix.rstrip("/") or "/api/v1"
    return f"{base}{prefix}/integrations/gmail/oauth/callback"


def _make_flow():
    from google_auth_oauthlib.flow import Flow

    s = get_settings()
    if not s.google_oauth_client_id or not s.google_oauth_client_secret:
        raise HTTPException(
            status_code=400,
            detail="Gmail OAuth is not configured on this hub (set GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET).",
        )
    redirect_uri = _gmail_oauth_redirect_uri()
    # Web client uses client_secret at token endpoint — PKCE is optional. If
    # autogenerate_code_verifier stays True, authorization_url() sets a verifier
    # on this Flow only; the callback builds a new Flow and fetch_token() then
    # sends no verifier → Google (invalid_grant) Missing code verifier.
    return Flow.from_client_config(
        client_config={
            "web": {
                "client_id": s.google_oauth_client_id,
                "client_secret": s.google_oauth_client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [redirect_uri],
            }
        },
        scopes=GMAIL_SCOPES,
        redirect_uri=redirect_uri,
        autogenerate_code_verifier=False,
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
    log.info("gmail_oauth_start_entered", tenant_id=str(tenant_id), agent_id=str(agent_id))
    await tenants_service.require_tenant(session, tenant_id)
    await agents_service.require_agent(session, tenant_id, agent_id)
    log.info("gmail_oauth_start_tenant_agent_ok", tenant_id=str(tenant_id), agent_id=str(agent_id))
    redirect_uri = _gmail_oauth_redirect_uri()
    log.info("gmail_oauth_start_redirect_uri", tenant_id=str(tenant_id), agent_id=str(agent_id), redirect_uri=redirect_uri)
    flow = _make_flow()
    state_payload = {"tenant_id": str(tenant_id), "agent_id": str(agent_id)}
    state = base64.urlsafe_b64encode(json.dumps(state_payload).encode()).decode()
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=state,
    )
    log.info(
        "gmail_oauth_start_redirecting",
        tenant_id=str(tenant_id),
        agent_id=str(agent_id),
        redirect_uri=redirect_uri,
        state_length=len(state),
    )
    return RedirectResponse(auth_url)


@router.get("/integrations/gmail/oauth/callback")
async def gmail_oauth_callback(
    session: DbSession,
    code: str = Query(...),
    state: str = Query(...),
    error: str | None = Query(default=None),
):
    log.info(
        "gmail_oauth_callback_entered",
        **_oauth_code_metrics(code),
        state_length=len(state) if state else 0,
        google_error=error,
    )
    if error:
        log.warning("gmail_oauth_callback_google_error", google_error=error)
        raise HTTPException(status_code=400, detail=f"OAuth error: {error}")
    try:
        state_data = json.loads(base64.urlsafe_b64decode(state.encode()).decode())
        tenant_id = UUID(state_data["tenant_id"])
        agent_id = UUID(state_data["agent_id"])
    except Exception as exc:
        log.warning("gmail_oauth_callback_invalid_state", error=str(exc))
        raise HTTPException(status_code=400, detail=f"Invalid state: {exc}") from exc

    log.info("gmail_oauth_callback_state_ok", tenant_id=str(tenant_id), agent_id=str(agent_id))
    await tenants_service.require_tenant(session, tenant_id)
    await agents_service.require_agent(session, tenant_id, agent_id)

    flow = _make_flow()
    log.info(
        "gmail_oauth_token_exchange_begin",
        tenant_id=str(tenant_id),
        agent_id=str(agent_id),
        redirect_uri=_gmail_oauth_redirect_uri(),
    )
    try:
        flow.fetch_token(code=code)
    except Exception as exc:
        log.exception(
            "gmail_oauth_token_exchange_failed",
            tenant_id=str(tenant_id),
            agent_id=str(agent_id),
        )
        raise HTTPException(status_code=400, detail=f"Token exchange failed: {exc}") from exc

    log.info(
        "gmail_oauth_token_exchange_ok",
        tenant_id=str(tenant_id),
        agent_id=str(agent_id),
        has_refresh_token=bool(flow.credentials and flow.credentials.refresh_token),
    )
    creds = flow.credentials
    settings = get_settings()

    log.info("gmail_oauth_userinfo_request", tenant_id=str(tenant_id), agent_id=str(agent_id))
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            userinfo = await client.get(
                "https://www.googleapis.com/oauth2/v2/userinfo",
                headers={"Authorization": f"Bearer {creds.token}"},
            )
    except httpx.RequestError as exc:
        log.exception(
            "gmail_oauth_userinfo_http_error",
            tenant_id=str(tenant_id),
            agent_id=str(agent_id),
        )
        raise HTTPException(
            status_code=400,
            detail="Could not reach Google userinfo. Check outbound network access and try again.",
        ) from exc
    log.info(
        "gmail_oauth_userinfo_response",
        tenant_id=str(tenant_id),
        agent_id=str(agent_id),
        http_status=userinfo.status_code,
    )
    if userinfo.status_code != 200:
        log.warning(
            "gmail_oauth_userinfo_failed",
            tenant_id=str(tenant_id),
            agent_id=str(agent_id),
            http_status=userinfo.status_code,
        )
        raise HTTPException(
            status_code=400,
            detail=f"Google userinfo request failed (HTTP {userinfo.status_code}).",
        )
    email_address = userinfo.json().get("email")
    if not email_address:
        log.warning(
            "gmail_oauth_userinfo_missing_email",
            tenant_id=str(tenant_id),
            agent_id=str(agent_id),
        )
        raise HTTPException(
            status_code=400,
            detail="Google userinfo response did not include an email address.",
        )
    log.info(
        "gmail_oauth_userinfo_ok",
        tenant_id=str(tenant_id),
        agent_id=str(agent_id),
        email_domain=(email_address.split("@")[-1] if "@" in str(email_address) else None),
    )

    secret_body = {
        "access_token": creds.token,
        "refresh_token": creds.refresh_token,
        "client_id": settings.google_oauth_client_id,
        "client_secret": settings.google_oauth_client_secret,
        "token_uri": "https://oauth2.googleapis.com/token",
        "scopes": list(creds.scopes or GMAIL_SCOPES),
    }
    secret_name = _secret_name(tenant_id, agent_id)
    log.info(
        "gmail_oauth_secret_upsert_begin",
        tenant_id=str(tenant_id),
        agent_id=str(agent_id),
        secret_name=secret_name,
    )
    try:
        secret_arn = await aws_secrets.upsert_secret_string(
            name=secret_name,
            secret_string=json.dumps(secret_body),
        )
    except ClientError as exc:
        err_code = (exc.response.get("Error") or {}).get("Code", "ClientError")
        log.error(
            "gmail_oauth_secret_upsert_failed",
            tenant_id=str(tenant_id),
            agent_id=str(agent_id),
            secret_name=secret_name,
            aws_error_code=err_code,
            exc_info=True,
        )
        detail = f"Failed to store Gmail OAuth token in AWS Secrets Manager ({err_code})."
        if err_code == "AccessDeniedException":
            detail += (
                f" Secret id `{secret_name}` must match the hub task role Secrets Manager resource pattern "
                f"(e.g. `arn:aws:secretsmanager:<region>:<account>:secret:{settings.app_name}/tenant/*`). "
                "Re-apply `infra/hub` Terraform so the hub policy includes that ARN prefix."
            )
        else:
            detail += " Check IAM permissions (CreateSecret, PutSecretValue, DescribeSecret) and KMS decrypt on the key used by those secrets."
        raise HTTPException(status_code=400, detail=detail) from exc
    except Exception as exc:
        log.exception(
            "gmail_oauth_secret_upsert_failed",
            tenant_id=str(tenant_id),
            agent_id=str(agent_id),
            secret_name=secret_name,
        )
        raise HTTPException(
            status_code=400,
            detail="Failed to store Gmail OAuth token (unexpected error while calling AWS).",
        ) from exc
    log.info(
        "gmail_oauth_secret_upsert_ok",
        tenant_id=str(tenant_id),
        agent_id=str(agent_id),
        secret_name=secret_name,
        has_secret_arn=bool(secret_arn),
    )

    topic = (settings.google_pubsub_topic or "").strip()
    if not topic:
        log.warning(
            "gmail_oauth_pubsub_topic_missing",
            tenant_id=str(tenant_id),
            agent_id=str(agent_id),
        )
        raise HTTPException(
            status_code=400,
            detail="GOOGLE_PUBSUB_TOPIC is not configured on this hub; cannot complete Gmail push setup (users.watch).",
        )

    oauth_creds = Credentials(
        token=creds.token,
        refresh_token=creds.refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.google_oauth_client_id,
        client_secret=settings.google_oauth_client_secret,
        scopes=list(creds.scopes or GMAIL_SCOPES),
    )
    gmail = build("gmail", "v1", credentials=oauth_creds, cache_discovery=False)
    log.info(
        "gmail_oauth_watch_begin",
        tenant_id=str(tenant_id),
        agent_id=str(agent_id),
        topic_suffix=topic.split("/")[-1] if "/" in topic else topic[:80],
    )
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
        log.exception(
            "gmail_watch_failed",
            tenant_id=str(tenant_id),
            agent_id=str(agent_id),
            topic_configured=bool(topic),
        )
        raise HTTPException(
            status_code=400,
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
    log.info(
        "gmail_oauth_watch_ok",
        tenant_id=str(tenant_id),
        agent_id=str(agent_id),
        watch_expires_at=watch_expires_at.isoformat(),
        has_history_id=bool(history_id),
    )

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
    try:
        await session.commit()
    except SQLAlchemyError as exc:
        log.exception(
            "gmail_oauth_db_commit_failed",
            tenant_id=str(tenant_id),
            agent_id=str(agent_id),
        )
        await session.rollback()
        raise HTTPException(
            status_code=400,
            detail="Failed to save Gmail integration to the database.",
        ) from exc
    log.info(
        "gmail_oauth_db_commit_ok",
        tenant_id=str(tenant_id),
        agent_id=str(agent_id),
        integration_updated=existing is not None,
    )

    dest = settings.oauth_browser_completion_url({"gmail": "connected=1"})
    log.info(
        "gmail_oauth_complete_redirect",
        tenant_id=str(tenant_id),
        agent_id=str(agent_id),
    )
    return RedirectResponse(dest, status_code=302)
