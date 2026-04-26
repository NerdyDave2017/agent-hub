locals {
  project     = "agent-hub"
  environment = var.environment
  agent_name  = "incident-triage"
  ecr_repo    = var.ecr_repository_name != "" ? var.ecr_repository_name : "${local.project}/${local.environment}/${local.agent_name}"
}

data "terraform_remote_state" "vpc" {
  backend = "s3"
  config = {
    bucket = "agent-hub-terraform-state"
    key    = "vpc/terraform.tfstate"
    region = var.aws_region
  }
}

data "terraform_remote_state" "rds" {
  backend = "s3"
  config = {
    bucket = "agent-hub-terraform-state"
    key    = "rds/terraform.tfstate"
    region = var.aws_region
  }
}

data "terraform_remote_state" "secrets" {
  backend = "s3"
  config = {
    bucket = "agent-hub-terraform-state"
    key    = "secrets/terraform.tfstate"
    region = var.aws_region
  }
}

data "terraform_remote_state" "hub" {
  backend = "s3"
  config = {
    bucket = "agent-hub-terraform-state"
    key    = "hub/terraform.tfstate"
    region = var.aws_region
  }
}

data "terraform_remote_state" "worker" {
  backend = "s3"
  config = {
    bucket = "agent-hub-terraform-state"
    key    = "worker/terraform.tfstate"
    region = var.aws_region
  }
}

resource "aws_ecr_repository" "agent" {
  name                 = local.ecr_repo
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = {
    Name        = "${local.project}-${local.environment}-${local.agent_name}-ecr"
    Environment = local.environment
    Project     = local.project
  }
}

resource "aws_ecr_lifecycle_policy" "agent" {
  repository = aws_ecr_repository.agent.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep last 10 images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 10
      }
      action = { type = "expire" }
    }]
  })
}

module "agent_staging" {
  count  = var.environment != "production" ? 1 : 0
  source = "../../modules/app-runner"

  project              = local.project
  environment          = local.environment
  service_name         = "${local.agent_name}-staging"
  image_uri            = "${aws_ecr_repository.agent.repository_url}:latest"
  container_port       = "8080"
  cpu                  = "1024"
  memory               = "2048"
  health_check_path    = "/health"
  vpc_connector_arn    = ""
  kms_key_arn          = data.terraform_remote_state.secrets.outputs.kms_key_arn
  create_instance_role = true
  instance_policy_json = data.terraform_remote_state.worker.outputs.agent_instance_policy_json

  environment_variables = {
    ENVIRONMENT       = local.environment
    TENANT_ID         = var.staging_tenant_id
    AGENT_ID          = var.staging_agent_id
    HUB_BASE_URL      = data.terraform_remote_state.hub.outputs.hub_service_url
    SLACK_OPS_CHANNEL = "#agent-hub-staging"
    LANGFUSE_HOST     = "https://cloud.langfuse.com"
    GMAIL_POLL_INTERVAL_SECONDS = tostring(var.gmail_poll_interval_seconds)
  }

  environment_secrets = {
    GMAIL_SECRET_ARN     = "arn:aws:secretsmanager:${var.aws_region}:${var.aws_account_id}:secret:${local.project}/${local.environment}/tenant/${var.staging_tenant_id}/agent/${var.staging_agent_id}/gmail_credentials"
    SLACK_SECRET_ARN     = "arn:aws:secretsmanager:${var.aws_region}:${var.aws_account_id}:secret:${local.project}/${local.environment}/tenant/${var.staging_tenant_id}/agent/${var.staging_agent_id}/slack_token"
    HUB_TOKEN_SECRET_ARN = "arn:aws:secretsmanager:${var.aws_region}:${var.aws_account_id}:secret:${local.project}/${local.environment}/tenant/${var.staging_tenant_id}/agent/${var.staging_agent_id}/hub_service_token"
    LANGFUSE_SECRET_ARN  = data.terraform_remote_state.secrets.outputs.langfuse_secret_arn
    DATABASE_URL         = data.terraform_remote_state.rds.outputs.db_secret_arn
  }
}
