"""Aggregate tool + incident stats into ``metric_snapshots`` (hourly windows)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from agent_hub_core.config.settings import get_settings
from agent_hub_core.db.job_transitions import claim_job_for_worker, complete_job_success, fail_job_while_running
from agent_hub_core.db.models import Agent, Job, MetricSnapshot
from agent_hub_core.domain.enums import JobStatus
from agent_hub_core.observability.logging import get_logger

from worker.handlers._idempotency import is_terminal_job
from worker.handlers.base import AbstractJobHandler
from worker.handlers.langfuse_public_metrics import fetch_langfuse_observation_totals

log = get_logger(__name__)

_RUNNING = "metrics_rollup_running"
_DONE = "metrics_rollup_complete"


class MetricsRollupHandler(AbstractJobHandler):
    async def execute(self, job: Job, session: AsyncSession) -> None:
        if is_terminal_job(job):
            log.info("metrics_rollup_skip_terminal", job_id=str(job.id))
            return

        if job.agent_id is None:
            await fail_job_while_running(session, job.id, message="metrics_rollup requires agent_id")
            return

        claimed = await claim_job_for_worker(session, job.id, running_step=_RUNNING)
        await session.refresh(job)
        if is_terminal_job(job):
            return
        if not claimed and job.status != JobStatus.running:
            log.warning(
                "metrics_rollup_skip_unexpected_status",
                job_id=str(job.id),
                job_status=job.status.value,
            )
            return

        payload = job.payload or {}
        now = datetime.now(timezone.utc)
        window_end = now.replace(minute=0, second=0, microsecond=0)
        window_start = window_end - timedelta(hours=1)
        if isinstance(payload.get("window_start"), str):
            ws = str(payload["window_start"]).replace("Z", "+00:00")
            window_start = datetime.fromisoformat(ws)
        if isinstance(payload.get("window_end"), str):
            we = str(payload["window_end"]).replace("Z", "+00:00")
            window_end = datetime.fromisoformat(we)

        agent = await session.get(Agent, job.agent_id)
        if agent is None or agent.tenant_id != job.tenant_id:
            await fail_job_while_running(session, job.id, message="agent not found for tenant")
            return

        agent_type_str = agent.agent_type.value

        irow = (
            await session.execute(
                text(
                    """
            SELECT
                COUNT(*)::bigint AS incident_count,
                COUNT(*) FILTER (WHERE confidence IS NOT NULL AND confidence < 0.6)::bigint
                    AS low_confidence_count,
                AVG(confidence)::float AS avg_confidence
            FROM incidents
            WHERE tenant_id = CAST(:tenant_id AS uuid)
              AND agent_id = CAST(:agent_id AS uuid)
              AND created_at >= :start
              AND created_at < :end
            """
                ),
                {
                    "tenant_id": str(job.tenant_id),
                    "agent_id": str(job.agent_id),
                    "start": window_start,
                    "end": window_end,
                },
            )
        ).mappings().first()

        trow = (
            await session.execute(
                text(
                    """
            SELECT
                COALESCE(SUM(prompt_tokens), 0)::bigint AS prompt_tokens,
                COALESCE(SUM(completion_tokens), 0)::bigint AS completion_tokens,
                COALESCE(SUM(cost_usd), 0)::double precision AS cost_usd,
                AVG(duration_ms) FILTER (WHERE node_name = 'classify')::float AS avg_latency_ms,
                COUNT(*) FILTER (WHERE succeeded IS FALSE OR succeeded IS NULL)::bigint AS error_count
            FROM tool_call_events
            WHERE tenant_id = CAST(:tenant_id AS uuid)
              AND agent_id = CAST(:agent_id AS uuid)
              AND created_at >= :start
              AND created_at < :end
            """
                ),
                {
                    "tenant_id": str(job.tenant_id),
                    "agent_id": str(job.agent_id),
                    "start": window_start,
                    "end": window_end,
                },
            )
        ).mappings().first()

        i = dict(irow or {})
        t = dict(trow or {})
        incident_count = int(i.get("incident_count") or 0)
        low_conf = int(i.get("low_confidence_count") or 0)
        avg_conf = float(i.get("avg_confidence") or 0.0)
        prompt_tokens = int(t.get("prompt_tokens") or 0)
        completion_tokens = int(t.get("completion_tokens") or 0)
        cost_usd = float(t.get("cost_usd") or 0.0)
        avg_latency = float(t.get("avg_latency_ms") or 0.0)
        error_count = int(t.get("error_count") or 0)

        metrics: dict[str, Any] = {
            "incident_count": incident_count,
            "error_count": error_count,
            "low_confidence_count": low_conf,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "estimated_cost_usd": cost_usd,
            "avg_latency_ms": avg_latency,
            "avg_confidence": avg_conf,
            "low_confidence_rate": low_conf / max(incident_count, 1),
        }

        lf = await fetch_langfuse_observation_totals(
            get_settings(),
            tenant_id=job.tenant_id,
            window_start=window_start,
            window_end=window_end,
        )
        if lf is not None:
            metrics["langfuse"] = lf

        existing = await session.scalar(
            select(MetricSnapshot).where(
                MetricSnapshot.tenant_id == job.tenant_id,
                MetricSnapshot.agent_type == agent_type_str,
                MetricSnapshot.window_start == window_start,
            )
        )
        if existing:
            existing.metrics = metrics
            existing.window_end = window_end
        else:
            session.add(
                MetricSnapshot(
                    tenant_id=job.tenant_id,
                    agent_type=agent_type_str,
                    window_start=window_start,
                    window_end=window_end,
                    metrics=metrics,
                )
            )
        await session.commit()

        await complete_job_success(session, job.id, final_step=_DONE)
        await session.refresh(job)
        log.info(
            "metrics_rollup_complete",
            job_id=str(job.id),
            tenant_id=str(job.tenant_id),
            agent_id=str(job.agent_id),
            window_start=window_start.isoformat(),
        )
