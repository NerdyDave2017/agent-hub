"""JWT verification for tenant-scoped dashboard routes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from jwt.exceptions import InvalidTokenError
from agent_hub_core.config.settings import get_settings
from agent_hub_core.db.models import User

from apis.dependencies import DbSession
from services import auth_service


@dataclass(frozen=True)
class DashboardPrincipal:
    """Authenticated subject for dashboard APIs — ``tenant_id`` is authoritative (from JWT)."""

    user_id: UUID
    tenant_id: UUID
    email: str


async def get_dashboard_principal(
    tenant_id: UUID,
    request: Request,
    session: DbSession,
) -> DashboardPrincipal:
    """
    Require ``Authorization: Bearer <JWT>`` whose ``tenant_id`` claim matches the path
    ``tenant_id`` (prevents tenant A from calling ``/tenants/{B}/dashboard/...`` with a
    token minted for A — path must align with token).
    """
    settings = get_settings()
    if not (settings.jwt_secret_key or "").strip():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="JWT_SECRET_KEY is not configured",
        )
    auth = (request.headers.get("Authorization") or request.headers.get("authorization") or "").strip()
    if not auth.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing or invalid Authorization header",
        )
    raw = auth.removeprefix("Bearer ").strip()
    if not raw:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="empty bearer token")
    try:
        payload = auth_service.decode_access_token(raw)
    except InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or expired token",
        ) from None
    try:
        token_tid = UUID(str(payload["tenant_id"]))
        user_id = UUID(str(payload["sub"]))
    except (KeyError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="token missing tenant_id or sub",
        ) from exc
    if token_tid != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="token tenant does not match URL",
        )
    user = await session.get(User, user_id)
    if user is None or user.tenant_id != token_tid or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="user not found or inactive")
    email = str(payload.get("email") or user.email)
    return DashboardPrincipal(user_id=user.id, tenant_id=user.tenant_id, email=email)


DashboardAuth = Annotated[DashboardPrincipal, Depends(get_dashboard_principal)]
