"""Persist canonical ``Incident`` row to shared Postgres (hub dashboards read this table)."""

from __future__ import annotations

import uuid

from sqlalchemy.exc import IntegrityError

from agent_hub_core.db.models import Incident
from agent_hub_core.domain.enums import IncidentSeverity, IncidentType

from incident_triage.db.session import get_session
from incident_triage.graph.state import TriageState
from incident_triage.instrumentation.decorator import traced_node


def _enum_to_str(value: IncidentType | IncidentSeverity | None) -> str | None:
    if value is None:
        return None
    return value.value


@traced_node("finalize")
async def run(state: TriageState) -> dict:
    try:
        tenant_uuid = uuid.UUID(state.tenant_id.strip())
    except ValueError:
        return {}

    agent_uuid: uuid.UUID | None = None
    if state.agent_id and state.agent_id.strip():
        try:
            agent_uuid = uuid.UUID(state.agent_id.strip())
        except ValueError:
            agent_uuid = None

    trace = (state.langfuse_trace_id or "").strip() or None
    raw = state.raw_email or {}

    incident = Incident(
        message_id=state.message_id,
        tenant_id=tenant_uuid,
        agent_id=agent_uuid,
        langfuse_trace_id=trace,
        incident_type=_enum_to_str(state.incident_type),
        severity=_enum_to_str(state.severity),
        summary=state.summary,
        confidence=state.confidence,
        actions_taken=list(state.actions_taken),
        slack_sent=state.slack_sent,
        slack_ts=state.slack_ts,
        raw_subject=raw.get("subject"),
        raw_sender=raw.get("sender"),
    )

    try:
        async with get_session() as session:
            session.add(incident)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
    except RuntimeError:
        return {}

    return {}
