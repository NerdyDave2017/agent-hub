# Agent instructions — agent-hub (capstone)

Align implementation with the full project plan in **[`docs/plan.md`](docs/plan.md)**. This file is the short operational contract; extend README and `docs/architecture.md` as the repo grows.

---

## North star

- **Hub:** FastAPI in `backend/` — client API, auth, tenants, agents registry, jobs, dashboard BFF.
- **Worker:** Python in `worker/` — SQS consumer, Postgres updates, provisioning / KPI handlers.
- **Agent (capstone only):** `agents/incident-triage/` — own HTTP service, Langfuse, LangGraph HITL. No second agent on the critical path.
- **Order:** **Local-first** (`docker compose`: Postgres + SQS emulator + hub + worker + agent when ready). **Terraform on AWS only after** hub → SQS → worker → DB works locally.

---

## Coding instructions

- **Minimal surface:** implement only what the task or plan requires. Prefer the **shortest correct solution**—one clear code path, obvious names, straightforward control flow. **Do not** add layers (extra services, indirection, generic “engines”) unless there is a concrete need spelled out in `docs/plan.md`.
- **No incidental complexity:** avoid drive-by refactors, unrelated files, speculative hooks, and “future-proof” abstractions. **Do not** complicate APIs, schemas, or Terraform to anticipate features that are not in scope.
- **Consistency:** follow patterns already in the repo (`backend/apis/`, `backend/schemas/`, `backend/db/`, worker layout). Extend existing types and routers instead of parallel conventions.
- **Clarity over cleverness:** readable beats clever; shared behavior only when it **removes** duplication (e.g. one Pydantic model for a job payload used by hub and worker), not when it adds indirection.
- **Comments and errors:** no long narrative comments or docstrings for obvious code. Errors should carry **enough context to debug** (ids, not secrets); avoid blanket try/except around large blocks.
- **Terraform:** each service root stays **as small as the service needs**; reuse `infra/modules/` for repetition, not for policy you only use once.

---

## Build order

1. **T0 — Local platform** — `docker-compose.yml` proves hub enqueues → worker consumes → DB (or logs then DB); **structured logs** on hub + worker (and agent when added). **No Terraform until T0 passes.**
2. **Agent wire-up** — `agents/incident-triage` `/health` + minimal run path; document hub→agent vs worker→agent in README.
3. **Product** — Langfuse (hub + agent), dashboard API, LangGraph HITL (interrupt + resume).
4. **AWS** — Apply Terraform roots in order: **`infra/backend/`** → **`infra/worker/`** → **`infra/agents/incident-triage/`** (optional **`infra/frontend/`**). Use **`terraform_remote_state`** where worker (or agent) needs backend outputs (e.g. SQS queue URLs).
5. **CI** — OIDC to AWS; path filters tie **app** and **Terraform** per service (see below).

---

## Docker

- Hub, worker, and incident-triage each have a **Dockerfile** and run as **separate compose services**. Canonical check: **`docker compose up`** healthy.

---

## Structured logging (all services)

- **Hub, worker, and `agents/incident-triage`** all emit **structured logs** (e.g. **structlog** → JSON) from **T0**, not only after AWS deploy.
- **Shared minimum fields:** `service` (`hub` | `worker` | `incident_triage`), `correlation_id` (or `request_id` on hub HTTP), `level`, `timestamp`; add `job_id` on async paths, `tenant_id` / `run_id` when known.
- **Propagation:** hub includes `correlation_id` in SQS job body (or header convention) so worker logs match; hub/agent pass **`X-Correlation-ID`** on HTTP where applicable. Document the contract in README and `docs/architecture.md`.
- **AWS:** ECS → CloudWatch Logs; JSON fields stay filterable.

---

## Database

- Postgres is a **separate** process: compose service `postgres` locally; **RDS** owned by the **`infra/backend/`** stack (or as you document—hub and worker share one DB for capstone). **`DATABASE_URL`** on hub and worker. **Alembic** via explicit migrate step (Makefile/CI), not uncoordinated startup migrate on every hub replica in prod.

---

## Hub ↔ worker (SQS)

- Hub **`send_message`** JSON jobs; worker **`receive_message`** / **`delete_message`**. Every job has **`job_id`** (UUID); worker idempotent on redelivery.
- **No secrets in SQS bodies.** Local: **`AWS_ENDPOINT_URL`** for LocalStack; prod: unset.

---

## Configuration

- **`pydantic-settings`:** `DATABASE_URL`, `SQS_QUEUE_URL`, optional `SQS_DLQ_URL`, `AWS_REGION`, `AWS_ENDPOINT_URL`, Langfuse vars locally; AWS uses Secrets Manager as stacks define. **Do not commit secrets.**

---

## Langfuse and dashboard

- Langfuse in **hub** and **incident-triage**; tags include `tenant_id`, `agent_type=incident_triage`, `deployment_env`.
- Dashboard BFF in `backend/apis/dashboard.py`; KPI rollups in Postgres (`metric_snapshots` or equivalent) as in plan.

---

## LangGraph HITL

- Only under **`agents/incident-triage/`**. Main `docs/architecture.md` diagram stays high-level; HITL detail in appendix.

---

## Terraform layout (per service)

Each deployable owns a **Terraform root** that declares **only** (or primarily) the resources that service needs:

| Root | Owns (typical) |
| --- | --- |
| **`infra/backend/`** | Hub ECS, hub ECR, ALB listener/TG for hub, **RDS**, **SQS** queues the hub publishes to, hub IAM/secrets/SG; **outputs** for queue URLs (and SG/RDS info worker needs). |
| **`infra/worker/`** | Worker ECS, worker ECR, worker IAM (SQS consume, DB, optional ECS API), worker SG; **`terraform_remote_state`** → backend for queue URLs etc. |
| **`infra/agents/incident-triage/`** | Agent ECS, ECR, ALB (or internal access), agent IAM/secrets/SG. |
| **`infra/frontend/`** | Optional: S3/CloudFront, Amplify, etc., when `frontend/` exists. |
| **`infra/modules/`** | Shared modules only — **no** standalone apply. |
| **`infra/_bootstrap/`** | Optional one-time state bucket + DynamoDB lock. |

Each root: own **state key** in a shared S3 backend + lock table is fine.

---

## CI path filters

- **`backend/**`** or **`infra/backend/**`** → build/push hub + plan/apply **`infra/backend/`**.
- **`worker/**`** or **`infra/worker/**`** → worker image + **`infra/worker/`**.
- **`agents/incident-triage/**`** or **`infra/agents/incident-triage/**`** → agent image + **`infra/agents/incident-triage/`**.
- **`frontend/**`** or **`infra/frontend/**`** → optional frontend + **`infra/frontend/`**.

Apply dependent stacks **after** backend when using `terraform_remote_state` from backend.

---

## Documentation

- **`docs/architecture.md`:** minimal mermaid, sequences, repo tree, local-first steps, **per-service Terraform map** and **apply order**, dependency table condensed, Langfuse/LangGraph in appendix.
- **README:** compose up, env vars, migrations, smoke test hub → SQS → worker.

---

## Out of scope (capstone)

- Second agent or multi-agent CI matrix.
- Full OAuth matrix for every integration — stub or one path + “phase 2” in docs.

---

## Quick paths

| Piece | Path |
| --- | --- |
| Hub | `backend/` |
| Worker | `worker/` |
| Agent | `agents/incident-triage/` |
| Local | `docker-compose.yml` |
| IaC | `infra/backend/`, `infra/worker/`, `infra/agents/incident-triage/`, optional `infra/frontend/` |
| Docs | `docs/architecture.md` |
