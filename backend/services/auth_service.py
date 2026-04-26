"""JWT mint/verify and password hashing for hub dashboard auth."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import bcrypt
import jwt
from jwt.exceptions import InvalidTokenError

from agent_hub_core.config.settings import Settings, get_settings


def hash_password(plain: str) -> str:
    """Return bcrypt ASCII string suitable for ``users.password_hash``."""
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(plain: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), password_hash.encode("ascii"))
    except ValueError:
        return False


def create_access_token(
    *,
    user_id: UUID,
    tenant_id: UUID,
    email: str,
    settings: Settings | None = None,
) -> tuple[str, int]:
    """Return (jwt, expires_in_seconds)."""
    s = settings or get_settings()
    secret = (s.jwt_secret_key or "").strip()
    if not secret:
        raise RuntimeError("JWT_SECRET_KEY is not configured")
    now = datetime.now(timezone.utc)
    exp_m = s.jwt_expire_minutes
    exp = now + timedelta(minutes=exp_m)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "tenant_id": str(tenant_id),
        "email": email,
        "iat": now,
        "exp": exp,
        "iss": s.jwt_issuer,
    }
    token = jwt.encode(payload, secret, algorithm=s.jwt_algorithm)
    return token, int((exp - now).total_seconds())


def decode_access_token(token: str, *, settings: Settings | None = None) -> dict[str, Any]:
    s = settings or get_settings()
    secret = (s.jwt_secret_key or "").strip()
    if not secret:
        raise InvalidTokenError("JWT not configured")
    return jwt.decode(
        token,
        secret,
        algorithms=[s.jwt_algorithm],
        issuer=s.jwt_issuer,
        options={"require": ["exp", "sub"]},
    )
