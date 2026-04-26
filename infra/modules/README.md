# Shared Terraform modules

| Module | Purpose |
| --- | --- |
| **`vpc/`** | Full VPC: public/private subnets, NAT, IGW, interface endpoints (SQS, SM, ECR, logs), App Runner VPC connector, worker/RDS SGs. |
| **`rds/`** | Postgres `aws_db_instance`, subnet group, encrypted DB URL in Secrets Manager. |
| **`sqs/`** | Hub → worker queue + DLQ (SSE-KMS), queue policy for hub send + worker consume, optional DLQ alarm. |
| **`secrets/`** | Customer-managed KMS key + Langfuse + internal service token **secret resources** (populate values after apply). |
| **`app-runner/`** | ECR access role + optional managed instance role + `aws_apprunner_service` + log group. Hub sets `create_instance_role = false` and passes a pre-built instance role. |
| **`ecs-worker/`** | ECS cluster, Fargate worker service, execution + task IAM, CloudWatch log group. |

Apply only from **roots** under `infra/vpc`, `infra/hub`, etc., never `terraform apply` inside a module directory.
