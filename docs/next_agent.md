## Instructions for the next agent (agent-hub)

### 0. Mandatory first step — **read before coding**

**Read [`response.md`](response.md) in full before any implementation.** It is the authoritative phased plan (UV workspace, `agent-hub-core` layout, worker handlers/registry, Alembic placement, Dockerfiles, CI path filters, idempotency rules, and “hard rules”). Align new work with that document unless the user explicitly overrides it.

---

### 1. Where the repo left off (executed work)

**Done (high level):**

- **Phase 1 — UV workspace:** Root [`pyproject.toml`](pyproject.toml) with members `packages/agent-hub-core`, `backend`, `worker`; root depends on all three; `uv sync` from repo root.
- **Phase 2 — Shared kernel:** [`packages/agent-hub-core/`](packages/agent-hub-core/) contains `agent_hub_core` with `config/`, `db/` (engine + `models.py` + `base`), `domain/`, `messaging/` (envelope + SQS), `schemas/`, `observability/`, Alembic under `src/agent_hub_core/migrations/` and [`packages/agent-hub-core/alembic.ini`](packages/agent-hub-core/alembic.ini). Hub is **only** `backend/main.py`, `apis/`, `services/`. Old `backend/core`, `backend/db`, `backend/domain`, `backend/schemas`, `backend/alembic.ini` were **removed**.
- **Logging:** Stdlib JSON logging was replaced with **structlog → JSON** in [`packages/agent-hub-core/src/agent_hub_core/observability/logging.py`](packages/agent-hub-core/src/agent_hub_core/observability/logging.py) (`configure_logging`, `get_logger`), with UTC `timestamp`, `message`, and callsite fields (`pathname`, `filename`, `lineno`, `func_name`).
- **Domain exceptions:** [`packages/agent-hub-core/src/agent_hub_core/domain/exceptions.py`](packages/agent-hub-core/src/agent_hub_core/domain/exceptions.py) uses a **`DomainError`** base with `status_code`, `error_code`, `message`, and `context`. Services **must** raise with IDs (e.g. `TenantNotFound(tenant_id)`). [`backend/main.py`](backend/main.py) maps them to **`JSONResponse`** with structured `detail` (not plain string `detail` anymore).
- **Phase 5 (scaffold):** [`worker/handlers/`](worker/handlers/) — `base.py`, `_idempotency.py`, `registry.py`, stub handlers (`provision`, `pause`, `destroy`, `integration_rotate`, `metrics_rollup`), and [`worker/handlers/aws/`](worker/handlers/aws/) stub adapters. [`worker/main.py`](worker/main.py) loads `Job`, dispatches via `handler_for_job_type`, **`DeleteMessage` only after successful handler** (no delete on malformed envelope / missing job / unknown type / handler exception).

**Extended `JobType` enum** in [`packages/agent-hub-core/src/agent_hub_core/domain/enums.py`](packages/agent-hub-core/src/agent_hub_core/domain/enums.py) for future jobs (`agent_pause`, `deployment_scale_to_zero`, etc.). DB `jobs.job_type` remains a **string**; no migration was added for new enum members.

**Not done or only stubbed (per `response.md`):**

- **Phase 6** — ✅ DB: [`agent_hub_core.db.job_transitions`](packages/agent-hub-core/src/agent_hub_core/db/job_transitions.py) + all handlers use conditional **claim** / **complete** updates; tests in [`tests/test_job_transitions_compile.py`](tests/test_job_transitions_compile.py). **AWS:** still pattern-only (describe-before-create, idempotent ECS tokens — see `worker/handlers/aws/ecs.py`).
- **Phase 7** — ✅ **`backend/Dockerfile`**, **`worker/Dockerfile`**, root **`.dockerignore`**, and **`docker-compose.yml`** `hub` + `worker` services (Postgres healthcheck + LocalStack deps).
- **Phase 8** — Alembic commands documented in [`README.md`](README.md) / [`response.md`](response.md) (`uv run --package agent-hub-core alembic ...`).
- **Phase 9** — ✅ [`.github/workflows/backend.yml`](.github/workflows/backend.yml) + [`.github/workflows/worker.yml`](.github/workflows/worker.yml) (`uv sync --frozen`, imports, `compileall`).
- **Handler bodies** — Stubs flip `queued` → `running` → `succeeded` for **any** dispatched type; **replace with real ECS/ECR/Secrets/pause/destroy** and align **`agents` / `deployments`** state with product rules.
- **`response.md`** — worker delete semantics, hard rules, `engine.py` naming, and structlog note reconciled with the repo (Apr 2026).

---

### 2. Commands the next agent should use

From **repo root**:

```bash
uv sync
uv run --package agent-hub-core alembic -c packages/agent-hub-core/alembic.ini upgrade head
uv run --package agent-hub-backend --directory backend uvicorn main:app --reload --host 0.0.0.0 --port 8000
uv run python -m worker
```

Settings load **`.env`** then **`backend/.env`** when present (see [`packages/agent-hub-core/src/agent_hub_core/config/settings.py`](packages/agent-hub-core/src/agent_hub_core/config/settings.py)).

---

### 3. Contracts and pitfalls

- **SQS:** No secrets in [`JobQueueEnvelope`](packages/agent-hub-core/src/agent_hub_core/messaging/envelope.py) / `payload`; hub still enqueues after DB commit.
- **Worker delete semantics:** Malformed body, missing `Job`, tenant mismatch, unknown `job_type` → **no** `DeleteMessage`; handler exception → **no** delete (retry/DLQ). Success path deletes after handler returns.
- **API clients:** HTTP errors for domain failures are now structured **`detail: { code, message, … }`** — may break tests/clients expecting `"detail": "tenant not found"`.
- **README:** [`README.md`](README.md) was updated for workspace, Alembic path, worker, and structlog; other docs ([`Agent.md`](Agent.md), [`docs/plan.md`](docs/plan.md)) may still reference old `backend/core` paths.

---

### 4. Suggested next tasks (after re-reading `response.md`)

1. Implement **real** `AgentProvisioningHandler` (and related AWS adapter usage) per plan — without breaking idempotency / DLQ semantics.
2. ~~Add **Phase 7 Dockerfiles** and wire **`docker-compose.yml`** hub + worker services if not present.~~ **Done** — verify in CI or add compose smoke test if desired.
3. Harden **Phase 6** idempotency (DB + AWS) and add tests.
4. ~~Reconcile **`response.md`** with the repo~~ **Done** (Apr 2026); optional model split remains future work.

---

**First action for you (next agent):** Open and read **[`response.md`](response.md)** end-to-end, then continue implementation from the sections that match the user’s current priority (usually real provisioning + Docker + compose, or CI).
