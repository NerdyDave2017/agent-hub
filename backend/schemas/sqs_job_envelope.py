"""
**Hub → worker** JSON body for `sqs:SendMessage` / `ReceiveMessage`.

Why this module exists (read this once)
---------------------------------------
The **database row** (`jobs`) is the durable source of truth for status, retries, and
audit. The **SQS message** is only a *pointer* so a separate worker process can notice
new work. That means the wire JSON must stay **small**, **non-secret**, and **stable**
enough that the hub (FastAPI) and the worker (later: `worker/`) agree without drifting.

Local vs production
-------------------
Use the **same** `MessageBody` shape everywhere:

* **Local:** boto3 against LocalStack or ElasticMQ (`AWS_ENDPOINT_URL` set; dummy keys).
* **Production:** boto3 against real AWS SQS (`AWS_ENDPOINT_URL` unset; IAM role).

Only endpoints and credentials change — **not** this schema.

Relationship to `JobCreate` / `jobs` table
------------------------------------------
* `JobCreate` is what **HTTP clients** send; `JobQueueEnvelope` is what **internal code**
  puts on the queue **after** a `Job` row is committed.
* `payload` must follow the same “no secrets” rule as `JobCreate` so logs, DLQs, and
  support dumps never store OAuth tokens by accident. Keep this validator aligned with
  `schemas.job.JobCreate.reject_secret_like_keys`.
"""

from __future__ import annotations

import uuid
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator


class JobQueueEnvelope(BaseModel):
    """
    JSON-serializable envelope — **one object** per SQS message body.

    Field guide (what the worker should assume)
    -------------------------------------------
    * **`job_id`** — Primary key of the `jobs` row. The worker should load or update that
      row instead of trusting echoed fields alone (defense in depth).
    * **`correlation_id`** — Same id the hub stored from `X-Correlation-ID` (or generated);
      repeat it in worker logs for cross-service grep.
    * **`tenant_id`**, **`job_type`**, **`agent_id`**, **`payload`** — Convenience copies
      so routing and structured logs work **before** a DB round-trip. They must match the
      row the hub inserted; the worker may re-read Postgres and reconcile if needed.
    """

    model_config = ConfigDict(extra="forbid")

    job_id: uuid.UUID = Field(description="Same as `jobs.id` after commit.")
    tenant_id: uuid.UUID = Field(description="Tenant that owns the job.")
    job_type: str = Field(
        min_length=1,
        max_length=64,
        description="Worker routing key; same string family as `Job.job_type` / `domain.enums.JobType`.",
    )
    correlation_id: str | None = Field(
        default=None,
        max_length=128,
        description="Tracing id shared with hub logs and the `jobs.correlation_id` column.",
    )
    agent_id: uuid.UUID | None = Field(
        default=None,
        description="Optional target agent; mirrors `jobs.agent_id` when set.",
    )
    payload: dict[str, Any] | None = Field(
        default=None,
        description="Non-secret JSON only; never OAuth tokens or raw passwords.",
    )

    @field_validator("payload")
    @classmethod
    def reject_secret_like_keys(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        # Keep rule aligned with `JobCreate.reject_secret_like_keys` — one policy for API + queue.
        if v is None:
            return v
        lowered = {k.lower() for k in v}
        forbidden = {"password", "secret", "token", "authorization", "refresh_token", "access_token"}
        if lowered & forbidden:
            raise ValueError("payload must not contain secret-like keys")
        return v

    @classmethod
    def from_committed_job(
        cls,
        *,
        job_id: uuid.UUID,
        tenant_id: uuid.UUID,
        job_type: str,
        correlation_id: str | None,
        agent_id: uuid.UUID | None,
        payload: dict[str, Any] | None,
    ) -> Self:
        """
        Build the envelope the hub will send **after** the `Job` INSERT commits.

        We take plain values (not the SQLAlchemy `Job` ORM object) so this schema stays
        importable without pulling in `db.models` — the worker can copy the same file later
        or depend on a tiny shared package without dragging the whole hub into its venv.
        """
        return cls(
            job_id=job_id,
            tenant_id=tenant_id,
            job_type=job_type,
            correlation_id=correlation_id,
            agent_id=agent_id,
            payload=payload,
        )
