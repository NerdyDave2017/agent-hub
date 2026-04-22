# agent-hub

Control-plane hub (`backend/`), async worker (`worker/`), and agents — see [`docs/plan.md`](docs/plan.md) and [`Agent.md`](Agent.md).

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

The canonical Pydantic model (serialize with `model_dump(mode="json")`) is [`backend/schemas/sqs_job_envelope.py`](backend/schemas/sqs_job_envelope.py) (`JobQueueEnvelope`).

## Local SQS (LocalStack)

### SQS main queue vs DLQ (how the worker fits in)

**Standard SQS queue (`agent-hub-jobs`)** — This is the **primary pipe** between hub and worker. The hub calls `SendMessage` here after it commits a job row. Your worker long-polls this queue with `ReceiveMessage`, processes the payload (often by loading `job_id` from Postgres), then calls `DeleteMessage` on success. **The worker is subscribed only to this queue URL** (`SQS_QUEUE_URL`).

**Dead-letter queue (`agent-hub-jobs-dlq`)** — This is **not** where the hub sends work directly. AWS (or LocalStack) **moves** messages here automatically when a message has been **received and not deleted** more than `maxReceiveCount` times (we set `5` in the init script). Typical reasons: your handler keeps throwing, the worker crashes before `DeleteMessage`, or the message is malformed and you `ChangeMessageVisibility` / let it time out until the cap is hit.

So: **hub → main queue → worker** is the happy path. **Main queue → DLQ** is AWS’s safety net for “this message is poison or the code is broken,” so one bad job does not block the queue forever. In ops you inspect the DLQ (replay, fix data, discard); the normal worker process still only **polls the main queue** unless you deliberately add a second consumer for DLQ inspection.

Compose runs **LocalStack** with **SQS only** and a **ready hook** that creates:

| Queue | Purpose |
| --- | --- |
| `agent-hub-jobs` | Main work queue — hub `SendMessage`, worker `ReceiveMessage`. |
| `agent-hub-jobs-dlq` | Dead-letter queue — `RedrivePolicy` on the main queue (`maxReceiveCount: 5`). |

**Start LocalStack**

```bash
docker compose up -d localstack
```

Wait until `docker compose ps` shows `localstack` healthy (first pull can take a few minutes).

**Confirm queues**

```bash
docker compose exec localstack awslocal sqs list-queues
```

**Queue URLs (typical LocalStack path-style)**

With default account `000000000000` and region `us-east-1`, URLs usually look like:

- Main: `http://localhost:4566/000000000000/agent-hub-jobs`
- DLQ: `http://localhost:4566/000000000000/agent-hub-jobs-dlq`

If your LocalStack build returns a different host (e.g. `sqs.us-east-1.localhost.localstack.cloud:4566`), use the URL from:

```bash
docker compose exec localstack awslocal sqs get-queue-url --queue-name agent-hub-jobs --output text
```

**Environment variables (hub / worker, local)**

| Variable | Local example | Production |
| --- | --- | --- |
| `AWS_ENDPOINT_URL` | `http://localhost:4566` (host) or `http://localstack:4566` (another compose service) | **Unset** — real AWS endpoint |
| `AWS_REGION` | `us-east-1` | Your region |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | `test` / `test` (LocalStack; **required** there because boto3 has no IAM role on your laptop) | **Omit** on ECS so the task role is used |
| `SQS_QUEUE_URL` | Full URL for `agent-hub-jobs` (see above) | Real SQS queue URL |
| `SQS_DLQ_URL` | DLQ URL (optional on hub; useful for ops scripts) | Real DLQ URL |

These names match [`backend/core/settings.py`](backend/core/settings.py) (`pydantic-settings`). The hub will use the same boto3 client shape locally and in AWS — only `AWS_ENDPOINT_URL` and credentials differ, per [`docs/plan.md`](docs/plan.md).

**Hub enqueue:** when `SQS_QUEUE_URL` is set, `POST /api/v1/tenants/{tenant_id}/jobs` commits the `jobs` row, then calls `SendMessage` with a [`JobQueueEnvelope`](backend/schemas/sqs_job_envelope.py). On success the row becomes **`queued`**; if SQS is misconfigured the row stays **`pending`** and the hub logs a warning with `job_id` / `correlation_id`. If `SQS_QUEUE_URL` is **unset**, rows stay **`pending`** (useful while you only exercise the REST + DB layer).

**Init script**

Hooks live under [`scripts/localstack-init/ready.d/`](scripts/localstack-init/ready.d/) and mount into the container as `/etc/localstack/init/ready.d/`. The shell script must stay **executable** (`chmod +x scripts/localstack-init/ready.d/01-init-sqs.sh`) so LocalStack can run it on boot.

## Smoke test: Postgres + hub + LocalStack SQS

Goal: prove **migrations**, **hub → DB**, and **hub → SQS** in one pass (no worker required for the queue part).

**1. Start Postgres + LocalStack**

```bash
docker compose up -d postgres localstack
```

Wait until both are healthy. Grab the main queue URL:

```bash
docker compose exec localstack awslocal sqs get-queue-url --queue-name agent-hub-jobs --output text
```

**2. Configure the hub (`backend/.env` or your shell)**

Use a DB URL that matches compose (from your laptop, not from inside a container):

```bash
export DATABASE_URL='postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/postgres'
export AWS_REGION=us-east-1
export AWS_ENDPOINT_URL=http://127.0.0.1:4566
export AWS_ACCESS_KEY_ID=test
export AWS_SECRET_ACCESS_KEY=test
export SQS_QUEUE_URL='<paste queue URL from step 1>'
```

**3. Run migrations**

From `backend/`:

```bash
uv run alembic upgrade head
```

**4. Run the API**

From `backend/`:

```bash
uv run uvicorn main:app --reload --host 0.0.0.0 --port 8000
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

You should see a JSON body matching [`JobQueueEnvelope`](backend/schemas/sqs_job_envelope.py) (`job_id`, `tenant_id`, `correlation_id`, etc.).
