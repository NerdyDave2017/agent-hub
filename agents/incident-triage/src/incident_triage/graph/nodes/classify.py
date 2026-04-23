"""LLM classification via OpenAI structured output; stub when ``OPENAI_API_KEY`` is unset."""

from __future__ import annotations

from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from agent_hub_core.domain.enums import IncidentSeverity, IncidentType
from agent_hub_core.observability.logging import get_logger

from incident_triage.graph.state import TriageState
from incident_triage.instrumentation.decorator import traced_node
from incident_triage.settings import get_settings

log = get_logger(__name__)

_TYPE_VALUES = {e.value for e in IncidentType}
_SEVERITY_VALUES = {e.value for e in IncidentSeverity}


class _ClassificationSchema(BaseModel):
    """Structured LLM output — string enums for robust parsing."""

    incident_type: str = Field(
        description="One of: outage, security_breach, performance, bug_report, billing, unknown"
    )
    severity: str = Field(description="One of: critical, high, medium, low")
    summary: str = Field(description="One-line summary for operators", max_length=500)
    confidence: float = Field(ge=0.0, le=1.0, description="0-1 confidence in this classification")


def _coerce_incident_type(raw: str) -> IncidentType:
    v = (raw or "").strip().lower()
    if v in _TYPE_VALUES:
        return IncidentType(v)
    return IncidentType.unknown


def _coerce_severity(raw: str) -> IncidentSeverity:
    v = (raw or "").strip().lower()
    if v in _SEVERITY_VALUES:
        return IncidentSeverity(v)
    return IncidentSeverity.low


def _stub_classify() -> dict:
    return {
        "incident_type": IncidentType.unknown,
        "severity": IncidentSeverity.low,
        "summary": "stub - For testing purposes",
        "confidence": 0.4,
    }


@traced_node("classify")
async def run(state: TriageState) -> dict:
    settings = get_settings()
    key = (settings.openai_api_key or "").strip()
    if not key:
        log.info("classify_stub_no_api_key", message_id=state.message_id)
        return _stub_classify()

    raw = state.raw_email or {}
    subject = str(raw.get("subject") or "").strip() or "(no subject)"
    sender = str(raw.get("sender") or "").strip() or "(unknown sender)"
    body = str(raw.get("body") or "").strip()
    body = body[:6000]

    model = ChatOpenAI(
        model=settings.classify_model,
        api_key=key,
        max_tokens=512,
        temperature=0,
    ).with_structured_output(_ClassificationSchema)

    prompt = (
        "You triage incoming support or operational email.\n"
        "Classify incident_type and severity conservatively. "
        "Use severity low and incident_type unknown if the message is unclear.\n\n"
        f"Subject: {subject}\n"
        f"From: {sender}\n\n"
        f"Body:\n{body}"
    )

    try:
        out = await model.ainvoke([HumanMessage(content=prompt)])
    except Exception as exc:
        log.warning(
            "classify_llm_failed",
            message_id=state.message_id,
            error=str(exc),
        )
        return _stub_classify()

    if not isinstance(out, _ClassificationSchema):
        return _stub_classify()
    return {
        "incident_type": _coerce_incident_type(out.incident_type),
        "severity": _coerce_severity(out.severity),
        "summary": (out.summary or "")[:512] or None,
        "confidence": float(out.confidence),
    }
