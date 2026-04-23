"""Load Gmail message into ``raw_email`` when OAuth credentials are configured; else stub."""

from __future__ import annotations

from googleapiclient.errors import HttpError

from agent_hub_core.observability.logging import get_logger

from incident_triage.graph.state import TriageState
from incident_triage.instrumentation.decorator import traced_node
from incident_triage.integrations import gmail as gmail_integration
from incident_triage.settings import get_settings

log = get_logger(__name__)

_STUB = {
    "subject": "(stub)",
    "body": "",
    "sender": "",
}


@traced_node("fetch")
async def run(state: TriageState) -> dict:
    settings = get_settings()
    secret = settings.gmail_credentials or {}
    if not gmail_integration.has_usable_credentials(secret):
        log.info("fetch_stub_no_gmail_credentials", message_id=state.message_id)
        return {"raw_email": {**_STUB}}

    user_id = (settings.gmail_user_id or "me").strip() or "me"
    try:
        raw = await gmail_integration.fetch_message_async(
            user_id=user_id,
            message_id=state.message_id,
            secret=secret,
        )
        return {"raw_email": raw}
    except HttpError as exc:
        log.warning(
            "gmail_fetch_http_error",
            message_id=state.message_id,
            status=exc.resp.status if exc.resp else None,
            detail=gmail_integration.http_error_reason(exc),
        )
    except Exception as exc:
        log.warning("gmail_fetch_failed", message_id=state.message_id, error=str(exc))

    return {"raw_email": {**_STUB, "subject": "(fetch failed)", "gmail_message_id": state.message_id}}
