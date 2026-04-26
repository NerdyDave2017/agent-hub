locals {
  default_tags = {
    Project     = var.project
    Environment = var.environment
    Service     = var.service_name
  }
}

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

  tags = merge(local.default_tags, { Name = "${var.project}-${var.environment}-${var.service_name}-ecr-access" })
}

resource "aws_iam_role_policy_attachment" "ecr_access" {
  role       = aws_iam_role.ecr_access.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSAppRunnerServicePolicyForECRAccess"
}

resource "aws_iam_role" "instance" {
  count = var.create_instance_role ? 1 : 0
  name  = "${var.project}-${var.environment}-${var.service_name}-instance"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "tasks.apprunner.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = merge(local.default_tags, { Name = "${var.project}-${var.environment}-${var.service_name}-instance" })
}

resource "aws_iam_role_policy" "instance" {
  count  = var.create_instance_role ? 1 : 0
  name   = "${var.project}-${var.environment}-${var.service_name}-policy"
  role   = aws_iam_role.instance[0].id
  policy = var.instance_policy_json
}

resource "aws_apprunner_service" "main" {
  service_name = "${var.project}-${var.environment}-${var.service_name}"

  source_configuration {
    image_repository {
      image_identifier      = var.image_uri
      image_repository_type = "ECR"

      image_configuration {
        port                            = var.container_port
        runtime_environment_variables   = var.environment_variables
        runtime_environment_secrets     = var.environment_secrets
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
    instance_role_arn = var.create_instance_role ? aws_iam_role.instance[0].arn : var.instance_role_arn
  }

  health_check_configuration {
    protocol            = "HTTP"
    path                = var.health_check_path
    interval            = 10
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  dynamic "network_configuration" {
    for_each = var.vpc_connector_arn != "" ? [1] : []
    content {
      egress_configuration {
        egress_type       = "VPC"
        vpc_connector_arn = var.vpc_connector_arn
      }
    }
  }

  tags = merge(local.default_tags, {
    Name        = "${var.project}-${var.environment}-${var.service_name}"
    Environment = var.environment
    Service     = var.service_name
  })
}

resource "aws_cloudwatch_log_group" "main" {
  name              = "/apprunner/${var.project}/${var.environment}/${var.service_name}"
  retention_in_days = var.environment == "production" ? 30 : 7
  kms_key_id        = var.kms_key_arn

  tags = merge(local.default_tags, { Name = "${var.project}-${var.environment}-${var.service_name}-logs" })
}
