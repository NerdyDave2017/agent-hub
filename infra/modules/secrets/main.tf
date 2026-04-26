locals {
  default_tags = {
    Project     = var.project
    Environment = var.environment
  }

  service_principal_arns = compact(concat(
    var.hub_role_arn != "" ? [var.hub_role_arn] : [],
    var.worker_role_arn != "" ? [var.worker_role_arn] : [],
    var.agent_role_arns,
  ))
}

resource "aws_kms_key" "main" {
  description             = "${var.project} ${var.environment} secrets encryption key"
  deletion_window_in_days = 10
  enable_key_rotation     = true

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat(
      [
        {
          Sid       = "EnableRootAccess"
          Effect    = "Allow"
          Principal = { AWS = "arn:aws:iam::${var.aws_account_id}:root" }
          Action    = "kms:*"
          Resource  = "*"
        },
        {
          Sid       = "AllowCloudWatchLogs"
          Effect    = "Allow"
          Principal = { Service = "logs.${var.aws_region}.amazonaws.com" }
          Action = [
            "kms:Encrypt",
            "kms:Decrypt",
            "kms:ReEncrypt*",
            "kms:GenerateDataKey*",
            "kms:CreateGrant",
            "kms:DescribeKey",
          ]
          Resource = "*"
          Condition = {
            ArnLike = {
              "kms:EncryptionContext:aws:logs:arn" = "arn:aws:logs:${var.aws_region}:${var.aws_account_id}:*"
            }
          }
        }
      ],
      length(local.service_principal_arns) > 0 ? [
        {
          Sid       = "AllowServiceRoles"
          Effect    = "Allow"
          Principal = { AWS = local.service_principal_arns }
          Action    = ["kms:Decrypt", "kms:GenerateDataKey"]
          Resource  = "*"
        }
      ] : []
    )
  })

  tags = merge(local.default_tags, { Name = "${var.project}-${var.environment}-kms" })
}

resource "aws_kms_alias" "main" {
  name          = "alias/${var.project}-${var.environment}"
  target_key_id = aws_kms_key.main.key_id
}

resource "aws_secretsmanager_secret" "langfuse" {
  name                    = "${var.project}/${var.environment}/langfuse/credentials"
  kms_key_id              = aws_kms_key.main.arn
  recovery_window_in_days = 7
  tags                    = merge(local.default_tags, { Name = "${var.project}-${var.environment}-langfuse-creds" })
}

resource "aws_secretsmanager_secret" "internal_service_token" {
  name                    = "${var.project}/${var.environment}/internal/service-token"
  kms_key_id              = aws_kms_key.main.arn
  recovery_window_in_days = 7
  tags                    = merge(local.default_tags, { Name = "${var.project}-${var.environment}-service-token" })
}
