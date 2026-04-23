"""Optional periodic poll for unread Gmail → schedules triage runs (dedup prevents replays)."""

from __future__ import annotations

from uuid import UUID

from fastapi import FastAPI
from sqlalchemy import select

from agent_hub_core.db.models import Integration
from agent_hub_core.observability.logging import get_logger

from incident_triage.integrations import gmail as gmail_integration
from incident_triage.scheduling import schedule_graph_run
from incident_triage.settings import get_settings

log = get_logger(__name__)


async def is_gmail_hub_push_watch_active(app: FastAPI) -> bool:
    """
    True when the hub has an active Gmail ``users.watch`` for this tenant+agent
    (``integrations.watch_active``). Agent reads shared Postgres — no HTTP from hub required.
    """
    factory = getattr(app.state, "session_factory", None)
    if factory is None:
        return False
    s = get_settings()
    try:
        tid = UUID((s.tenant_id or "").strip())
        aid = UUID((s.agent_id or "").strip())
    except ValueError:
        return False
    async with factory() as session:
        row = await session.scalar(
            select(Integration).where(
                Integration.tenant_id == tid,
                Integration.agent_id == aid,
                Integration.provider == "gmail",
            )
        )
    return bool(row and row.watch_active)


async def poll_unread_and_schedule(app: FastAPI) -> None:
    s = get_settings()
    secret = s.gmail_credentials or {}
    if not gmail_integration.has_usable_credentials(secret):
        return
    user_id = (s.gmail_user_id or "me").strip() or "me"
    try:
        ids = await gmail_integration.list_unread_message_ids_async(
            user_id=user_id,
            secret=secret,
            max_results=8,
        )
    except Exception as exc:
        log.warning("gmail_poll_list_failed", error=str(exc))
        return

    for mid in ids:
        schedule_graph_run(app, mid)
    if ids:
        log.info("gmail_poll_scheduled_runs", count=len(ids))
