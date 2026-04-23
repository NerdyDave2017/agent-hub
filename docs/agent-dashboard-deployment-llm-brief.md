# Agent dashboard, observability, and deployment — LLM design brief

This document captures the **current state** of the **agent-hub** codebase, **deployment intentions** discussed so far, and **open product/architecture questions** (especially dashboards and real-time agent visibility). It is meant to be shared with an LLM or human architect to propose concrete designs, trade-offs, and implementation steps.

---

## 1. Purpose

- Align on **where each runtime should live** (hub, worker, agent) given traffic patterns.
- Resolve the **“agent is not client-facing”** deployment dilemma while honoring **worker ↔ agent** HTTP (and optional **hub → agent** notify today); evaluate **removing hub → agent** entirely (§4.4).
- Choose approaches for **tenant overview** and **per-agent dashboards**, including **near–real-time** “decisions and actions” vs **aggregated** KPIs.
- Clarify what **Langfuse** (with **LangGraph** / LangChain) already gives vs what must be **built in Postgres + hub APIs + frontend**.

---

## 2. Current application state (repository)

### 2.1 High-level architecture

| Component | Path / package | Role |
| --- | --- | --- |
| **Hub (control plane)** | `backend/` — FastAPI | Public REST under `/api/v1`, OAuth (Gmail/Slack), Gmail Pub/Sub receiver, job creation, DB registry, **dashboard** routes under `/tenants/{tenant_id}/dashboard/*`. **Internal** routes under `/internal/*` (Bearer `INTERNAL_SERVICE_TOKEN`) for agents. **No HTTP to agents.** |
| **Worker** | `worker/` | Long-polls **SQS**, dispatches by `JobType`, updates **`jobs`** in Postgres, calls **agent HTTP** where needed (e.g. triage run). No inbound HTTP on the critical path. |
| **Shared kernel** | `packages/agent-hub-core/` | Settings, ORM models, Alembic migrations, SQS envelope, logging. |
| **Agent (capstone)** | `agents/incident-triage/` | FastAPI + **LangGraph** graph; **`POST /api/v1/runs`** invoked by worker; optional Gmail webhooks; **outbound** `httpx` to hub `/internal/...` when `HUB_BASE_URL` + `HUB_SERVICE_TOKEN` set; Gmail poll skip when **`integrations.watch_active`** (§2.2.1). **Langfuse** `CallbackHandler` on graph invoke when keys configured. |

### 2.2 Request / job flows (factual)

1. **Hub → SQS → Worker** — Hub commits a `jobs` row and may `SendMessage` with `JobQueueEnvelope` (`packages/agent-hub-core/.../messaging/`).
2. **Worker → Agent** — Example: `worker/handlers/gmail_process_message.py` builds `POST {agent_base_url}/api/v1/runs` using `Deployment.base_url` or `INCIDENT_TRIAGE_AGENT_URL`.
3. **Agent → Hub** — Example: `agents/.../graph/nodes/enrich.py` calls hub `GET /internal/tenants/{id}` and incidents list with bearer token.
4. **Gmail** — Pub/Sub push targets **hub** (`backend/apis/webhooks_gmail.py`), which enqueues worker jobs; not the primary “internet → agent” path for triage in the current design.
5. **Hub → Agent** — **None** (Gmail push coordination uses **`integrations.watch_active`** in Postgres; see §2.2.1).

### 2.2.1 Gmail push vs agent polling (no hub → agent HTTP)

After a successful Gmail OAuth callback and `users.watch()`, the hub sets **`integrations.watch_active = True`** on the Gmail integration row (shared Postgres). The incident-triage **poll loop** reads that flag via `is_gmail_hub_push_watch_active()` (`agents/incident-triage/.../triggers/poller.py`) and **skips polling** while hub Pub/Sub is authoritative. **Hub never HTTPs to the agent** (see `docs/deployment-and-dashboard-instructions.md`).

**Other hub `httpx` calls** are **not** to the agent: Google userinfo (`integrations_gmail.py`) and Slack `oauth.v2.access` (`integrations_slack.py`).

### 2.3 Data already modeled for dashboards (Postgres)

| Table / model | Purpose (from code / comments) |
| --- | --- |
| **`tenants`**, **`agents`**, **`deployments`**, **`integrations`**, **`jobs`** | Registry, lifecycle, async work, correlation IDs. |
| **`incidents`** | Canonical triage outcomes; **`langfuse_trace_id`** optional link to Langfuse; **`actions_taken`** JSONB. Hub-oriented “what happened” per message/incident. |
| **`tool_call_events`** | Per-node/tool spans from agent instrumentation (`write_tool_event`): `node_name`, `tool_name`, `decision`, tokens, cost, etc. Good for **action/decision feeds** if exposed via hub APIs. |
| **`metric_snapshots`** | **Pre-aggregated KPI JSON** per tenant + `agent_type` + time window (`window_start` / `window_end`). Intended for **fast dashboard reads**; not yet populated by implemented rollup logic. |

### 2.4 What is stub / not wired for dashboards

| Area | State |
| --- | --- |
| **`worker/handlers/metrics_rollup.py`** | **Implemented (Postgres-only)**: aggregates **`incidents`** + **`tool_call_events`** into **`metric_snapshots`** for the job’s tenant/agent and hour window (payload may override `window_start` / `window_end`). **Langfuse API** enrichment and **EventBridge** scheduling are still optional follow-ups (`docs/deployment-and-dashboard-instructions.md`). |
| **Hub “dashboard” APIs** | `backend/apis/dashboard.py` — tenant overview, per-agent SSE feed, safe incidents list/detail (`LANGFUSE_HOST` for trace links). Same **URL-scoped `tenant_id`** pattern as other hub APIs until JWT land. |
| **Langfuse trace ID on `Incident`** | Handoff doc notes optional follow-up: propagate `langfuse_trace_id` into graph state for stronger hub ↔ Langfuse correlation. |

### 2.5 Observability stack in the agent today

- **Langfuse**: LangChain `CallbackHandler` in `scheduling.py` / run path when `LANGFUSE_*` keys set — traces generations, tool usage, latency in **Langfuse’s** product/API.
- **LangGraph**: graph structure, checkpoints, `GET /api/v1/traces/{thread_id}` when checkpointing enabled (503 otherwise).
- **First-party DB telemetry**: `ToolCallEvent` rows and `Incident` rows written during graph execution (see `finalize.py`, instrumentation).

### 2.6 Local / infra in repo

- **Docker Compose**: Postgres, LocalStack (SQS + IAM + STS + Secrets Manager for Terraform), optional hub/worker/agent images.
- **Terraform**: `infra/localstack/` for local emulated AWS; **`infra/backend/`** exists as an AWS-oriented stack (may still reflect earlier ALB-centric hub assumptions — **hub is now intended to move to App Runner** per product discussion).

---

## 3. Deployment direction (as agreed in conversation)

| Service | Target | Rationale (summary) |
| --- | --- | --- |
| **Hub** | **AWS App Runner** | Managed HTTPS, scaling, good fit for **browser + OAuth + Gmail webhook** traffic; VPC connector for private RDS. |
| **Worker** | **Amazon ECS (Fargate)** | SQS-driven, no public HTTP; task role for SQS, RDS, Secrets Manager, outbound HTTPS. |
| **Agent** | **TBD (see §4–§4.4)** | Not browser-first; must remain reachable by **worker** (e.g. `POST /api/v1/runs`). **Hub → agent** today is only Gmail watch notify (§2.2.1); may be **removable** if replaced by DB/SQS/worker patterns (§4.4). |

---

## 4. The agent deployment dilemma

### 4.1 Facts

- Agents **usually do not** serve **end-user** clients directly (no “tenant user opens agent URL” as primary UX).
- Agents **do** expose **HTTP** used by:
  - **Worker** — e.g. `POST /api/v1/runs`.
  - **Hub** — **no** outbound HTTP to agents (`backend/`). Gmail uses **`integrations.watch_active`** only.
- Agents **call hub** on `/internal/*` with service token.

### 4.2 Tension

- **Private-only agent** (internal NLB, Service Discovery, same VPC): minimizes attack surface; **worker and hub** must resolve DNS and have **security groups** allowing egress/ingress to the agent port. **No public ALB** required if no third party must call the agent directly.
- **Public agent** (internet ALB): only needed if **external systems** must hit the agent URL (e.g. some Gmail/agent webhook setups). Current **Pub/Sub → hub** path avoids requiring a public agent for Gmail.

### 4.3 Questions for LLM

1. Given **App Runner (hub)** + **ECS (worker)**, what is the **minimal secure pattern** for **ECS (agent)** (private vs public, NLB vs Cloud Map, port, mTLS vs bearer-only)?
2. How should **`Deployment.base_url`** be set in production: internal DNS only, split internal/public URLs, or single URL behind split-horizon DNS?
3. What changes if a **future** integration requires **internet → agent** webhooks?

### 4.4 Is hub → agent `gmail-watch-active` necessary? Can the agent be free of inbound HTTP from outside?

**Goal some stakeholders want:** agent receives **no HTTP initiated from outside** the agent process boundary — interpretable as: (a) **no calls from the public internet**, (b) **no calls from the hub at all**, or (c) **no inbound HTTP whatsoever** (only outbound + non-HTTP triggers). These differ.

**What the notify call does today:** it flips agent-side behavior so **polling** for Gmail can stop when **hub Pub/Sub** is authoritative. Without it, the agent may still poll Gmail (wasted quota / duplicate paths) unless polling is disabled by other means (env, deploy-time flag, or reading hub state from DB — which the agent does not do today).

**Removal / replacement options to explore:**

| Approach | Effect on “hub never HTTPs to agent” | Notes |
| --- | --- | --- |
| **Drop notify; disable polling when `GMAIL_PUSH` / integration flag in shared DB** | Hub never calls agent; agent reads **Postgres** (or SQS message) for “push active” | Requires agent DB access and a clear source of truth row the hub updates after watch. |
| **Enqueue SQS job `gmail_watch_active` handled only inside worker** | Hub → SQS only; **worker** calls agent (still inbound HTTP to agent from worker) | Removes **hub** as HTTP client; agent still has HTTP from worker. |
| **Keep notify** | Hub must reach agent URL | Simplest today; tight coupling and extra network path from App Runner → private agent. |

**Questions for LLM**

1. Is **`POST /internal/gmail-watch-active`** **strictly necessary** for correctness, or is it an **optimization** that can be removed if polling is always off when integrations + watch are live in DB?
2. If we **strip** hub → agent HTTP entirely, what is the **simplest alternative** that preserves correct Gmail behavior (no double-fetch, no stale poll): **DB signal**, **SQS-only**, or **worker-only** fan-out?
3. If the requirement is **“no inbound HTTP from the internet”** but worker → agent is OK, does **private VPC-only** listener on the agent satisfy security, with hub replaced by **worker** as the only in-VPC HTTP client besides optional internal tools?
4. If the requirement is **“no inbound HTTP at all”** to the agent container, is the realistic pattern **“agent is not an HTTP server”** (e.g. **Lambda** or **always-pull** from queue inside the agent runtime) — and what is the migration cost vs current FastAPI + worker `POST /runs`?

---

## 5. “Real-time” agent decisions and actions on the client UI

### 5.1 Product ask

- A **feature** should show **agent decisions and actions** in **(near) real time** on a **client UI** (tenant-facing dashboard).
- Separately, the team agreed **Langfuse data can be summarized into DB** for the **hub** to serve the **frontend** (async / pull model).

### 5.2 Dilemma

- **DB + hub polling** gives **eventual** visibility (seconds–minutes latency) unless rollups run very frequently or the UI polls aggressively.
- **True real-time** (sub-second updates) usually needs **push**: WebSockets or **SSE** from hub, or **client** subscription to a **stream** fed by agent/worker events (message bus, change data capture, or Langfuse live APIs).

### 5.3 What Langfuse + LangGraph provide (and what they do not)

| Capability | Langfuse / LangGraph | Gap for _your_ UI |
| --- | --- | --- |
| **Traces, spans, generations, tool calls, costs** | Langfuse UI and **Langfuse HTTP API** when keys/project exist. LangChain callback integration already in agent. | **Not** your branded tenant UI unless you **embed** Langfuse or **proxy** its API with strict auth/Z mapping. |
| **LangGraph structure / checkpoints** | Good for **debugging** and **HITL** flows; optional trace endpoint on agent. | Same: data lives in **agent/Langfuse**, not automatically in **hub Postgres** in a dashboard-ready shape. |
| **Per-tenant “live ticker” of decisions** | Langfuse updates as the run executes **inside Langfuse**; latency is low **there**. | Getting the same into **your React app** requires **explicit integration** (poll Langfuse API, webhook from Langfuse if available, or **emit events to your own bus/table** from graph nodes). |

### 5.4 Questions for LLM

1. For **“almost real-time”** (e.g. &lt;5s) **decisions/actions** in **our** UI, recommend a **concrete architecture**: e.g. append-only **`agent_run_events`** table + **SSE** from hub vs **Langfuse API polling** vs **EventBridge** fan-out from worker/agent.
2. How should **authorization** work so tenant A never sees tenant B’s traces (Langfuse project-per-tenant vs tags vs hub-only BFF)?
3. Does **Langfuse** offer **webhooks** or **streaming APIs** suitable for pushing into our stack, or is **instrumentation in graph nodes** (writing to Postgres + optional Redis pub/sub) the more reliable path?

---

## 6. Aggregated data: agent dashboard vs tenant overview

### 6.1 Surfaces

| Dashboard | Typical questions | Candidate data sources (today / planned) |
| --- | --- | --- |
| **Per-agent** | Runs, errors, latency, tool usage, “what did it decide?” | **`incidents`**, **`tool_call_events`**, **`jobs`**, Langfuse API, future **`metric_snapshots`**. |
| **Tenant overview** | Usage across agents, health, spend, triage volume | **Aggregate** `metric_snapshots`, COUNT/SUM over `incidents` / `jobs`, Langfuse aggregated metrics (via worker rollup). |

### 6.2 Ways to obtain aggregated data (design space)

1. **Hub reads Postgres only** — Implement rollup jobs + APIs over **`metric_snapshots`**, **`incidents`**, **`tool_call_events`**, **`jobs`**. Best for **single source of truth** in your product DB.
2. **Hub BFF calls Langfuse API** — On-demand charts; watch **rate limits**, **latency**, and **tenant isolation**.
3. **Hybrid** — **Hot** recent data in Postgres (last N hours), **cold** history from Langfuse or S3 exports.
4. **Worker `metrics_rollup`** — Implement stub: pull Langfuse aggregates (per tenant/project/tags), write **`metric_snapshots`**.

### 6.3 Questions for LLM

1. Propose **one recommended MVP** and **one scale-up path** for aggregated tenant + agent dashboards using **`metric_snapshots` + `tool_call_events` + `incidents`** vs Langfuse API.
2. Define **exact KPI keys** for `metric_snapshots.metrics` JSONB (e.g. `run_count`, `error_rate`, `p95_latency_ms`, `tokens_total`, `hours_saved_estimate`) aligned with incident triage.
3. How often should **`metrics_rollup`** run (cron, EventBridge schedule, post-job hook)?

---

## 7. Simplest metrics obtainable with _current_ setup (minimal new code)

Without implementing rollup yet, the **simplest** factual metrics come from **Postgres** and **`jobs`**:

| Metric | Source | Notes |
| --- | --- | --- |
| **Job volume / backlog** | `jobs` filtered by `tenant_id`, `job_type`, `status`, `created_at` | Already authoritative. |
| **Job failures** | `jobs.status`, `error_message` | Surface recent failures per tenant/agent. |
| **Incidents triaged** | `incidents` count per tenant/agent/time | Requires agent writing rows (`finalize` path). |
| **Tool / node activity** | `tool_call_events` count, `decision`, `node_name`, error rate | Requires instrumentation writes during runs. |
| **Trace drill-down link** | `incidents.langfuse_trace_id` | Optional link out to Langfuse UI or API fetch. |

**Langfuse** adds richer **token/cost/latency** and **nested spans** but requires **keys + project strategy** and **hub policy** on whether the frontend talks to Langfuse at all.

---

## 8. Consolidated questions for the LLM (checklist)

Use these as a prompt appendix.

### Deployment

1. **Hub on App Runner + Worker on ECS + Agent on ECS (private)** — validate SG/VPC connector/DNS layout and list **Terraform** module boundaries per service.
2. **`Deployment.base_url`** — internal-only URL format and how **hub** and **worker** should each resolve it in prod.
3. **Remove hub → agent `POST /internal/gmail-watch-active`** — design the replacement (§4.4): DB-backed flag, SQS job from hub, or worker-only notify; assess polling / duplicate-work risk.

### Dashboards and data

4. **MVP** for **tenant overview** using only **`jobs` + `incidents` + `tool_call_events`** — SQL sketches and hub API shapes.
5. **Implementation spec** for **`metrics_rollup`** (inputs, Langfuse queries, idempotency, `metric_snapshots` row shape, scheduling).
6. **Near–real-time “actions” feed** — choose among: Postgres tail + SSE, Langfuse polling, bus + worker publisher, or LangGraph streaming hooks.

### Langfuse / LangGraph

7. What is the **minimal Langfuse project/tag model** for multi-tenant isolation matching **`tenant_id`** / **`agent_id`** already used in callbacks?
8. Does **LangGraph** streaming (`astream_events` / v2 APIs) belong **in the agent** only, with **events forwarded** to hub, or should the **client** never talk to the agent directly?

### Security and compliance

9. What must **never** leave the agent or hub in dashboard APIs (PII in `raw_subject`, tokens in Langfuse metadata, etc.)?
10. Should **`tool_call_events`** be **tenant-scoped** in all hub queries by construction (RLS vs application-level checks)?

### Product

11. Define **“real-time enough”** SLAs (e.g. 5s vs 60s) per dashboard widget to drive infra cost.

---

## 9. References (in-repo)

- `docs/deployment-and-dashboard-instructions.md` — **authoritative** deployment + dashboard spec (supersedes conflicting notes).
- `backend/apis/integrations_gmail.py` — Gmail OAuth; sets `integrations.watch_active`.
- `agents/incident-triage/src/incident_triage/triggers/poller.py` — reads `watch_active` to skip polling.
- `docs/plan.md` — KPI rollups, Langfuse, dashboard direction.
- `docs/INCIDENT_TRIAGE_AGENT_HANDOFF.md` — implemented agent surfaces and gaps.
- `packages/agent-hub-core/src/agent_hub_core/db/models.py` — `MetricSnapshot`, `ToolCallEvent`, `Incident`, `Job`.
- `worker/handlers/metrics_rollup.py` — rollup job stub.
- `agents/incident-triage/.../scheduling.py` — Langfuse callback wiring.
- `README.md` — local stack, SQS contract.

---

_Document generated to support cross-team / LLM-assisted architecture decisions; update as implementation lands._
