"""
Domain string enums — safe to import from **`db.models`**, **`schemas.*`**, and **`services.*`**.

Keep this module free of FastAPI and SQLAlchemy so nothing in the domain graph depends on
transport or persistence frameworks.
"""

from __future__ import annotations

import enum


class AgentStatus(str, enum.Enum):
    """Lifecycle of an agent row in the registry (not the same as ECS task state)."""

    draft = "draft"  # created in DB only; no infra requested yet
    provisioning = "provisioning"  # worker is applying infra / waiting for health
    active = "active"  # deployment considered healthy and routable
    failed = "failed"  # terminal error while provisioning or operating
    deprovisioning = "deprovisioning"  # tear-down in progress
    archived = "archived"  # retained for audit; should not receive new traffic


class AgentType(str, enum.Enum):
    """Which productized agent implementation this row refers to."""

    incident_triage = "incident_triage"  # capstone agent
    meeting_intelligence = "meeting_intelligence"  # example second product; not on critical path for capstone


class DeploymentStatus(str, enum.Enum):
    """State of one deployment record (you may have several rows over time per agent)."""

    pending = "pending"  # row created; ARNs/URL not filled yet or not verified
    live = "live"  # base_url should be trusted for routing
    draining = "draining"  # still up but should not get new sessions (rollout/teardown)
    failed = "failed"  # last reconcile or health check failed


class JobType(str, enum.Enum):
    """Kinds of async work sent to SQS; extend as you add worker handlers."""

    agent_provisioning = "agent_provisioning"
    agent_pause = "agent_pause"
    deployment_scale_to_zero = "deployment_scale_to_zero"
    agent_deprovision = "agent_deprovision"
    agent_destroy = "agent_destroy"
    integration_rotate = "integration_rotate"
    metrics_rollup = "metrics_rollup"
    gmail_history_sync = "gmail_history_sync"
    gmail_process_message = "gmail_process_message"
    gmail_watch_renewal = "gmail_watch_renewal"


class IncidentType(str, enum.Enum):
    outage = "outage"
    security_breach = "security_breach"
    performance = "performance"
    bug_report = "bug_report"
    billing = "billing"
    unknown = "unknown"


class IncidentSeverity(str, enum.Enum):
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"


class JobStatus(str, enum.Enum):
    """Hub-side job state; the worker moves rows through queued → running → terminal states."""

    pending = "pending"  # row created; not yet successfully sent to SQS (or send failed)
    queued = "queued"  # message is on the queue (or assumed so after send)
    running = "running"  # worker has claimed / started processing
    succeeded = "succeeded"  # handler finished without error
    failed = "failed"  # handler error; may retry until DLQ policy applies
    dead_lettered = "dead_lettered"  # moved to DLQ or exceeded retries—manual follow-up
