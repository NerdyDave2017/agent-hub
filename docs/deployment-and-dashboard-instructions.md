# Agent Hub — Deployment Architecture & Dashboard Implementation Instructions

> **For the AI coding agent.** This document is the single source of truth for deployment topology, agent triggering, and dashboard implementation. Previous instructions that conflict with this document are superseded. Read every section before writing any code.

---

## 1. Settled Architecture Decisions (Non-Negotiable)

| Decision | Resolution |
| --- | --- |
| **Hub deployment** | AWS App Runner — managed HTTPS, auto-scaling, VPC connector for RDS |
| **Worker deployment** | AWS ECS Fargate — private, no inbound HTTP, SQS-driven |
| **Agent deployment** | AWS App Runner — managed HTTPS, accessible `base_url`, no VPC complexity |
| **Worker → Agent communication** | Worker HTTP POSTs to agent App Runner URL. **Only the worker may call agent URLs.** Hub never calls agents. |
| **Hub → Worker communication** | SQS only — hub enqueues jobs, worker consumes asynchronously |
| **Hub → Agent communication** | **None.** Hub never calls agents directly. Ever. |
| **`Deployment.base_url`** | App Runner public HTTPS URL, set by worker provisioner after deploy |
| **Gmail watch notify** | DB flag only — `integrations.watch_active`. No HTTP call from hub to agent. |
| **No per-agent SQS queues** | Worker calls agent App Runner URL directly via HTTP. Simpler, less AWS resources. |

### The Clean Call Graph

```
Browser/Tenant
    │ HTTPS
    ▼
App Runner (Hub)           — public, tenant-facing, OAuth, webhooks
    │ SQS only
    ▼
ECS Fargate (Worker)       — private, no inbound HTTP, job orchestrator
    │ HTTPS to App Runner URL
    ▼
App Runner (Agent)         — accessible URL, worker-only caller, autonomous runtime
    │ HTTPS to Hub /internal/*
    ▼
App Runner (Hub)           — read-only internal API for agent enrichment
```

**Rules enforced in code:**

- Hub has no `httpx` calls to any agent URL (except the now-deleted gmail-watch notify)
- Worker is the only service that reads `Deployment.base_url` and POSTs to it
- Agents call hub `/internal/*` read-only endpoints with a service bearer token
- Tenants never get agent URLs — they interact only with hub

### 1.1 Terraform repository layout

The repo under [`infra/`](../infra/) is ordered **modules first**, then **per-service roots**. Shared building blocks live under **`infra/modules/`**; service-specific IAM and runtime wiring live in each root.

```text
infra/
├── modules/
│   ├── vpc/               → VPC, subnets, NAT, VPC endpoints (SQS, ECR, SM)
│   ├── rds/               → RDS Postgres, subnet group, DB URL secret
│   ├── sqs/               → hub→worker queue + DLQ + queue policy (used by `infra/hub`)
│   ├── secrets/           → KMS + Langfuse + internal token secret resources
│   ├── app-runner/        → reusable App Runner service (hub + staging agents)
│   └── ecs-worker/        → worker Fargate cluster, task definition, service, IAM
├── localstack/            → development: emulated SQS + IAM + Secrets Manager (LocalStack)
├── vpc/                   → composition root: full VPC (remote state `vpc/terraform.tfstate`)
├── rds/                   → Postgres (depends on vpc + secrets)
├── secrets/               → KMS + operator secrets (remote state `secrets/terraform.tfstate`)
├── hub/                   → hub IAM, SQS, App Runner (see `docs/terraform-infra-instructions.md`)
├── worker/                → worker ECS + EventBridge (see `docs/terraform-infra-instructions.md`)
└── agents/
    └── incident-triage/   → ECR + optional staging App Runner (see `terraform-infra-instructions.md`)
```

**Development (this branch):** run Terraform only in [`infra/localstack/`](../infra/localstack/) (`make local-provision`) for queues and IAM shapes. Run the hub API, worker, and agents via **Docker Compose** against that stack.

**Production AWS:** compose **`infra/hub`**, **`infra/worker`**, and optional **`infra/agents/*`** with outputs from **`modules/vpc`**, **`modules/rds`**, **`modules/sqs`**, and **`modules/secrets`** as those modules gain real resources. The legacy **ECS + ALB hub** tree (`infra/backend/`) has been **removed** in favour of **App Runner** in `infra/hub/`.

See [`infra/README.md`](../infra/README.md) and the full specification in [`docs/terraform-infra-instructions.md`](terraform-infra-instructions.md).

---

## 2. `Deployment` Model Changes

Update `packages/agent-hub-core/src/agent_hub_core/db/models/deployment.py`:

```python
from sqlalchemy import Column, String, DateTime, ForeignKey, Enum
from sqlalchemy.dialects.postgresql import UUID
from datetime import datetime, timezone
from uuid import uuid4
from agent_hub_core.db.base import Base

class Deployment(Base):
    __tablename__ = "deployments"

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    agent_id        = Column(UUID(as_uuid=True), ForeignKey("agents.id"), index=True)
    tenant_id       = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), index=True)

    # App Runner specific
    service_arn     = Column(String)          # App Runner service ARN
    service_id      = Column(String)          # App Runner service ID
    base_url        = Column(String)          # https://{id}.{region}.awsapprunner.com
                                              # Worker uses this to POST /api/v1/runs
                                              # Hub reads this ONLY for health display

    status          = Column(String, default="provisioning")
    # provisioning | active | paused | failed | deprovisioned

    created_at      = Column(DateTime(timezone=True),
                             default=lambda: datetime.now(timezone.utc))
    updated_at      = Column(DateTime(timezone=True),
                             default=lambda: datetime.now(timezone.utc))
```

**Generate Alembic migration after updating the model.**

---

## 3. Worker: Agent Provisioning Handler (App Runner)

The worker creates the App Runner service. After it stabilises, it writes `base_url` to the `Deployment` row. This is the only place `base_url` is set.

File: `worker/handlers/provision.py`

```python
import boto3
import time
from sqlalchemy.ext.asyncio import AsyncSession
from agent_hub_core.db.models.job import Job
from agent_hub_core.db.models.deployment import Deployment
from agent_hub_core.db.models.agent import Agent
from agent_hub_core.domain.enums import JobStatus, AgentStatus
from worker.handlers.base import AbstractJobHandler
import structlog

logger = structlog.get_logger()

class AgentProvisioningHandler(AbstractJobHandler):
    async def execute(self, job: Job, session: AsyncSession) -> None:
        if job.status == JobStatus.SUCCEEDED:
            return  # idempotency guard

        payload  = job.payload
        agent_id = str(job.agent_id)

        await self._advance(job, session, step="creating_secrets")
        await self._ensure_secrets(payload)

        await self._advance(job, session, step="creating_app_runner_service")
        service_arn, service_url = await self._create_app_runner_service(
            agent_id, payload
        )

        await self._advance(job, session, step="waiting_for_health")
        await self._wait_for_running(service_arn)

        await self._advance(job, session, step="writing_deployment")

        # Update agent status
        agent = await session.get(Agent, job.agent_id)
        agent.status = AgentStatus.ACTIVE

        # Write deployment row
        existing = await session.scalar(
            select(Deployment).where(Deployment.agent_id == job.agent_id)
        )
        if existing:
            existing.service_arn = service_arn
            existing.base_url    = service_url
            existing.status      = "active"
            existing.updated_at  = datetime.now(timezone.utc)
        else:
            session.add(Deployment(
                agent_id    = job.agent_id,
                tenant_id   = job.tenant_id,
                service_arn = service_arn,
                base_url    = service_url,
                status      = "active",
            ))

        job.status = JobStatus.SUCCEEDED
        await session.commit()

        logger.info("agent_provisioned",
                    agent_id=agent_id,
                    base_url=service_url)

    async def _create_app_runner_service(
        self, agent_id: str, payload: dict
    ) -> tuple[str, str]:
        client = boto3.client("apprunner")

        short_id  = agent_id[:8]
        svc_name  = f"agent-incident-triage-{short_id}"

        response = client.create_service(
            ServiceName=svc_name,
            SourceConfiguration={
                "ImageRepository": {
                    "ImageIdentifier":     f"{payload['ecr_repo']}:{payload['ecr_tag']}",
                    "ImageRepositoryType": "ECR",
                    "ImageConfiguration": {
                        "Port": "8080",
                        "RuntimeEnvironmentVariables": {
                            # Plain config — not secret
                            "TENANT_ID":         payload["tenant_id"],
                            "AGENT_ID":          agent_id,
                            "HUB_BASE_URL":      payload["hub_base_url"],
                            "ENVIRONMENT":       payload["environment"],
                            "SLACK_OPS_CHANNEL": payload.get("slack_channel", "#ops-alerts"),
                            "LANGFUSE_HOST":     "https://cloud.langfuse.com",
                        },
                        "RuntimeEnvironmentSecrets": {
                            # App Runner resolves these from Secrets Manager at start
                            "GMAIL_SECRET_ARN":       payload["gmail_secret_arn"],
                            "SLACK_SECRET_ARN":       payload["slack_secret_arn"],
                            "HUB_TOKEN_SECRET_ARN":   payload["hub_token_secret_arn"],
                            "LANGFUSE_SECRET_ARN":    payload["langfuse_secret_arn"],
                            "DATABASE_SECRET_ARN":    payload["database_secret_arn"],
                        },
                    },
                },
                "AutoDeploymentsEnabled": False,
            },
            InstanceConfiguration={
                "Cpu":    "1 vCPU",
                "Memory": "2 GB",
                "InstanceRoleArn": payload["instance_role_arn"],
            },
            HealthCheckConfiguration={
                "Protocol":           "HTTP",
                "Path":               "/health",
                "Interval":           10,
                "Timeout":            5,
                "HealthyThreshold":   2,
                "UnhealthyThreshold": 3,
            },
        )

        service_arn = response["Service"]["ServiceArn"]
        service_url = f"https://{response['Service']['ServiceUrl']}"
        return service_arn, service_url

    async def _wait_for_running(self, service_arn: str, timeout: int = 600) -> None:
        """Poll App Runner until service is RUNNING or timeout."""
        client   = boto3.client("apprunner")
        deadline = time.time() + timeout

        while time.time() < deadline:
            resp   = client.describe_service(ServiceArn=service_arn)
            status = resp["Service"]["Status"]

            if status == "RUNNING":
                return
            if status in ("CREATE_FAILED", "DELETE_FAILED", "PAUSED"):
                raise RuntimeError(f"App Runner service entered bad state: {status}")

            logger.info("waiting_for_app_runner", status=status)
            time.sleep(15)

        raise TimeoutError("App Runner service did not reach RUNNING within timeout")

    async def _ensure_secrets(self, payload: dict) -> None:
        """
        Verify all required secret ARNs in payload exist in Secrets Manager.
        Raises if any are missing — fail fast before creating App Runner service.
        """
        sm       = boto3.client("secretsmanager")
        required = [
            "gmail_secret_arn", "slack_secret_arn",
            "hub_token_secret_arn", "langfuse_secret_arn",
            "database_secret_arn",
        ]
        for key in required:
            arn = payload.get(key)
            if not arn:
                raise ValueError(f"Missing required secret ARN in payload: {key}")
            sm.describe_secret(SecretId=arn)  # raises if not found
```

---

## 4. Worker: Triggering Agent Runs via HTTP

When the worker needs to trigger an agent run (e.g. after resolving a Gmail message ID), it POSTs to the agent's App Runner URL. This is the **only** service allowed to call agent URLs.

File: `worker/handlers/gmail_process_message.py`

```python
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from agent_hub_core.db.models.job import Job
from agent_hub_core.db.models.deployment import Deployment
from agent_hub_core.domain.enums import JobStatus
from worker.handlers.base import AbstractJobHandler
import structlog

logger = structlog.get_logger()

class GmailProcessMessageHandler(AbstractJobHandler):
    async def execute(self, job: Job, session: AsyncSession) -> None:
        if job.status == JobStatus.SUCCEEDED:
            return  # idempotency guard

        message_id = job.payload["message_id"]

        # Resolve agent's App Runner URL from Deployment table
        deployment = await session.scalar(
            select(Deployment).where(
                Deployment.agent_id == job.agent_id,
                Deployment.status   == "active",
            )
        )

        if not deployment or not deployment.base_url:
            job.status        = JobStatus.FAILED
            job.error_message = "No active deployment found for agent"
            await session.commit()
            return

        # POST to agent App Runner URL — worker is the only caller
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    f"{deployment.base_url}/api/v1/runs",
                    json={
                        "message_id":     message_id,
                        "source":         job.payload.get("source", "push"),
                        "correlation_id": str(job.id),
                    },
                    headers={
                        # Worker authenticates with a shared internal token
                        "Authorization":    f"Bearer {settings.internal_service_token}",
                        "X-Correlation-Id": str(job.id),
                    },
                )

                if response.status_code == 409:
                    # Agent already processing this message — idempotent success
                    logger.info("agent_already_processing", message_id=message_id)
                    job.status = JobStatus.SUCCEEDED
                    await session.commit()
                    return

                response.raise_for_status()

        except httpx.TimeoutException:
            # App Runner is slow to cold-start — let SQS retry
            raise
        except httpx.HTTPStatusError as e:
            job.status        = JobStatus.FAILED
            job.error_message = f"Agent HTTP error: {e.response.status_code}"
            await session.commit()
            return

        job.status = JobStatus.SUCCEEDED
        await session.commit()

        logger.info("gmail_message_dispatched",
                    message_id=message_id,
                    agent_id=str(job.agent_id),
                    base_url=deployment.base_url)
```

---

## 5. Remove Hub → Agent HTTP Call

**Delete these entirely. Do not refactor — delete.**

- `backend/apis/integrations_gmail.py` — delete `_notify_agent_gmail_watch_active()` and the `asyncio.create_task(...)` call that invokes it at the end of the OAuth callback handler.
- `agents/incident-triage/src/incident_triage/main.py` — delete the `POST /internal/gmail-watch-active` endpoint.

**Replacement:** The agent's `poll_loop` already reads `integrations.watch_active` from Postgres on every cycle. When the hub sets `watch_active=True` during the OAuth callback, the agent sees it within 60 seconds automatically. No HTTP call needed.

The `integrations_gmail.py` OAuth callback should end at:

```python
    await session.commit()
    return RedirectResponse(
        f"{settings.frontend_url}/dashboard/integrations?connected=gmail"
    )
    # Nothing after this. No asyncio.create_task. No HTTP to agent.
```

---

## 6. Hub Internal API for Agent Enrichment (Read-Only)

The agent calls these two endpoints during the `enrich` graph node. These are the **only** hub endpoints the agent calls. They are authenticated with `INTERNAL_SERVICE_TOKEN`.

```python
# backend/apis/internal.py
from fastapi import APIRouter, Depends, HTTPException
from backend.deps.auth import require_internal_token  # checks Bearer token

router = APIRouter(prefix="/internal", dependencies=[Depends(require_internal_token)])

@router.get("/tenants/{tenant_id}")
async def get_tenant_context(tenant_id: UUID, session=Depends(get_async_session)):
    """
    Returns safe tenant metadata for LLM enrichment context.
    NEVER returns: OAuth tokens, secret ARNs, billing data, PII beyond contact email.
    """
    tenant = await session.get(Tenant, tenant_id)
    if not tenant:
        raise HTTPException(status_code=404)
    return {
        "id":            str(tenant.id),
        "name":          tenant.name,
        "sla_tier":      tenant.sla_tier,        # "standard" | "pro" | "enterprise"
        "contact_email": tenant.contact_email,
        "plan":          tenant.plan,
    }

@router.get("/tenants/{tenant_id}/incidents/recent")
async def get_recent_incidents(
    tenant_id: UUID,
    limit: int = 5,
    session=Depends(get_async_session),
):
    """
    Last N incidents for this tenant — used by classify node for context.
    Returns only safe fields: type, severity, summary, created_at.
    NEVER returns: raw_subject, raw_sender, message_id, langfuse_trace_id.
    """
    rows = await session.execute(
        select(Incident)
        .where(Incident.tenant_id == tenant_id)
        .order_by(Incident.created_at.desc())
        .limit(limit)
    )
    return [
        {
            "incident_type": r.incident_type,
            "severity":      r.severity,
            "summary":       r.summary,
            "created_at":    r.created_at.isoformat(),
        }
        for r in rows.scalars()
    ]
```

---

## 7. Dashboard Architecture — What to Build

### 7.1 Philosophy

The observability dashboard exists to answer one question: **"Is my AI agent behaving correctly, and how much is it costing me?"**

AI agents in production can go wrong silently:

- They can over-consume tokens without visible errors
- They can misclassify at scale if prompt drift occurs
- They can fail to fire tools (silently skip Slack notifications)
- They can accumulate cost that compounds unnoticed

The dashboard is the monitoring and evaluation layer that makes these problems visible before they become business failures.

### 7.2 Two Dashboard Surfaces

```
/dashboard                    → Tenant Overview
/dashboard/agents/{id}        → Per-Agent Observability
```

---

### 7.3 Tenant Overview Dashboard (`/dashboard`)

**Purpose:** High-level health across ALL agents for this tenant. Agents have different use cases — do not show agent-specific metrics here.

**What to show:**

```
┌─────────────────────────────────────────────────────────────────┐
│  KPI STRIP (4 cards)                                            │
│                                                                 │
│  Total Agents    Total Tokens Used    Total Cost (USD)   Alerts │
│  ● 3 active      1,240,500 tokens     $4.23 this month   2 ⚠   │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  TOKEN SPEND OVER TIME (AreaChart, all agents combined)         │
│  X: last 7 days  Y: tokens  Colour: by agent type              │
└─────────────────────────────────────────────────────────────────┘

┌──────────────────────────┐  ┌──────────────────────────────────┐
│  COST BREAKDOWN          │  │  AGENT STATUS LIST               │
│  By agent, last 30d      │  │  Name | Type | Status | Cost 7d  │
│  DonutChart              │  │  Incident Triage | ● Active | $1.2│
└──────────────────────────┘  └──────────────────────────────────┘
```

**Hub API for overview:**

```python
# backend/apis/dashboard.py

@router.get("/dashboard/overview")
async def tenant_overview(
    tenant_id: UUID = Depends(get_current_tenant_id),
    window_days: int = 30,
    session: AsyncSession = Depends(get_async_session),
):
    """
    Aggregated metrics across all agents for this tenant.
    Source: tool_call_events + metric_snapshots (no jobs data).
    """

    # Total tokens + cost (from tool_call_events — agent writes these)
    token_totals = await session.execute(text("""
        SELECT
            SUM(prompt_tokens)       AS total_prompt_tokens,
            SUM(completion_tokens)   AS total_completion_tokens,
            SUM(cost_usd)            AS total_cost_usd,
            COUNT(DISTINCT agent_id) AS agent_count
        FROM tool_call_events
        WHERE tenant_id = :tenant_id
          AND created_at >= NOW() - INTERVAL ':days days'
          AND node_name = 'classify'   -- only count LLM node calls, not all nodes
    """), {"tenant_id": tenant_id, "days": window_days})

    # Daily token spend timeseries (for chart)
    daily_spend = await session.execute(text("""
        SELECT
            DATE_TRUNC('day', created_at) AS day,
            agent_id,
            SUM(prompt_tokens + completion_tokens) AS tokens,
            SUM(cost_usd)                          AS cost_usd
        FROM tool_call_events
        WHERE tenant_id = :tenant_id
          AND created_at >= NOW() - INTERVAL ':days days'
          AND node_name = 'classify'
        GROUP BY 1, 2
        ORDER BY 1
    """), {"tenant_id": tenant_id, "days": window_days})

    # Per-agent summary
    agents = await session.execute(
        select(Agent, Deployment)
        .join(Deployment, Deployment.agent_id == Agent.id, isouter=True)
        .where(Agent.tenant_id == tenant_id)
    )

    return TenantOverviewResponse(
        total_agents       = ...,
        total_tokens       = ...,
        total_cost_usd     = ...,
        daily_spend        = [...],
        agents             = [...],
    )
```

**Response shape:**

```python
class TenantOverviewResponse(BaseModel):
    window_days:     int
    total_agents:    int
    active_agents:   int
    total_tokens:    int        # prompt + completion combined
    total_cost_usd:  float
    daily_spend:     list[DailySpendRow]   # [{day, tokens, cost_usd}]
    agents:          list[AgentSummaryRow] # [{name, type, status, tokens_7d, cost_7d}]

class DailySpendRow(BaseModel):
    day:      str       # ISO date
    tokens:   int
    cost_usd: float

class AgentSummaryRow(BaseModel):
    agent_id:   str
    name:       str
    agent_type: str
    status:     str     # active | paused | failed
    tokens_7d:  int
    cost_7d:    float
```

---

### 7.4 Per-Agent Observability Dashboard (`/dashboard/agents/{id}`)

**Purpose:** Deep observability into one agent's behaviour. This is the capstone showpiece — proves understanding that AI systems need monitoring.

**Tabs:** Overview | Traces | Incidents

---

#### Tab 1: Overview

```
┌──────────────────────────────────────────────────────────────────┐
│  KPI STRIP                                                       │
│                                                                  │
│  Incidents  Prompt Tokens  Completion Tokens  Total Cost  Errors │
│  47 (7d)    89,200         35,300             $0.83      2 runs  │
└──────────────────────────────────────────────────────────────────┘

┌───────────────────────────┐  ┌────────────────────────────────── ┐
│  TOKEN BREAKDOWN (7d)     │  │  CLASSIFICATION CONFIDENCE TREND  │
│  StackedBarChart          │  │  LineChart — rolling avg per day  │
│  Prompt | Completion      │  │  Reference line at 0.60 (red dash)│
│  Optional: reasoning      │  │  "Degrading" label if < 0.70 avg  │
│  Optional: tool_use       │  └───────────────────────────────────┘
└───────────────────────────┘
```

**Token breakdown note:** Break down tokens by type IF the data is available in `tool_call_events`. The columns `prompt_tokens` and `completion_tokens` are always available. For Anthropic models, `reasoning_tokens` and `tool_use_tokens` may be available depending on API response. Store whatever Langfuse / the LLM callback provides. Display what is available, gracefully omit what is not.

```python
# Token breakdown categories to store and display:
{
    "prompt_tokens":      int,   # always available
    "completion_tokens":  int,   # always available
    "reasoning_tokens":   int,   # available if model uses extended thinking
    "tool_use_tokens":    int,   # available if tool calls are made
    "cost_usd":           float, # Langfuse-provided estimate
}
```

---

#### Tab 2: Traces (Live Decision Feed)

This is the most important panel for demonstrating production AI observability.

```
┌─────────────────────────────────────────────────────────────────┐
│  LIVE DECISION FEED                          Auto-refreshes 5s  │
│                                                                 │
│  Time    Node       Tool/Decision    Duration   Tokens   Status │
│  14:32   fetch      —                42ms       —        ✓      │
│  14:32   dedup      —                 3ms       —        ✓      │
│  14:32   enrich     —                87ms       —        ✓      │
│  14:32   classify   LLM call        1240ms     892tk    ✓      │
│  14:32   router     → notify         0ms        —        ✓      │
│  14:32   slack      post_message    310ms       —        ✓      │
│  14:32   finalize   —                 8ms       —        ✓      │
│                                                                 │
│  [View full trace →]  links to Incidents tab for this run      │
└─────────────────────────────────────────────────────────────────┘
```

**SSE endpoint for live feed:**

```python
# backend/apis/dashboard.py

from fastapi.responses import StreamingResponse
import asyncio, json

@router.get("/dashboard/agents/{agent_id}/feed")
async def live_decision_feed(
    agent_id: UUID,
    tenant_id: UUID = Depends(get_current_tenant_id),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Server-Sent Events stream of tool_call_events for one agent.
    Frontend opens EventSource — receives new rows as they are written.
    Poll interval: 2 seconds. Latency: 2–5 seconds end-to-end.
    """
    async def stream():
        last_seen_id = None

        while True:
            query = (
                select(ToolCallEvent)
                .where(
                    ToolCallEvent.tenant_id == tenant_id,   # tenant isolation
                    ToolCallEvent.agent_id  == agent_id,    # agent scoping
                )
                .order_by(ToolCallEvent.created_at.desc())
                .limit(30)
            )
            if last_seen_id:
                query = query.where(ToolCallEvent.id > last_seen_id)

            rows = (await session.execute(query)).scalars().all()

            for row in reversed(rows):
                payload = {
                    "id":           str(row.id),
                    "node_name":    row.node_name,
                    "tool_name":    row.tool_name,
                    "decision":     row.decision,
                    "duration_ms":  row.duration_ms,
                    "succeeded":    row.succeeded,
                    "error":        row.error,
                    "created_at":   row.created_at.isoformat(),
                    # Token data — only present on LLM nodes
                    "prompt_tokens":     row.prompt_tokens,
                    "completion_tokens": row.completion_tokens,
                    "cost_usd":          float(row.cost_usd) if row.cost_usd else None,
                    # NEVER include: raw email content, OAuth tokens, ARNs
                }
                yield f"data: {json.dumps(payload)}\n\n"
                last_seen_id = row.id

            await asyncio.sleep(2)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":    "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
```

**Frontend EventSource:**

```typescript
// components/dashboard/DecisionFeed.tsx
useEffect(() => {
  const source = new EventSource(`/api/v1/dashboard/agents/${agentId}/feed`, {
    withCredentials: true,
  });

  source.onmessage = (e) => {
    const event = JSON.parse(e.data);
    setEvents((prev) => [event, ...prev].slice(0, 100)); // keep last 100
  };

  source.onerror = () => {
    source.close();
    // Reconnect after 5s
    setTimeout(() => reconnect(), 5000);
  };

  return () => source.close();
}, [agentId]);
```

---

#### Tab 3: Incidents

```
┌─────────────────────────────────────────────────────────────────┐
│  INCIDENT LOG                                    Last 7 days ▾  │
│                                                                 │
│  Time    Type          Severity   Summary           Actions     │
│  14:32   bug_report    medium     Login fails on..  Slack ✓     │
│  13:10   outage        high       API returning 5.. Slack ✓     │
│  09:45   performance   medium     Latency spike in. Slack ✓     │
│                                                                 │
│  Row click → opens incident detail panel                       │
└─────────────────────────────────────────────────────────────────┘

Incident detail panel (slide-over):
  - Incident type badge + severity badge
  - Confidence score: 0.91 (87%)
  - One-sentence summary
  - Actions taken: [Slack ✓]
  - "View LLM trace in Langfuse →" (if langfuse_trace_id is set)
  - NEVER show: raw email subject/sender, message_id
```

**Incidents API:**

```python
@router.get("/dashboard/agents/{agent_id}/incidents")
async def get_agent_incidents(
    agent_id: UUID,
    tenant_id: UUID = Depends(get_current_tenant_id),
    window_days: int = 7,
    limit: int = 50,
    session: AsyncSession = Depends(get_async_session),
):
    rows = await session.execute(
        select(Incident)
        .where(
            Incident.tenant_id == tenant_id,    # tenant isolation always first
            Incident.agent_id  == agent_id,
            Incident.created_at >= datetime.now(timezone.utc)
                                  - timedelta(days=window_days),
        )
        .order_by(Incident.created_at.desc())
        .limit(limit)
    )
    return [_safe_incident_row(r) for r in rows.scalars()]


def _safe_incident_row(incident: Incident) -> dict:
    """
    NEVER return raw_subject, raw_sender, message_id, or langfuse_trace_id
    in list views. Only in detail view with explicit lookup.
    """
    return {
        "id":             str(incident.id),
        "incident_type":  incident.incident_type,
        "severity":       incident.severity,
        "summary":        incident.summary,
        "confidence":     incident.confidence,
        "actions_taken":  incident.actions_taken,
        "slack_sent":     incident.slack_sent,
        "created_at":     incident.created_at.isoformat(),
    }


@router.get("/dashboard/agents/{agent_id}/incidents/{incident_id}")
async def get_incident_detail(
    agent_id: UUID,
    incident_id: UUID,
    tenant_id: UUID = Depends(get_current_tenant_id),
    session: AsyncSession = Depends(get_async_session),
):
    incident = await session.scalar(
        select(Incident).where(
            Incident.id        == incident_id,
            Incident.tenant_id == tenant_id,   # ownership check
            Incident.agent_id  == agent_id,
        )
    )
    if not incident:
        raise HTTPException(status_code=404)

    return {
        **_safe_incident_row(incident),
        # Detail view only — langfuse link for trace inspection
        "langfuse_trace_url": (
            f"{settings.langfuse_host}/trace/{incident.langfuse_trace_id}"
            if incident.langfuse_trace_id else None
        ),
    }
```

---

### 7.5 `metrics_rollup` Worker Job

Fills `metric_snapshots` for the historical charts. Runs hourly via EventBridge → SQS.

File: `worker/handlers/metrics_rollup.py`

```python
from langfuse import Langfuse
from sqlalchemy.dialects.postgresql import insert as pg_insert
from statistics import mean, quantiles

class MetricsRollupHandler(AbstractJobHandler):
    async def execute(self, job: Job, session: AsyncSession) -> None:
        from datetime import datetime, timezone
        p            = job.payload
        tenant_id    = job.tenant_id
        agent_id     = job.agent_id
        window_start = datetime.fromisoformat(p["window_start"])
        window_end   = datetime.fromisoformat(p["window_end"])

        # ── 1. Postgres: incident + token stats ───────────────────────────
        incident_stats = await session.execute(text("""
            SELECT
                COUNT(*)                                   AS incident_count,
                COUNT(*) FILTER (WHERE confidence < 0.6)  AS low_confidence_count,
                AVG(confidence)                            AS avg_confidence,
                json_build_object(
                    'critical', COUNT(*) FILTER (WHERE severity='critical'),
                    'high',     COUNT(*) FILTER (WHERE severity='high'),
                    'medium',   COUNT(*) FILTER (WHERE severity='medium'),
                    'low',      COUNT(*) FILTER (WHERE severity='low')
                ) AS severity_dist,
                json_build_object(
                    'outage',          COUNT(*) FILTER (WHERE incident_type='outage'),
                    'security_breach', COUNT(*) FILTER (WHERE incident_type='security_breach'),
                    'performance',     COUNT(*) FILTER (WHERE incident_type='performance'),
                    'bug_report',      COUNT(*) FILTER (WHERE incident_type='bug_report'),
                    'billing',         COUNT(*) FILTER (WHERE incident_type='billing')
                ) AS type_dist
            FROM incidents
            WHERE tenant_id = :tenant_id
              AND agent_id  = :agent_id
              AND created_at BETWEEN :start AND :end
        """), {"tenant_id": tenant_id, "agent_id": agent_id,
               "start": window_start, "end": window_end})

        token_stats = await session.execute(text("""
            SELECT
                SUM(prompt_tokens)                         AS prompt_tokens,
                SUM(completion_tokens)                     AS completion_tokens,
                SUM(cost_usd)                              AS cost_usd,
                AVG(duration_ms) FILTER
                    (WHERE node_name='classify')           AS avg_latency_ms,
                PERCENTILE_CONT(0.95) WITHIN GROUP
                    (ORDER BY duration_ms)
                    FILTER (WHERE node_name='classify')    AS p95_latency_ms,
                COUNT(*) FILTER (WHERE NOT succeeded)      AS error_count
            FROM tool_call_events
            WHERE tenant_id = :tenant_id
              AND agent_id  = :agent_id
              AND created_at BETWEEN :start AND :end
        """), {"tenant_id": tenant_id, "agent_id": agent_id,
               "start": window_start, "end": window_end})

        i = incident_stats.mappings().first()
        t = token_stats.mappings().first()

        metrics = {
            # Volume
            "incident_count":       int(i["incident_count"] or 0),
            "error_count":          int(t["error_count"] or 0),
            "low_confidence_count": int(i["low_confidence_count"] or 0),

            # Tokens — broken down by type
            "prompt_tokens":      int(t["prompt_tokens"] or 0),
            "completion_tokens":  int(t["completion_tokens"] or 0),
            "total_tokens":       int((t["prompt_tokens"] or 0)
                                    + (t["completion_tokens"] or 0)),
            "estimated_cost_usd": float(t["cost_usd"] or 0),

            # Latency (classify node only — the LLM call)
            "avg_latency_ms": float(t["avg_latency_ms"] or 0),
            "p95_latency_ms": float(t["p95_latency_ms"] or 0),

            # Quality
            "avg_confidence":      float(i["avg_confidence"] or 0),
            "low_confidence_rate": (
                int(i["low_confidence_count"] or 0)
                / max(int(i["incident_count"] or 1), 1)
            ),

            # Distributions
            "severity_dist": i["severity_dist"] or {},
            "type_dist":     i["type_dist"] or {},
        }

        # ── 2. Upsert metric_snapshots — idempotent ────────────────────────
        stmt = pg_insert(MetricSnapshot).values(
            tenant_id    = tenant_id,
            agent_id     = agent_id,
            window_start = window_start,
            window_end   = window_end,
            metrics      = metrics,
        ).on_conflict_do_update(
            index_elements=["tenant_id", "agent_id", "window_start"],
            set_={"metrics": metrics, "window_end": window_end},
        )
        await session.execute(stmt)
        job.status = JobStatus.SUCCEEDED
        await session.commit()
```

**`metric_snapshots` unique constraint — add to Alembic migration:**

```python
UniqueConstraint("tenant_id", "agent_id", "window_start",
                 name="uq_metric_snapshot_tenant_agent_window")
```

**Scheduling — EventBridge (Terraform `infra/worker/`):** Hourly `metrics_rollup` and daily Gmail watch renewal rules target the hub SQS queue (see [`infra/worker/main.tf`](../infra/worker/main.tf)). The application worker may also treat other scheduled payloads (e.g. `kind: scheduled_metrics_rollup`) as documented in worker code. See [`infra/worker/README.md`](../infra/worker/README.md) and [`docs/terraform-infra-instructions.md`](terraform-infra-instructions.md).

```hcl
resource "aws_cloudwatch_event_rule" "metrics_rollup_hourly" {
  name                = "agent-hub-metrics-rollup-hourly"
  schedule_expression = "rate(1 hour)"
}
# Target: SQS — worker handles non-envelope tick JSON (see worker/main.py).
```

---

## 8. Security Rules for All Dashboard APIs

These are absolute. Implement them as a base query pattern, not case-by-case.

### 8.1 Tenant isolation — every query

```python
# backend/deps/tenant.py
# Every dashboard endpoint receives tenant_id from the auth token — NOT from URL params
# This prevents tenant A from querying /dashboard/agents/{tenant_B_agent_id}

async def get_current_tenant_id(
    current_user=Depends(get_current_user),
) -> UUID:
    return current_user.tenant_id   # from JWT — cannot be spoofed
```

### 8.2 Agent ownership check

```python
# Before any per-agent query, verify the agent belongs to the requesting tenant:
async def verify_agent_ownership(
    agent_id: UUID,
    tenant_id: UUID,
    session: AsyncSession,
) -> Agent:
    agent = await session.scalar(
        select(Agent).where(
            Agent.id        == agent_id,
            Agent.tenant_id == tenant_id,   # ownership enforced here
        )
    )
    if not agent:
        raise HTTPException(status_code=404)   # 404 not 403 — don't leak existence
    return agent
```

### 8.3 Fields that must never appear in any API response

| Field                             | Reason                                   |
| --------------------------------- | ---------------------------------------- |
| `incidents.raw_subject`           | Contains email content — PII             |
| `incidents.raw_sender`            | Email address — PII                      |
| `incidents.message_id`            | Gmail message ID — not useful to tenants |
| `tool_call_events.input_summary`  | May contain email content fragments      |
| Any `*_secret_arn` field          | AWS resource ARN — internal only         |
| `deployments.service_arn`         | Internal AWS resource identifier         |
| `langfuse_trace_id` in list views | Only in detail view, as a link           |
| Any OAuth token or credential     | Never in any API, anywhere               |

---

## 9. `tool_call_events` — Token Detail Storage

The agent's `@traced_node` decorator must capture token breakdown where available. Update `write_tool_event` to include all token types:

```python
# agents/incident-triage/src/incident_triage/instrumentation/events.py

async def write_tool_event(
    tenant_id:          str,
    agent_id:           str,
    trace_id:           str,
    message_id:         str,
    node_name:          str,
    tool_name:          str | None    = None,
    decision:           str | None    = None,
    duration_ms:        int           = 0,
    succeeded:          bool          = True,
    error:              str | None    = None,
    # Token breakdown — populate whatever is available
    prompt_tokens:      int | None    = None,
    completion_tokens:  int | None    = None,
    reasoning_tokens:   int | None    = None,   # Anthropic extended thinking
    tool_use_tokens:    int | None    = None,   # tool call token overhead
    cost_usd:           float | None  = None,   # from Langfuse or manual calc
) -> None:
    try:
        async with get_session() as session:
            session.add(ToolCallEvent(
                tenant_id         = tenant_id,
                agent_id          = agent_id,
                trace_id          = trace_id,
                message_id        = message_id,
                node_name         = node_name,
                tool_name         = tool_name,
                decision          = decision,
                duration_ms       = duration_ms,
                succeeded         = succeeded,
                error             = error,
                prompt_tokens     = prompt_tokens,
                completion_tokens = completion_tokens,
                reasoning_tokens  = reasoning_tokens,
                tool_use_tokens   = tool_use_tokens,
                cost_usd          = cost_usd,
            ))
            await session.commit()
    except Exception:
        pass  # observability must never crash the agent
```

Update `ToolCallEvent` model to include `reasoning_tokens` and `tool_use_tokens` columns. Generate Alembic migration.

In the `classify` node, extract token usage from the LLM response:

```python
# agents/incident-triage/src/incident_triage/graph/nodes/classify.py

@traced_node("classify")
async def run(state: TriageState) -> dict:
    # Use with_structured_output for classification
    result = await _llm.ainvoke(prompt)

    # Extract token usage from Langchain response metadata
    usage = getattr(result, "usage_metadata", {}) or {}

    return {
        "incident_type": result.incident_type,
        "severity":      result.severity,
        "summary":       result.summary,
        "confidence":    result.confidence,
        # Pass to decorator for storage
        "_prompt_tokens":     usage.get("input_tokens"),
        "_completion_tokens": usage.get("output_tokens"),
        "_reasoning_tokens":  usage.get("reasoning_tokens"),   # if available
        "_tool_use_tokens":   usage.get("tool_use_tokens"),    # if available
        "_cost_usd":          _estimate_cost(usage),
    }


def _estimate_cost(usage: dict) -> float | None:
    """
    Estimate cost in USD for claude-sonnet-4-20250514.
    Update pricing constants when model changes.
    Langfuse also provides this — use whichever is available.
    """
    INPUT_COST_PER_1K  = 0.003   # $3 per 1M input tokens
    OUTPUT_COST_PER_1K = 0.015   # $15 per 1M output tokens

    input_tokens  = usage.get("input_tokens", 0) or 0
    output_tokens = usage.get("output_tokens", 0) or 0

    if not input_tokens and not output_tokens:
        return None

    return (
        (input_tokens  / 1000) * INPUT_COST_PER_1K
        + (output_tokens / 1000) * OUTPUT_COST_PER_1K
    )
```

---

## 10. Hub API Router Registration

Register all new dashboard endpoints in `backend/apis/router.py`:

```python
from backend.apis import (
    tenants, agents, jobs, integrations,
    dashboard,      # ← new
    internal,       # ← new (agent-facing internal endpoints)
    webhooks_gmail, # ← existing
)

app.include_router(dashboard.router, prefix="/api/v1")
app.include_router(internal.router, prefix="")   # /internal/* — no /api/v1 prefix
```

---

## 11. Complete Dashboard API Surface (Summary)

```
GET  /api/v1/dashboard/overview
     → Tenant-level: total agents, total tokens, total cost, daily spend chart

GET  /api/v1/dashboard/agents/{id}/overview
     → Agent KPIs: incident count, token breakdown, cost, confidence, error count

GET  /api/v1/dashboard/agents/{id}/feed          (SSE)
     → Live tool_call_events stream, 2s poll, last 30 events

GET  /api/v1/dashboard/agents/{id}/incidents
     → Paginated incident list (safe fields only)

GET  /api/v1/dashboard/agents/{id}/incidents/{incident_id}
     → Incident detail including Langfuse trace link

GET  /api/v1/dashboard/agents/{id}/tokens
     → Token timeseries: daily prompt/completion/reasoning/tool_use + cost

GET  /api/v1/dashboard/agents/{id}/snapshots
     → metric_snapshots rows for historical KPI charts

Internal (agent-facing, Bearer token):
GET  /internal/tenants/{tenant_id}
GET  /internal/tenants/{tenant_id}/incidents/recent
```

---

## 12. Hard Rules (Absolute — Do Not Deviate)

### Architecture

- Hub **never** calls agent URLs. `httpx` calls in `backend/` must only target Google APIs, Slack OAuth, and AWS services.
- Worker is the **only** service that reads `Deployment.base_url` and POSTs to it.
- Agent calls hub `/internal/*` read-only. No writes to hub-owned tables.
- `Deployment.base_url` is set **only** by the worker provisioning handler after App Runner service reaches RUNNING status.

### Dashboard data

- **No jobs table data** anywhere in the dashboard. Not in overview, not in agent detail, not in any API response to the frontend.
- Every SQL query touching `incidents`, `tool_call_events`, or `metric_snapshots` **must** include `WHERE tenant_id = :tenant_id` as the first filter condition.
- `tool_call_events` token columns (`prompt_tokens`, `completion_tokens`, `reasoning_tokens`, `tool_use_tokens`, `cost_usd`) must all be nullable — only the `classify` node populates them, not all nodes.

### Security

- Fields listed in Section 8.3 must never appear in any API response.
- `langfuse_trace_id` appears only in the incident detail endpoint, formatted as a full Langfuse URL, never as a raw ID.
- All token cost estimates are display-only — never used for billing.

### Observability writing

- `write_tool_event` is always wrapped in try/except and called via `asyncio.create_task()`. It must never block graph execution or propagate exceptions to the graph.
- `langfuse.flush()` must be called in the agent's lifespan shutdown before the event loop closes.
