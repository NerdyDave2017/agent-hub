"""Claim ``message_id`` in ``processed_emails`` or set ``duplicate_message`` to short-circuit the graph."""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from incident_triage.db.models import ProcessedEmail
from incident_triage.db.session import get_session
from incident_triage.graph.state import TriageState
from incident_triage.instrumentation.decorator import traced_node


@traced_node("dedup")
async def run(state: TriageState) -> dict:
    try:
        async with get_session() as session:
            existing = await session.scalar(
                select(ProcessedEmail).where(ProcessedEmail.message_id == state.message_id)
            )
            if existing:
                return {"duplicate_message": True}
            session.add(
                ProcessedEmail(message_id=state.message_id, tenant_id=state.tenant_id)
            )
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                return {"duplicate_message": True}
            return {}
    except RuntimeError:
        return {}
