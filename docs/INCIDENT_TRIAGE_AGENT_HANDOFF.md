# Incident triage agent — handoff for the next implementer

This document summarizes what is already built and what to do next. Primary spec: `docs/incident-triage-agent-spec.md`. Platform context: `docs/plan.md`.

---

## Implemented

| Area | Location / notes |
| --- | --- |
| **App shell** | `agents/incident-triage/src/incident_triage/main.py` — lifespan: `resolve_secrets()` → SQLAlchemy + `init_agent_schema` → psycopg `AsyncConnectionPool` → `AsyncPostgresSaver.setup()` → `build_graph(checkpointer)`; optional Gmail poll task; correlation middleware; `/health`; `/api/v1/meta`; `POST /api/v1/runs`; Gmail webhooks; `GET /api/v1/traces/{thread_id}` (503 if checkpointing disabled). |
| **Settings** | `agents/incident-triage/src/incident_triage/settings.py` — tenant/agent/hub/Langfuse/Slack, ARNs, resolved secrets, `get_settings()`. |
| **Agent DB** | `agents/incident-triage/src/incident_triage/db/` — async engine/session, `ProcessedEmail` on `LocalBase`, `psycopg_conninfo` for LangGraph DSN. |
| **Core DB models** | `packages/agent-hub-core/src/agent_hub_core/db/models.py` — `ToolCallEvent`, `Incident`. Migrations: `0003_tool_call_events`, `0004_incidents` under `packages/agent-hub-core/src/agent_hub_core/migrations/versions/`. **Run `alembic upgrade head`** where hub migrations are applied. |
| **Graph** | `graph/state.py` — `TriageState` (incl. `duplicate_message`, `messages` + `add_messages`). `graph/builder.py` — fetch → dedup → (continue → enrich → … or stop → END), classify → Slack vs finalize. |
| **Dedup** | `graph/nodes/dedup.py` — real `ProcessedEmail` row + duplicate / `IntegrityError` handling; no DB → no-op `{}`. |
| **Enrich** | `graph/nodes/enrich.py` — `httpx` to hub `/internal/tenants/{id}` and `/internal/tenants/{id}/incidents/recent` when `HUB_BASE_URL` + `HUB_SERVICE_TOKEN` set. |
| **Hub internal API** | `backend/apis/internal.py` — Bearer `INTERNAL_SERVICE_TOKEN` (must match agent `HUB_SERVICE_TOKEN`). `backend/main.py` mounts router at `/internal`. |
| **Gmail Pub/Sub (hub)** | `backend/apis/integrations_gmail.py` — OAuth start + callback with `users.watch()`, Secrets Manager upsert, `integrations` row. `backend/apis/webhooks_gmail.py` — `POST /webhooks/gmail/push` (query `token` vs `GMAIL_WEBHOOK_SECRET`), enqueues `gmail_history_sync`. Env: `HUB_PUBLIC_URL`, `GMAIL_*`, `GCP_PROJECT_ID`, `INCIDENT_TRIAGE_AGENT_URL` (worker → agent). See `gmail-pubsub-implementation.md`. |
| **Gmail jobs (worker)** | `gmail_history_sync`, `gmail_process_message`, `gmail_watch_renewal` in `worker/handlers/` + `worker/messaging/enqueue.py` for child jobs. |
| **Instrumentation** | `instrumentation/decorator.py` — `@traced_node`; `instrumentation/events.py` — `write_tool_event` → `tool_call_events` (separate session; failures ignored). |
| **Classify + Langfuse** | `graph/nodes/classify.py` — `ChatOpenAI` + structured output when `OPENAI_API_KEY` (or `OPENAI_SECRET_ARN`) is set; else stub. `main.py` `POST …/runs` passes `CallbackHandler` + `metadata` (`langfuse_user_id` = tenant, `langfuse_session_id` = message id) + tags when Langfuse keys are set. |
| **Slack** | `integrations/slack.py` — `post_message` via `slack_sdk.WebClient` + `asyncio.to_thread`. `graph/nodes/tools/slack_tool.py` — posts mrkdwn alert to `SLACK_OPS_CHANNEL` when `SLACK_BOT_TOKEN` is set; otherwise `slack_sent=False` and `slack:skipped_no_token`. |
| **Gmail** | `integrations/gmail.py` — OAuth user JSON (`refresh_token` + `client_id` + `client_secret`); `fetch_message` / `mark_as_read` / `list_unread` via `asyncio.to_thread`. `fetch.py` loads real message when creds set. `mark_read.py` after `finalize` removes `UNREAD` when `GMAIL_MARK_READ` (default on). `scheduling.py` — shared `schedule_graph_run` / `invoke_graph_run`. `triggers/poller.py` + lifespan when `GMAIL_POLL_INTERVAL_SECONDS` > 0. Webhooks: `POST …/webhooks/gmail/message`, `POST …/webhooks/gmail/pubsub` (schedules only if JSON contains `messageId`). |
| **Stubs** | Pub/Sub default payload has **no** `messageId` — use history sync or bridge to `…/webhooks/gmail/message` until extended. |

---

## Recommended next steps (order)

1. ~~**`finalize.py`**~~ — **Done:** `Incident` insert via `get_session()` when `tenant_id` is a valid UUID; `IntegrityError` rollback; `raw_subject` / `raw_sender` from `raw_email` only.
2. ~~**Classify + Langfuse**~~ — **Done:** `langchain-openai` + `langfuse` + `langchain` deps; structured classify + `CallbackHandler` on graph `ainvoke`. Optional follow-up: propagate `langfuse_trace_id` into `TriageState` for `Incident.langfuse_trace_id`.
3. ~~**Slack**~~ — **Done:** `integrations/slack.py` + `slack_tool.py`; `slack-sdk` dependency.
4. ~~**Gmail**~~ — **Done:** `integrations/gmail.py`, `fetch.py`, `mark_read` node, poller + webhooks, `GMAIL_*` settings. Follow-up: Gmail history API for real Pub/Sub → message ids; optional “mark read only if finalize succeeded”.
5. **Ops / tests** — Dockerfile/env docs for token alignment; tests for dedup, enrich (mocked hub), finalize; worker `metrics_rollup` + Langfuse (broader product, outside agent package).

---

## Gotchas

- **`tool_call_events` and `Incident`** expect UUID `tenant_id` / `agent_id`. Values like `"local"` skip telemetry inserts or will break DB writes until real UUIDs are used.
- LangGraph nodes should return **state update dicts** (or empty); do **not** return `END` from node functions — use conditional edges (as with dedup).
- Apply migrations so **`incidents`** and **`tool_call_events`** exist before relying on finalize or incident list APIs in production.

---

## Key files (quick map)

| Purpose | Path |
| --- | --- |
| FastAPI + lifespan + routes | `agents/incident-triage/src/incident_triage/main.py` |
| Env / secrets | `agents/incident-triage/src/incident_triage/settings.py` |
| Graph wiring | `agents/incident-triage/src/incident_triage/graph/builder.py` |
| State | `agents/incident-triage/src/incident_triage/graph/state.py` |
| Nodes | `agents/incident-triage/src/incident_triage/graph/nodes/*.py` |
| Hub internal reads | `backend/apis/internal.py` |
| Hub settings (internal token) | `packages/agent-hub-core/src/agent_hub_core/config/settings.py` |
| Shared ORM (`Incident`, `ToolCallEvent`) | `packages/agent-hub-core/src/agent_hub_core/db/models.py` |
| Node tracing | `agents/incident-triage/src/incident_triage/instrumentation/` |
| Slack client | `agents/incident-triage/src/incident_triage/integrations/slack.py` |
| Gmail + scheduling | `integrations/gmail.py`, `scheduling.py`, `triggers/poller.py`, `graph/nodes/mark_read.py` |
