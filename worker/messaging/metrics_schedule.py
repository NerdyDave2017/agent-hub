"""Enqueue ``metrics_rollup`` jobs for the previous UTC hour (in-process scheduler + EventBridge tick)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_hub_core.db.models import Agent
from agent_hub_core.domain.enums import AgentStatus, JobType

from worker.messaging.enqueue import enqueue_job_default_settings


async def enqueue_metrics_rollup_for_previous_hour(session: AsyncSession) -> int:
    """
    For each **active** agent, enqueue one ``metrics_rollup`` job for ``[window_start, window_end)`` = previous full UTC hour.

    Uses the same idempotency keys as the worker's background scheduler so duplicate ticks are harmless.
    """
    now = datetime.now(timezone.utc)
    window_end = now.replace(minute=0, second=0, microsecond=0)
    window_start = window_end - timedelta(hours=1)
    ws_iso = window_start.isoformat()
    we_iso = window_end.isoformat()

    agents = list(
        (await session.scalars(select(Agent).where(Agent.status == AgentStatus.active))).all()
    )
    count = 0
    for ag in agents:
        await enqueue_job_default_settings(
            session,
            tenant_id=ag.tenant_id,
            agent_id=ag.id,
            job_type=JobType.metrics_rollup.value,
            correlation_id=f"metrics-scheduler:{ag.id}:{ws_iso}",
            idempotency_key=f"metrics_sched:{ag.id}:{ws_iso}",
            payload={"window_start": ws_iso, "window_end": we_iso},
        )
        count += 1
    return count
