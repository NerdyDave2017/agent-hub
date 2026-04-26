"""Request and response models for tenant APIs."""

import re
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_NON_SLUG_CHARS = re.compile(r"[^a-z0-9]+")


def slug_from_workspace_name(name: str) -> str:
    """
    Derive a valid tenant ``slug`` from arbitrary workspace / org text.

    Falls back to ``workspace`` when the label yields nothing URL-safe. Caller should
    append a short suffix on unique-constraint conflicts.
    """
    raw = (name or "").strip().lower()
    s = _NON_SLUG_CHARS.sub("-", raw).strip("-")
    while "--" in s:
        s = s.replace("--", "-")
    if not s:
        s = "workspace"
    if not _SLUG_RE.match(s):
        s = "workspace"
    if len(s) < 2:
        s = (s + "x")[:2]
    return s[:128]


class TenantCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    slug: str = Field(min_length=2, max_length=128, description="URL-safe tenant identifier")

    @field_validator("slug")
    @classmethod
    def slug_format(cls, v: str) -> str:
        s = v.strip().lower()
        if not _SLUG_RE.match(s):
            raise ValueError("slug must be lowercase letters, digits, and hyphens only")
        return s


class TenantUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)


class TenantRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    created_at: datetime
    updated_at: datetime
