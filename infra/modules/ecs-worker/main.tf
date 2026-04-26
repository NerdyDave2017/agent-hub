locals {
  default_tags = {
    Project     = var.project
    Environment = var.environment
  }
  # PassRole only on the two agent roles Terraform owns. Omit iam:PassedToService: App Runner's
  # CreateService can evaluate PassRole with a principal that does not match tasks.* / build.*
  # literally, which caused AccessDenied even when trust policies were correct.
  apprunner_pass_role_statements = concat(
    trimspace(var.agent_instance_role_arn) != "" ? [{
      Sid      = "IAMPassAgentInstanceForAppRunner"
      Effect   = "Allow"
      Action   = "iam:PassRole"
      Resource = var.agent_instance_role_arn
    }] : [],
    trimspace(var.agent_ecr_access_role_arn) != "" ? [{
      Sid      = "IAMPassAgentEcrAccessForAppRunner"
      Effect   = "Allow"
      Action   = "iam:PassRole"
      Resource = var.agent_ecr_access_role_arn
    }] : [],
  )
}

resource "aws_ecs_cluster" "worker" {
  name = "${var.project}-${var.environment}-worker"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  tags = merge(local.default_tags, { Name = "${var.project}-${var.environment}-worker-cluster" })
}

resource "aws_cloudwatch_log_group" "worker" {
  name              = "/ecs/${var.project}/${var.environment}/worker"
  retention_in_days = var.environment == "production" ? 30 : 7
  kms_key_id        = var.kms_key_arn

  tags = merge(local.default_tags, { Name = "${var.project}-${var.environment}-worker-logs" })
}

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

  tags = merge(local.default_tags, { Name = "${var.project}-${var.environment}-worker-execution" })
}

resource "aws_iam_role_policy_attachment" "execution_basic" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "execution_secrets" {
  name = "${var.project}-${var.environment}-worker-execution-secrets"
  role = aws_iam_role.execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "secretsmanager:GetSecretValue",
        "kms:Decrypt",
        "kms:DescribeKey",
      ]
      Resource = compact([var.db_secret_arn, var.internal_service_token_arn, var.kms_key_arn])
    }]
  })
}

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

  tags = merge(local.default_tags, { Name = "${var.project}-${var.environment}-worker-task" })
}

resource "aws_iam_role_policy" "task" {
  name = "${var.project}-${var.environment}-worker-task-policy"
  role = aws_iam_role.task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat([
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
      {
        Sid    = "SecretsRead"
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue",
          "secretsmanager:DescribeSecret",
        ]
        Resource = compact([
          var.db_secret_arn,
          var.internal_service_token_arn,
          "arn:aws:secretsmanager:${var.aws_region}:${var.aws_account_id}:secret:${var.project}/${var.environment}/tenant/*",
        ])
      },
      {
        Sid    = "SecretsWrite"
        Effect = "Allow"
        Action = [
          "secretsmanager:CreateSecret",
          "secretsmanager:PutSecretValue",
          "secretsmanager:UpdateSecret",
          "secretsmanager:TagResource",
        ]
        Resource = "arn:aws:secretsmanager:${var.aws_region}:${var.aws_account_id}:secret:${var.project}/${var.environment}/tenant/*"
      },
      {
        Sid      = "KMSDecrypt"
        Effect   = "Allow"
        Action   = ["kms:Decrypt", "kms:GenerateDataKey"]
        Resource = var.kms_key_arn
      },
      {
        Sid    = "AppRunnerProvision"
        Effect = "Allow"
        Action = [
          "apprunner:CreateService",
          "apprunner:UpdateService",
          "apprunner:DeleteService",
          "apprunner:DescribeService",
          "apprunner:ListOperations",
          "apprunner:ListServices",
          "apprunner:PauseService",
          "apprunner:ResumeService",
          "apprunner:TagResource",
        ]
        Resource = "*"
      },
      ], local.apprunner_pass_role_statements, [
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
      {
        Sid    = "CloudWatchLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents",
        ]
        Resource = "${aws_cloudwatch_log_group.worker.arn}:*"
      },
      {
        Sid      = "CloudWatchCreateLogGroups"
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:TagLogGroup"]
        Resource = "arn:aws:logs:${var.aws_region}:${var.aws_account_id}:log-group:/apprunner/${var.project}/${var.environment}/agent-*"
      },
    ])
  })
}

resource "aws_ecs_task_definition" "worker" {
  family                   = "${var.project}-${var.environment}-worker"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.cpu
  memory                   = var.memory

  execution_role_arn = aws_iam_role.execution.arn
  task_role_arn      = aws_iam_role.task.arn

  container_definitions = jsonencode([{
    name      = "worker"
    image     = var.image_uri
    essential = true

    environment = concat(
      [
        { name = "ENVIRONMENT", value = var.environment },
        { name = "AWS_REGION", value = var.aws_region },
        { name = "SQS_QUEUE_URL", value = var.hub_queue_url },
        { name = "HUB_QUEUE_URL", value = var.hub_queue_url },
        { name = "HUB_BASE_URL", value = var.hub_base_url },
        { name = "GCP_PROJECT_ID", value = var.gcp_project_id },
        { name = "GMAIL_PUBSUB_TOPIC", value = var.gmail_pubsub_topic },
        { name = "ECR_REGISTRY", value = var.ecr_registry },
        { name = "AGENT_INSTANCE_ROLE_ARN", value = var.agent_instance_role_arn },
        { name = "AGENT_ECR_ACCESS_ROLE_ARN", value = var.agent_ecr_access_role_arn },
      ],
      var.extra_environment,
    )

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

  tags = merge(local.default_tags, { Name = "${var.project}-${var.environment}-worker-task" })
}

resource "aws_ecs_service" "worker" {
  name            = "${var.project}-${var.environment}-worker"
  cluster         = aws_ecs_cluster.worker.id
  task_definition = aws_ecs_task_definition.worker.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [var.worker_sg_id]
    assign_public_ip = false
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  tags = merge(local.default_tags, { Name = "${var.project}-${var.environment}-worker-service" })
}
