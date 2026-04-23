# IAM roles mirror **ECS task role** shapes for prod. Local apps still use
# AWS_ACCESS_KEY_ID=test against LocalStack unless you wire STS assume-role.

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.name

  secrets_arn_prefix = "arn:aws:secretsmanager:${local.region}:${local.account_id}:secret"
}

data "aws_iam_policy_document" "ecs_task_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "hub" {
  name               = "agent-hub-local-hub-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_task_assume.json

  tags = {
    Service = "agent-hub-backend"
    Stack   = "localstack"
  }
}

resource "aws_iam_role" "worker" {
  name               = "agent-hub-local-worker-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_task_assume.json

  tags = {
    Service = "agent-hub-worker"
    Stack   = "localstack"
  }
}

resource "aws_iam_role" "agent" {
  name               = "agent-hub-local-incident-triage-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_task_assume.json

  tags = {
    Service = "agent-hub-incident-triage"
    Stack   = "localstack"
  }
}

# --- Hub: enqueue jobs + manage integration secrets (OAuth flows) ---

resource "aws_iam_role_policy" "hub" {
  name = "hub-localstack"
  role = aws_iam_role.hub.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "JobsQueueSend"
        Effect = "Allow"
        Action = [
          "sqs:SendMessage",
          "sqs:GetQueueAttributes",
          "sqs:GetQueueUrl",
        ]
        Resource = aws_sqs_queue.jobs.arn
      },
      {
        Sid    = "IntegrationSecrets"
        Effect = "Allow"
        Action = [
          "secretsmanager:CreateSecret",
          "secretsmanager:PutSecretValue",
          "secretsmanager:UpdateSecret",
          "secretsmanager:DescribeSecret",
          "secretsmanager:GetSecretValue",
          "secretsmanager:TagResource",
          "secretsmanager:DeleteSecret",
        ]
        Resource = "${local.secrets_arn_prefix}:*"
      }
    ]
  })
}

# --- Worker: consume queue + read/write tenant secrets + AWS adapter scaffolds ---

resource "aws_iam_role_policy" "worker" {
  name = "worker-localstack"
  role = aws_iam_role.worker.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "JobsQueueConsume"
        Effect = "Allow"
        Action = [
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:ChangeMessageVisibility",
          "sqs:GetQueueAttributes",
          "sqs:GetQueueUrl",
        ]
        Resource = aws_sqs_queue.jobs.arn
      },
      {
        Sid      = "DlqInspect"
        Effect   = "Allow"
        Action   = ["sqs:GetQueueAttributes", "sqs:GetQueueUrl", "sqs:ReceiveMessage"]
        Resource = aws_sqs_queue.jobs_dlq.arn
      },
      {
        Sid    = "TenantSecrets"
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue",
          "secretsmanager:PutSecretValue",
          "secretsmanager:DescribeSecret",
        ]
        Resource = "${local.secrets_arn_prefix}:*"
      },
      {
        Sid    = "EcsProvisioningScaffold"
        Effect = "Allow"
        Action = [
          "ecs:DescribeClusters",
          "ecs:DescribeServices",
          "ecs:DescribeTaskDefinition",
          "ecs:DescribeTasks",
          "ecs:ListTasks",
          "ecs:RegisterTaskDefinition",
          "ecs:RunTask",
          "ecs:CreateService",
          "ecs:UpdateService",
        ]
        Resource = "*"
      },
      {
        Sid    = "EcrScaffold"
        Effect = "Allow"
        Action = [
          "ecr:GetAuthorizationToken",
          "ecr:BatchCheckLayerAvailability",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchGetImage",
          "ecr:DescribeRepositories",
          "ecr:DescribeImages",
        ]
        Resource = "*"
      },
      {
        Sid    = "Elbv2Scaffold"
        Effect = "Allow"
        Action = [
          "elasticloadbalancing:DescribeLoadBalancers",
          "elasticloadbalancing:DescribeTargetGroups",
          "elasticloadbalancing:DescribeTargetHealth",
          "elasticloadbalancing:DescribeListeners",
          "elasticloadbalancing:CreateLoadBalancer",
          "elasticloadbalancing:CreateTargetGroup",
          "elasticloadbalancing:CreateListener",
          "elasticloadbalancing:RegisterTargets",
        ]
        Resource = "*"
      }
    ]
  })
}

# --- Agent: resolve operator secrets from ARNs (no SQS) ---

resource "aws_iam_role_policy" "agent" {
  name = "incident-triage-localstack"
  role = aws_iam_role.agent.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ReadRuntimeSecrets"
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue",
          "secretsmanager:DescribeSecret",
        ]
        Resource = "${local.secrets_arn_prefix}:*"
      }
    ]
  })
}
