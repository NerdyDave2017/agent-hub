"""Compile-time checks for Phase 6 conditional ``UPDATE`` statements (no database)."""

from __future__ import annotations

import uuid

from sqlalchemy.dialects import postgresql

from agent_hub_core.db.models import Job
from agent_hub_core.domain.enums import JobStatus


def test_claim_update_filters_claimable_statuses() -> None:
    from sqlalchemy import update

    stmt = (
        update(Job)
        .where(
            Job.id == uuid.uuid4(),
            Job.status.in_((JobStatus.pending, JobStatus.queued)),
        )
        .values(
            status=JobStatus.running,
            job_step="step",
            error_message=None,
        )
    )
    compiled = str(stmt.compile(dialect=postgresql.dialect())).lower()
    assert "update jobs" in compiled
    assert "jobs.status" in compiled
    assert " in (" in compiled or "in(__" in compiled  # ``IN`` for claimable statuses


def test_complete_success_requires_running() -> None:
    from sqlalchemy import update

    stmt = (
        update(Job)
        .where(
            Job.id == uuid.uuid4(),
            Job.status == JobStatus.running,
        )
        .values(
            status=JobStatus.succeeded,
            job_step="done",
            error_message=None,
        )
    )
    compiled = str(stmt.compile(dialect=postgresql.dialect())).lower()
    assert "update jobs" in compiled
    assert "jobs.status" in compiled
    assert "where" in compiled
