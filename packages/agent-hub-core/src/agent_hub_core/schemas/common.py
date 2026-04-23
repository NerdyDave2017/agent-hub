"""Shared response shapes and pagination wrappers used across routers."""

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class HealthResponse(BaseModel):
    """Process is up; does not prove Postgres is reachable."""

    status: str = Field(examples=["ok"])
    service: str = Field(default="hub", description="Logical service name for logs and dashboards")


class ReadyResponse(BaseModel):
    """Process can reach Postgres (used by orchestrators and compose healthchecks)."""

    status: str
    database: bool = Field(description="True when SELECT 1 succeeds through the pool")


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: dict[str, Any] | list[Any] | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail


class PaginatedMeta(BaseModel):
    total: int
    skip: int = Field(ge=0)
    limit: int = Field(ge=1, le=200)


class PaginatedResponse(BaseModel, Generic[T]):
    items: list[T]
    meta: PaginatedMeta
