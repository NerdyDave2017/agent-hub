locals {
  project     = "agent-hub"
  environment = var.environment
  # ECR repository path only (no registry, tag, or digest). Avoid regexreplace for older Terraform.
  hub_image_stripped = replace(
    var.hub_image_uri,
    "${var.aws_account_id}.dkr.ecr.${var.aws_region}.amazonaws.com/",
    "",
  )
  hub_ecr_repo = length(split("@", local.hub_image_stripped)) > 1 ? split("@", local.hub_image_stripped)[0] : split(":", local.hub_image_stripped)[0]
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

resource "aws_ecr_repository" "hub" {
  name                 = local.hub_ecr_repo
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = {
    Name        = "${local.project}-${local.environment}-hub-ecr"
    Environment = local.environment
    Project     = local.project
  }
}

resource "aws_ecr_lifecycle_policy" "hub" {
  repository = aws_ecr_repository.hub.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep last 30 hub images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 30
      }
      action = { type = "expire" }
    }]
  })
}

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

  tags = {
    Name        = "${local.project}-${local.environment}-hub-instance"
    Environment = local.environment
    Project     = local.project
  }
}

module "sqs" {
  source = "../modules/sqs"

  project         = local.project
  environment     = local.environment
  kms_key_id      = data.terraform_remote_state.secrets.outputs.kms_key_id
  hub_role_arn    = aws_iam_role.hub.arn
  worker_role_arn = var.worker_role_arn
  alarm_sns_arns  = var.alarm_sns_arns
}

resource "aws_iam_role_policy" "hub" {
  name = "${local.project}-${local.environment}-hub-policy"
  role = aws_iam_role.hub.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "SQSSend"
        Effect   = "Allow"
        Action   = ["sqs:SendMessage", "sqs:GetQueueUrl", "sqs:GetQueueAttributes"]
        Resource = module.sqs.hub_queue_arn
      },
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
          # Hub code uses ``{APP_NAME}/tenant/{tenant_id}/agent/{agent_id}/…`` (no env segment); keep env path for any legacy secrets.
          "arn:aws:secretsmanager:${var.aws_region}:${var.aws_account_id}:secret:${local.project}/${local.environment}/tenant/*",
          "arn:aws:secretsmanager:${var.aws_region}:${var.aws_account_id}:secret:${local.project}/tenant/*",
          data.terraform_remote_state.secrets.outputs.internal_service_token_arn,
          data.terraform_remote_state.secrets.outputs.jwt_secret_key_arn,
          data.terraform_remote_state.rds.outputs.db_secret_arn,
        ]
      },
      {
        Sid      = "KMSDecrypt"
        Effect   = "Allow"
        Action   = ["kms:Decrypt", "kms:GenerateDataKey"]
        Resource = data.terraform_remote_state.secrets.outputs.kms_key_arn
      },
      {
        Sid      = "CloudWatchLogs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents", "logs:CreateLogGroup"]
        Resource = "arn:aws:logs:${var.aws_region}:${var.aws_account_id}:log-group:/apprunner/${local.project}/${local.environment}/hub*"
      },
    ]
  })
}

module "apprunner" {
  source = "../modules/app-runner"

  project              = local.project
  environment          = local.environment
  service_name         = "hub"
  image_uri            = var.hub_image_uri
  container_port       = "8000"
  cpu                  = "1024"
  memory               = "2048"
  health_check_path    = "/health"
  vpc_connector_arn    = data.terraform_remote_state.vpc.outputs.app_runner_vpc_connector_arn
  kms_key_arn          = data.terraform_remote_state.secrets.outputs.kms_key_arn
  create_instance_role = false
  instance_role_arn    = aws_iam_role.hub.arn
  instance_policy_json = "{}"

  environment_variables = {
    ENVIRONMENT                = local.environment
    AWS_REGION                 = var.aws_region
    SQS_QUEUE_URL              = module.sqs.hub_queue_url
    HUB_QUEUE_URL              = module.sqs.hub_queue_url
    HUB_PUBLIC_URL             = var.hub_public_url
    FRONTEND_URL               = var.frontend_url
    GOOGLE_PUBSUB_TOPIC        = var.google_pubsub_topic
    GCP_PROJECT_ID             = var.gcp_project_id
    GOOGLE_OAUTH_CLIENT_ID     = var.google_oauth_client_id
    GOOGLE_OAUTH_CLIENT_SECRET = var.google_oauth_client_secret
    SLACK_OAUTH_CLIENT_ID      = var.slack_oauth_client_id
    SLACK_OAUTH_CLIENT_SECRET  = var.slack_oauth_client_secret
    GOOGLE_WEBHOOK_SECRET      = var.google_webhook_secret
    LANGFUSE_HOST              = var.langfuse_host
  }

  environment_secrets = {
    DATABASE_URL           = data.terraform_remote_state.rds.outputs.db_secret_arn
    INTERNAL_SERVICE_TOKEN = data.terraform_remote_state.secrets.outputs.internal_service_token_arn
    JWT_SECRET_KEY         = data.terraform_remote_state.secrets.outputs.jwt_secret_key_arn
  }

  depends_on = [aws_iam_role_policy.hub]
}
