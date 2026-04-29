"""
Atomic ``Job`` row transitions for SQS at-least-once processing (see docs/plan.md).

Use **conditional** ``UPDATE`` statements so only one worker wins the ``queued``/``pending``
→ ``running`` claim and so terminal transitions do not race with redeliveries.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from agent_hub_core.db.models import Job
from agent_hub_core.domain.enums import JobStatus

_CLAIMABLE = (JobStatus.pending, JobStatus.queued)


async def claim_job_for_worker(
    session: AsyncSession,
    job_id: UUID,
    *,
    running_step: str,
) -> bool:
    """
    Move ``pending`` / ``queued`` → ``running`` if still claimable.

    Returns ``True`` when this execution won the row (should run side effects).
    Returns ``False`` when another worker claimed first or the row is not claimable;
    callers should ``refresh`` the ``Job`` and treat ``succeeded`` / ``failed`` / ``dead_lettered``
    as idempotent no-ops, and ``running`` as safe to continue **only** when side effects are
    themselves idempotent (see handler docs).
    """
    result = await session.execute(
        update(Job)
        .where(
            Job.id == job_id,
            Job.status.in_(_CLAIMABLE),
        )
        .values(
            status=JobStatus.running,
            job_step=running_step,
            error_message=None,
        )
    )
    await session.commit()
    return (result.rowcount or 0) > 0


async def complete_job_success(
    session: AsyncSession,
    job_id: UUID,
    *,
    final_step: str,
) -> bool:
    """Set ``running`` → ``succeeded`` when still ``running``. Idempotent if already terminal."""
    result = await session.execute(
        update(Job)
        .where(
            Job.id == job_id,
            Job.status == JobStatus.running,
        )
        .values(
            status=JobStatus.succeeded,
            job_step=final_step,
            error_message=None,
        )
    )
    await session.commit()
    return (result.rowcount or 0) > 0


async def fail_job_while_running(
    session: AsyncSession,
    job_id: UUID,
    *,
    message: str | None,
    failed_step: str | None = None,
) -> bool:
    """Set ``running`` → ``failed`` when still ``running``."""
    values: dict[str, Any] = {
        "status": JobStatus.failed,
        "error_message": message,
    }
    if failed_step is not None:
        values["job_step"] = failed_step

    result = await session.execute(
        update(Job)
        .where(
            Job.id == job_id,
            Job.status == JobStatus.running,
        )
        .values(**values),
    )
    await session.commit()
    return (result.rowcount or 0) > 0
