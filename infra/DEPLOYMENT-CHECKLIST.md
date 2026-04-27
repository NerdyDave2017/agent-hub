# AWS deploy checklist — Hub, worker, App Runner provisioning

Use this before and after applying Terraform and cutting traffic. Full order and context live in [`docs/terraform-infra-instructions.md`](../docs/terraform-infra-instructions.md).

## Preconditions (confidence: **must verify manually**)

| # | Item | Notes |
| --- | --- | --- |
| 1 | S3 backend bucket + DynamoDB lock table exist | Same names as each root `backend.tf`. |
| 2 | AWS CLI + Terraform ≥ 1.5, correct `aws_account_id` / `aws_region` |  |
| 3 | **ECR**: agent image exists at the URI Terraform computes | Default repo path `agent-hub/{environment}/incident-triage` + tag `agent_image_tag` (default `latest`). Push from CI before relying on `CreateService`. |
| 4 | **ECR**: worker image exists at `worker_image_uri` | Worker task pulls this first. |
| 5 | Domain / TLS / Route53 (if custom) | Hub App Runner URL comes from hub state. |

## Apply order (confidence: **high** — matches repo layout)

1. `infra/vpc`
2. `infra/secrets`
3. `infra/rds`
4. `infra/hub` (SQS + hub App Runner)
5. `infra/worker` (ECS worker + **agent ECR access / instance roles** + worker task env for App Runner create)
6. `infra/agents/incident-triage` (optional: ECR + staging agent; ECR repo should align with worker default image path)

## Worker ↔ App Runner provisioning (confidence: **high** for wiring, **medium** until first successful `CreateService` in your account)

| # | Item | Status in repo |
| --- | --- | --- |
| W1 | Worker task role allows `apprunner:*` create/describe/pause/delete + `iam:PassRole` to `…-agent-*` roles | `infra/modules/ecs-worker/main.tf` |
| W2 | **Access** + **instance** roles for agents exist; trust policies for `build.apprunner.amazonaws.com` / `tasks.apprunner.amazonaws.com` | `infra/worker/main.tf` |
| W3 | Worker container receives **`AGENT_ECR_ACCESS_ROLE_ARN`**, **`AGENT_INSTANCE_ROLE_ARN`** (aliases in Python `Settings`) | `ecs-worker` module |
| W4 | Worker receives **`APP_RUNNER_CREATE_IMAGE_IDENTIFIER`** (built from `ecr_registry` + repo + `agent_image_tag`) | `worker/main.tf` → `ecs-worker` **`extra_environment`** |
| W5 | Worker receives **`APP_RUNNER_CREATE_VPC_CONNECTOR_ARN`** from VPC state | Same **`extra_environment`** concat |
| W6 | Hub App Runner has **`HUB_PUBLIC_URL`** (`infra/hub` var `hub_public_url`; deploy workflow syncs from `terraform output hub_service_url` when GitHub var `TF_VAR_hub_public_url` is unset). Worker has **`HUB_BASE_URL`** (same logical URL) | OAuth redirects + `AGENT_HUB_PUBLIC_URL` in provision (`AliasChoices` with `HUB_PUBLIC_URL`) |

## After `terraform apply` on worker (confidence: **high**)

| # | Item | Why |
| --- | --- | --- |
| P1 | **Force new ECS deployment** if the service uses `lifecycle { ignore_changes = [task_definition] }` | Otherwise new env vars may not roll out to running tasks. Example: `aws ecs update-service --cluster … --service … --force-new-deployment`. |
| P2 | Hub task/env has **`SQS_QUEUE_URL`** matching hub queue | So `POST /agents` jobs reach the worker. |
| P3 | Worker has **`SQS_QUEUE_URL`** (same queue URL) | Consumer polls the correct queue. |
| P4 | Database migrations applied (hub or job) | Agents/jobs need schema. |

## Smoke test (confidence: **medium** — depends on auth + DB)

1. `POST /api/v1/tenants/{id}/agents` with a valid token / tenant.
2. Confirm **`jobs`** row `agent_provisioning` → `queued` (if SQS send succeeded).
3. Worker logs: `CreateService` / poll / `provision_apprunner_create_ok`.
4. **`deployments`** row: `app_runner_arn`, `base_url`, `live`; **`agents.status`** → `active`.

## Risks / gaps (confidence: **medium–low**)

- **First `CreateService`** can fail on IAM, subnet-less VPC connector, bad health path, or missing image; read CloudWatch + `jobs.error_message`.
- **Service name** is `ah-{agent_uuid}`; must be unique per region/account (destroy old service before reusing if needed).
- **Meeting / other agent types** may need a different ECR repo (`agent_ecr_repository` / image in job payload) — default path targets **incident-triage** layout.
- **Hub lifecycle** and **worker lifecycle** on ECS may block automatic rollout — treat **force new deployment** as normal ops after infra changes.

## Confidence summary

| Area | Rating | Reason |
| --- | --- | --- |
| Terraform dependency order | **High** | Documented + consistent with remote state. |
| IAM roles (worker + agent access/instance) | **High** | Implemented in `worker/` + `ecs-worker` module. |
| Env wiring for `CreateService` | **High** | Image + VPC connector + role aliases wired; `Settings` accepts `AGENT_*` and `HUB_BASE_URL`. |
| First production `CreateService` | **Medium** | Needs real ECR image, healthy `/health`, correct VPC connector SG paths to RDS, and account limits. |
| End-to-end without manual ECS rollout | **Medium–Low** | `ignore_changes` on `task_definition` may require `--force-new-deployment`. |

When P1–P4 and preconditions pass, you are in a good position to **provision on AWS today**; keep a short rollback plan (empty `APP_RUNNER_CREATE_IMAGE_IDENTIFIER` via `extra_environment` override is not supported—instead scale worker to 0 or remove create env in a hotfix task def if you must disable create).
