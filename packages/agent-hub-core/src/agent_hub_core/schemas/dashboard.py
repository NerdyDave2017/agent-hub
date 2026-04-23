"""Pydantic models for tenant / agent observability APIs."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class DailySpendRow(BaseModel):
    day: str
    agent_id: str | None = None
    tokens: int = 0
    cost_usd: float = 0.0


class AgentSummaryRow(BaseModel):
    agent_id: str
    name: str
    agent_type: str
    status: str
    tokens_7d: int = 0
    cost_7d: float = 0.0


class TenantOverviewResponse(BaseModel):
    window_days: int
    total_agents: int
    active_agents: int
    total_tokens: int
    total_cost_usd: float
    daily_spend: list[DailySpendRow] = Field(default_factory=list)
    agents: list[AgentSummaryRow] = Field(default_factory=list)


class ToolCallEventRow(BaseModel):
    id: str
    node_name: str | None
    tool_name: str | None
    decision: str | None
    duration_ms: int | None
    succeeded: bool | None
    error: str | None
    created_at: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cost_usd: float | None = None


class SafeIncidentRow(BaseModel):
    id: str
    incident_type: str | None
    severity: str | None
    summary: str | None
    confidence: float | None
    actions_taken: Any
    slack_sent: bool
    created_at: str


class IncidentDetailResponse(SafeIncidentRow):
    langfuse_trace_url: str | None = None
