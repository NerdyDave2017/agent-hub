# Agent Hub — Terraform Infrastructure Instructions

> **For the AI coding agent.** Implement every module and root exactly as specified. Do not skip outputs — every module's outputs feed into other modules via `terraform_remote_state`. Do not combine modules. Do not add resources that are not specified. Read every section before writing any `.tf` file.

**Repository status:** The layout below is implemented under [`../infra/`](../infra/) (`modules/*`, `vpc/`, `rds/`, `secrets/`, `hub/`, `worker/`, `agents/incident-triage/`, plus `localstack/` for LocalStack dev). Small intentional deviations: hub SQS worker consume statement is omitted until `worker_role_arn` is set and hub is re-applied; RDS `engine_version` is pinned to `16.4`; EventBridge metrics target includes `role_arn` (per AWS requirements).

---

## Deployment Architecture (Reference)

```
Internet
  │ HTTPS
  ▼
App Runner (Hub)          — public, managed HTTPS, VPC connector → RDS
  │ SQS only
  ▼
ECS Fargate (Worker)      — private subnet, no inbound, SQS consumer
  │ HTTPS to App Runner URL
  ▼
App Runner (Agent)        — accessible HTTPS URL, worker is only caller
  │ HTTPS to Hub /internal/*
  ▼
App Runner (Hub)          — read-only enrichment endpoints
```

**IAM rule:** Hub, Worker, and each Agent have separate IAM roles. Least privilege — no role is a copy of another.

---

## Directory Structure to Create

```
infra/
├── backend.tf                        ← S3 remote state config (shared)
├── terraform.tfvars.example
├── modules/
│   ├── vpc/
│   │   ├── main.tf
│   │   ├── variables.tf
│   │   └── outputs.tf
│   ├── rds/
│   │   ├── main.tf
│   │   ├── variables.tf
│   │   └── outputs.tf
│   ├── sqs/
│   │   ├── main.tf
│   │   ├── variables.tf
│   │   └── outputs.tf
│   ├── secrets/
│   │   ├── main.tf
│   │   ├── variables.tf
│   │   └── outputs.tf
│   ├── app-runner/
│   │   ├── main.tf
│   │   ├── variables.tf
│   │   └── outputs.tf
│   └── ecs-worker/
│       ├── main.tf
│       ├── variables.tf
│       └── outputs.tf
├── hub/
│   ├── main.tf
│   ├── variables.tf
│   ├── outputs.tf
│   └── terraform.tfvars
├── worker/
│   ├── main.tf
│   ├── variables.tf
│   ├── outputs.tf
│   └── terraform.tfvars
└── agents/
    └── incident-triage/
        ├── main.tf
        ├── variables.tf
        ├── outputs.tf
        └── terraform.tfvars
```

---

## Remote State Configuration

### `infra/backend.tf` (copy into EVERY root module directory)

```hcl
# This file is identical in hub/, worker/, and agents/incident-triage/
# Change the key per root.

terraform {
  backend "s3" {
    bucket         = "agent-hub-terraform-state"
    key            = "hub/terraform.tfstate"   # change per root
    region         = "us-east-1"
    encrypt        = true
    dynamodb_table = "agent-hub-terraform-locks"
  }

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}
```

**State keys per root:**

- `hub/` → `"hub/terraform.tfstate"`
- `worker/` → `"worker/terraform.tfstate"`
- `agents/incident-triage/` → `"agents/incident-triage/terraform.tfstate"`

---

## Module: VPC (`infra/modules/vpc/`)

### `main.tf`

```hcl
# VPC with public + private subnets.
# App Runner uses a VPC connector to reach private subnets (for RDS).
# ECS Worker runs in private subnets.
# No ALB needed — App Runner handles HTTPS termination.

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = "${var.project}-${var.environment}-vpc" }
}

# ── Public subnets (for NAT gateway) ─────────────────────────────────
resource "aws_subnet" "public" {
  count             = length(var.availability_zones)
  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index)
  availability_zone = var.availability_zones[count.index]
  map_public_ip_on_launch = true

  tags = { Name = "${var.project}-${var.environment}-public-${count.index + 1}" }
}

# ── Private subnets (Worker ECS + RDS) ───────────────────────────────
resource "aws_subnet" "private" {
  count             = length(var.availability_zones)
  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index + 10)
  availability_zone = var.availability_zones[count.index]

  tags = { Name = "${var.project}-${var.environment}-private-${count.index + 1}" }
}

# ── Internet Gateway ──────────────────────────────────────────────────
resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "${var.project}-${var.environment}-igw" }
}

# ── NAT Gateway (one, in first public subnet) ─────────────────────────
resource "aws_eip" "nat" {
  domain = "vpc"
  tags   = { Name = "${var.project}-${var.environment}-nat-eip" }
}

resource "aws_nat_gateway" "main" {
  allocation_id = aws_eip.nat.id
  subnet_id     = aws_subnet.public[0].id
  tags          = { Name = "${var.project}-${var.environment}-nat" }
}

# ── Route Tables ──────────────────────────────────────────────────────
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }
  tags = { Name = "${var.project}-${var.environment}-public-rt" }
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.main.id
  }
  tags = { Name = "${var.project}-${var.environment}-private-rt" }
}

resource "aws_route_table_association" "public" {
  count          = length(aws_subnet.public)
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table_association" "private" {
  count          = length(aws_subnet.private)
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

# ── VPC Endpoints (reduce NAT cost for AWS services) ──────────────────
# SQS endpoint — worker consumes SQS without going through NAT
resource "aws_vpc_endpoint" "sqs" {
  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${var.aws_region}.sqs"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = aws_subnet.private[*].id
  security_group_ids  = [aws_security_group.vpc_endpoints.id]
  private_dns_enabled = true
  tags                = { Name = "${var.project}-${var.environment}-sqs-endpoint" }
}

# Secrets Manager endpoint — worker + ECS tasks read secrets without NAT
resource "aws_vpc_endpoint" "secretsmanager" {
  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${var.aws_region}.secretsmanager"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = aws_subnet.private[*].id
  security_group_ids  = [aws_security_group.vpc_endpoints.id]
  private_dns_enabled = true
  tags                = { Name = "${var.project}-${var.environment}-sm-endpoint" }
}

# ECR endpoints — worker pulls images without NAT
resource "aws_vpc_endpoint" "ecr_api" {
  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${var.aws_region}.ecr.api"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = aws_subnet.private[*].id
  security_group_ids  = [aws_security_group.vpc_endpoints.id]
  private_dns_enabled = true
  tags                = { Name = "${var.project}-${var.environment}-ecr-api-endpoint" }
}

resource "aws_vpc_endpoint" "ecr_dkr" {
  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${var.aws_region}.ecr.dkr"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = aws_subnet.private[*].id
  security_group_ids  = [aws_security_group.vpc_endpoints.id]
  private_dns_enabled = true
  tags                = { Name = "${var.project}-${var.environment}-ecr-dkr-endpoint" }
}

# CloudWatch Logs endpoint — worker + ECS log shipping
resource "aws_vpc_endpoint" "cloudwatch_logs" {
  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${var.aws_region}.logs"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = aws_subnet.private[*].id
  security_group_ids  = [aws_security_group.vpc_endpoints.id]
  private_dns_enabled = true
  tags                = { Name = "${var.project}-${var.environment}-cw-logs-endpoint" }
}

# ── Security group for VPC endpoints ─────────────────────────────────
resource "aws_security_group" "vpc_endpoints" {
  name        = "${var.project}-${var.environment}-vpc-endpoints-sg"
  description = "Allows HTTPS from private subnets to VPC endpoints"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "HTTPS from private subnets"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [for s in aws_subnet.private : s.cidr_block]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# ── App Runner VPC Connector ──────────────────────────────────────────
# Allows App Runner hub to reach RDS in private subnets
resource "aws_apprunner_vpc_connector" "hub" {
  vpc_connector_name = "${var.project}-${var.environment}-hub-connector"
  subnets            = aws_subnet.private[*].id
  security_groups    = [aws_security_group.app_runner_connector.id]
  tags               = { Name = "${var.project}-${var.environment}-hub-vpc-connector" }
}

resource "aws_security_group" "app_runner_connector" {
  name        = "${var.project}-${var.environment}-app-runner-connector-sg"
  description = "App Runner VPC Connector — hub outbound to RDS only"
  vpc_id      = aws_vpc.main.id

  # No inbound — App Runner connector is outbound only
  egress {
    description = "PostgreSQL to RDS"
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = [for s in aws_subnet.private : s.cidr_block]
  }

  egress {
    description = "HTTPS to AWS services via VPC endpoints"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# ── Worker ECS Security Group ─────────────────────────────────────────
resource "aws_security_group" "worker" {
  name        = "${var.project}-${var.environment}-worker-sg"
  description = "Worker ECS tasks — no inbound HTTP, outbound to RDS + AWS services"
  vpc_id      = aws_vpc.main.id

  # NO inbound rules — worker only consumes from SQS, never receives HTTP

  egress {
    description = "PostgreSQL to RDS"
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = [for s in aws_subnet.private : s.cidr_block]
  }

  egress {
    description = "HTTPS to SQS, Secrets Manager, ECR, App Runner agents, external"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# ── RDS Security Group ────────────────────────────────────────────────
resource "aws_security_group" "rds" {
  name        = "${var.project}-${var.environment}-rds-sg"
  description = "RDS Postgres — accepts connections from worker and App Runner connector"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "PostgreSQL from worker"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.worker.id]
  }

  ingress {
    description     = "PostgreSQL from App Runner VPC connector (hub)"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.app_runner_connector.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}
```

### `variables.tf`

```hcl
variable "project"            { type = string }
variable "environment"        { type = string }
variable "aws_region"         { type = string }
variable "vpc_cidr"           { type = string  default = "10.0.0.0/16" }
variable "availability_zones" { type = list(string) }
```

### `outputs.tf`

```hcl
output "vpc_id"                      { value = aws_vpc.main.id }
output "private_subnet_ids"          { value = aws_subnet.private[*].id }
output "public_subnet_ids"           { value = aws_subnet.public[*].id }
output "worker_sg_id"                { value = aws_security_group.worker.id }
output "rds_sg_id"                   { value = aws_security_group.rds.id }
output "app_runner_connector_sg_id"  { value = aws_security_group.app_runner_connector.id }
output "app_runner_vpc_connector_arn"{ value = aws_apprunner_vpc_connector.hub.arn }
```

---

## Module: RDS (`infra/modules/rds/`)

### `main.tf`

```hcl
resource "aws_db_subnet_group" "main" {
  name       = "${var.project}-${var.environment}-db-subnet-group"
  subnet_ids = var.private_subnet_ids
  tags       = { Name = "${var.project}-${var.environment}-db-subnet-group" }
}

resource "aws_db_instance" "postgres" {
  identifier             = "${var.project}-${var.environment}-postgres"
  engine                 = "postgres"
  engine_version         = "16.3"
  instance_class         = var.instance_class   # "db.t4g.micro" for dev, "db.t4g.small" for prod
  allocated_storage      = 20
  max_allocated_storage  = 100
  storage_type           = "gp3"
  storage_encrypted      = true
  kms_key_id             = var.kms_key_arn

  db_name  = var.db_name      # "agent_hub"
  username = var.db_username  # "agent_hub_admin"
  password = var.db_password  # from tfvars, never hardcoded

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [var.rds_sg_id]

  # No public access — only reachable from inside VPC
  publicly_accessible = false

  backup_retention_period = 7
  deletion_protection     = var.environment == "production" ? true : false
  skip_final_snapshot     = var.environment == "production" ? false : true
  final_snapshot_identifier = "${var.project}-${var.environment}-final-snapshot"

  performance_insights_enabled = true

  tags = { Name = "${var.project}-${var.environment}-postgres" }
}

# Store DB connection string in Secrets Manager
# All services read from this secret — never hardcode the URL
resource "aws_secretsmanager_secret" "db_url" {
  name                    = "${var.project}/${var.environment}/database/url"
  kms_key_id              = var.kms_key_arn
  recovery_window_in_days = 7
  tags                    = { Name = "${var.project}-${var.environment}-db-url" }
}

resource "aws_secretsmanager_secret_version" "db_url" {
  secret_id     = aws_secretsmanager_secret.db_url.id
  secret_string = "postgresql+asyncpg://${var.db_username}:${var.db_password}@${aws_db_instance.postgres.endpoint}/${var.db_name}"
}
```

### `variables.tf`

```hcl
variable "project"           { type = string }
variable "environment"       { type = string }
variable "private_subnet_ids"{ type = list(string) }
variable "rds_sg_id"         { type = string }
variable "kms_key_arn"       { type = string }
variable "instance_class"    { type = string  default = "db.t4g.micro" }
variable "db_name"           { type = string  default = "agent_hub" }
variable "db_username"       { type = string  default = "agent_hub_admin" }
variable "db_password"       { type = string  sensitive = true }
```

### `outputs.tf`

```hcl
output "db_endpoint"       { value = aws_db_instance.postgres.endpoint }
output "db_name"           { value = aws_db_instance.postgres.db_name }
output "db_secret_arn"     { value = aws_secretsmanager_secret.db_url.arn }
output "db_instance_id"    { value = aws_db_instance.postgres.id }
```

---

## Module: SQS (`infra/modules/sqs/`)

### `main.tf`

```hcl
# ── Hub → Worker queue (main job queue) ──────────────────────────────
resource "aws_sqs_queue" "hub_dlq" {
  name                       = "${var.project}-${var.environment}-hub-dlq"
  message_retention_seconds  = 1209600  # 14 days
  kms_master_key_id          = var.kms_key_id
  tags                       = { Name = "${var.project}-${var.environment}-hub-dlq" }
}

resource "aws_sqs_queue" "hub" {
  name                        = "${var.project}-${var.environment}-hub-queue"
  visibility_timeout_seconds  = 300       # 5 minutes — matches worker task timeout
  message_retention_seconds   = 86400     # 1 day
  receive_wait_time_seconds   = 20        # long polling
  kms_master_key_id           = var.kms_key_id

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.hub_dlq.arn
    maxReceiveCount     = 3
  })

  tags = { Name = "${var.project}-${var.environment}-hub-queue" }
}

# ── Queue policy: allow hub App Runner role to send ───────────────────
resource "aws_sqs_queue_policy" "hub" {
  queue_url = aws_sqs_queue.hub.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AllowHubSend"
        Effect    = "Allow"
        Principal = { AWS = var.hub_role_arn }
        Action    = ["sqs:SendMessage"]
        Resource  = aws_sqs_queue.hub.arn
      },
      {
        Sid       = "AllowWorkerConsume"
        Effect    = "Allow"
        Principal = { AWS = var.worker_role_arn }
        Action    = [
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes",
          "sqs:ChangeMessageVisibility",
        ]
        Resource  = aws_sqs_queue.hub.arn
      }
    ]
  })
}

# ── DLQ alarm ─────────────────────────────────────────────────────────
resource "aws_cloudwatch_metric_alarm" "dlq_depth" {
  alarm_name          = "${var.project}-${var.environment}-dlq-messages"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "ApproximateNumberOfMessagesVisible"
  namespace           = "AWS/SQS"
  period              = 60
  statistic           = "Sum"
  threshold           = 0
  alarm_description   = "DLQ has messages — worker job failures require investigation"
  alarm_actions       = var.alarm_sns_arns

  dimensions = {
    QueueName = aws_sqs_queue.hub_dlq.name
  }
}
```

### `variables.tf`

```hcl
variable "project"        { type = string }
variable "environment"    { type = string }
variable "kms_key_id"     { type = string }
variable "hub_role_arn"   { type = string }
variable "worker_role_arn"{ type = string }
variable "alarm_sns_arns" { type = list(string)  default = [] }
```

### `outputs.tf`

```hcl
output "hub_queue_url"  { value = aws_sqs_queue.hub.id }
output "hub_queue_arn"  { value = aws_sqs_queue.hub.arn }
output "hub_dlq_url"    { value = aws_sqs_queue.hub_dlq.id }
output "hub_dlq_arn"    { value = aws_sqs_queue.hub_dlq.arn }
```

---

## Module: Secrets (`infra/modules/secrets/`)

### `main.tf`

```hcl
# ── KMS key for all secrets ───────────────────────────────────────────
resource "aws_kms_key" "main" {
  description             = "${var.project} ${var.environment} secrets encryption key"
  deletion_window_in_days = 10
  enable_key_rotation     = true

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "EnableRootAccess"
        Effect = "Allow"
        Principal = { AWS = "arn:aws:iam::${var.aws_account_id}:root" }
        Action   = "kms:*"
        Resource = "*"
      },
      {
        Sid    = "AllowServiceRoles"
        Effect = "Allow"
        Principal = {
          AWS = concat(
            [var.hub_role_arn, var.worker_role_arn],
            var.agent_role_arns
          )
        }
        Action   = ["kms:Decrypt", "kms:GenerateDataKey"]
        Resource = "*"
      }
    ]
  })

  tags = { Name = "${var.project}-${var.environment}-kms" }
}

resource "aws_kms_alias" "main" {
  name          = "alias/${var.project}-${var.environment}"
  target_key_id = aws_kms_key.main.key_id
}

# ── Langfuse credentials (operator-level — shared) ────────────────────
resource "aws_secretsmanager_secret" "langfuse" {
  name                    = "${var.project}/${var.environment}/langfuse/credentials"
  kms_key_id              = aws_kms_key.main.arn
  recovery_window_in_days = 7
  tags                    = { Name = "${var.project}-${var.environment}-langfuse-creds" }
}
# Populate manually after creation:
# aws secretsmanager put-secret-value \
#   --secret-id agent-hub/production/langfuse/credentials \
#   --secret-string '{"public_key":"pk-...","secret_key":"sk-..."}'

# ── Internal service token (worker ↔ agent auth) ──────────────────────
resource "aws_secretsmanager_secret" "internal_service_token" {
  name                    = "${var.project}/${var.environment}/internal/service-token"
  kms_key_id              = aws_kms_key.main.arn
  recovery_window_in_days = 7
  tags                    = { Name = "${var.project}-${var.environment}-service-token" }
}
# Populate with a random 64-char token:
# openssl rand -hex 32 | aws secretsmanager put-secret-value \
#   --secret-id agent-hub/production/internal/service-token --secret-string file:///dev/stdin

# ── Per-tenant/agent secrets (naming convention — created by worker) ──
# Worker creates these at provisioning time using this naming pattern:
# Pattern: agent-hub/{env}/tenant/{tenantId}/agent/{agentId}/{integration}
# Examples:
#   agent-hub/production/tenant/abc123/agent/xyz789/gmail_credentials
#   agent-hub/production/tenant/abc123/agent/xyz789/slack_token
#   agent-hub/production/tenant/abc123/agent/xyz789/hub_service_token
# These are NOT pre-created in Terraform — worker creates them dynamically.
# IAM policies use a wildcard path to grant access.
```

### `variables.tf`

```hcl
variable "project"          { type = string }
variable "environment"      { type = string }
variable "aws_account_id"   { type = string }
variable "aws_region"       { type = string }
variable "hub_role_arn"     { type = string }
variable "worker_role_arn"  { type = string }
variable "agent_role_arns"  { type = list(string)  default = [] }
```

### `outputs.tf`

```hcl
output "kms_key_arn"                  { value = aws_kms_key.main.arn }
output "kms_key_id"                   { value = aws_kms_key.main.key_id }
output "langfuse_secret_arn"          { value = aws_secretsmanager_secret.langfuse.arn }
output "internal_service_token_arn"   { value = aws_secretsmanager_secret.internal_service_token.arn }
```

---

## Module: App Runner (`infra/modules/app-runner/`)

Reused for both hub and each agent. Variables control differences.

### `main.tf`

```hcl
# ── ECR access role (App Runner → ECR) ───────────────────────────────
resource "aws_iam_role" "ecr_access" {
  name = "${var.project}-${var.environment}-${var.service_name}-ecr-access"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "build.apprunner.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ecr_access" {
  role       = aws_iam_role.ecr_access.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSAppRunnerServicePolicyForECRAccess"
}

# ── Instance role (what the running container can do) ─────────────────
resource "aws_iam_role" "instance" {
  name = "${var.project}-${var.environment}-${var.service_name}-instance"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "tasks.apprunner.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "instance" {
  name   = "${var.project}-${var.environment}-${var.service_name}-policy"
  role   = aws_iam_role.instance.id
  policy = var.instance_policy_json
}

# ── App Runner service ────────────────────────────────────────────────
resource "aws_apprunner_service" "main" {
  service_name = "${var.project}-${var.environment}-${var.service_name}"

  source_configuration {
    image_repository {
      image_identifier      = var.image_uri
      image_repository_type = "ECR"

      image_configuration {
        port = var.container_port

        runtime_environment_variables = var.environment_variables

        runtime_environment_secrets = var.environment_secrets
      }
    }

    authentication_configuration {
      access_role_arn = aws_iam_role.ecr_access.arn
    }

    auto_deployments_enabled = false
  }

  instance_configuration {
    cpu               = var.cpu
    memory            = var.memory
    instance_role_arn = aws_iam_role.instance.arn
  }

  health_check_configuration {
    protocol            = "HTTP"
    path                = var.health_check_path
    interval            = 10
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  # VPC connector — only for hub (needs to reach private RDS)
  dynamic "network_configuration" {
    for_each = var.vpc_connector_arn != "" ? [1] : []
    content {
      egress_configuration {
        egress_type       = "VPC"
        vpc_connector_arn = var.vpc_connector_arn
      }
    }
  }

  tags = {
    Name        = "${var.project}-${var.environment}-${var.service_name}"
    Environment = var.environment
    Service     = var.service_name
  }
}

# ── CloudWatch log group ──────────────────────────────────────────────
resource "aws_cloudwatch_log_group" "main" {
  name              = "/apprunner/${var.project}/${var.environment}/${var.service_name}"
  retention_in_days = var.environment == "production" ? 30 : 7
  kms_key_id        = var.kms_key_arn
}
```

### `variables.tf`

```hcl
variable "project"               { type = string }
variable "environment"           { type = string }
variable "service_name"          { type = string }
variable "image_uri"             { type = string }
variable "container_port"        { type = string  default = "8080" }
variable "cpu"                   { type = string  default = "1 vCPU" }
variable "memory"                { type = string  default = "2 GB" }
variable "health_check_path"     { type = string  default = "/health" }
variable "vpc_connector_arn"     { type = string  default = "" }
variable "kms_key_arn"           { type = string }
variable "instance_policy_json"  { type = string }

variable "environment_variables" {
  type    = map(string)
  default = {}
  description = "Non-secret environment variables injected into container"
}

variable "environment_secrets" {
  type    = map(string)
  default = {}
  description = "Map of env var name → Secrets Manager ARN. App Runner resolves at start."
}
```

### `outputs.tf`

```hcl
output "service_url"          { value = "https://${aws_apprunner_service.main.service_url}" }
output "service_arn"          { value = aws_apprunner_service.main.arn }
output "service_id"           { value = aws_apprunner_service.main.service_id }
output "instance_role_arn"    { value = aws_iam_role.instance.arn }
output "instance_role_name"   { value = aws_iam_role.instance.name }
```

---

## Module: ECS Worker (`infra/modules/ecs-worker/`)

### `main.tf`

```hcl
# ── ECS Cluster ───────────────────────────────────────────────────────
resource "aws_ecs_cluster" "worker" {
  name = "${var.project}-${var.environment}-worker"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  tags = { Name = "${var.project}-${var.environment}-worker-cluster" }
}

# ── CloudWatch Log Group ──────────────────────────────────────────────
resource "aws_cloudwatch_log_group" "worker" {
  name              = "/ecs/${var.project}/${var.environment}/worker"
  retention_in_days = var.environment == "production" ? 30 : 7
  kms_key_id        = var.kms_key_arn
}

# ── Task Execution Role (ECS infrastructure — pull image, log) ────────
resource "aws_iam_role" "execution" {
  name = "${var.project}-${var.environment}-worker-execution"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "execution_basic" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# Allow execution role to read secrets (for env var injection)
resource "aws_iam_role_policy" "execution_secrets" {
  name = "${var.project}-${var.environment}-worker-execution-secrets"
  role = aws_iam_role.execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue", "kms:Decrypt"]
      Resource = [
        var.db_secret_arn,
        var.kms_key_arn,
      ]
    }]
  })
}

# ── Task Role (what the worker app can do) ────────────────────────────
resource "aws_iam_role" "task" {
  name = "${var.project}-${var.environment}-worker-task"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "task" {
  name = "${var.project}-${var.environment}-worker-task-policy"
  role = aws_iam_role.task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # ── SQS: consume from hub queue only ──────────────────────────
      {
        Sid    = "SQSConsume"
        Effect = "Allow"
        Action = [
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes",
          "sqs:ChangeMessageVisibility",
        ]
        Resource = var.hub_queue_arn
      },

      # ── RDS: connect via Secrets Manager (no direct RDS IAM auth needed) ──
      # Worker reads DB URL from Secrets Manager

      # ── Secrets Manager: read DB url + per-tenant credentials ─────
      {
        Sid    = "SecretsRead"
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue",
          "secretsmanager:DescribeSecret",
        ]
        Resource = [
          var.db_secret_arn,
          var.internal_service_token_arn,
          # Wildcard for per-tenant agent secrets — worker creates and reads these
          "arn:aws:secretsmanager:${var.aws_region}:${var.aws_account_id}:secret:${var.project}/${var.environment}/tenant/*",
        ]
      },

      # ── Secrets Manager: create/update per-tenant secrets ─────────
      {
        Sid    = "SecretsWrite"
        Effect = "Allow"
        Action = [
          "secretsmanager:CreateSecret",
          "secretsmanager:PutSecretValue",
          "secretsmanager:UpdateSecret",
          "secretsmanager:TagResource",
        ]
        Resource = [
          "arn:aws:secretsmanager:${var.aws_region}:${var.aws_account_id}:secret:${var.project}/${var.environment}/tenant/*",
        ]
      },

      # ── KMS: decrypt secrets ──────────────────────────────────────
      {
        Sid    = "KMSDecrypt"
        Effect = "Allow"
        Action = ["kms:Decrypt", "kms:GenerateDataKey"]
        Resource = var.kms_key_arn
      },

      # ── App Runner: create/update/describe services (agent provisioning) ──
      {
        Sid    = "AppRunnerProvision"
        Effect = "Allow"
        Action = [
          "apprunner:CreateService",
          "apprunner:UpdateService",
          "apprunner:DeleteService",
          "apprunner:DescribeService",
          "apprunner:ListServices",
          "apprunner:PauseService",
          "apprunner:ResumeService",
          "apprunner:TagResource",
        ]
        Resource = "*"
      },

      # ── IAM: pass the agent instance role to App Runner ───────────
      # Worker must pass the pre-created agent instance role when creating
      # App Runner services. Scoped to roles matching naming convention.
      {
        Sid    = "IAMPassAgentRole"
        Effect = "Allow"
        Action = "iam:PassRole"
        Resource = [
          "arn:aws:iam::${var.aws_account_id}:role/${var.project}-${var.environment}-agent-*",
        ]
        Condition = {
          StringEquals = {
            "iam:PassedToService" = [
              "apprunner.amazonaws.com",
              "build.apprunner.amazonaws.com",
              "tasks.apprunner.amazonaws.com",
            ]
          }
        }
      },

      # ── ECR: describe images for provisioning validation ──────────
      {
        Sid    = "ECRDescribe"
        Effect = "Allow"
        Action = [
          "ecr:DescribeImages",
          "ecr:DescribeRepositories",
          "ecr:GetAuthorizationToken",
        ]
        Resource = "*"
      },

      # ── CloudWatch: write structured logs ────────────────────────
      {
        Sid    = "CloudWatchLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents",
        ]
        Resource = "${aws_cloudwatch_log_group.worker.arn}:*"
      },

      # ── CloudWatch: create agent-specific log groups ───────────────
      {
        Sid    = "CloudWatchCreateLogGroups"
        Effect = "Allow"
        Action = ["logs:CreateLogGroup", "logs:TagLogGroup"]
        Resource = "arn:aws:logs:${var.aws_region}:${var.aws_account_id}:log-group:/apprunner/${var.project}/${var.environment}/agent-*"
      },
    ]
  })
}

# ── Task Definition ────────────────────────────────────────────────────
resource "aws_ecs_task_definition" "worker" {
  family                   = "${var.project}-${var.environment}-worker"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.cpu     # "512"
  memory                   = var.memory  # "1024"

  execution_role_arn = aws_iam_role.execution.arn
  task_role_arn      = aws_iam_role.task.arn

  container_definitions = jsonencode([{
    name      = "worker"
    image     = var.image_uri
    essential = true

    environment = [
      { name = "ENVIRONMENT",               value = var.environment },
      { name = "AWS_REGION",                value = var.aws_region },
      { name = "HUB_QUEUE_URL",             value = var.hub_queue_url },
      { name = "HUB_BASE_URL",              value = var.hub_base_url },
      { name = "GCP_PROJECT_ID",            value = var.gcp_project_id },
      { name = "GMAIL_PUBSUB_TOPIC",        value = var.gmail_pubsub_topic },
      { name = "ECR_REGISTRY",              value = var.ecr_registry },
      { name = "AGENT_INSTANCE_ROLE_ARN",   value = aws_iam_role.task.arn },
      # Worker passes this ARN when creating agent App Runner services
      { name = "AGENT_ECR_ACCESS_ROLE_ARN", value = var.agent_ecr_access_role_arn },
    ]

    secrets = [
      {
        name      = "DATABASE_URL"
        valueFrom = var.db_secret_arn
      },
      {
        name      = "INTERNAL_SERVICE_TOKEN"
        valueFrom = var.internal_service_token_arn
      },
    ]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.worker.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "ecs"
      }
    }

    healthCheck = {
      command     = ["CMD-SHELL", "python -c \"import sys; sys.exit(0)\""]
      interval    = 30
      timeout     = 5
      retries     = 3
      startPeriod = 60
    }
  }])

  tags = { Name = "${var.project}-${var.environment}-worker-task" }
}

# ── ECS Service ────────────────────────────────────────────────────────
resource "aws_ecs_service" "worker" {
  name            = "${var.project}-${var.environment}-worker"
  cluster         = aws_ecs_cluster.worker.id
  task_definition = aws_ecs_task_definition.worker.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [var.worker_sg_id]
    assign_public_ip = false   # private subnet + NAT for outbound
  }

  # No load balancer — worker has no inbound HTTP
  # No service discovery — worker is not called by anything

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  tags = { Name = "${var.project}-${var.environment}-worker-service" }

  lifecycle {
    ignore_changes = [task_definition]  # CI/CD updates task def externally
  }
}
```

### `variables.tf`

```hcl
variable "project"                    { type = string }
variable "environment"                { type = string }
variable "aws_region"                 { type = string }
variable "aws_account_id"             { type = string }
variable "image_uri"                  { type = string }
variable "cpu"                        { type = string  default = "512" }
variable "memory"                     { type = string  default = "1024" }
variable "private_subnet_ids"         { type = list(string) }
variable "worker_sg_id"               { type = string }
variable "hub_queue_url"              { type = string }
variable "hub_queue_arn"              { type = string }
variable "db_secret_arn"              { type = string }
variable "kms_key_arn"                { type = string }
variable "internal_service_token_arn" { type = string }
variable "hub_base_url"               { type = string }
variable "gcp_project_id"             { type = string }
variable "gmail_pubsub_topic"         { type = string }
variable "ecr_registry"               { type = string }
variable "agent_ecr_access_role_arn"  { type = string }
```

### `outputs.tf`

```hcl
output "cluster_arn"       { value = aws_ecs_cluster.worker.arn }
output "task_role_arn"     { value = aws_iam_role.task.arn }
output "task_role_name"    { value = aws_iam_role.task.name }
output "execution_role_arn"{ value = aws_iam_role.execution.arn }
output "service_name"      { value = aws_ecs_service.worker.name }
output "log_group_name"    { value = aws_cloudwatch_log_group.worker.name }
```

---

## Root: Hub (`infra/hub/`)

### `main.tf`

```hcl
locals {
  project     = "agent-hub"
  environment = var.environment
}

# ── Read shared state ─────────────────────────────────────────────────
data "terraform_remote_state" "vpc" {
  backend = "s3"
  config  = {
    bucket = "agent-hub-terraform-state"
    key    = "vpc/terraform.tfstate"
    region = var.aws_region
  }
}

data "terraform_remote_state" "rds" {
  backend = "s3"
  config  = {
    bucket = "agent-hub-terraform-state"
    key    = "rds/terraform.tfstate"
    region = var.aws_region
  }
}

data "terraform_remote_state" "secrets" {
  backend = "s3"
  config  = {
    bucket = "agent-hub-terraform-state"
    key    = "secrets/terraform.tfstate"
    region = var.aws_region
  }
}

# ── Hub IAM role (created first so SQS module can reference it) ───────
resource "aws_iam_role" "hub" {
  name = "${local.project}-${local.environment}-hub-instance"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "tasks.apprunner.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "hub" {
  name = "${local.project}-${local.environment}-hub-policy"
  role = aws_iam_role.hub.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # ── SQS: send jobs to hub queue only ──────────────────────────
      {
        Sid      = "SQSSend"
        Effect   = "Allow"
        Action   = ["sqs:SendMessage", "sqs:GetQueueUrl", "sqs:GetQueueAttributes"]
        Resource = module.sqs.hub_queue_arn
      },

      # ── Secrets Manager: read per-tenant agent secrets ─────────────
      # Hub reads these during OAuth callback to store credentials
      {
        Sid    = "SecretsReadWrite"
        Effect = "Allow"
        Action = [
          "secretsmanager:CreateSecret",
          "secretsmanager:PutSecretValue",
          "secretsmanager:GetSecretValue",
          "secretsmanager:DescribeSecret",
          "secretsmanager:TagResource",
        ]
        Resource = [
          "arn:aws:secretsmanager:${var.aws_region}:${var.aws_account_id}:secret:${local.project}/${local.environment}/tenant/*",
          data.terraform_remote_state.secrets.outputs.internal_service_token_arn,
        ]
      },

      # ── KMS: decrypt secrets ──────────────────────────────────────
      {
        Sid    = "KMSDecrypt"
        Effect = "Allow"
        Action = ["kms:Decrypt", "kms:GenerateDataKey"]
        Resource = data.terraform_remote_state.secrets.outputs.kms_key_arn
      },

      # ── CloudWatch Logs ───────────────────────────────────────────
      {
        Sid    = "CloudWatchLogs"
        Effect = "Allow"
        Action = ["logs:CreateLogStream", "logs:PutLogEvents", "logs:CreateLogGroup"]
        Resource = "arn:aws:logs:${var.aws_region}:${var.aws_account_id}:log-group:/apprunner/${local.project}/${local.environment}/hub*"
      },
    ]
  })
}

# ── SQS queues (depends on hub role ARN) ─────────────────────────────
module "sqs" {
  source = "../modules/sqs"

  project        = local.project
  environment    = local.environment
  kms_key_id     = data.terraform_remote_state.secrets.outputs.kms_key_id
  hub_role_arn   = aws_iam_role.hub.arn
  worker_role_arn = var.worker_role_arn   # passed in after worker is deployed
  alarm_sns_arns  = var.alarm_sns_arns
}

# ── Hub App Runner Service ────────────────────────────────────────────
module "hub" {
  source = "../modules/app-runner"

  project          = local.project
  environment      = local.environment
  service_name     = "hub"
  image_uri        = var.hub_image_uri
  container_port   = "8000"
  cpu              = "1 vCPU"
  memory           = "2 GB"
  health_check_path = "/health"
  vpc_connector_arn = data.terraform_remote_state.vpc.outputs.app_runner_vpc_connector_arn
  kms_key_arn       = data.terraform_remote_state.secrets.outputs.kms_key_arn
  instance_policy_json = aws_iam_role_policy.hub.policy  # reference above role policy

  environment_variables = {
    ENVIRONMENT        = local.environment
    AWS_REGION         = var.aws_region
    HUB_QUEUE_URL      = module.sqs.hub_queue_url
    FRONTEND_URL       = var.frontend_url
    GMAIL_PUBSUB_TOPIC = var.gmail_pubsub_topic
    GCP_PROJECT_ID     = var.gcp_project_id
  }

  environment_secrets = {
    DATABASE_URL             = data.terraform_remote_state.rds.outputs.db_secret_arn
    GMAIL_OAUTH_CLIENT_ID    = var.gmail_oauth_client_secret_arn
    GMAIL_OAUTH_CLIENT_SECRET= var.gmail_oauth_client_secret_arn
    GMAIL_WEBHOOK_SECRET     = var.gmail_webhook_secret_arn
    INTERNAL_SERVICE_TOKEN   = data.terraform_remote_state.secrets.outputs.internal_service_token_arn
  }
}
```

### `variables.tf`

```hcl
variable "environment"                    { type = string }
variable "aws_region"                     { type = string  default = "us-east-1" }
variable "aws_account_id"                 { type = string }
variable "hub_image_uri"                  { type = string }
variable "worker_role_arn"                { type = string  default = "" }
variable "frontend_url"                   { type = string }
variable "gmail_pubsub_topic"             { type = string }
variable "gcp_project_id"                 { type = string }
variable "gmail_oauth_client_secret_arn"  { type = string }
variable "gmail_webhook_secret_arn"       { type = string }
variable "alarm_sns_arns"                 { type = list(string)  default = [] }
```

### `outputs.tf`

```hcl
output "hub_service_url"    { value = module.hub.service_url }
output "hub_service_arn"    { value = module.hub.service_arn }
output "hub_role_arn"       { value = aws_iam_role.hub.arn }
output "hub_queue_url"      { value = module.sqs.hub_queue_url }
output "hub_queue_arn"      { value = module.sqs.hub_queue_arn }
```

---

## Root: Worker (`infra/worker/`)

### `main.tf`

```hcl
locals {
  project     = "agent-hub"
  environment = var.environment
}

data "terraform_remote_state" "vpc" {
  backend = "s3"
  config  = { bucket = "agent-hub-terraform-state", key = "vpc/terraform.tfstate", region = var.aws_region }
}

data "terraform_remote_state" "rds" {
  backend = "s3"
  config  = { bucket = "agent-hub-terraform-state", key = "rds/terraform.tfstate", region = var.aws_region }
}

data "terraform_remote_state" "hub" {
  backend = "s3"
  config  = { bucket = "agent-hub-terraform-state", key = "hub/terraform.tfstate", region = var.aws_region }
}

data "terraform_remote_state" "secrets" {
  backend = "s3"
  config  = { bucket = "agent-hub-terraform-state", key = "secrets/terraform.tfstate", region = var.aws_region }
}

# ── Pre-create agent ECR access role (worker passes this when creating agents) ─
# This role is created here so worker knows its ARN before provisioning agents.
resource "aws_iam_role" "agent_ecr_access" {
  name = "${local.project}-${local.environment}-agent-ecr-access"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "build.apprunner.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "agent_ecr_access" {
  role       = aws_iam_role.agent_ecr_access.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSAppRunnerServicePolicyForECRAccess"
}

# ── Agent instance role template (one per agent type) ─────────────────
# Worker uses this ARN when creating App Runner services for agents.
# Each agent gets the same role — they share a permission boundary.
resource "aws_iam_role" "agent_instance" {
  name = "${local.project}-${local.environment}-agent-instance"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "tasks.apprunner.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "agent_instance" {
  name = "${local.project}-${local.environment}-agent-instance-policy"
  role = aws_iam_role.agent_instance.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # ── Secrets Manager: read own credentials only ────────────────
      # Agent reads its credentials using ARNs injected by worker at deploy time.
      # Wildcard scoped to tenant path to support all provisioned agents.
      {
        Sid    = "SecretsRead"
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue",
          "secretsmanager:DescribeSecret",
        ]
        Resource = [
          "arn:aws:secretsmanager:${var.aws_region}:${var.aws_account_id}:secret:${local.project}/${local.environment}/tenant/*",
          data.terraform_remote_state.secrets.outputs.langfuse_secret_arn,
        ]
      },

      # ── KMS: decrypt secrets ──────────────────────────────────────
      {
        Sid    = "KMSDecrypt"
        Effect = "Allow"
        Action = ["kms:Decrypt", "kms:GenerateDataKey"]
        Resource = data.terraform_remote_state.secrets.outputs.kms_key_arn
      },

      # ── CloudWatch Logs ───────────────────────────────────────────
      {
        Sid    = "CloudWatchLogs"
        Effect = "Allow"
        Action = ["logs:CreateLogStream", "logs:PutLogEvents", "logs:CreateLogGroup"]
        Resource = "arn:aws:logs:${var.aws_region}:${var.aws_account_id}:log-group:/apprunner/${local.project}/${local.environment}/agent-*"
      },

      # ── SES: send tenant emails ───────────────────────────────────
      {
        Sid      = "SESSend"
        Effect   = "Allow"
        Action   = ["ses:SendEmail", "ses:SendRawEmail"]
        Resource = "*"
        Condition = {
          StringEquals = { "ses:FromAddress" = "noreply@${var.domain_name}" }
        }
      },
    ]
  })
}

# ── Worker ECS module ─────────────────────────────────────────────────
module "worker" {
  source = "../modules/ecs-worker"

  project                   = local.project
  environment               = local.environment
  aws_region                = var.aws_region
  aws_account_id            = var.aws_account_id
  image_uri                 = var.worker_image_uri
  cpu                       = "512"
  memory                    = "1024"
  private_subnet_ids        = data.terraform_remote_state.vpc.outputs.private_subnet_ids
  worker_sg_id              = data.terraform_remote_state.vpc.outputs.worker_sg_id
  hub_queue_url             = data.terraform_remote_state.hub.outputs.hub_queue_url
  hub_queue_arn             = data.terraform_remote_state.hub.outputs.hub_queue_arn
  db_secret_arn             = data.terraform_remote_state.rds.outputs.db_secret_arn
  kms_key_arn               = data.terraform_remote_state.secrets.outputs.kms_key_arn
  internal_service_token_arn = data.terraform_remote_state.secrets.outputs.internal_service_token_arn
  hub_base_url              = data.terraform_remote_state.hub.outputs.hub_service_url
  gcp_project_id            = var.gcp_project_id
  gmail_pubsub_topic        = var.gmail_pubsub_topic
  ecr_registry              = var.ecr_registry
  agent_ecr_access_role_arn = aws_iam_role.agent_ecr_access.arn
}

# ── EventBridge: daily metrics rollup trigger ─────────────────────────
resource "aws_cloudwatch_event_rule" "metrics_rollup" {
  name                = "${local.project}-${local.environment}-metrics-rollup"
  description         = "Trigger metrics rollup job hourly"
  schedule_expression = "rate(1 hour)"
}

resource "aws_cloudwatch_event_target" "metrics_rollup" {
  rule      = aws_cloudwatch_event_rule.metrics_rollup.name
  target_id = "MetricsRollupSQS"
  arn       = data.terraform_remote_state.hub.outputs.hub_queue_arn

  input = jsonencode({
    job_type       = "metrics_rollup"
    correlation_id = "scheduled-metrics-rollup"
    payload        = {}
  })
}

resource "aws_iam_role" "eventbridge_sqs" {
  name = "${local.project}-${local.environment}-eventbridge-sqs"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "events.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "eventbridge_sqs" {
  name = "${local.project}-${local.environment}-eventbridge-sqs-policy"
  role = aws_iam_role.eventbridge_sqs.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "sqs:SendMessage"
      Resource = data.terraform_remote_state.hub.outputs.hub_queue_arn
    }]
  })
}

# ── EventBridge: daily Gmail watch renewal ────────────────────────────
resource "aws_cloudwatch_event_rule" "gmail_watch_renewal" {
  name                = "${local.project}-${local.environment}-gmail-watch-renewal"
  description         = "Renew Gmail push watch subscriptions daily at 3am UTC"
  schedule_expression = "cron(0 3 * * ? *)"
}

resource "aws_cloudwatch_event_target" "gmail_watch_renewal" {
  rule      = aws_cloudwatch_event_rule.gmail_watch_renewal.name
  target_id = "GmailWatchRenewalSQS"
  arn       = data.terraform_remote_state.hub.outputs.hub_queue_arn
  role_arn  = aws_iam_role.eventbridge_sqs.arn

  input = jsonencode({
    job_type       = "gmail_watch_renewal"
    correlation_id = "scheduled-gmail-renewal"
    payload        = {}
  })
}
```

### `variables.tf`

```hcl
variable "environment"       { type = string }
variable "aws_region"        { type = string  default = "us-east-1" }
variable "aws_account_id"    { type = string }
variable "worker_image_uri"  { type = string }
variable "gcp_project_id"    { type = string }
variable "gmail_pubsub_topic"{ type = string }
variable "ecr_registry"      { type = string }
variable "domain_name"       { type = string  default = "agent-hub.io" }
```

### `outputs.tf`

```hcl
output "worker_task_role_arn"      { value = module.worker.task_role_arn }
output "worker_cluster_arn"        { value = module.worker.cluster_arn }
output "agent_instance_role_arn"   { value = aws_iam_role.agent_instance.arn }
output "agent_ecr_access_role_arn" { value = aws_iam_role.agent_ecr_access.arn }
```

---

## Root: Agent — Incident Triage (`infra/agents/incident-triage/`)

> **Note:** This root deploys a **platform-level** App Runner service used as the base image template. Per-tenant agents are provisioned dynamically by the worker. This root creates the shared IAM roles and optionally a dev/staging instance.

### `main.tf`

```hcl
locals {
  project     = "agent-hub"
  environment = var.environment
  agent_name  = "incident-triage"
}

data "terraform_remote_state" "vpc"     { backend = "s3"; config = { bucket = "agent-hub-terraform-state", key = "vpc/terraform.tfstate",     region = var.aws_region } }
data "terraform_remote_state" "rds"     { backend = "s3"; config = { bucket = "agent-hub-terraform-state", key = "rds/terraform.tfstate",     region = var.aws_region } }
data "terraform_remote_state" "secrets" { backend = "s3"; config = { bucket = "agent-hub-terraform-state", key = "secrets/terraform.tfstate", region = var.aws_region } }
data "terraform_remote_state" "hub"     { backend = "s3"; config = { bucket = "agent-hub-terraform-state", key = "hub/terraform.tfstate",     region = var.aws_region } }
data "terraform_remote_state" "worker"  { backend = "s3"; config = { bucket = "agent-hub-terraform-state", key = "worker/terraform.tfstate",  region = var.aws_region } }

# ── ECR Repository for agent image ────────────────────────────────────
resource "aws_ecr_repository" "agent" {
  name                 = "${local.project}/${local.environment}/${local.agent_name}"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = { Name = "${local.project}-${local.environment}-${local.agent_name}-ecr" }
}

resource "aws_ecr_lifecycle_policy" "agent" {
  repository = aws_ecr_repository.agent.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep last 10 images"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 10 }
      action       = { type = "expire" }
    }]
  })
}

# ── Dev/staging agent instance (optional — only in non-production) ─────
# In production, agents are provisioned per-tenant by the worker.
# In staging, we deploy one shared instance for testing.
module "agent_staging" {
  count  = var.environment != "production" ? 1 : 0
  source = "../../modules/app-runner"

  project          = local.project
  environment      = local.environment
  service_name     = "${local.agent_name}-staging"
  image_uri        = "${aws_ecr_repository.agent.repository_url}:latest"
  container_port   = "8080"
  cpu              = "1 vCPU"
  memory           = "2 GB"
  health_check_path = "/health"
  vpc_connector_arn = ""   # staging agent connects to external services only
  kms_key_arn       = data.terraform_remote_state.secrets.outputs.kms_key_arn
  instance_policy_json = data.terraform_remote_state.worker.outputs.agent_instance_role_arn

  environment_variables = {
    ENVIRONMENT        = local.environment
    TENANT_ID          = var.staging_tenant_id
    AGENT_ID           = var.staging_agent_id
    HUB_BASE_URL       = data.terraform_remote_state.hub.outputs.hub_service_url
    SLACK_OPS_CHANNEL  = "#agent-hub-staging"
    LANGFUSE_HOST      = "https://cloud.langfuse.com"
  }

  environment_secrets = {
    GMAIL_SECRET_ARN      = "arn:aws:secretsmanager:${var.aws_region}:${var.aws_account_id}:secret:${local.project}/${local.environment}/tenant/${var.staging_tenant_id}/agent/${var.staging_agent_id}/gmail_credentials"
    SLACK_SECRET_ARN      = "arn:aws:secretsmanager:${var.aws_region}:${var.aws_account_id}:secret:${local.project}/${local.environment}/tenant/${var.staging_tenant_id}/agent/${var.staging_agent_id}/slack_token"
    HUB_TOKEN_SECRET_ARN  = "arn:aws:secretsmanager:${var.aws_region}:${var.aws_account_id}:secret:${local.project}/${local.environment}/tenant/${var.staging_tenant_id}/agent/${var.staging_agent_id}/hub_service_token"
    LANGFUSE_SECRET_ARN   = data.terraform_remote_state.secrets.outputs.langfuse_secret_arn
    DATABASE_SECRET_ARN   = data.terraform_remote_state.rds.outputs.db_secret_arn
  }
}
```

### `variables.tf`

```hcl
variable "environment"        { type = string }
variable "aws_region"         { type = string  default = "us-east-1" }
variable "aws_account_id"     { type = string }
variable "staging_tenant_id"  { type = string  default = "" }
variable "staging_agent_id"   { type = string  default = "" }
```

### `outputs.tf`

```hcl
output "ecr_repository_url"     { value = aws_ecr_repository.agent.repository_url }
output "ecr_repository_name"    { value = aws_ecr_repository.agent.name }
output "staging_service_url"    {
  value = length(module.agent_staging) > 0 ? module.agent_staging[0].service_url : ""
}
```

---

## Deployment Order

Apply in exactly this order. Each step depends on the previous.

```
Step 1 — Bootstrap (one time only):
  Create S3 bucket + DynamoDB table for remote state manually:
    aws s3 mb s3://agent-hub-terraform-state
    aws s3api put-bucket-versioning \
      --bucket agent-hub-terraform-state \
      --versioning-configuration Status=Enabled
    aws dynamodb create-table \
      --table-name agent-hub-terraform-locks \
      --attribute-definitions AttributeName=LockID,AttributeType=S \
      --key-schema AttributeName=LockID,KeyType=HASH \
      --billing-mode PAY_PER_REQUEST

Step 2 — VPC (no dependencies):
  cd infra/modules/vpc  # This is a module — create a vpc/ root if needed
  terraform init && terraform apply

Step 3 — RDS (depends on VPC):
  cd infra/rds          # Create a rds/ root
  terraform init && terraform apply

Step 4 — Secrets (depends on nothing — creates KMS key):
  cd infra/secrets
  terraform init && terraform apply
  # Then populate secrets manually:
  #   aws secretsmanager put-secret-value --secret-id .../langfuse/credentials ...
  #   aws secretsmanager put-secret-value --secret-id .../internal/service-token ...

Step 5 — Hub (depends on VPC, RDS, Secrets):
  cd infra/hub
  terraform init && terraform apply

Step 6 — Worker (depends on Hub for queue URL):
  cd infra/worker
  terraform init && terraform apply

Step 7 — Agents/incident-triage (depends on Worker for role ARNs):
  cd infra/agents/incident-triage
  terraform init && terraform apply
```

---

## IAM Permission Summary

| Service | Can Do | Cannot Do |
| --- | --- | --- |
| **Hub** | SQS SendMessage to hub queue, Secrets create/read for tenant paths, KMS decrypt | Call agent URLs, read worker state, access other tenants' secrets |
| **Worker** | SQS consume hub queue, Secrets read+write tenant paths, App Runner CRUD, IAM PassRole to agent roles, ECR describe | SQS SendMessage to hub queue, access hub-internal APIs |
| **Agent** | Secrets read own tenant path only, KMS decrypt, SES send, CloudWatch logs | SQS any queue, App Runner APIs, create secrets, access other tenant paths |

---

## Hard Rules for Terraform Implementation

- **Never** add `*` resource ARNs to any policy unless explicitly shown above
- **Never** give agents SQS permissions — agents have no queue
- **Never** give hub App Runner access to ECR, App Runner APIs, or ECS
- **Always** use `var.environment` in every resource name — prevents name collision across envs
- **Always** add a `tags` block to every resource with at least `Name` and `Environment`
- **Always** set `storage_encrypted = true` on RDS
- **Always** set `private_dns_enabled = true` on all VPC endpoints
- **Always** use `lifecycle { ignore_changes = [task_definition] }` on ECS services — CI/CD manages image updates, not Terraform
- The worker's `iam:PassRole` must include the `Condition` block scoped to App Runner services — never omit it
- All secrets must use the project KMS key — never use the default AWS managed key
