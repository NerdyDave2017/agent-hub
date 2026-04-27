"""Slack OAuth (Install to Workspace) — store bot token in Secrets Manager and mirror in ``Integration``."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from typing import Any, Literal
from urllib.parse import urlencode, urlparse
from uuid import UUID

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
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


def _slack_pkce_verifier() -> str:
    """RFC 7636 verifier: 43–128 chars from unreserved charset; ``token_urlsafe`` is a practical subset."""
    return secrets.token_urlsafe(32)


def _slack_pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def _slack_oauth_redirect_uri() -> str:
    s = get_settings()
    override = (s.slack_oauth_redirect_uri or "").strip()
    if override:
        return override.rstrip("/")
    base = s.hub_public_url.rstrip("/")
    prefix = (s.api_v1_prefix or "/api/v1").strip() or "/api/v1"
    if not prefix.startswith("/"):
        prefix = "/" + prefix
    prefix = prefix.rstrip("/") or "/api/v1"
    return f"{base}{prefix}/integrations/slack/oauth/callback"


def _origin_from_public_url(url: str) -> str:
    """Origin for postMessage (e.g. http://127.0.0.1:8000)."""
    p = urlparse((url or "").strip())
    if not p.scheme or not p.netloc:
        return "*"
    return f"{p.scheme}://{p.netloc}"


def _slack_post_message_target_origin() -> str:
    s = get_settings()
    custom = (s.slack_oauth_parent_origin or "").strip()
    if custom:
        return custom.rstrip("/")
    return _origin_from_public_url(s.hub_public_url)


def _slack_oauth_finish_html(
    *,
    success: bool,
    detail: str | None,
    fallback_query: str,
) -> str:
    """Notify opener (popup flow) or redirect when opened in a normal tab."""
    s = get_settings()
    hub = s.hub_public_url.rstrip("/")
    dest = f"{hub}{fallback_query}"
    target_origin = _slack_post_message_target_origin()
    payload: dict[str, Any] = {"type": "agent-hub-slack-oauth", "status": "success" if success else "error"}
    if detail:
        payload["detail"] = detail
    payload_js = json.dumps(payload)
    origin_js = json.dumps(target_origin)
    dest_js = json.dumps(dest)
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Slack</title></head><body>
<script>
(function() {{
  var payload = {payload_js};
  var targetOrigin = {origin_js};
  var fallback = {dest_js};
  if (window.opener && !window.opener.closed) {{
    try {{ window.opener.postMessage(payload, targetOrigin); }} catch (e) {{}}
    window.close();
  }} else {{
    window.location.replace(fallback);
  }}
}})();
</script>
<p>You can close this window.</p>
</body></html>"""


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


class SlackOAuthStartJson(BaseModel):
    """Returned when ``response_mode=json`` so the client can ``window.open(authorization_url)``."""

    authorization_url: str


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
    response_mode: Literal["redirect", "json"] = Query(
        "redirect",
        description="`json` returns `{authorization_url}` — open it in a new tab with `window.open(url, '_blank', 'noopener')` "
        "so the dashboard stays on the current page.",
    ),
    return_mode: Literal["redirect", "post_message"] = Query(
        "redirect",
        description="`post_message`: after Slack authorizes, the callback page notifies `window.opener` and closes "
        "(pair with a popup or new tab). Set `SLACK_OAUTH_PARENT_ORIGIN` if the UI origin differs from `HUB_PUBLIC_URL`.",
    ),
):
    """
    Begin Slack OAuth.

    **Redirect URI:** Slack → *OAuth & Permissions* → *Redirect URLs* must include the exact URL the hub sends
    (see ``SLACK_OAUTH_REDIRECT_URI`` or ``HUB_PUBLIC_URL`` + ``API_V1_PREFIX`` + ``/integrations/slack/oauth/callback``).
    ``http://127.0.0.1:8000/...`` and ``http://localhost:8000/...`` are different strings to Slack.

    **PKCE:** If your Slack app has PKCE enabled, set ``SLACK_OAUTH_PKCE=true`` so the hub sends
    ``code_challenge`` / ``code_challenge_method`` on authorize and ``code_verifier`` on ``oauth.v2.access``
    (Slack PKCE token step omits ``client_secret``).
    """
    await tenants_service.require_tenant(session, tenant_id)
    await agents_service.require_agent(session, tenant_id, agent_id)
    s = get_settings()
    if not s.slack_oauth_client_id or not s.slack_oauth_client_secret:
        raise HTTPException(status_code=503, detail="Slack OAuth is not configured on the hub")

    state_payload: dict[str, Any] = {"tenant_id": str(tenant_id), "agent_id": str(agent_id)}
    if return_mode == "post_message":
        state_payload["post_message"] = True
    if s.slack_oauth_pkce:
        verifier = _slack_pkce_verifier()
        state_payload["code_verifier"] = verifier
    state = base64.urlsafe_b64encode(json.dumps(state_payload).encode()).decode()
    redirect_uri = _slack_oauth_redirect_uri()
    params = {
        "client_id": s.slack_oauth_client_id,
        "scope": SLACK_BOT_SCOPES,
        "redirect_uri": redirect_uri,
        "state": state,
    }
    if s.slack_oauth_pkce:
        v = state_payload.get("code_verifier")
        if isinstance(v, str) and v:
            params["code_challenge"] = _slack_pkce_challenge(v)
            params["code_challenge_method"] = "S256"
    auth_url = f"https://slack.com/oauth/v2/authorize?{urlencode(params)}"
    if response_mode == "json":
        return JSONResponse(SlackOAuthStartJson(authorization_url=auth_url).model_dump())
    return RedirectResponse(auth_url)


@router.get("/integrations/slack/oauth/callback")
async def slack_oauth_callback(
    session: DbSession,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
):
    post_message = False
    if state:
        try:
            _preview = json.loads(base64.urlsafe_b64decode(state.encode()).decode())
            post_message = bool(_preview.get("post_message"))
        except Exception:
            post_message = False

    if error:
        if post_message:
            return HTMLResponse(
                _slack_oauth_finish_html(
                    success=False,
                    detail=error,
                    fallback_query="/?slack=error=1",
                ),
                status_code=200,
            )
        raise HTTPException(status_code=400, detail=f"OAuth error: {error}")
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code or state")
    try:
        state_data = json.loads(base64.urlsafe_b64decode(state.encode()).decode())
        tenant_id = UUID(state_data["tenant_id"])
        agent_id = UUID(state_data["agent_id"])
        post_message = bool(state_data.get("post_message"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid state: {exc}") from exc

    await tenants_service.require_tenant(session, tenant_id)
    await agents_service.require_agent(session, tenant_id, agent_id)

    s = get_settings()
    if not s.slack_oauth_client_id or not s.slack_oauth_client_secret:
        raise HTTPException(status_code=503, detail="Slack OAuth is not configured on the hub")

    redirect_uri = _slack_oauth_redirect_uri()
    code_verifier = state_data.get("code_verifier")
    if isinstance(code_verifier, str) and code_verifier.strip():
        # Slack PKCE: token exchange uses verifier, not client_secret (see Slack Using PKCE).
        token_body: dict[str, str] = {
            "client_id": s.slack_oauth_client_id,
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier.strip(),
        }
    else:
        token_body = {
            "client_id": s.slack_oauth_client_id,
            "client_secret": s.slack_oauth_client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
        }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            "https://slack.com/api/oauth.v2.access",
            data=token_body,
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

    if post_message:
        return HTMLResponse(
            _slack_oauth_finish_html(
                success=True,
                detail=None,
                fallback_query="/?slack=connected=1",
            ),
            status_code=200,
        )
    dest = f"{s.hub_public_url.rstrip('/')}/?slack=connected=1"
    return RedirectResponse(dest, status_code=302)
