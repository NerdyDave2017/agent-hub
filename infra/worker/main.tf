locals {
  project     = "agent-hub"
  environment = var.environment
  worker_image_stripped = replace(
    var.worker_image_uri,
    "${var.aws_account_id}.dkr.ecr.${var.aws_region}.amazonaws.com/",
    "",
  )
  worker_ecr_repo = length(split("@", local.worker_image_stripped)) > 1 ? split("@", local.worker_image_stripped)[0] : split(":", local.worker_image_stripped)[0]
  # ECR repo name matches infra/agents/incident-triage default: agent-hub/{env}/incident-triage
  agent_ecr_repo = var.agent_ecr_repository != "" ? var.agent_ecr_repository : "${local.project}/${local.environment}/incident-triage"
  # Full image ref for worker App Runner CreateService (must exist in ECR before first provision).
  agent_app_runner_image_identifier = "${var.ecr_registry}/${local.agent_ecr_repo}:${var.agent_image_tag}"
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

data "terraform_remote_state" "hub" {
  backend = "s3"
  config = {
    bucket = "agent-hub-terraform-state"
    key    = "hub/terraform.tfstate"
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

resource "aws_ecr_repository" "worker" {
  name                 = local.worker_ecr_repo
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = {
    Name        = "${local.project}-${local.environment}-worker-ecr"
    Environment = local.environment
    Project     = local.project
  }
}

resource "aws_ecr_lifecycle_policy" "worker" {
  repository = aws_ecr_repository.worker.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep last 30 worker images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 30
      }
      action = { type = "expire" }
    }]
  })
}

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

  tags = {
    Name        = "${local.project}-${local.environment}-agent-ecr-access"
    Environment = local.environment
    Project     = local.project
  }
}

resource "aws_iam_role_policy_attachment" "agent_ecr_access" {
  role       = aws_iam_role.agent_ecr_access.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSAppRunnerServicePolicyForECRAccess"
}

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

  tags = {
    Name        = "${local.project}-${local.environment}-agent-instance"
    Environment = local.environment
    Project     = local.project
  }
}

resource "aws_iam_role_policy" "agent_instance" {
  name = "${local.project}-${local.environment}-agent-instance-policy"
  role = aws_iam_role.agent_instance.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "SecretsRead"
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue",
          "secretsmanager:DescribeSecret",
        ]
        Resource = [
          "arn:aws:secretsmanager:${var.aws_region}:${var.aws_account_id}:secret:${local.project}/${local.environment}/tenant/*",
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
        Resource = "arn:aws:logs:${var.aws_region}:${var.aws_account_id}:log-group:/apprunner/${local.project}/${local.environment}/agent-*"
      },
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

module "worker" {
  source = "../modules/ecs-worker"

  project                    = local.project
  environment                = local.environment
  aws_region                 = var.aws_region
  aws_account_id             = var.aws_account_id
  image_uri                  = var.worker_image_uri
  cpu                        = "512"
  memory                     = "1024"
  private_subnet_ids         = data.terraform_remote_state.vpc.outputs.private_subnet_ids
  worker_sg_id               = data.terraform_remote_state.vpc.outputs.worker_sg_id
  hub_queue_url              = data.terraform_remote_state.hub.outputs.hub_queue_url
  hub_queue_arn              = data.terraform_remote_state.hub.outputs.hub_queue_arn
  db_secret_arn              = data.terraform_remote_state.rds.outputs.db_secret_arn
  kms_key_arn                = data.terraform_remote_state.secrets.outputs.kms_key_arn
  internal_service_token_arn = data.terraform_remote_state.secrets.outputs.internal_service_token_arn
  hub_base_url               = data.terraform_remote_state.hub.outputs.hub_service_url
  gcp_project_id             = var.gcp_project_id
  google_pubsub_topic        = var.google_pubsub_topic
  ecr_registry               = var.ecr_registry
  agent_ecr_access_role_arn  = aws_iam_role.agent_ecr_access.arn
  agent_instance_role_arn    = aws_iam_role.agent_instance.arn
  extra_environment = concat(
    var.worker_extra_environment,
    var.worker_deploy_revision == "" ? [] : [
      {
        name  = "AGENT_HUB_DEPLOY_REVISION"
        value = var.worker_deploy_revision
      },
    ],
    [
      {
        name  = "APP_RUNNER_CREATE_IMAGE_IDENTIFIER"
        value = local.agent_app_runner_image_identifier
      },
      {
        name  = "APP_RUNNER_CREATE_VPC_CONNECTOR_ARN"
        value = data.terraform_remote_state.vpc.outputs.app_runner_vpc_connector_arn
      },
      {
        name  = "LANGFUSE_HOST"
        value = var.langfuse_host
      },
      {
        name  = "LANGFUSE_PUBLIC_KEY"
        value = var.langfuse_public_key
      },
      {
        name  = "LANGFUSE_SECRET_KEY"
        value = var.langfuse_secret_key
      },
    ],
  )
}

resource "aws_cloudwatch_event_rule" "metrics_rollup" {
  name                = "${local.project}-${local.environment}-metrics-rollup"
  description         = "Trigger metrics rollup job hourly"
  schedule_expression = "rate(1 hour)"
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

  tags = {
    Name        = "${local.project}-${local.environment}-eventbridge-sqs"
    Environment = local.environment
    Project     = local.project
  }
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

resource "aws_cloudwatch_event_target" "metrics_rollup" {
  rule      = aws_cloudwatch_event_rule.metrics_rollup.name
  target_id = "MetricsRollupSQS"
  arn       = data.terraform_remote_state.hub.outputs.hub_queue_arn
  role_arn  = aws_iam_role.eventbridge_sqs.arn

  input = jsonencode({
    job_type       = "metrics_rollup"
    correlation_id = "scheduled-metrics-rollup"
    payload        = {}
  })
}

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
