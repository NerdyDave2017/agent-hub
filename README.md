# agent-hub

Control-plane hub (`backend/`), async worker (`worker/`), and agents — see [`docs/plan.md`](docs/plan.md) and [`Agent.md`](Agent.md).

## UV workspace (Phase 1 + 2)

The repo root [`pyproject.toml`](pyproject.toml) defines a **[`uv` workspace](https://docs.astral.sh/uv/concepts/projects/workspaces/)** with members:

| Member | Role |
| --- | --- |
| [`packages/agent-hub-core`](packages/agent-hub-core/) | Shared kernel (`import agent_hub_core`) — settings, async DB, ORM, Alembic, domain, Pydantic schemas, SQS envelope + client helpers. |
| [`backend/`](backend/) | FastAPI hub only (`main`, `apis/`, `services/`). |
| [`worker/`](worker/) | SQS worker (`python -m worker`). |

From the **repository root**:

```bash
uv sync
```

That creates `.venv/` and installs **agent-hub-core**, **agent-hub-backend**, and **agent-hub-worker** in editable mode. Examples:

```bash
uv run --package agent-hub-backend --directory backend uvicorn main:app --reload --host 0.0.0.0 --port 8000

uv run --package agent-hub-core alembic -c packages/agent-hub-core/alembic.ini upgrade head
```

Run the worker (no `PYTHONPATH`):

```bash
uv run python -m worker
```

`pydantic-settings` loads `./.env` first, then `backend/.env` when that file exists, so keeping secrets in `backend/.env` still works from the repo root.

## Hub → worker (SQS) message contract

After the hub commits a row in `jobs`, it will publish **one JSON object** per message (same shape in **local** queues and **AWS SQS**).

| Field | Type (JSON) | Meaning |
| --- | --- | --- |
| `job_id` | string (UUID) | Primary key of `jobs.id` — worker should load/update this row. |
| `tenant_id` | string (UUID) | Owning tenant; must match the `jobs` row. |
| `job_type` | string | Routing key (e.g. `agent_provisioning`); same family as `Job.job_type`. |
| `correlation_id` | string or `null` | Same as hub `X-Correlation-ID` / `jobs.correlation_id` for log correlation. |
| `agent_id` | string (UUID) or `null` | Optional; mirrors `jobs.agent_id`. |
| `payload` | object or `null` | Non-secret JSON only — **no** tokens, passwords, or `Authorization`-like keys. |

**Rules:** the queue body is **not** a second source of truth — treat Postgres as authoritative for status and retries. **Never** put secrets in `payload` or anywhere in the message.

The canonical Pydantic model (serialize with `model_dump(mode="json")`) is [`packages/agent-hub-core/src/agent_hub_core/messaging/envelope.py`](packages/agent-hub-core/src/agent_hub_core/messaging/envelope.py) (`JobQueueEnvelope`).

## Local SQS (LocalStack)

### SQS main queue vs DLQ (how the worker fits in)

**Standard SQS queue (`agent-hub-jobs`)** — This is the **primary pipe** between hub and worker. The hub calls `SendMessage` here after it commits a job row. Your worker long-polls this queue with `ReceiveMessage`, processes the payload (often by loading `job_id` from Postgres), then calls `DeleteMessage` on success. **The worker is subscribed only to this queue URL** (`SQS_QUEUE_URL`).

**Dead-letter queue (`agent-hub-jobs-dlq`)** — This is **not** where the hub sends work directly. AWS (or LocalStack) **moves** messages here automatically when a message has been **received and not deleted** more than `maxReceiveCount` times (Terraform sets `5` on the main queue’s redrive policy). Typical reasons: your handler keeps throwing, the worker crashes before `DeleteMessage`, or the message is malformed and you `ChangeMessageVisibility` / let it time out until the cap is hit.

So: **hub → main queue → worker** is the happy path. **Main queue → DLQ** is AWS’s safety net for “this message is poison or the code is broken,” so one bad job does not block the queue forever. In ops you inspect the DLQ (replay, fix data, discard); the normal worker process still only **polls the main queue** unless you deliberately add a second consumer for DLQ inspection.

**AWS worker (ECS + EventBridge)** — Production worker stack lives in [`infra/worker/`](infra/worker/) (`modules/ecs-worker` + scheduled rules in `main.tf`). See [`docs/terraform-infra-instructions.md`](docs/terraform-infra-instructions.md), [`infra/worker/README.md`](infra/worker/README.md), and [`docs/deployment-and-dashboard-instructions.md`](docs/deployment-and-dashboard-instructions.md) §7.3.

**Terraform + Makefile (source of truth)** — Queues (`modules/sqs`), secrets (`modules/secrets`), and IAM (App Runner trust for hub, ECS for worker/agent) are composed in [`infra/localstack/`](infra/localstack/). Full layout: [`infra/README.md`](infra/README.md). From the repo root:

```bash
make local-up          # postgres + localstack
make local-provision   # wait for LocalStack → terraform apply → writes localstack.auto.env
```

`localstack.auto.env` holds `SQS_QUEUE_URL` / `SQS_DLQ_URL` from Terraform (LocalStack may use a different host/path than the legacy defaults). Use it with Compose:

```bash
docker compose --env-file localstack.auto.env up -d hub worker
# or: make local-apps-up
```

| Queue | Purpose |
| --- | --- |
| `agent-hub-jobs` | Main work queue — hub `SendMessage`, worker `ReceiveMessage`. |
| `agent-hub-jobs-dlq` | Dead-letter queue — `RedrivePolicy` on the main queue (`maxReceiveCount: 5`). |

**Confirm queues**

```bash
docker compose exec localstack awslocal sqs list-queues
```

**Queue URLs** — Prefer values from `localstack.auto.env` or:

```bash
cd infra/localstack && terraform output -raw sqs_queue_url
```

**Environment variables (hub / worker, local)**

| Variable | Local example | Production |
| --- | --- | --- |
| `AWS_ENDPOINT_URL` | `http://localhost:4566` (host) or `http://localstack:4566` (another compose service) | **Unset** — real AWS endpoint |
| `AWS_REGION` | `us-east-1` | Your region |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | `test` / `test` (LocalStack; **required** there because boto3 has no IAM role on your laptop) | **Omit** on ECS so the task role is used |
| `SQS_QUEUE_URL` | Full URL for `agent-hub-jobs` (see above) | Real SQS queue URL |
| `SQS_DLQ_URL` | DLQ URL (optional on hub; useful for ops scripts) | Real DLQ URL |

These names match [`packages/agent-hub-core/src/agent_hub_core/config/settings.py`](packages/agent-hub-core/src/agent_hub_core/config/settings.py) (`pydantic-settings`). The hub will use the same boto3 client shape locally and in AWS — only `AWS_ENDPOINT_URL` and credentials differ, per [`docs/plan.md`](docs/plan.md).

**Hub enqueue:** when `SQS_QUEUE_URL` is set, `POST /api/v1/tenants/{tenant_id}/jobs` commits the `jobs` row, then calls `SendMessage` with a **`JobQueueEnvelope`** ([`messaging/envelope.py`](packages/agent-hub-core/src/agent_hub_core/messaging/envelope.py)). On success the row becomes **`queued`**; if SQS is misconfigured the row stays **`pending`** and the hub logs a warning with `job_id` / `correlation_id`. If `SQS_QUEUE_URL` is **unset**, rows stay **`pending`** (useful while you only exercise the REST + DB layer).

**Init hooks** — [`scripts/localstack-init/ready.d/`](scripts/localstack-init/ready.d/) still mounts into the container; queue creation is **not** done there anymore (Terraform owns SQS). Keep scripts **executable** if you add new `ready.d` steps (`chmod +x scripts/localstack-init/ready.d/*.sh`).

## Smoke test: Postgres + hub + LocalStack SQS

Goal: prove **migrations**, **hub → DB**, and **hub → SQS** in one pass (no worker required for the queue part).

**1. Start Postgres + LocalStack and provision AWS emulators**

```bash
make local-up
make local-provision
```

**2. Configure the hub (`backend/.env` or your shell)**

Use a DB URL that matches compose (from your laptop, not from inside a container). Queue URLs: copy from `make local-print-env` or `cat localstack.auto.env`.

```bash
export DATABASE_URL='postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/postgres'
export AWS_REGION=us-east-1
export AWS_ENDPOINT_URL=http://127.0.0.1:4566
export AWS_ACCESS_KEY_ID=test
export AWS_SECRET_ACCESS_KEY=test
export SQS_QUEUE_URL='<from localstack.auto.env or terraform output -raw sqs_queue_url>'
```

**3. Run migrations**

From the **repository root** (with the workspace `.venv`):

```bash
uv run --package agent-hub-core alembic -c packages/agent-hub-core/alembic.ini upgrade head
```

**4. Run the API**

From the **repository root**:

```bash
uv run --package agent-hub-backend --directory backend uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

**5. Exercise the API**

Create a tenant, then an agent (that path enqueues **`agent_provisioning`**):

```bash
BASE=http://127.0.0.1:8000/api/v1
TENANT=$(curl -sS -X POST "$BASE/tenants" -H 'Content-Type: application/json' \
  -d '{"name":"Demo Org","slug":"demo-org"}' | jq -r .id)
curl -sS -X POST "$BASE/tenants/$TENANT/agents" -H 'Content-Type: application/json' \
  -H "X-Correlation-ID: smoke-test-1" \
  -d '{"agent_type":"incident_triage","name":"Demo agent"}' | jq .
```

**6. Verify Postgres**

Connect with `psql` or a GUI: you should see a row in **`jobs`** with `job_type = agent_provisioning`, `status` **`queued`** if SQS send succeeded, or **`pending`** if `SQS_QUEUE_URL` / LocalStack was wrong (check hub logs).

**7. Verify SQS (optional)**

Peek one message (then it becomes visible again after timeout if you do not delete):

```bash
docker compose exec localstack awslocal sqs receive-message \
  --queue-url "$SQS_QUEUE_URL" --max-number-of-messages 1
```

You should see a JSON body matching **`JobQueueEnvelope`** (see [`messaging/envelope.py`](packages/agent-hub-core/src/agent_hub_core/messaging/envelope.py)) (`job_id`, `tenant_id`, `correlation_id`, etc.).

## Worker (slice 1 — SQS + envelope + DB ping)

The worker lives under [`worker/`](worker/): **orchestration** in [`worker/main.py`](worker/main.py), **structlog → JSON** via [`agent_hub_core.observability.logging`](packages/agent-hub-core/src/agent_hub_core/observability/logging.py) (UTC `timestamp`, `pathname` / `filename` / `lineno` / `func_name`, default `service`), **SQS receive/delete** in [`worker/queue/sqs_receive.py`](worker/queue/sqs_receive.py), and **job dispatch** under [`worker/handlers/`](worker/handlers/) (`registry.py` + per-`JobType` handlers; AWS adapters scaffolded in [`worker/handlers/aws/`](worker/handlers/aws/)).

**Behaviour:** DB ping on startup; long-poll SQS; validate **`JobQueueEnvelope`**; load **`jobs`** row; run the registered handler (stub transitions for now); **`DeleteMessage`** only after the handler completes without raising (malformed envelope, missing job, or handler errors leave the message for retry / DLQ).

From the **repository root** (with Postgres + LocalStack already up and env vars set like the hub smoke test):

```bash
uv run python -m worker
```

Use the same `DATABASE_URL`, `SQS_QUEUE_URL`, and AWS variables as the hub. Stop the process with Ctrl+C; the pool is disposed in a `finally` block.
