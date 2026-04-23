# Cursor Agent Instructions: Monorepo Restructure for agent-hub

You are implementing a monorepo restructure for a Python project called `agent-hub`. Follow these instructions precisely. Do not deviate from the target structure. Implement all phases in order.

---

## Context

This project has two deployables (`backend/` FastAPI hub and `worker/` SQS consumer) that currently share code via fragile `PYTHONPATH` hacks. The goal is to extract a proper shared kernel package (`agent-hub-core`) installed as an editable local dependency into both, while scaffolding a clean handler registry pattern in the worker.

**Stack:** Python 3.11+, SQLAlchemy 2 async, Pydantic v2, FastAPI (hub only), boto3 (hub + worker), Alembic, uv workspaces.

---

## Target Project Structure

```text
agent-hub/
├── pyproject.toml                        # uv workspace root
├── docker-compose.yml
├── README.md
├── Agent.md
├── docs/
│   └── plan.md
├── scripts/
│   └── localstack-init/
│
├── packages/
│   └── agent-hub-core/                   # shared kernel — pip-installable
│       ├── pyproject.toml
│       └── src/
│           └── agent_hub_core/           # importable name (underscores, not hyphens)
│               ├── __init__.py
│               ├── config/
│               │   ├── __init__.py
│               │   └── settings.py       # pydantic-settings BaseSettings
│               ├── db/
│               │   ├── __init__.py
│               │   ├── engine.py         # async engine + get_session_factory() (+ get_db() for FastAPI)
│               │   ├── base.py           # SQLAlchemy DeclarativeBase
│               │   └── models/
│               │       ├── __init__.py   # imports ALL models (required for Alembic autogenerate)
│               │       ├── tenant.py
│               │       ├── user.py
│               │       ├── agent.py
│               │       ├── deployment.py
│               │       ├── job.py
│               │       └── integration.py
│               ├── migrations/           # Alembic — single history, lives here only
│               │   ├── env.py
│               │   ├── script.py.mako
│               │   └── versions/
│               ├── alembic.ini           # script_location points to migrations/ above
│               ├── domain/
│               │   ├── __init__.py
│               │   ├── enums.py          # JobType, JobStatus, JobStep, AgentStatus, …
│               │   ├── exceptions.py     # DomainError, JobConflictError, …
│               │   └── rules.py          # payload validation invariants
│               ├── messaging/
│               │   ├── __init__.py
│               │   ├── envelope.py       # JobQueueEnvelope Pydantic v2 model
│               │   └── sqs_client.py     # send_job_message() boto3 wrapper
│               ├── observability/
│               │   ├── __init__.py
│               │   └── logging.py        # configure_logging(service: str) structured JSON
│               └── schemas/              # Pydantic DTOs shared across HTTP + async
│                   ├── __init__.py
│                   ├── job.py
│                   ├── agent.py
│                   └── deployment.py
│
├── backend/                              # FastAPI hub — control plane only
│   ├── pyproject.toml                    # depends on agent-hub-core + fastapi
│   ├── main.py
│   ├── apis/
│   ├── services/
│   └── middleware/
│
├── worker/                               # SQS consumer — orchestration only
│   ├── pyproject.toml                    # depends on agent-hub-core + boto3
│   ├── __main__.py
│   ├── main.py                           # poll loop — dispatches via registry, never changes
│   └── handlers/
│       ├── __init__.py
│       ├── base.py                       # AbstractJobHandler ABC
│       ├── registry.py                   # JobType → handler class dispatch table
│       ├── provision.py                  # JobType.AGENT_PROVISIONING
│       ├── pause.py                      # JobType.AGENT_PAUSE / DEPLOYMENT_SCALE_TO_ZERO
│       ├── destroy.py                    # JobType.AGENT_DEPROVISION / AGENT_DESTROY
│       ├── integration_rotate.py         # JobType.INTEGRATION_ROTATE
│       ├── metrics_rollup.py             # JobType.METRICS_ROLLUP
│       └── aws/                          # AWS SDK adapters (ports — swappable in tests)
│           ├── __init__.py
│           ├── ecs.py
│           ├── ecr.py
│           ├── secrets_manager.py
│           └── elb.py
│
└── agents/
    └── incident-triage/
        ├── pyproject.toml                # minimal deps — does NOT depend on agent-hub-core
        └── src/
            └── main.py
```

---

## Phase 1: Workspace Root

Create `/pyproject.toml` at repo root:

```toml
[tool.uv.workspace]
members = ["packages/agent-hub-core", "backend", "worker"]
```

---

## Phase 2: Create `packages/agent-hub-core`

### `packages/agent-hub-core/pyproject.toml`

```toml
[project]
name = "agent-hub-core"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "sqlalchemy[asyncio]>=2.0",
    "pydantic>=2.0",
    "pydantic-settings>=2.0",
    "boto3>=1.34",
    "alembic>=1.13",
    "structlog>=24.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/agent_hub_core"]
```

### `src/agent_hub_core/config/settings.py`

Move content from `backend/core/settings.py` here. Use `pydantic-settings` `BaseSettings`. Include:

- `database_url: str`
- `sqs_queue_url: str`
- `aws_endpoint_url: str | None = None` — `None` in prod, LocalStack URL locally
- `environment: str = "local"`

### `src/agent_hub_core/db/engine.py`

Move `backend/core/database.py` here. Export `get_async_session()` as an async context manager using SQLAlchemy 2 `async_sessionmaker`.

### `src/agent_hub_core/db/base.py`

```python
from sqlalchemy.orm import DeclarativeBase

class Base(DeclarativeBase):
    pass
```

### `src/agent_hub_core/db/models/__init__.py`

**Critical:** import every model explicitly so Alembic autogenerate detects all tables:

```python
from agent_hub_core.db.models.tenant import Tenant      # noqa: F401
from agent_hub_core.db.models.user import User          # noqa: F401
from agent_hub_core.db.models.agent import Agent        # noqa: F401
from agent_hub_core.db.models.deployment import Deployment  # noqa: F401
from agent_hub_core.db.models.job import Job            # noqa: F401
from agent_hub_core.db.models.integration import Integration  # noqa: F401
```

Move all ORM model files from `backend/db/models/` to `packages/agent-hub-core/src/agent_hub_core/db/models/`. Update all `Base` imports to `from agent_hub_core.db.base import Base`.

### `src/agent_hub_core/domain/enums.py`

Define all shared enums. **Do not duplicate these anywhere else in the repo.**

```python
from enum import StrEnum

class JobType(StrEnum):
    AGENT_PROVISIONING = "agent_provisioning"
    AGENT_PAUSE = "agent_pause"
    DEPLOYMENT_SCALE_TO_ZERO = "deployment_scale_to_zero"
    AGENT_DEPROVISION = "agent_deprovision"
    AGENT_DESTROY = "agent_destroy"
    INTEGRATION_ROTATE = "integration_rotate"
    METRICS_ROLLUP = "metrics_rollup"

class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"

class JobStep(StrEnum):
    ECS_TASK_DEFINITION = "ecs_task_definition"
    SECRETS_ATTACHED = "secrets_attached"
    SERVICE_STABLE = "service_stable"

class AgentStatus(StrEnum):
    DRAFT = "draft"
    PROVISIONING = "provisioning"
    ACTIVE = "active"
    PAUSED = "paused"
    DEPROVISIONING = "deprovisioning"
    ARCHIVED = "archived"
    FAILED = "failed"
```

### `src/agent_hub_core/messaging/envelope.py`

```python
from pydantic import BaseModel, field_validator
from agent_hub_core.domain.enums import JobType

_FORBIDDEN_PAYLOAD_KEYS = {"token", "secret", "password", "key", "credential"}

class JobQueueEnvelope(BaseModel):
    job_id: str
    tenant_id: str
    job_type: JobType
    correlation_id: str
    agent_id: str | None = None
    payload: dict = {}

    @field_validator("payload")
    @classmethod
    def reject_secret_material(cls, v: dict) -> dict:
        for k in v:
            if any(forbidden in k.lower() for forbidden in _FORBIDDEN_PAYLOAD_KEYS):
                raise ValueError(
                    f"Payload key '{k}' looks like secret material. "
                    "Use ARNs or resource names only."
                )
        return v
```

### `src/agent_hub_core/migrations/env.py`

Move from `backend/alembic/env.py`. Update to:

```python
from agent_hub_core.config.settings import settings
from agent_hub_core.db.base import Base
import agent_hub_core.db.models  # noqa — ensures all models are registered

config.set_main_option("sqlalchemy.url", settings.database_url)
target_metadata = Base.metadata
```

### `src/agent_hub_core/alembic.ini`

```ini
[alembic]
script_location = src/agent_hub_core/migrations
```

### `src/agent_hub_core/observability/logging.py`

Configure `structlog` to emit JSON with the minimum field contract: `timestamp`, `level`, `service`, `correlation_id`, `job_id`, `tenant_id`, `agent_id`. Export:

```python
def configure_logging(service: str) -> None: ...
```

**Repo note:** the implementation also adds standard callsite fields (`pathname`, `filename`, `lineno`, `func_name`) and uses `configure_logging("hub"|"worker")` — same JSON pipeline, slightly richer than this minimal list.

---

## Phase 3: Update `backend/pyproject.toml`

```toml
[project]
name = "agent-hub-backend"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "agent-hub-core",
    "fastapi>=0.110",
    "uvicorn[standard]>=0.29",
    "asyncpg>=0.29",
]
```

Update all imports in `backend/` from `core.*`, `db.*`, `domain.*`, `schemas.sqs_*` to `agent_hub_core.*`. Remove any `sys.path` manipulation or `PYTHONPATH` settings. Delete `backend/alembic.ini` and `backend/db/migrations/` — Alembic now lives in core only.

In `backend/main.py`, add at startup:

```python
from agent_hub_core.observability.logging import configure_logging
configure_logging(service="hub")
```

---

## Phase 4: Update `worker/pyproject.toml`

```toml
[project]
name = "agent-hub-worker"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "agent-hub-core",
    "boto3>=1.34",
    "asyncpg>=0.29",
]
```

Remove `worker/core/logging_setup.py` — replace with `agent_hub_core.observability.logging`.

In `worker/main.py`, add at startup:

```python
from agent_hub_core.observability.logging import configure_logging
configure_logging(service="worker")
```

---

## Phase 5: Scaffold Worker Handlers

### `worker/handlers/base.py`

```python
from abc import ABC, abstractmethod
from sqlalchemy.ext.asyncio import AsyncSession
from agent_hub_core.db.models.job import Job

class AbstractJobHandler(ABC):
    @abstractmethod
    async def execute(self, job: Job, session: AsyncSession) -> None: ...

    async def _advance(self, job: Job, session: AsyncSession, *, step: str) -> None:
        job.job_step = step
        await session.commit()
```

### `worker/handlers/registry.py`

```python
from agent_hub_core.domain.enums import JobType
from worker.handlers.provision import AgentProvisioningHandler
from worker.handlers.pause import AgentPauseHandler
from worker.handlers.destroy import AgentDestroyHandler
from worker.handlers.integration_rotate import IntegrationRotateHandler
from worker.handlers.metrics_rollup import MetricsRollupHandler
from worker.handlers.base import AbstractJobHandler

REGISTRY: dict[JobType, type[AbstractJobHandler]] = {
    JobType.AGENT_PROVISIONING:        AgentProvisioningHandler,
    JobType.AGENT_PAUSE:               AgentPauseHandler,
    JobType.DEPLOYMENT_SCALE_TO_ZERO:  AgentPauseHandler,
    JobType.AGENT_DEPROVISION:         AgentDestroyHandler,
    JobType.AGENT_DESTROY:             AgentDestroyHandler,
    JobType.INTEGRATION_ROTATE:        IntegrationRotateHandler,
    JobType.METRICS_ROLLUP:            MetricsRollupHandler,
}
```

**Adding a new job type in future:** (1) add enum value to `agent_hub_core/domain/enums.py`, (2) create `worker/handlers/new_handler.py`, (3) add one line to `REGISTRY`. Nothing else changes.

### `worker/main.py` poll loop

**Implemented semantics (authoritative):** `DeleteMessage` **only** after the handler returns successfully. Malformed JSON / invalid envelope, missing `Job` row, tenant mismatch, unknown `job_type`, or handler exceptions **do not** delete the message (visibility timeout / DLQ policy applies). This differs from an older draft that deleted on “job not found”; retries can help if the row appears after enqueue races.

```python
from agent_hub_core.messaging.envelope import JobQueueEnvelope
from agent_hub_core.db.models.job import Job
from agent_hub_core.db.engine import get_session_factory
from worker.handlers.registry import handler_for_job_type

async def process_message(settings, raw: dict) -> None:
    body = raw.get("Body")
    receipt = raw.get("ReceiptHandle")
    if not isinstance(body, str) or not isinstance(receipt, str):
        return  # no delete — cannot ack

    try:
        envelope = JobQueueEnvelope.model_validate_json(body)
    except ValidationError:
        return  # no delete

    factory = get_session_factory(settings)
    async with factory() as session:
        job = await session.get(Job, envelope.job_id)
        if job is None or job.tenant_id != envelope.tenant_id:
            return  # no delete

        handler_cls = handler_for_job_type(job.job_type)
        if handler_cls is None:
            return  # no delete

        await handler_cls().execute(job, session)

    delete_message(receipt)  # success path only
```

### `worker/handlers/aws/*.py`

Each adapter reads `settings.aws_endpoint_url` (None in prod, LocalStack URL locally) so the same code path works in both environments:

```python
# Pattern for every adapter
import boto3
from agent_hub_core.config.settings import settings

class ECSAdapter:
    def __init__(self):
        self._client = boto3.client("ecs", endpoint_url=settings.aws_endpoint_url)
```

Handlers receive adapter instances as constructor arguments so tests can inject mocks without touching boto3.

---

## Phase 6: Idempotency Rules (implement in every handler)

Every handler **must** guard against SQS at-least-once redelivery at the top of `execute()`:

```python
async def execute(self, job: Job, session: AsyncSession) -> None:
    if job.status in (JobStatus.succeeded, JobStatus.failed, JobStatus.dead_lettered):
        return  # already terminal — safe no-op on redelivery (or use ``is_terminal_job``)
    ...
```

**DB (implemented):** use [`agent_hub_core.db.job_transitions`](packages/agent-hub-core/src/agent_hub_core/db/job_transitions.py) — `claim_job_for_worker()` issues `UPDATE ... WHERE id = :id AND status IN ('pending','queued')` so only one worker flips to `running`; `complete_job_success()` updates only while `status = 'running'`. Handlers still `refresh()` after each commit so the in-memory `Job` matches Postgres.

**AWS (pattern):** conditional updates on the row do not replace _describe-before-create_ and idempotent ECS API usage (see `worker/handlers/aws/ecs.py` module docstring). Stub handlers re-run the succeed step safely when redelivered mid-`running`.

---

## Phase 7: Dockerfiles

### `backend/Dockerfile`

**Repo layout:** `agent-hub-backend` exposes the FastAPI app as `main:app` (see `backend/pyproject.toml`), so the container runs `uvicorn` from `/app/backend` after editable installs.

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY packages/agent-hub-core /app/packages/agent-hub-core
COPY backend /app/backend
WORKDIR /app/backend
RUN pip install --no-cache-dir -e /app/packages/agent-hub-core -e .
CMD ["sh", "-c", "alembic -c /app/packages/agent-hub-core/alembic.ini upgrade head && exec uvicorn main:app --host 0.0.0.0 --port 8000"]
```

### `worker/Dockerfile`

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY packages/agent-hub-core /app/packages/agent-hub-core
COPY worker /app/worker
WORKDIR /app/worker
RUN pip install --no-cache-dir -e /app/packages/agent-hub-core -e .
CMD ["python", "-m", "worker"]
```

The worker image **never** runs Alembic migrations.

---

## Phase 8: Alembic Commands

```bash
# Generate a new migration (run from repo root)
uv run --package agent-hub-core alembic -c packages/agent-hub-core/alembic.ini revision --autogenerate -m "describe_change"

# Apply migrations
uv run --package agent-hub-core alembic -c packages/agent-hub-core/alembic.ini upgrade head

# Without uv workspaces
cd packages/agent-hub-core && alembic upgrade head
```

---

## Phase 9: CI Path Filtering

Implemented under [`.github/workflows/backend.yml`](.github/workflows/backend.yml) and [`.github/workflows/worker.yml`](.github/workflows/worker.yml): `push` and `pull_request` with the path sets below, plus **`pyproject.toml`**, **`uv.lock`**, and the **workflow file itself** so lockfile / CI edits still run checks.

```yaml
# .github/workflows/backend.yml (subset)
on:
  push:
    paths:
      - "backend/**"
      - "packages/agent-hub-core/**"
      - "pyproject.toml"
      - "uv.lock"
      - ".github/workflows/backend.yml"
  pull_request:
    paths: # same as push
      - "backend/**"
      - "packages/agent-hub-core/**"
      # …

# .github/workflows/worker.yml (subset)
on:
  push:
    paths:
      - "worker/**"
      - "packages/agent-hub-core/**"
      - "pyproject.toml"
      - "uv.lock"
      - ".github/workflows/worker.yml"
```

Jobs run **`uv sync --frozen --group dev`**, **`pytest -q`** (see `tests/`), import checks for **hub** / **worker** / **core**, and **`compileall`** on the relevant sources.

---

## Hard Rules (enforce throughout)

- **Never** import `fastapi` inside `agent_hub_core/`. Add a test: `assert "fastapi" not in sys.modules` after importing core in a clean environment.
- **Never** put secret values in `JobQueueEnvelope.payload`. The Pydantic validator above enforces this — do not remove it.
- **Never** run Alembic from `backend/` or `worker/` — only from `packages/agent-hub-core/`.
- **Never** duplicate `JobType`, `JobStatus`, or `AgentStatus` enums. One definition in `agent_hub_core/domain/enums.py` only.
- **Never** `DeleteMessage` on a transient AWS/DB error — let visibility timeout expire so SQS retries automatically.
- **Never** `DeleteMessage` when the message is structurally broken, the `Job` row is missing, the tenant does not match, or `job_type` is unknown — leave the message for visibility timeout / DLQ (ops can inspect the poison message). **Only** delete after a successful handler (same rule for handler exceptions).

---

## Future Split (do not implement now)

If agents ever need the envelope or settings without SQLAlchemy, extract `packages/agent-hub-wire/` containing only `agent_hub_core/messaging/envelope.py` and `agent_hub_core/config/settings.py`. This is a 30-minute internal move with no API breakage. Do not do this prematurely.
