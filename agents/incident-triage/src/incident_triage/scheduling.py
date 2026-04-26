"""Fire-and-forget LangGraph triage runs (HTTP routes, poller, webhooks share this)."""

from __future__ import annotations

import asyncio
import uuid

from fastapi import FastAPI

from agent_hub_core.observability.logging import get_logger

from incident_triage.graph.state import TriageState
from incident_triage.settings import get_settings

log = get_logger(__name__)


async def await_agent_bootstrap(app: FastAPI, *, timeout_s: float = 600.0) -> None:
    """Wait until background lifespan init finished (graph + DB) or timeout / failure."""
    fin = getattr(app.state, "bootstrap_finished", None)
    if fin is not None:
        try:
            await asyncio.wait_for(fin.wait(), timeout=timeout_s)
        except asyncio.TimeoutError as exc:
            raise RuntimeError("agent bootstrap timed out") from exc
        if not getattr(app.state, "bootstrap_ok", False):
            raise RuntimeError("incident triage agent bootstrap failed (see logs)")
    if getattr(app.state, "graph", None) is None:
        raise RuntimeError("graph not initialized")


def thread_id_for_message(message_id: str) -> str:
    s = get_settings()
    return f"{s.agent_id or 'local'}:{message_id}"


async def invoke_graph_run(app: FastAPI, message_id: str) -> None:
    """Await one full triage graph execution (use from tasks or tests)."""
    await await_agent_bootstrap(app)
    s = get_settings()
    tid = thread_id_for_message(message_id)
    callbacks: list = []
    # Pre-generated id matches Langfuse root trace (``CallbackHandler(trace_context=...)``) so
    # ``tool_call_events.trace_id`` and ``incidents.langfuse_trace_id`` align with the Langfuse UI.
    langfuse_trace_id = ""
    if s.langfuse_public_key.strip() and s.langfuse_secret_key.strip():
        from langfuse.langchain import CallbackHandler

        langfuse_trace_id = uuid.uuid4().hex
        callbacks.append(
            CallbackHandler(
                public_key=s.langfuse_public_key.strip(),
                trace_context={"trace_id": langfuse_trace_id},
            )
        )
    config: dict = {
        "configurable": {"thread_id": tid},
        "metadata": {
            "langfuse_user_id": s.tenant_id or "anonymous",
            "langfuse_session_id": message_id,
        },
        "tags": ["agent_type:incident_triage", f"env:{s.environment}"],
    }
    if callbacks:
        config["callbacks"] = callbacks
    try:
        await app.state.graph.ainvoke(
            TriageState(
                message_id=message_id,
                tenant_id=s.tenant_id or "local",
                agent_id=s.agent_id or "local",
                langfuse_trace_id=langfuse_trace_id,
            ),
            config=config,
        )
    except Exception as exc:
        log.error("graph_run_failed", thread_id=tid, error=str(exc))


def schedule_graph_run(app: FastAPI, message_id: str) -> str:
    """Schedule a triage run on the event loop; returns ``thread_id`` immediately."""
    tid = thread_id_for_message(message_id)
    asyncio.create_task(invoke_graph_run(app, message_id))
    return tid
