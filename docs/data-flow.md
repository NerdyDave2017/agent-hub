# Agent Hub — data flows and user interactions

This document shows **how important actions move through the system** using sequence diagrams. It complements [architecture.md](architecture.md) (components and boundaries) and [design-decisions.md](design-decisions.md) (why). API prefixes follow the hub’s versioned routes (typically `/api/v1`).

---

## Table of contents

1. [Create workspace (sign up)](#1-create-workspace-sign-up)
2. [Sign in (email password)](#2-sign-in-email-password)
3. [Sign in with Google (hub account)](#3-sign-in-with-google-hub-account)
4. [Open dashboard overview](#4-open-dashboard-overview)
5. [Create an agent](#5-create-an-agent)
6. [Background: provision an agent](#6-background-provision-an-agent)
7. [Track provisioning from the UI](#7-track-provisioning-from-the-ui)
8. [Connect Gmail for an agent](#8-connect-gmail-for-an-agent)

---

## 1. Create workspace (sign up)

A new customer submits **workspace name**, **email**, **name**, and **password**. The hub creates a **tenant** and **owner user** in one transaction, then returns a **hub JWT** so the browser can call tenant-scoped APIs.

```mermaid
sequenceDiagram
  actor User
  participant UI as Dashboard_UI
  participant Hub as Hub_FastAPI
  participant TS as tenants_service
  participant AS as auth_service
  participant DB as Postgres
  User->>UI: Enter_workspace_details
  UI->>Hub: POST_/auth/signup
  Hub->>TS: create_tenant_with_owner
  TS->>DB: INSERT_tenant_and_user
  Hub->>AS: create_access_token
  AS-->>Hub: JWT
  Hub-->>UI: SignupResponse_token_tenant_ids
  UI-->>User: Signed_in_store_token
```

**Notes:** Slug collisions are retried server-side. If `JWT_SECRET_KEY` is missing, the hub returns **503** (see [`backend/apis/auth.py`](../backend/apis/auth.py)).

---

## 2. Sign in (email password)

Returning users send **email + password**; the hub validates credentials and issues the same style of **access token** as sign-up.

```mermaid
sequenceDiagram
  actor User
  participant UI as Dashboard_UI
  participant Hub as Hub_FastAPI
  participant AS as auth_service
  participant DB as Postgres
  User->>UI: Enter_email_password
  UI->>Hub: POST_/auth/login
  Hub->>DB: Load_user_by_email
  Hub->>AS: verify_password_issue_token
  Hub-->>UI: LoginResponse_access_token
  UI-->>User: Signed_in
```

---

## 3. Sign in with Google (hub account)

The **dashboard** uses Google’s **Sign-In** SDK; the browser sends a Google **`id_token`** to the hub. The hub **verifies** the token with Google’s public keys, then **finds or creates** a `User`. New Google users may have **no tenant** yet (`has_workspace=false`) until they complete workspace creation — a separate flow from **Gmail agent OAuth** (section 8).

```mermaid
sequenceDiagram
  actor User
  participant UI as Dashboard_UI
  participant Google as Google_SignIn
  participant Hub as Hub_FastAPI
  participant DB as Postgres
  User->>UI: Click_Sign_in_with_Google
  UI->>Google: OAuth_popup_or_redirect
  Google-->>UI: id_token
  UI->>Hub: POST_/auth/google
  Hub->>Google: Verify_id_token_jwks
  Hub->>DB: Find_or_create_user
  Hub-->>UI: GoogleAuthResponse_token_workspace_flags
```

---

## 4. Open dashboard overview

The **overview** screen (agents, tokens, cost) calls a **tenant-scoped** dashboard route. The hub checks the **Bearer JWT**, ensures the path `tenant_id` matches the token, then reads **aggregates from Postgres**.

```mermaid
sequenceDiagram
  actor User
  participant UI as Dashboard_UI
  participant Hub as Hub_FastAPI
  participant DS as dashboard_service
  participant DB as Postgres
  User->>UI: Open_overview
  UI->>Hub: GET_/tenants/{tenant_id}/dashboard/overview_Authorization_Bearer
  Hub->>Hub: Validate_JWT_and_tenant_scope
  Hub->>DS: get_tenant_overview
  DS->>DB: Query_rollups_and_agent_rows
  DS-->>Hub: TenantOverviewResponse
  Hub-->>UI: JSON_metrics_and_lists
```

**Related routes:** Per-agent drill-down under `/tenants/{tenant_id}/dashboard/agents/{agent_id}/…` (see [`backend/apis/dashboard.py`](../backend/apis/dashboard.py)).

---

## 5. Create an agent

When the user finishes the **create agent** wizard, the UI **`POST`s** the new agent. The hub **inserts** the `agents` row, then creates a **`jobs`** row for `agent_provisioning` and **publishes** a `JobQueueEnvelope` to **SQS** when configured. The HTTP response is the **agent record**; provisioning continues **asynchronously**.

```mermaid
sequenceDiagram
  actor User
  participant UI as Dashboard_UI
  participant Hub as Hub_FastAPI
  participant AS as agents_service
  participant JS as jobs_service
  participant Core as agent_hub_core
  participant DB as Postgres
  participant Q as SQS
  User->>UI: Submit_create_agent
  UI->>Hub: POST_/tenants/{tenant_id}/agents_X-Correlation-ID
  Hub->>AS: create_agent
  AS->>DB: INSERT_agent
  Hub->>JS: create_job_with_publish
  JS->>Core: Build_JobQueueEnvelope
  JS->>DB: INSERT_job_commit
  JS->>Q: SendMessage_optional
  JS->>DB: UPDATE_job_status_if_sent
  Hub-->>UI: 201_AgentRead
```

**If `SQS_QUEUE_URL` is unset:** the job may stay **`pending`** while the agent row still exists — useful for local API-only testing ([`services/jobs_service.py`](../backend/services/jobs_service.py)).

---

## 6. Background: provision an agent

The **worker** long-polls **SQS**, parses the envelope with **`agent_hub_core`**, loads the **job** and **agent**, then runs the **`agent_provisioning`** handler (App Runner / ECS / dev URL depending on settings). It updates **job status** and **agent/deployment** rows in **Postgres**, then **deletes** the SQS message on success.

```mermaid
sequenceDiagram
  participant Q as SQS
  participant Worker as Worker_process
  participant Core as agent_hub_core
  participant DB as Postgres
  participant AWS as AWS_runtime_AppRunner_or_ECS
  Q->>Worker: ReceiveMessage
  Worker->>Core: Parse_JobQueueEnvelope
  Worker->>DB: claim_job_load_agent
  Worker->>AWS: Create_or_describe_service
  AWS-->>Worker: URL_or_resource_ids
  Worker->>DB: UPDATE_deployment_agent_job_success
  Worker->>Q: DeleteMessage
```

**Failure path:** On repeated errors, SQS redrives until the message lands on the **DLQ**; the job row can move to **failed** / **dead_lettered** depending on handler logic ([`worker/handlers/provision.py`](../worker/handlers/provision.py)).

---

## 7. Track provisioning from the UI

The UI can **`GET`** provisioning status or **long-poll** until the watermark changes, so the “Create agent” experience can show **spinner → active** without blocking the original `POST`.

```mermaid
sequenceDiagram
  actor User
  participant UI as Dashboard_UI
  participant Hub as Hub_FastAPI
  participant DB as Postgres
  User->>UI: Wait_on_provisioning_screen
  loop Poll_or_long_poll
    UI->>Hub: GET_/agents/{id}/provisioning_status_or_long_poll
    Hub->>DB: Read_agent_and_latest_job
    Hub-->>UI: AgentProvisioningStatusRead
  end
  UI-->>User: Show_active_or_error
```

---

## 8. Connect Gmail for an agent

**Gmail OAuth** (agent integration) is separate from **Google Sign-In** (section 3). The user starts OAuth from the hub; the hub redirects to **Google**, then handles the **callback**: exchanges the **code** for tokens, stores secrets in **AWS Secrets Manager**, and updates the **`integrations`** row. Optionally, **Gmail watch** is registered when Pub/Sub is configured.

```mermaid
sequenceDiagram
  actor User
  participant UI as Dashboard_UI
  participant Hub as Hub_FastAPI
  participant Google as Google_OAuth
  participant SM as Secrets_Manager
  participant DB as Postgres
  User->>UI: Connect_Gmail
  UI->>Hub: GET_integrations_gmail_oauth_start
  Hub-->>UI: Redirect_to_Google_consent
  User->>Google: Approve_scopes
  Google-->>UI: Redirect_with_authorization_code
  UI->>Hub: GET_oauth_callback_code
  Hub->>Google: Exchange_code_for_tokens
  Google-->>Hub: access_and_refresh_tokens
  Hub->>SM: PutSecretValue
  Hub->>DB: UPSERT_integration_row
  Hub-->>UI: Redirect_back_to_dashboard
```

**Slack:** The same **start URL → OAuth provider → callback → secrets + DB** pattern applies under [`backend/apis/integrations_slack.py`](../backend/apis/integrations_slack.py).

---

## Related documentation

| Document | Use |
| --- | --- |
| [architecture.md](architecture.md) | System context, `agent-hub-core`, Terraform map |
| [design-decisions.md](design-decisions.md) | Rationale for async jobs, queues, shared library |
| [explanatory-brief-for-llms.md](explanatory-brief-for-llms.md) | Compact problem/solution narrative |

---

_Update this file when new first-class user journeys ship or route shapes change._
