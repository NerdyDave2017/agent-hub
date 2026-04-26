"""LangGraph state schema: email + classification fields, hub context, Slack flags, LLM message channel."""

from __future__ import annotations

from typing import Annotated

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field

from agent_hub_core.domain.enums import IncidentSeverity, IncidentType


class TriageState(BaseModel):
    """Graph state; ``langfuse_trace_id`` is set at run start when Langfuse callbacks are active."""

    message_id: str
    tenant_id: str
    agent_id: str
    langfuse_trace_id: str = Field(default="", description="Langfuse root trace id when tracing is on.")
    raw_email: dict = Field(default_factory=dict)
    incident_type: IncidentType | None = None
    severity: IncidentSeverity | None = None
    summary: str | None = None
    confidence: float = 0.0
    tenant_context: dict = Field(default_factory=dict)
    prior_incidents: list[dict] = Field(default_factory=list)
    actions_taken: list[str] = Field(default_factory=list)
    slack_ts: str | None = None
    slack_sent: bool = False
    duplicate_message: bool = False
    messages: Annotated[list[AnyMessage], add_messages] = Field(default_factory=list)
    error: str | None = None
