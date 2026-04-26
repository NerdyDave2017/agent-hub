"""HTTP entrypoint: loads settings + secrets, optional SQLAlchemy + LangGraph Postgres checkpointer,
compiles the triage graph, and exposes health, meta, async graph runs, and trace inspection."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import uuid
from contextlib import asynccontextmanager, suppress

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import FastAPI, HTTPException, Request
from starlette.responses import JSONResponse, Response
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from pydantic import BaseModel, Field
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from agent_hub_core.observability.logging import configure_logging, get_logger

from incident_triage.db.session import (
    configure_database,
    dispose_database,
    get_session_factory,
    init_agent_schema,
    psycopg_conninfo,
)
from incident_triage.graph.builder import build_graph
from incident_triage.integrations import gmail as gmail_integration
from incident_triage.scheduling import schedule_graph_run
from incident_triage.settings import get_settings


class RunRequest(BaseModel):
    """Body for ``POST …/runs`` — Gmail (or manual) message id to triage."""

    message_id: str = Field(..., min_length=1)


class AgentPublicMeta(BaseModel):
    """Operator-facing config snapshot (no secrets)."""

    app_name: str
    api_v1_prefix: str
    host: str
    port: int
    tenant_id: str
    agent_id: str
    environment: str
    hub_base_url: str
    slack_ops_channel: str
    langfuse_host: str
    secrets_manager_hydration: bool
    database_configured: bool
    gmail_configured: bool
    gmail_poll_interval_seconds: int
    gmail_push_active: bool


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Start HTTP quickly for App Runner health checks, then finish DB/graph init in the background.

    Starlette does not accept connections until this context **yields**. If Postgres or Secrets
    Manager is slow or wedged, blocking here caused ``/health`` on port 8080 to never respond and
    App Runner deployments to fail after long health-check timeouts.
    """
    log = get_logger(__name__)
    app.state.session_factory = None
    app.state.checkpoint_pool = None
    app.state.graph = None
    app.state.bootstrap_finished = asyncio.Event()
    app.state.bootstrap_ok = False
    app.state.gmail_poll_task = None

    async def _bootstrap() -> None:
        settings = get_settings()
        poll_task: asyncio.Task[None] | None = None
        try:
            try:
                # Sync boto3; run off the event loop so /health stays responsive during SM reads
                # (App Runner treats missed responses as failed health checks).
                await asyncio.to_thread(settings.resolve_secrets)
            except (ClientError, BotoCoreError) as exc:
                log.error(
                    "secrets_resolution_failed",
                    phase="startup",
                    error=str(exc),
                    tenant_id=settings.tenant_id or None,
                    agent_id=settings.agent_id or None,
                )
                raise

            if settings.langfuse_public_key.strip():
                os.environ.setdefault("LANGFUSE_PUBLIC_KEY", settings.langfuse_public_key.strip())
            if settings.langfuse_secret_key.strip():
                os.environ.setdefault("LANGFUSE_SECRET_KEY", settings.langfuse_secret_key.strip())
            if settings.langfuse_host.strip():
                os.environ.setdefault("LANGFUSE_BASE_URL", settings.langfuse_host.strip().rstrip("/"))

            if settings.database_url.strip():
                configure_database(settings.database_url)
                await init_agent_schema()
                app.state.session_factory = get_session_factory()
                pool = AsyncConnectionPool(
                    conninfo=psycopg_conninfo(settings.database_url),
                    open=False,
                    kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
                    min_size=1,
                    max_size=10,
                )
                await pool.open()
                checkpointer = AsyncPostgresSaver(conn=pool)
                await checkpointer.setup()
                app.state.graph = build_graph(checkpointer)
                app.state.checkpoint_pool = pool
            else:
                app.state.graph = build_graph(None)

            log.info(
                "agent_ready",
                phase="startup",
                service="incident_triage",
                app_name=settings.app_name,
                tenant_id=settings.tenant_id or None,
                agent_id=settings.agent_id or None,
                environment=settings.environment,
                secrets_manager_hydration=settings.secrets_manager_hydration,
                database_configured=app.state.session_factory is not None,
                gmail_poll_interval_seconds=settings.gmail_poll_interval_seconds,
            )

            if settings.gmail_poll_interval_seconds > 0:

                async def _gmail_poll_loop() -> None:
                    from incident_triage.triggers import poller as poller_mod

                    while True:
                        interval = get_settings().gmail_poll_interval_seconds
                        if interval <= 0:
                            return
                        await asyncio.sleep(interval)
                        if await poller_mod.is_gmail_hub_push_watch_active(app):
                            log.debug("gmail_poll_skipped_hub_watch_active")
                        else:
                            await poller_mod.poll_unread_and_schedule(app)

                poll_task = asyncio.create_task(_gmail_poll_loop())
                app.state.gmail_poll_task = poll_task

            app.state.bootstrap_ok = True
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("agent_bootstrap_failed", phase="startup")
            app.state.bootstrap_ok = False
        finally:
            app.state.bootstrap_finished.set()

    boot = asyncio.create_task(_bootstrap())
    app.state._bootstrap_task = boot

    yield

    boot.cancel()
    with suppress(asyncio.CancelledError):
        await boot

    poll = getattr(app.state, "gmail_poll_task", None)
    if poll is not None:
        poll.cancel()
        with suppress(asyncio.CancelledError):
            await poll
        app.state.gmail_poll_task = None

    if app.state.checkpoint_pool is not None:
        await app.state.checkpoint_pool.close()
        app.state.checkpoint_pool = None
    await dispose_database()
    log.info("agent_shutdown", phase="shutdown", service="incident_triage")


def create_app() -> FastAPI:
    """Construct the ASGI app (routes registered here; ``app`` at module bottom is the default instance)."""
    configure_logging("incident_triage", attach_to_root=True)
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        lifespan=lifespan,
        openapi_tags=[{"name": "system"}, {"name": "agent"}],
    )

    @app.middleware("http")
    async def correlation_middleware(request: Request, call_next):
        """Propagate ``X-Correlation-ID`` for cross-service log joins (hub → agent)."""
        cid = request.headers.get("x-correlation-id") or str(uuid.uuid4())
        request.state.correlation_id = cid
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = cid
        return response

    @app.get("/health", tags=["system"])
    async def health(request: Request) -> dict[str, str] | JSONResponse:
        s = get_settings()
        if not request.app.state.bootstrap_finished.is_set():
            return {
                "status": "starting",
                "service": "incident_triage",
                "tenant_id": s.tenant_id,
                "agent_id": s.agent_id,
                "environment": s.environment,
                "database": "unknown",
            }
        if not request.app.state.bootstrap_ok:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "unhealthy",
                    "service": "incident_triage",
                    "detail": "bootstrap_failed",
                },
            )
        return {
            "status": "ok",
            "service": "incident_triage",
            "tenant_id": s.tenant_id,
            "agent_id": s.agent_id,
            "environment": s.environment,
            "database": "configured" if app.state.session_factory else "disabled",
        }

    @app.get(
        f"{settings.api_v1_prefix}/meta",
        response_model=AgentPublicMeta,
        tags=["agent"],
    )
    async def agent_meta(request: Request) -> AgentPublicMeta:
        from incident_triage.triggers import poller as poller_mod

        s = get_settings()
        push = await poller_mod.is_gmail_hub_push_watch_active(request.app)
        return AgentPublicMeta(
            app_name=s.app_name,
            api_v1_prefix=s.api_v1_prefix,
            host=s.host,
            port=s.port,
            tenant_id=s.tenant_id,
            agent_id=s.agent_id,
            environment=s.environment,
            hub_base_url=s.hub_base_url,
            slack_ops_channel=s.slack_ops_channel,
            langfuse_host=s.langfuse_host,
            secrets_manager_hydration=s.secrets_manager_hydration,
            database_configured=app.state.session_factory is not None,
            gmail_configured=gmail_integration.has_usable_credentials(s.gmail_credentials),
            gmail_poll_interval_seconds=s.gmail_poll_interval_seconds,
            gmail_push_active=push,
        )

    @app.post(f"{settings.api_v1_prefix}/runs", tags=["agent"])
    async def trigger_run(payload: RunRequest, request: Request) -> dict[str, str]:
        tid = schedule_graph_run(request.app, payload.message_id)
        return {"status": "accepted", "thread_id": tid}

    class GmailMessageWebhook(BaseModel):
        """Bridge or operator pushes a Gmail API ``message_id`` to triage."""

        message_id: str = Field(..., min_length=1)

    @app.post(f"{settings.api_v1_prefix}/webhooks/gmail/message", tags=["agent"])
    async def gmail_message_webhook(
        body: GmailMessageWebhook,
        request: Request,
    ) -> dict[str, str]:
        tid = schedule_graph_run(request.app, body.message_id)
        return {"status": "accepted", "thread_id": tid}

    @app.post(f"{settings.api_v1_prefix}/webhooks/gmail/pubsub", tags=["agent"])
    async def gmail_pubsub_push(request: Request) -> Response:
        """Google Pub/Sub push: decodes payload; schedules a run only if ``messageId`` is present."""
        log = get_logger(__name__)
        try:
            envelope = await request.json()
            inner = envelope.get("message") or {}
            raw_b64 = inner.get("data") or ""
            if not raw_b64:
                return Response(status_code=204)
            decoded = base64.b64decode(raw_b64).decode("utf-8")
            payload = json.loads(decoded)
            mid = payload.get("messageId") or payload.get("message_id")
            if isinstance(mid, str) and mid.strip():
                schedule_graph_run(request.app, mid.strip())
            else:
                log.info(
                    "gmail_pubsub_no_message_id",
                    payload_keys=sorted(payload.keys()) if isinstance(payload, dict) else None,
                )
        except Exception as exc:
            log.warning("gmail_pubsub_parse_failed", error=str(exc))
        return Response(status_code=204)

    @app.get(f"{settings.api_v1_prefix}/traces/{{thread_id}}", tags=["agent"])
    async def trace_state(thread_id: str, request: Request) -> dict:
        if not request.app.state.bootstrap_finished.is_set():
            raise HTTPException(status_code=503, detail="agent starting")
        if not request.app.state.bootstrap_ok:
            raise HTTPException(status_code=503, detail="agent bootstrap failed")
        if request.app.state.checkpoint_pool is None:
            raise HTTPException(status_code=503, detail="checkpointing requires DATABASE_URL")
        snap = await request.app.state.graph.aget_state(
            config={"configurable": {"thread_id": thread_id}},
        )
        return {"thread_id": thread_id, "values": snap.values, "next": snap.next}

    return app


app = create_app()
