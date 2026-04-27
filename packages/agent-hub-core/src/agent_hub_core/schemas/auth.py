"""Auth request/response models for hub JWT login."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, EmailStr, Field, model_validator


class LoginRequest(BaseModel):
    """Exchange email + password for a JWT."""

    email: EmailStr
    password: str = Field(..., min_length=1, max_length=256)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    has_workspace: bool = Field(
        default=True,
        description="False when the user exists but has no tenant yet (Google first-login). "
        "Frontend must redirect to workspace creation before proceeding to dashboard.",
    )


class UserResponse(TokenResponse):
    """JWT plus user and tenant profile (shared by login, signup, Google, and GET /auth/{user_id})."""

    user_id: uuid.UUID
    email: str
    display_name: str | None = None
    tenant_id: uuid.UUID | None = None
    tenant_slug: str | None = None
    tenant_name: str | None = None


class LoginResponse(UserResponse):
    """Password login."""


class GoogleAuthRequest(BaseModel):
    """Frontend sends the Google ID token JWT from Google Sign-In SDK."""

    id_token: str = Field(..., min_length=1, description="Google ID token (credential) from frontend")


class GoogleAuthResponse(UserResponse):
    """Returned from POST /auth/google. Includes tenant info when a workspace exists."""


class SignupRequest(BaseModel):
    """Create a workspace (tenant) and owner account; returns JWT like login."""

    name: str = Field(..., min_length=1, max_length=255, description="Display name for the user")
    email: EmailStr = Field(..., description="Work email; becomes login email for this workspace")
    workspace_name: str = Field(..., min_length=1, max_length=255, description="Organization / workspace title")
    password: str = Field(..., min_length=8, max_length=256)
    password_confirm: str = Field(..., min_length=8, max_length=256)

    @model_validator(mode="after")
    def passwords_match(self) -> SignupRequest:
        if self.password != self.password_confirm:
            raise ValueError("passwords do not match")
        return self


class SignupResponse(UserResponse):
    """New workspace; tenant fields are always set."""

    tenant_id: uuid.UUID
    tenant_slug: str
    tenant_name: str
