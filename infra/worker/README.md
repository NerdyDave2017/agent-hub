# Worker — Terraform root (`infra/worker`)

Deploys the **ECS Fargate worker** via `modules/ecs-worker`, **agent ECR access** and **agent instance** IAM roles for App Runner provisioning, and **EventBridge** rules (hourly metrics rollup + daily Gmail watch renewal) targeting the hub SQS queue, per [`docs/terraform-infra-instructions.md`](../../docs/terraform-infra-instructions.md).

## Dependencies

Remote state: **`vpc`**, **`rds`**, **`hub`**, **`secrets`**.

## Outputs

`agent_instance_policy_json` is consumed by **`infra/agents/incident-triage`** for the optional staging App Runner module.

`worker_app_runner_image_identifier` is the full **ECR URI:tag** wired into the worker as **`APP_RUNNER_CREATE_IMAGE_IDENTIFIER`** (worker `CreateService` path).

## App Runner per-agent provisioning (worker)

The ECS task receives:

- **`AGENT_ECR_ACCESS_ROLE_ARN`** / **`AGENT_INSTANCE_ROLE_ARN`** — IAM for App Runner (also read by app settings via aliases).
- **`APP_RUNNER_CREATE_IMAGE_IDENTIFIER`** / **`APP_RUNNER_CREATE_VPC_CONNECTOR_ARN`** — injected via **`extra_environment`** from this root (`concat` with optional `worker_extra_environment`).

See **[`../DEPLOYMENT-CHECKLIST.md`](../DEPLOYMENT-CHECKLIST.md)** for apply order and **ECS force-new-deployment** after task-definition changes.

## Files

| File | Role |
| --- | --- |
| `backend.tf` | S3 backend + versions |
| `main.tf` | Remote state, IAM roles, `ecs-worker` module, EventBridge |
| `variables.tf` / `outputs.tf` | Root interface |
| `terraform.tfvars.example` | Sample values |
