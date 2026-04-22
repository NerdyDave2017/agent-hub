"""
FastAPI application entrypoint.

This layer sits *above* `core/database.py`: it owns HTTP concerns and process lifespan,
while sessions and transactions stay explicit in route handlers. Pydantic contracts for
HTTP bodies and responses live under `schemas/`.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from apis.router import api_router
from core.database import dispose_engine, get_db
from core.settings import get_settings
from domain.exceptions import AgentNotFound, JobNotFound, TenantNotFound, TenantSlugConflict
from schemas.common import HealthResponse, ReadyResponse


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Release the SQLAlchemy engine pool on shutdown so connections do not leak."""
    yield
    await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        lifespan=lifespan,
        openapi_tags=[
            {"name": "system", "description": "Liveness and readiness"},
            {"name": "tenants", "description": "Tenant registry"},
            {"name": "agents", "description": "Agent types and lifecycle metadata"},
            {"name": "jobs", "description": "Async jobs — Postgres row then optional SQS publish when SQS_QUEUE_URL is set"},
        ],
    )

    @app.get("/health", response_model=HealthResponse, tags=["system"])
    async def health() -> HealthResponse:
        return HealthResponse(status="ok", service="hub")

    @app.get("/ready", response_model=ReadyResponse, tags=["system"])
    async def ready(session: Annotated[AsyncSession, Depends(get_db)]) -> ReadyResponse:
        try:
            await session.execute(text("SELECT 1"))
        except Exception as exc:  # noqa: BLE001 — surface any driver/pool error as 503
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"message": "database unreachable", "error": str(exc)},
            ) from exc
        return ReadyResponse(status="ok", database=True)

    @app.exception_handler(TenantNotFound)
    async def _tenant_not_found(_request: Request, _exc: TenantNotFound) -> JSONResponse:
        return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content={"detail": "tenant not found"})

    @app.exception_handler(AgentNotFound)
    async def _agent_not_found(_request: Request, _exc: AgentNotFound) -> JSONResponse:
        return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content={"detail": "agent not found"})

    @app.exception_handler(JobNotFound)
    async def _job_not_found(_request: Request, _exc: JobNotFound) -> JSONResponse:
        return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content={"detail": "job not found"})

    @app.exception_handler(TenantSlugConflict)
    async def _tenant_slug_conflict(_request: Request, _exc: TenantSlugConflict) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": {"code": "tenant_conflict", "message": "slug already exists"}},
        )

    app.include_router(api_router, prefix=settings.api_v1_prefix)
    return app


app = create_app()
