"""Best-effort insert of a ``ToolCallEvent`` row on its own DB session (failures ignored)."""

import uuid

from agent_hub_core.db.models import ToolCallEvent

from incident_triage.db.session import get_session


async def write_tool_event(
    *,
    tenant_id: str,
    agent_id: str,
    trace_id: str,
    message_id: str,
    node_name: str,
    tool_name: str | None,
    decision: str | None,
    duration_ms: int,
    succeeded: bool,
    error: str | None,
    prompt_tokens: int | None,
    completion_tokens: int | None,
) -> None:
    try:
        tid = uuid.UUID(tenant_id.strip())
        aid = uuid.UUID(agent_id.strip())
    except ValueError:
        return
    try:
        async with get_session() as session:
            session.add(
                ToolCallEvent(
                    tenant_id=tid,
                    agent_id=aid,
                    trace_id=trace_id or None,
                    message_id=message_id or None,
                    node_name=node_name,
                    tool_name=tool_name,
                    decision=decision,
                    duration_ms=duration_ms,
                    succeeded=succeeded,
                    error=error,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                )
            )
            await session.commit()
    except Exception:
        pass
