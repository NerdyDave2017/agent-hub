"""
Shared idempotency guards for SQS at-least-once delivery (``response.md`` Phase 6).

Terminal rows are skipped here; **claim** / **complete** use conditional ``UPDATE`` helpers
in :mod:`agent_hub_core.db.job_transitions` so only one worker wins ``queued`` → ``running``.
"""

from __future__ import annotations

from agent_hub_core.db.models import Job
from agent_hub_core.domain.enums import JobStatus


def is_terminal_job(job: Job) -> bool:
    """If true, handler should no-op and the consumer may ack the message."""
    return job.status in (
        JobStatus.succeeded,
        JobStatus.failed,
        JobStatus.dead_lettered,
    )
