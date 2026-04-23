data "aws_iam_policy_document" "ecs_tasks_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ecs_execution" {
  name               = "${var.stack_name}-ecs-exec"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role_policy_attachment" "ecs_execution" {
  role       = aws_iam_role.ecs_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role" "hub_task" {
  name               = "${var.stack_name}-hub-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

# Narrow this once you wire real SQS ARNs from the worker/backend stack (or use remote_state).
resource "aws_iam_role_policy" "hub_task_sqs" {
  count = length(var.hub_sqs_queue_arns) > 0 ? 1 : 0
  name  = "sqs-send"
  role  = aws_iam_role.hub_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["sqs:SendMessage", "sqs:GetQueueAttributes", "sqs:GetQueueUrl"]
        Resource = var.hub_sqs_queue_arns
      }
    ]
  })
}

resource "aws_iam_role_policy" "hub_task_secrets" {
  count = var.hub_allow_secrets_manager ? 1 : 0
  name  = "secrets-integration"
  role  = aws_iam_role.hub_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "OAuthSecrets"
        Effect = "Allow"
        Action = [
          "secretsmanager:CreateSecret",
          "secretsmanager:PutSecretValue",
          "secretsmanager:UpdateSecret",
          "secretsmanager:DescribeSecret",
          "secretsmanager:GetSecretValue",
          "secretsmanager:TagResource",
        ]
        Resource = "arn:aws:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:*"
      }
    ]
  })
}
