"""
FastAPI application entrypoint.

This layer sits *above* ``agent_hub_core.db.engine``: it owns HTTP concerns and process lifespan,
while sessions and transactions stay explicit in route handlers. Pydantic contracts live in
``agent_hub_core.schemas``.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, status
from sqlalchemy import text
from starlette.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession

from agent_hub_core.config.settings import get_settings
from agent_hub_core.db.engine import dispose_engine, get_db
from agent_hub_core.observability.logging import configure_logging
from agent_hub_core.schemas.common import HealthResponse, ReadyResponse

from apis import internal, webhooks_gmail
from apis.router import api_router
from middleware.error_sanitizer import register_error_handlers


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Release the SQLAlchemy engine pool on shutdown so connections do not leak."""
    yield
    await dispose_engine()


def create_app() -> FastAPI:
    configure_logging("hub", attach_to_root=True)
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
            {"name": "dashboard", "description": "Tenant overview and per-agent observability (Postgres)"},
            {"name": "auth", "description": "JWT sign-up and login for dashboard APIs"},
        ],
    )
    # Demo: permissive CORS so any origin (e.g. localhost UI → App Runner API) can call the hub.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # -- Error sanitizer: logs real 5xx / unhandled errors internally,
    # returns generic "Internal Server Error" to clients. ----------------
    register_error_handlers(app)

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

    app.include_router(api_router, prefix=settings.api_v1_prefix)
    app.include_router(internal.router, prefix="/internal", tags=["internal"])
    app.include_router(webhooks_gmail.router, prefix="/webhooks/gmail", tags=["webhooks"])
    return app


app = create_app()
