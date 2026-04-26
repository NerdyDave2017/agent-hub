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


class AgentOverviewResponse(BaseModel):
    """Per-agent rollup for the observability dashboard (Postgres)."""

    agent_id: str
    name: str
    agent_type: str
    status: str
    window_days: int
    total_tokens: int
    total_cost_usd: float
    incidents_count: int


class AgentTokenDailyRow(BaseModel):
    day: str
    tokens: int = 0
    cost_usd: float = 0.0


class MetricSnapshotRow(BaseModel):
    """Hourly (or custom) KPI bag from ``metric_snapshots`` — scoped by agent *type* rollups."""

    agent_type: str
    window_start: str
    window_end: str | None
    metrics: dict[str, Any] = Field(default_factory=dict)
