"""Remove Gmail ``UNREAD`` after a full triage pass (no-op without credentials or when disabled)."""

from __future__ import annotations

from googleapiclient.errors import HttpError

from agent_hub_core.observability.logging import get_logger

from incident_triage.graph.state import TriageState
from incident_triage.instrumentation.decorator import traced_node
from incident_triage.integrations import gmail as gmail_integration
from incident_triage.settings import get_settings

log = get_logger(__name__)


@traced_node("mark_read")
async def run(state: TriageState) -> dict:
    settings = get_settings()
    if not settings.gmail_mark_read:
        return {"_tool_name": "gmail_modify_unread", "_decision": "skipped_disabled"}

    secret = settings.gmail_credentials or {}
    if not gmail_integration.has_usable_credentials(secret):
        return {"_tool_name": "gmail_modify_unread", "_decision": "skipped_no_credentials"}

    user_id = (settings.gmail_user_id or "me").strip() or "me"
    try:
        await gmail_integration.mark_as_read_async(
            user_id=user_id,
            message_id=state.message_id,
            secret=secret,
        )
    except HttpError as exc:
        log.warning(
            "gmail_mark_read_http_error",
            message_id=state.message_id,
            status=exc.resp.status if exc.resp else None,
            detail=gmail_integration.http_error_reason(exc),
        )
        return {
            "_tool_name": "gmail_modify_unread",
            "_decision": "http_error",
        }
    except Exception as exc:
        log.warning("gmail_mark_read_failed", message_id=state.message_id, error=str(exc))
        return {"_tool_name": "gmail_modify_unread", "_decision": "error"}

    return {
        "actions_taken": [*state.actions_taken, "gmail:marked_read"],
        "_tool_name": "gmail_modify_unread",
        "_decision": "remove_unread",
    }
