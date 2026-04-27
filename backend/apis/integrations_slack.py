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
from botocore.exceptions import ClientError
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
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
    """Safe for logs: never log the authorization code itself."""
    if not code:
        return {"oauth_code_present": False}
    return {"oauth_code_present": True, "oauth_code_length": len(code)}

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
    fe = (s.frontend_url or "").strip()
    if fe:
        return _origin_from_public_url(fe)
    return _origin_from_public_url(s.hub_public_url)


def _slack_oauth_finish_html(
    *,
    success: bool,
    detail: str | None,
    query: dict[str, str],
) -> str:
    """Notify opener (popup flow) or redirect when opened in a normal tab."""
    s = get_settings()
    dest = s.oauth_browser_completion_url(query)
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
        "(pair with a popup or new tab). `postMessage` target origin defaults to `FRONTEND_URL` when set, else "
        "`HUB_PUBLIC_URL`. Set `SLACK_OAUTH_PARENT_ORIGIN` to override.",
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
    log.info(
        "slack_oauth_start_entered",
        tenant_id=str(tenant_id),
        agent_id=str(agent_id),
        response_mode=response_mode,
        return_mode=return_mode,
    )
    await tenants_service.require_tenant(session, tenant_id)
    await agents_service.require_agent(session, tenant_id, agent_id)
    log.info(
        "slack_oauth_start_tenant_agent_ok",
        tenant_id=str(tenant_id),
        agent_id=str(agent_id),
    )
    s = get_settings()
    if not s.slack_oauth_client_id or not s.slack_oauth_client_secret:
        log.warning("slack_oauth_start_not_configured", tenant_id=str(tenant_id), agent_id=str(agent_id))
        raise HTTPException(
            status_code=400,
            detail="Slack OAuth is not configured on this hub (set SLACK_OAUTH_CLIENT_ID and SLACK_OAUTH_CLIENT_SECRET).",
        )

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
    log.info(
        "slack_oauth_start_redirecting",
        tenant_id=str(tenant_id),
        agent_id=str(agent_id),
        redirect_uri=redirect_uri,
        slack_oauth_pkce=s.slack_oauth_pkce,
        response_mode=response_mode,
        return_mode=return_mode,
        state_length=len(state),
    )
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
    log.info(
        "slack_oauth_callback_entered",
        **_oauth_code_metrics(code),
        state_present=bool(state),
        state_length=len(state) if state else 0,
        slack_error=error,
    )
    post_message = False
    if state:
        try:
            _preview = json.loads(base64.urlsafe_b64decode(state.encode()).decode())
            post_message = bool(_preview.get("post_message"))
        except Exception:
            post_message = False

    if error:
        log.warning(
            "slack_oauth_callback_slack_denied",
            slack_error=error,
            post_message=post_message,
        )
        if post_message:
            return HTMLResponse(
                _slack_oauth_finish_html(
                    success=False,
                    detail=error,
                    query={"slack": "error=1"},
                ),
                status_code=200,
            )
        raise HTTPException(status_code=400, detail=f"OAuth error: {error}")
    if not code or not state:
        log.warning("slack_oauth_callback_missing_code_or_state", has_code=bool(code), has_state=bool(state))
        raise HTTPException(status_code=400, detail="Missing code or state")
    try:
        state_data = json.loads(base64.urlsafe_b64decode(state.encode()).decode())
        tenant_id = UUID(state_data["tenant_id"])
        agent_id = UUID(state_data["agent_id"])
        post_message = bool(state_data.get("post_message"))
    except Exception as exc:
        log.warning("slack_oauth_callback_invalid_state", error=str(exc))
        raise HTTPException(status_code=400, detail=f"Invalid state: {exc}") from exc

    log.info(
        "slack_oauth_callback_state_ok",
        tenant_id=str(tenant_id),
        agent_id=str(agent_id),
        post_message=post_message,
        pkce_in_state=bool(
            isinstance(state_data.get("code_verifier"), str) and str(state_data.get("code_verifier", "")).strip()
        ),
    )
    await tenants_service.require_tenant(session, tenant_id)
    await agents_service.require_agent(session, tenant_id, agent_id)

    s = get_settings()
    if not s.slack_oauth_client_id or not s.slack_oauth_client_secret:
        log.warning(
            "slack_oauth_callback_not_configured",
            tenant_id=str(tenant_id),
            agent_id=str(agent_id),
        )
        raise HTTPException(
            status_code=400,
            detail="Slack OAuth is not configured on this hub (set SLACK_OAUTH_CLIENT_ID and SLACK_OAUTH_CLIENT_SECRET).",
        )

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
    token_mode = "pkce" if "code_verifier" in token_body else "client_secret"
    log.info(
        "slack_oauth_token_exchange_begin",
        tenant_id=str(tenant_id),
        agent_id=str(agent_id),
        redirect_uri=redirect_uri,
        token_mode=token_mode,
    )
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://slack.com/api/oauth.v2.access",
                data=token_body,
            )
    except httpx.RequestError as exc:
        log.exception(
            "slack_oauth_token_http_error",
            tenant_id=str(tenant_id),
            agent_id=str(agent_id),
        )
        raise HTTPException(
            status_code=400,
            detail="Could not reach Slack to exchange the OAuth code. Check outbound network access and try again.",
        ) from exc
    log.info(
        "slack_oauth_token_http_response",
        tenant_id=str(tenant_id),
        agent_id=str(agent_id),
        http_status=resp.status_code,
        content_type=resp.headers.get("content-type"),
    )
    try:
        data: dict[str, Any] = resp.json()
    except Exception as exc:
        log.exception(
            "slack_oauth_token_response_not_json",
            tenant_id=str(tenant_id),
            agent_id=str(agent_id),
            http_status=resp.status_code,
        )
        raise HTTPException(
            status_code=400,
            detail=f"Slack token response was not valid JSON (HTTP {resp.status_code}).",
        ) from exc

    if not data.get("ok"):
        err = data.get("error", "unknown")
        log.warning(
            "slack_oauth_token_slack_error",
            tenant_id=str(tenant_id),
            agent_id=str(agent_id),
            slack_api_error=err,
            http_status=resp.status_code,
        )
        raise HTTPException(status_code=400, detail=f"Slack oauth.v2.access failed: {err}")

    bot_token = data.get("access_token")
    if not bot_token or not isinstance(bot_token, str):
        log.error(
            "slack_oauth_token_missing_access_token",
            tenant_id=str(tenant_id),
            agent_id=str(agent_id),
            response_keys=sorted(str(k) for k in data.keys()),
            token_type=data.get("token_type"),
        )
        hint = (
            " Slack returned a user-only OAuth payload (no bot token). "
            "Ensure the Slack app is installed to the workspace with bot scopes (e.g. chat:write)."
            if data.get("authed_user")
            else ""
        )
        raise HTTPException(
            status_code=400,
            detail="Slack did not return a bot access_token after install." + hint,
        )

    log.info(
        "slack_oauth_token_exchange_ok",
        tenant_id=str(tenant_id),
        agent_id=str(agent_id),
        token_type=data.get("token_type"),
        app_id=data.get("app_id"),
    )
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
    log.info(
        "slack_oauth_secret_upsert_begin",
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
            "slack_oauth_secret_upsert_failed",
            tenant_id=str(tenant_id),
            agent_id=str(agent_id),
            secret_name=secret_name,
            aws_error_code=err_code,
            exc_info=True,
        )
        detail = f"Failed to store Slack token in AWS Secrets Manager ({err_code})."
        if err_code == "AccessDeniedException":
            detail += (
                f" Secret id `{secret_name}` must match the hub task role pattern "
                f"`arn:aws:secretsmanager:<region>:<account>:secret:{s.app_name}/tenant/*`. "
                "Re-apply `infra/hub` Terraform so the hub policy includes that ARN prefix."
            )
        else:
            detail += " Check IAM for secretsmanager:CreateSecret, PutSecretValue, DescribeSecret and KMS decrypt."
        raise HTTPException(status_code=400, detail=detail) from exc
    except Exception as exc:
        log.exception(
            "slack_oauth_secret_upsert_failed",
            tenant_id=str(tenant_id),
            agent_id=str(agent_id),
            secret_name=secret_name,
        )
        raise HTTPException(
            status_code=400,
            detail="Failed to store Slack token (unexpected error while calling AWS).",
        ) from exc
    log.info(
        "slack_oauth_secret_upsert_ok",
        tenant_id=str(tenant_id),
        agent_id=str(agent_id),
        secret_name=secret_name,
        has_secret_arn=bool(secret_arn),
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
    try:
        await session.commit()
    except SQLAlchemyError as exc:
        log.exception(
            "slack_oauth_db_commit_failed",
            tenant_id=str(tenant_id),
            agent_id=str(agent_id),
        )
        await session.rollback()
        raise HTTPException(
            status_code=400,
            detail="Failed to save Slack integration to the database.",
        ) from exc
    log.info(
        "slack_oauth_db_commit_ok",
        tenant_id=str(tenant_id),
        agent_id=str(agent_id),
        team_id=str(team_id) if team_id else None,
        integration_updated=existing is not None,
    )

    if post_message:
        log.info(
            "slack_oauth_complete_post_message",
            tenant_id=str(tenant_id),
            agent_id=str(agent_id),
        )
        return HTMLResponse(
            _slack_oauth_finish_html(
                success=True,
                detail=None,
                query={"slack": "connected=1"},
            ),
            status_code=200,
        )
    dest = s.oauth_browser_completion_url({"slack": "connected=1"})
    log.info(
        "slack_oauth_complete_redirect",
        tenant_id=str(tenant_id),
        agent_id=str(agent_id),
        redirect_to=dest,
    )
    return RedirectResponse(dest, status_code=302)
