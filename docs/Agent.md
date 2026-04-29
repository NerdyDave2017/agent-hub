# Agent instructions — agent-hub

Align implementation with **[`plan.md`](plan.md)**. This file is the short operational contract; extend [`architecture.md`](architecture.md) and the root [`README.md`](../README.md) as the repo grows.

---

## North star

- **Hub:** FastAPI in `backend/` — client API, auth, tenants, agents registry, jobs, dashboard BFF.
- **Worker:** Python in `worker/` — SQS consumer, Postgres updates, provisioning / KPI handlers.
- **Reference agent:** `agents/incident-triage/` — own HTTP service, Langfuse, LangGraph HITL. Add more agents under `agents/` using the same pattern when needed.
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
4. **AWS** — Apply Terraform roots in order once implemented: shared **`infra/modules/*`** via **`infra/localstack`** (dev only) or prod composition → **`infra/hub/`** (App Runner) → **`infra/worker/`** → **`infra/agents/incident-triage/`** (optional **`infra/frontend/`**). Use **`terraform_remote_state`** (or shared module outputs) where worker needs queue URLs / VPC inputs from the stack that owns SQS.
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

- Postgres is a **separate** process: compose service `postgres` locally; **RDS** via Terraform (`infra/modules/rds` and related roots). Hub and worker share one database in typical deployments. **`DATABASE_URL`** on hub and worker. **Alembic** via explicit migrate step (Makefile/CI), not uncoordinated startup migrate on every hub replica in prod.

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
| **`infra/localstack/`** | **Development:** emulated SQS (`modules/sqs`), secrets (`modules/secrets`), IAM (hub App Runner trust, worker/agent ECS task roles). |
| **`infra/hub/`** | **Production hub:** App Runner service, VPC connector, IAM — **not** ECS+ALB (legacy `infra/backend/` removed). |
| **`infra/worker/`** | Worker ECS Fargate (`modules/ecs-worker`), agent IAM roles, EventBridge → SQS (see `docs/terraform-infra-instructions.md`). |
| **`infra/agents/incident-triage/`** | Optional on-AWS agent: ECS, Cloud Map, agent IAM/SG (SaaS default remains App Runner from worker provisioner). |
| **`infra/frontend/`** | Optional: S3/CloudFront, Amplify, etc., when `frontend/` exists. |
| **`infra/modules/`** | Shared VPC, RDS, SQS, secrets, ECS cluster — composed by roots; **no** standalone apply. |
| **`infra/_bootstrap/`** | Optional one-time state bucket + DynamoDB lock. |

Each root: own **state key** in a shared S3 backend + lock table is fine.

---

## CI path filters

- **`backend/**`** or **`infra/hub/**`** → build/push hub + plan/apply **`infra/hub/`** (when App Runner Terraform is implemented).
- **`worker/**`** or **`infra/worker/**`** → worker image + **`infra/worker/`**.
- **`agents/incident-triage/**`** or **`infra/agents/incident-triage/**`** → agent image + **`infra/agents/incident-triage/`**.
- **`frontend/**`** or **`infra/frontend/**`** → optional frontend + **`infra/frontend/`**.

Apply dependent stacks **after** the root that owns shared queues / VPC / RDS when using `terraform_remote_state`.

---

## Documentation

- **`docs/architecture.md`:** minimal mermaid, sequences, repo tree, local-first steps, **per-service Terraform map** and **apply order**, dependency table condensed, Langfuse/LangGraph in appendix.
- **README:** compose up, env vars, migrations, smoke test hub → SQS → worker.

---

## Out of scope (v1)

- Arbitrary multi-agent CI matrix before the second agent is a product requirement.
- Full OAuth matrix for every integration — prefer one path plus documented “phase 2” gaps.

---

## Quick paths

| Piece | Path |
| --- | --- |
| Hub | `backend/` |
| Worker | `worker/` |
| Agent | `agents/incident-triage/` |
| Local | `docker-compose.yml` |
| IaC | `infra/localstack/` (dev), `infra/hub/`, `infra/worker/`, `infra/agents/incident-triage/`, `infra/modules/*`, optional `infra/frontend/` |
| Docs | `docs/plan.md`, `docs/architecture.md`, `docs/terraform-infra-instructions.md` |
