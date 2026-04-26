"""Password login / sign-up → JWT (dashboard and future tenant-scoped APIs)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from agent_hub_core.config.settings import get_settings
from agent_hub_core.db.models import Tenant, User
from agent_hub_core.domain.exceptions import TenantSlugConflict
from agent_hub_core.observability.logging import get_logger
from agent_hub_core.schemas.auth import LoginRequest, SignupRequest, SignupResponse, TokenResponse
from agent_hub_core.schemas.tenant import slug_from_workspace_name

from apis.dependencies import DbSession
from services import auth_service, tenants_service

log = get_logger(__name__)

router = APIRouter()

_SIGNUP_SLUG_ATTEMPTS = 12


@router.post("/login", response_model=TokenResponse)
async def login(session: DbSession, body: LoginRequest) -> TokenResponse:
    """Mint a short-lived JWT. Requires ``users.password_hash`` and ``JWT_SECRET_KEY``."""
    settings = get_settings()
    if not (settings.jwt_secret_key or "").strip():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="JWT_SECRET_KEY is not configured",
        )
    slug = body.tenant_slug.strip().lower()
    tenant = await session.scalar(select(Tenant).where(Tenant.slug == slug))
    if tenant is None:
        # Same message as invalid password to avoid tenant enumeration
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid tenant, email, or password",
        )
    email_norm = str(body.email).strip().lower()
    user = await session.scalar(
        select(User).where(User.tenant_id == tenant.id, User.email == email_norm)
    )
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid tenant, email, or password",
        )
    if not user.password_hash or not auth_service.verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid tenant, email, or password",
        )
    try:
        token, ttl = auth_service.create_access_token(
            user_id=user.id,
            tenant_id=user.tenant_id,
            email=user.email,
            settings=settings,
        )
    except RuntimeError as exc:
        log.exception("jwt_mint_failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    return TokenResponse(access_token=token, expires_in=ttl)


@router.post("/signup", response_model=SignupResponse, status_code=status.HTTP_201_CREATED)
async def signup(session: DbSession, body: SignupRequest) -> SignupResponse:
    """Create tenant + owner user from workspace name, work email, name, and password."""
    settings = get_settings()
    if not (settings.jwt_secret_key or "").strip():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="JWT_SECRET_KEY is not configured",
        )
    tenant_display = body.workspace_name.strip()
    email_norm = str(body.email).strip().lower()
    display_name = body.name.strip()
    base_slug = slug_from_workspace_name(tenant_display)

    tenant: Tenant | None = None
    user: User | None = None
    last_slug = base_slug
    for attempt in range(_SIGNUP_SLUG_ATTEMPTS):
        suffix = "" if attempt == 0 else f"-{uuid.uuid4().hex[:8]}"
        candidate = (base_slug + suffix)[:128]
        if len(candidate) < 2:
            candidate = f"ws-{uuid.uuid4().hex[:12]}"
        last_slug = candidate
        try:
            tenant, user = await tenants_service.create_tenant_with_owner(
                session,
                tenant_name=tenant_display,
                slug=candidate,
                owner_email=email_norm,
                owner_display_name=display_name,
                password_hash=auth_service.hash_password(body.password),
            )
            break
        except TenantSlugConflict:
            continue
    if tenant is None or user is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="could not allocate a unique workspace URL; try a different workspace name",
        )
    try:
        token, ttl = auth_service.create_access_token(
            user_id=user.id,
            tenant_id=user.tenant_id,
            email=user.email,
            settings=settings,
        )
    except RuntimeError as exc:
        log.exception("jwt_mint_failed_after_signup", tenant_id=str(tenant.id))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    return SignupResponse(
        access_token=token,
        expires_in=ttl,
        tenant_id=tenant.id,
        tenant_slug=tenant.slug,
        user_id=user.id,
    )
