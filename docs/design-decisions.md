# Agent Hub — design decisions

This document explains **which technologies we use and why**, how **provisioning and async work** are modeled, **why the architecture is shaped this way**, and **which problems the approach is meant to solve**. It complements [architecture.md](architecture.md) (structure and diagrams) and [data-flow.md](data-flow.md) (sequence diagrams for user journeys).

---

## 1. Problems we are solving

| Problem | Consequence if ignored | Our approach |
| --- | --- | --- |
| **HTTP tied to slow side effects** | Timeouts, poor UX, hard-to-reason partial failures | Hub **commits** work to Postgres and **returns**; a **worker** performs provisioning, AWS calls, and rollups **asynchronously** via SQS. |
| **Two sources of truth** | Retries double-apply, support cannot trust “status” | **Postgres** is authoritative for job and registry state; the queue carries a **small envelope** (`job_id`, `tenant_id`, `job_type`, …) so the worker knows **what** to load, not a second copy of truth. |
| **Secrets in messages or logs** | Compliance failure, credential rotation nightmares | **JobQueueEnvelope** and API validators reject secret-like keys in payloads; OAuth and API keys live in **Secrets Manager** / env and are read only where IAM allows. |
| **At-least-once delivery** | Duplicate side effects, stuck queues | Handlers are **idempotent** on `job_id` (and DB constraints where applicable); **DLQ** isolates poison messages after `maxReceiveCount`. |
| **Hub and worker drift** | Subtle production bugs (“works on hub”) | Single Python package **`agent-hub-core`**: same models, migrations, envelope schema, and settings for **both** processes. |
| **Opaque AI operations** | No cost/latency accountability | **Structured logging** everywhere; **Langfuse** (and planned hub DB rollups) for traces and business-facing metrics. |
| **One agent becomes a monolith** | Cannot scale or ship agent types independently | **Agents are separate services**; hub stays a **control plane**; optional **LangGraph HITL** stays **inside** an agent, not on the main platform diagram. |

---

## 2. Technology choices

| Technology | Role | Why we chose it |
| --- | --- | --- |
| **Python ≥ 3.11** | Hub, worker, agents, core | Strong async ecosystem; one language across control plane and agents reduces context switching and lets `agent-hub-core` be a real shared library. |
| **uv workspace** | Monorepo packaging | Fast, reproducible installs; **editable** workspace members (`agent-hub-core`, backend, worker, agents) with a single lockfile and CI-friendly `uv sync --frozen`. |
| **FastAPI + Uvicorn** | Hub HTTP API | OpenAPI-first, async routes, dependency injection for DB/session and auth; fits **lifespan** hooks for pools and clients. |
| **SQLAlchemy 2 + asyncpg** | Async persistence | Mature ORM with async support; aligns with FastAPI async handlers and worker DB access. |
| **Alembic (in core)** | Migrations | **One migration stream** for schema shared by hub and worker avoids split-brain DDL; run from `agent-hub-core` package path documented in repo. |
| **pydantic-settings** | Configuration | Typed settings, `.env` loading, validation at startup — fewer “wrong env name” incidents across services. |
| **AWS SQS + boto3** | Async pipe | Managed **at-least-once** queue, **DLQ** via redrive policy, same API in **AWS** and **LocalStack** (`AWS_ENDPOINT_URL` only in dev). |
| **Postgres** | System of record | ACID semantics for jobs and multi-tenant data; natural fit for **idempotency keys**, status transitions, and future rollups. |
| **structlog → JSON** | Logging | Queryable logs in CloudWatch or ELK-like stacks; **shared fields** across hub, worker, agents for incident response. |
| **Terraform** | Infra as code | **Per-service roots** under `infra/` limit blast radius and match CI path filters; **modules** reuse VPC, SQS, RDS, App Runner, ECS patterns. |
| **Docker + Compose** | Local and deploy artifacts | Same images locally and in CI; compose bundles Postgres + LocalStack + hub + worker for **local-first** verification. |
| **Langfuse** (where integrated) | LLM observability | Traces, cost, latency for agent workloads; complements logs, does not replace them. |
| **LangGraph** (agent-local, optional) | HITL | Interrupt/resume inside an **agent** service keeps the **platform** diagram stable while still shipping approvals for high-impact steps. |

**Explicit non-decision:** We do **not** require gRPC for public APIs; REST remains the default. Typed internal RPC (e.g. Connect/gRPC) is optional later if latency and contract tooling justify it.

---

## 3. Provisioning model

**Intent:** “User clicks create / deploy” should not block on ECS, DNS, or image pulls finishing.

1. **Hub** validates the request, writes **durable state** (e.g. agent + job rows), and enqueues a **metadata-only** [`JobQueueEnvelope`](../packages/agent-hub-core/src/agent_hub_core/messaging/envelope.py).
2. **Worker** picks up the message, loads the job, and executes a **handler** registered for that `job_type` (see [`worker/handlers/registry.py`](../worker/handlers/registry.py)).
3. **Side effects** (start/update tasks, secrets, load balancers) live in handler code and **AWS adapters** under [`worker/handlers/aws/`](../worker/handlers/aws/) — the **provisioner abstraction**: ECS, App Runner, k8s, or local Docker can sit behind the same orchestration shape as implementations mature.

**Why not provision synchronously in the hub?** Long-running cloud APIs would inflate tail latency, tie up worker threads, and complicate retries. Separating **acceptance** (hub) from **application** (worker) gives clear retries, DLQ behavior, and horizontal scaling of consumers.

**Development ergonomics:** If `SQS_QUEUE_URL` is **unset**, jobs can remain **`pending`** while engineers exercise REST and Postgres only — no queue emulator required for early API work.

---

## 4. Why this architecture (pillars)

1. **Control plane vs data plane** — The hub is **not** an LLM runtime; agents own tools and models. That boundary keeps security scopes, scaling, and release cadences **separate**.
2. **Hub vs worker process** — Same repo, **different processes**: independent deploy, IAM, and scaling; worker can be scaled or throttled without touching the API fleet.
3. **Monorepo + `agent-hub-core`** — One place for **envelope**, **Job** model, and **migrations** prevents the producer and consumer from disagreeing on JSON or enums.
4. **Queue as notification, DB as truth** — SQS visibility timeouts and redrives are **infrastructure**; **business state** lives in Postgres so admins and APIs query one place.
5. **Per-service Terraform roots** — Aligns CI, ownership, and blast radius with **one deployable per stack**; shared code stays in `infra/modules/` without its own apply lifecycle.

---

## 5. Tradeoffs and alternatives (documented)

| Topic | Current bias | Alternatives we acknowledge |
| --- | --- | --- |
| **Queue backend** | SQS (+ DLQ) for AWS-shaped dev and prod | **Postgres** (`SKIP LOCKED` / outbox) or **Redis/NATS** for fewer managed pieces on a VPS; abstract enqueue/dequeue if swapping becomes real. |
| **Where the worker runs** | Separate service (ECS in AWS layout) | Same image, second entrypoint on a single VPS for minimal footprint. |
| **Agent isolation** | **Per agent type** deployment first | **Per-tenant** namespaces or clusters when compliance or noisy-neighbor risk justifies cost. |
| **Local dev complexity** | LocalStack for SQS parity | **Postgres-only** async later to reduce containers; trade fewer parts against AWS fidelity. |

---

## 6. Quick reference — decision log

| Decision | Choice |
| --- | --- |
| Source of truth for jobs | Postgres |
| Queue payload | Non-secret `JobQueueEnvelope` only |
| Shared code between hub and worker | `agent-hub-core` package |
| Hub without queue | Allowed; jobs stay `pending` |
| Infra layout | Per-deployable Terraform roots + shared modules |
| Agent HITL | LangGraph (or similar) **inside** agent services |
| Primary API style | REST / OpenAPI (FastAPI) |

---

## 7. Related reading

- [data-flow.md](data-flow.md) — sequence diagrams for sign-up, dashboard, agents, worker, integrations
- [architecture.md](architecture.md) — diagrams, `agent-hub-core`, infra map
- [explanatory-brief-for-llms.md](explanatory-brief-for-llms.md) — condensed technical + business orientation
- [plan.md](plan.md) — phased delivery and scope guardrails

---

_Update this file when the primary queue implementation, default compute target, or agent packaging strategy changes._
