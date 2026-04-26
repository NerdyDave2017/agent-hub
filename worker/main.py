"""
Worker process entry — orchestration only.

Poll loop stays here; job semantics live under ``worker.handlers`` (registry + per-type classes).
"""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress

from pydantic import ValidationError
from sqlalchemy import text

from agent_hub_core.config.settings import Settings, get_settings
from agent_hub_core.db.engine import dispose_engine, get_engine, get_session_factory
from agent_hub_core.db.models import Job
from agent_hub_core.messaging.envelope import JobQueueEnvelope
from agent_hub_core.observability.logging import configure_logging, get_logger

from worker.handlers.registry import handler_for_job_type
from worker.messaging.metrics_schedule import enqueue_metrics_rollup_for_previous_hour
from worker.sqs_transport.sqs_receive import delete_message, receive_long_poll

log = get_logger(__name__)


async def _gmail_renewal_scheduler_loop() -> None:
    """Periodically enqueue ``gmail_watch_renewal`` for integrations whose watch expires soon."""
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import select

    from agent_hub_core.db.models import Integration
    from agent_hub_core.domain.enums import JobType

    from worker.messaging.enqueue import enqueue_job_default_settings

    while True:
        s = get_settings()
        interval = s.gmail_renewal_scheduler_seconds
        if interval <= 0:
            return
        try:
            factory = get_session_factory(s)
            horizon = datetime.now(timezone.utc) + timedelta(days=2)
            async with factory() as session:
                stmt = select(Integration).where(
                    Integration.provider == "gmail",
                    Integration.watch_active.is_(True),
                    Integration.watch_expires_at.is_not(None),
                    Integration.watch_expires_at < horizon,
                )
                rows = list((await session.scalars(stmt)).all())
                day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                for integ in rows:
                    if integ.agent_id is None:
                        continue
                    await enqueue_job_default_settings(
                        session,
                        tenant_id=integ.tenant_id,
                        agent_id=integ.agent_id,
                        job_type=JobType.gmail_watch_renewal.value,
                        correlation_id=f"gmail-renew-scheduler:{integ.id}:{day}",
                        idempotency_key=f"gmail_renew_sched:{integ.id}:{day}",
                        payload={"integration_id": str(integ.id)},
                    )
        except Exception:
            log.exception("gmail_renewal_scheduler_tick_failed")

        s = get_settings()
        interval = s.gmail_renewal_scheduler_seconds
        if interval <= 0:
            return
        await asyncio.sleep(interval)


async def _metrics_rollup_scheduler_loop() -> None:
    """Periodically enqueue ``metrics_rollup`` for each active agent (previous UTC hour bucket)."""
    while True:
        s = get_settings()
        interval = s.metrics_rollup_scheduler_seconds
        if interval <= 0:
            return
        try:
            factory = get_session_factory(s)
            async with factory() as session:
                n = await enqueue_metrics_rollup_for_previous_hour(session)
                log.info("metrics_rollup_scheduler_tick", jobs_enqueued=n)
        except Exception:
            log.exception("metrics_rollup_scheduler_tick_failed")

        s = get_settings()
        interval = s.metrics_rollup_scheduler_seconds
        if interval <= 0:
            return
        await asyncio.sleep(interval)


async def _verify_database_connectivity() -> None:
    """Prove the worker can reach the same Postgres the hub uses (compose-friendly)."""
    engine = get_engine()
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        log.exception("database_connectivity_failed", phase="db_ping_failed")
        raise
    log.info("database_reachable", phase="db_ping_ok")


async def _handle_raw_message(settings: Settings, raw: dict[str, object]) -> None:
    """
    Parse envelope → load ``Job`` → dispatch handler → **DeleteMessage** only on success.

    Malformed JSON, missing rows, or unknown ``job_type`` leave the message on the queue
    (visibility timeout / DLQ policy).
    """
    body = raw.get("Body")
    receipt = raw.get("ReceiptHandle")
    message_id = raw.get("MessageId")
    if not isinstance(body, str) or not isinstance(receipt, str):
        log.warning(
            "sqs_malformed",
            sqs_message_id=message_id,
            phase="sqs_malformed",
        )
        return

    # EventBridge (or ops) → SQS tick: fan out metrics_rollup jobs without a JobQueueEnvelope.
    try:
        tick = json.loads(body)
    except json.JSONDecodeError:
        tick = None
    if isinstance(tick, dict) and tick.get("kind") == "scheduled_metrics_rollup":
        try:
            factory = get_session_factory(settings)
            async with factory() as session:
                n = await enqueue_metrics_rollup_for_previous_hour(session)
            await asyncio.to_thread(delete_message, settings=settings, receipt_handle=receipt)
            log.info(
                "scheduled_metrics_rollup_tick_ok",
                sqs_message_id=message_id,
                jobs_enqueued=n,
                source=tick.get("source"),
            )
        except Exception:
            log.exception(
                "scheduled_metrics_rollup_tick_failed",
                sqs_message_id=message_id,
            )
        return

    try:
        envelope = JobQueueEnvelope.model_validate_json(body)
    except ValidationError as exc:
        log.error(
            "envelope_invalid",
            sqs_message_id=message_id,
            error=str(exc),
            phase="envelope_invalid",
        )
        return

    cid = envelope.correlation_id
    log.info(
        "envelope_received",
        correlation_id=cid,
        job_id=str(envelope.job_id),
        tenant_id=str(envelope.tenant_id),
        agent_id=str(envelope.agent_id) if envelope.agent_id else None,
        job_type=envelope.job_type,
        sqs_message_id=message_id,
        phase="envelope_received",
    )

    try:
        factory = get_session_factory(settings)
        async with factory() as session:
            job = await session.get(Job, envelope.job_id)
            if job is None:
                log.error(
                    "job_row_missing",
                    job_id=str(envelope.job_id),
                    tenant_id=str(envelope.tenant_id),
                    sqs_message_id=message_id,
                )
                return
            if job.tenant_id != envelope.tenant_id:
                log.error(
                    "job_tenant_mismatch",
                    job_id=str(job.id),
                    db_tenant_id=str(job.tenant_id),
                    envelope_tenant_id=str(envelope.tenant_id),
                )
                return

            handler_cls = handler_for_job_type(job.job_type)
            if handler_cls is None:
                log.error("unknown_job_type", job_type=job.job_type, job_id=str(job.id))
                return

            await handler_cls().execute(job, session)

        await asyncio.to_thread(delete_message, settings=settings, receipt_handle=receipt)
        log.info(
            "sqs_deleted",
            correlation_id=cid,
            job_id=str(envelope.job_id),
            tenant_id=str(envelope.tenant_id),
            sqs_message_id=message_id,
            phase="sqs_deleted",
        )
    except Exception:
        log.exception(
            "handler_failed_message_will_retry",
            correlation_id=cid,
            job_id=str(envelope.job_id),
            tenant_id=str(envelope.tenant_id),
            sqs_message_id=message_id,
        )


async def run() -> None:
    configure_logging("worker")
    settings = get_settings()
    if not settings.sqs_queue_url:
        raise SystemExit(
            "SQS_QUEUE_URL is required for the worker. "
            "See README (LocalStack) and agent_hub_core.config.settings."
        )

    log.info("worker_starting", phase="startup")
    renew_task: asyncio.Task[None] | None = None
    if settings.gmail_renewal_scheduler_seconds > 0:
        renew_task = asyncio.create_task(_gmail_renewal_scheduler_loop())
    rollup_task: asyncio.Task[None] | None = None
    if settings.metrics_rollup_scheduler_seconds > 0:
        rollup_task = asyncio.create_task(_metrics_rollup_scheduler_loop())
    try:
        await _verify_database_connectivity()
        while True:
            messages = await asyncio.to_thread(
                receive_long_poll,
                settings=settings,
            )
            for raw in messages:
                if isinstance(raw, dict):
                    await _handle_raw_message(settings, raw)
    finally:
        if renew_task is not None:
            renew_task.cancel()
            with suppress(asyncio.CancelledError):
                await renew_task
        if rollup_task is not None:
            rollup_task.cancel()
            with suppress(asyncio.CancelledError):
                await rollup_task
        await dispose_engine()
        log.info("worker_stopped", phase="shutdown")
