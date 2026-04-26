# `modules/vpc`

Implements the full VPC stack from [`docs/terraform-infra-instructions.md`](../../../docs/terraform-infra-instructions.md): public and private subnets, single NAT gateway, route tables, interface VPC endpoints (SQS, Secrets Manager, ECR API/DKR, CloudWatch Logs), security groups for endpoints and worker/RDS/App Runner connector, and the **App Runner VPC connector** used by the hub service.

Consumed by the **`infra/vpc/`** composition root (remote state key `vpc/terraform.tfstate`).
