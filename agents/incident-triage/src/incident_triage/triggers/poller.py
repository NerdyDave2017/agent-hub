"""Optional periodic poll for unread Gmail → schedules triage runs (dedup prevents replays)."""

from __future__ import annotations

from fastapi import FastAPI

from agent_hub_core.observability.logging import get_logger

from incident_triage.integrations import gmail as gmail_integration
from incident_triage.scheduling import schedule_graph_run
from incident_triage.settings import get_settings

log = get_logger(__name__)


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
