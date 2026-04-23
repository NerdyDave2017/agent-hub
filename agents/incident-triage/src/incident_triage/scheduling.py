"""Fire-and-forget LangGraph triage runs (HTTP routes, poller, webhooks share this)."""

from __future__ import annotations

import asyncio

from fastapi import FastAPI

from agent_hub_core.observability.logging import get_logger

from incident_triage.graph.state import TriageState
from incident_triage.settings import get_settings

log = get_logger(__name__)


def thread_id_for_message(message_id: str) -> str:
    s = get_settings()
    return f"{s.agent_id or 'local'}:{message_id}"


async def invoke_graph_run(app: FastAPI, message_id: str) -> None:
    """Await one full triage graph execution (use from tasks or tests)."""
    s = get_settings()
    tid = thread_id_for_message(message_id)
    callbacks: list = []
    if s.langfuse_public_key.strip() and s.langfuse_secret_key.strip():
        from langfuse.langchain import CallbackHandler

        callbacks.append(
            CallbackHandler(
                public_key=s.langfuse_public_key.strip(),
                secret_key=s.langfuse_secret_key.strip(),
                host=(s.langfuse_host or "https://cloud.langfuse.com").strip(),
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
