"""Post a formatted incident alert to the ops Slack channel."""

from __future__ import annotations

from agent_hub_core.observability.logging import get_logger

from incident_triage.graph.state import TriageState
from incident_triage.instrumentation.decorator import traced_node
from incident_triage.integrations import slack as slack_integration
from incident_triage.settings import get_settings

log = get_logger(__name__)


def _one_line(s: str, max_len: int = 480) -> str:
    t = " ".join(s.split())
    t = t.replace("`", "'")
    return t[:max_len] if t else "(empty)"


def _mrkdwn_alert(state: TriageState) -> str:
    itype = state.incident_type.value if state.incident_type else "unknown"
    sev = state.severity.value if state.severity else "unknown"
    summary = _one_line(state.summary or "(no summary)")
    subj = _one_line(str((state.raw_email or {}).get("subject") or "") or "(no subject)", max_len=200)
    return (
        f"*Incident triage — {itype} / {sev}*\n"
        f"*Summary:* {summary}\n"
        f"*Email subject:* {subj}\n"
        f"*message_id:* `{state.message_id}` · *agent_id:* `{state.agent_id}` · "
        f"*confidence:* `{state.confidence:.2f}`"
    )


@traced_node("slack")
async def run(state: TriageState) -> dict:
    settings = get_settings()
    token = (settings.slack_bot_token or "").strip()
    channel = (settings.slack_ops_channel or "").strip() or "#ops-alerts"

    if not token:
        log.info("slack_skip_no_token", message_id=state.message_id)
        return {
            "slack_ts": None,
            "slack_sent": False,
            "actions_taken": [*state.actions_taken, "slack:skipped_no_token"],
            "_tool_name": "slack_chat_postMessage",
            "_decision": "skip_no_credentials",
        }

    text = _mrkdwn_alert(state)
    try:
        ts = await slack_integration.post_message(token=token, channel=channel, text=text)
    except Exception as exc:
        log.warning(
            "slack_post_failed",
            message_id=state.message_id,
            error=str(exc),
        )
        return {
            "slack_ts": None,
            "slack_sent": False,
            "actions_taken": [*state.actions_taken, f"slack:error:{exc!s}"],
            "_tool_name": "slack_chat_postMessage",
            "_decision": "post_failed",
        }

    return {
        "slack_ts": ts,
        "slack_sent": True,
        "actions_taken": [*state.actions_taken, "slack:posted"],
        "_tool_name": "slack_chat_postMessage",
        "_decision": "notify_ops",
    }
