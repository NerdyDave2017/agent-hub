# Infrastructure (Terraform)

Layout and apply order follow [`docs/terraform-infra-instructions.md`](../docs/terraform-infra-instructions.md).

## Directory layout

```text
infra/
├── terraform.tfvars.example   # sample vars for composition roots (copy per root)
├── modules/
│   ├── vpc/           # VPC, subnets, NAT, endpoints, SGs, App Runner VPC connector
│   ├── rds/           # Postgres + DB URL secret
│   ├── sqs/           # Hub → worker queue, DLQ, queue policy, DLQ alarm
│   ├── secrets/       # KMS + Langfuse + internal service token secrets
│   ├── app-runner/    # Reusable App Runner (hub + staging agents)
│   └── ecs-worker/    # Worker Fargate cluster, task, roles, service
├── vpc/               # Root: apply first (state key vpc/terraform.tfstate)
├── secrets/           # KMS + secret shells (before rds for encryption key)
├── rds/               # Depends on vpc + secrets
├── hub/               # Hub IAM, SQS, App Runner (depends on vpc, rds, secrets)
├── worker/            # Worker ECS + EventBridge (depends on hub outputs)
├── agents/
│   └── incident-triage/  # ECR + optional staging App Runner (depends on worker)
├── ci-oidc/           # GitHub OIDC + IAM role for Actions (no long-lived AWS keys in CI)
└── localstack/        # Local dev: SQS + IAM + Secrets Manager (no S3 backend)
```

## Remote state

Each AWS root uses the **S3 backend** defined in its `backend.tf` (`agent-hub-terraform-state` bucket and `agent-hub-terraform-locks` table). Create those once (see the deployment order section in `docs/terraform-infra-instructions.md`), then:

```bash
cd infra/vpc && terraform init && terraform apply
```

Use **`-backend-config`** files in CI if you do not hardcode the bucket name.

## Local development

Use **`infra/localstack/`** with `make local-provision` (see repository root `README.md`). That path does **not** use the production modules (no KMS on queues) so LocalStack stays lightweight.

## Same-day AWS provisioning

See **[`DEPLOYMENT-CHECKLIST.md`](DEPLOYMENT-CHECKLIST.md)** for order, worker App Runner env wiring, and post-apply ECS rollout notes.
