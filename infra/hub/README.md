# Hub — Terraform root (`infra/hub`)

Creates the **hub App Runner** service, **hub instance IAM role** + inline policy, and **SQS** hub→worker queue (via `modules/sqs`), following [`docs/terraform-infra-instructions.md`](../../docs/terraform-infra-instructions.md).

## Dependencies

Remote state: **`vpc`**, **`rds`**, **`secrets`** (see `main.tf`).

## Two-phase SQS policy

`modules/sqs` allows the worker to consume only when `worker_role_arn` is non-empty. After the first **`infra/worker`** apply, set `worker_role_arn` in `terraform.tfvars` here and **re-apply** `infra/hub` so the queue policy includes `ReceiveMessage` / `DeleteMessage` for the worker task role.

## Files

| File | Role |
| --- | --- |
| `backend.tf` | S3 backend + provider version constraints |
| `providers.tf` | AWS provider |
| `main.tf` | Remote state, IAM, SQS module, App Runner module |
| `variables.tf` / `outputs.tf` | Root interface |
| `terraform.tfvars.example` | Copy to `terraform.tfvars` |
