"""Password login / sign-up / Google Sign-In → JWT (dashboard and future tenant-scoped APIs)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import APIRouter, HTTPException, status
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from pydantic import BaseModel, Field as PydanticField
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from agent_hub_core.config.settings import get_settings, Settings
from agent_hub_core.db.models import Tenant, User
from agent_hub_core.domain.exceptions import TenantSlugConflict
from agent_hub_core.observability.logging import get_logger
from agent_hub_core.schemas.auth import (
    GoogleAuthRequest,
    GoogleAuthResponse,
    LoginRequest,
    LoginResponse,
    SignupRequest,
    SignupResponse,
    TokenResponse,
)
from agent_hub_core.schemas.tenant import slug_from_workspace_name

from apis.dependencies import DbSession
from services import auth_service, tenants_service


log = get_logger(__name__)

router = APIRouter()

_SIGNUP_SLUG_ATTEMPTS = 12


@dataclass(frozen=True)
class _GoogleIdentity:
    """Normalized user identity extracted from a verified Google ID token."""

    sub: str
    email: str
    display_name: str


@router.post("/login", response_model=LoginResponse)
async def login(session: DbSession, body: LoginRequest) -> LoginResponse:
    """Mint a short-lived JWT. Requires ``users.password_hash`` and ``JWT_SECRET_KEY``."""
    settings = get_settings()
    if not (settings.jwt_secret_key or "").strip():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="JWT_SECRET_KEY is not configured",
        )
    email_norm = str(body.email).strip().lower()
    user = await session.scalar(
        select(User)
        .options(selectinload(User.tenant))
        .where(User.email == email_norm)
    )
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email or password is incorrect.",
        )
    if not user.password_hash or not auth_service.verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email or password is incorrect.",
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
    t = user.tenant
    tenant_name = t.name if t is not None else ""
    tenant_slug = t.slug if t is not None else None
    return LoginResponse(
        access_token=token,
        expires_in=ttl,
        tenant_name=tenant_name,
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        tenant_id=user.tenant_id,
        tenant_slug=tenant_slug,
    )


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
        tenant_name=tenant.name,
        user_id=user.id,
    )


# ---------------------------------------------------------------------------
# Google Sign-In  (ID-token verification — NOT the Gmail agent OAuth flow)
# ---------------------------------------------------------------------------

@router.post("/google", response_model=GoogleAuthResponse)
async def google_auth(session: DbSession, body: GoogleAuthRequest) -> GoogleAuthResponse:
    """
    Authenticate or register a user via Google Sign-In.

    The frontend sends the ``id_token`` it receives from the Google
    Sign-In SDK. The backend verifies it against Google's public keys and either
    finds the existing user or creates a new one based on verified token claims.

    **New users** are created *without* a tenant. The response will have
    ``has_workspace=False`` — the frontend must redirect to a "create workspace"
    step before allowing access to the dashboard.
    """
    settings = get_settings()
    if not (settings.jwt_secret_key or "").strip():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="JWT_SECRET_KEY is not configured",
        )
    if not (settings.google_oauth_client_id or "").strip():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="GOOGLE_OAUTH_CLIENT_ID is not configured",
        )

    identity = _extract_google_identity(body.id_token, settings)
    google_sub = identity.sub
    email = identity.email
    display_name = identity.display_name

    # 2. Find or create user --------------------------------------------
    # First try by google_sub (stable across email changes)
    user = await session.scalar(
        select(User).where(User.google_sub == google_sub)
    )

    if user is None:
        # Try matching by email — handles the case where a password user
        # later signs in with Google for the first time.
        user = await session.scalar(
            select(User).where(User.email == email)
        )
        if user is not None:
            # Link Google identity to existing account
            changed = False
            user.google_sub = google_sub
            changed = True
            if user.auth_provider == "password":
                user.auth_provider = "google"
                changed = True
            if not (user.display_name or "").strip():
                user.display_name = display_name
                changed = True
            if changed:
                await session.commit()
                await session.refresh(user)

    if user is None:
        # Brand-new user — create without a tenant
        # We need a tenant_id — create a placeholder-free user.
        # Since User.tenant_id is NOT nullable in the model, we must handle
        # the "no workspace yet" state differently. We'll check if tenant exists.
        # For now, the user must create a workspace — we signal this via has_workspace=False.
        # We need to allow tenant_id to be nullable for Google users without workspaces,
        # OR we create the user without committing until workspace creation.
        #
        # Strategy: Return a temporary JWT (without tenant_id) that only allows
        # workspace creation. The user record is NOT persisted until workspace is created.
        token, ttl = _mint_google_provisional_token(
            google_sub=google_sub,
            email=email,
            name=display_name,
            settings=settings,
        )
        return GoogleAuthResponse(
            access_token=token,
            expires_in=ttl,
            has_workspace=False,
            user_id=uuid.UUID(int=0),  # placeholder — no persisted user yet
            email=email,
            display_name=display_name,
            tenant_id=None,
            tenant_slug=None,
            tenant_name=None,
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Your account is disabled. Contact support if you need help.",
        )

    # 3. Existing user with workspace — mint full JWT --------------------
    tenant = await session.get(Tenant, user.tenant_id)
    try:
        token, ttl = auth_service.create_access_token(
            user_id=user.id,
            tenant_id=user.tenant_id,
            email=user.email,
            settings=settings,
        )
    except RuntimeError as exc:
        log.exception("jwt_mint_failed_google", google_sub=google_sub)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    return GoogleAuthResponse(
        access_token=token,
        expires_in=ttl,
        has_workspace=True,
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        tenant_id=tenant.id if tenant else None,
        tenant_slug=tenant.slug if tenant else None,
        tenant_name=tenant.name if tenant else None,
    )


def _extract_google_identity(id_token: str, settings: Settings) -> _GoogleIdentity:
    """Verify Google ID token and return normalized identity claims."""
    try:
        id_info = google_id_token.verify_oauth2_token(
            id_token,
            google_requests.Request(),
            audience=settings.google_oauth_client_id,
        )
    except ValueError as exc:
        log.warning("google_id_token_invalid", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Google sign-in failed. Please try again.",
        ) from exc

    google_sub: str = (id_info.get("sub") or "").strip()
    email: str = (id_info.get("email") or "").strip().lower()
    display_name: str = (
        (id_info.get("name") or "").strip()
        or (id_info.get("given_name") or "").strip()
        or email.split("@")[0]
    )
    if not google_sub or not email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Google sign-in could not be verified. Please try again.",
        )
    if not id_info.get("email_verified", False):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Your Google email is not verified. Verify it with Google and try again.",
        )
    return _GoogleIdentity(sub=google_sub, email=email, display_name=display_name)


def _mint_google_provisional_token(
    *,
    google_sub: str,
    email: str,
    name: str,
    settings,
) -> tuple[str, int]:
    """
    Mint a short-lived JWT for a Google user who has no workspace yet.

    The token carries ``google_sub`` instead of ``user_id``/``tenant_id`` and
    is only valid for the ``POST /auth/google/complete-signup`` endpoint.
    """
    import jwt as pyjwt
    from datetime import datetime, timedelta, timezone

    secret = (settings.jwt_secret_key or "").strip()
    if not secret:
        raise RuntimeError("JWT_SECRET_KEY is not configured")
    now = datetime.now(timezone.utc)
    exp = now + timedelta(minutes=30)  # short window for workspace creation
    payload = {
        "sub": google_sub,
        "email": email,
        "name": name,
        "scope": "google_signup",  # restricted scope — only workspace creation
        "iat": now,
        "exp": exp,
        "iss": settings.jwt_issuer,
    }
    token = pyjwt.encode(payload, secret, algorithm=settings.jwt_algorithm)
    return token, int((exp - now).total_seconds())


class _GoogleCompleteSignupRequest(BaseModel):
    """Body for ``POST /auth/google/complete-signup``."""

    provisional_token: str = PydanticField(
        ..., min_length=1, description="The provisional JWT from POST /auth/google (has_workspace=False)"
    )
    workspace_name: str = PydanticField(
        ..., min_length=1, max_length=255, description="Organization / workspace title"
    )


@router.post(
    "/google/complete-signup",
    response_model=SignupResponse,
    status_code=status.HTTP_201_CREATED,
)
async def google_complete_signup(
    session: DbSession,
    body: _GoogleCompleteSignupRequest,
) -> SignupResponse:
    """
    Complete Google Sign-In registration by creating a workspace.

    Called after ``POST /auth/google`` returns ``has_workspace=False``. The frontend
    must collect a workspace name and send it here with the provisional JWT.
    """
    import jwt as pyjwt
    from jwt.exceptions import InvalidTokenError

    settings = get_settings()
    secret = (settings.jwt_secret_key or "").strip()
    if not secret:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="JWT_SECRET_KEY not configured")

    # Verify the provisional token
    try:
        payload = pyjwt.decode(
            body.provisional_token,
            secret,
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "sub", "email"]},
        )
    except InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Your Google sign-up session expired. Please continue with Google again.",
        ) from None

    if payload.get("scope") != "google_signup":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your sign-up session is invalid. Please continue with Google again.",
        )

    google_sub = payload["sub"]
    email = payload["email"]
    name = payload.get("name", email.split("@")[0])

    # Guard: if this google_sub already has an account, reject
    existing = await session.scalar(select(User).where(User.google_sub == google_sub))
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this Google login already exists. Please sign in.",
        )

    # Create tenant + user
    tenant_display = body.workspace_name.strip()
    base_slug = slug_from_workspace_name(tenant_display)

    tenant: Tenant | None = None
    user: User | None = None
    for attempt in range(_SIGNUP_SLUG_ATTEMPTS):
        suffix = "" if attempt == 0 else f"-{uuid.uuid4().hex[:8]}"
        candidate = (base_slug + suffix)[:128]
        if len(candidate) < 2:
            candidate = f"ws-{uuid.uuid4().hex[:12]}"
        try:
            tenant, user = await tenants_service.create_tenant_with_owner(
                session,
                tenant_name=tenant_display,
                slug=candidate,
                owner_email=email,
                owner_display_name=name,
                password_hash=None,
                auth_provider="google",
                google_sub=google_sub,
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
        log.exception("jwt_mint_failed_google_signup", google_sub=google_sub)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    return SignupResponse(
        access_token=token,
        expires_in=ttl,
        tenant_id=tenant.id,
        tenant_slug=tenant.slug,
        tenant_name=tenant.name,
        user_id=user.id,
    )

