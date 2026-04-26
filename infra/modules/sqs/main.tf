locals {
  default_tags = {
    Project     = var.project
    Environment = var.environment
  }
}

resource "aws_sqs_queue" "hub_dlq" {
  name                      = "${var.project}-${var.environment}-hub-dlq"
  message_retention_seconds = 1209600
  kms_master_key_id         = var.kms_key_id
  tags                      = merge(local.default_tags, { Name = "${var.project}-${var.environment}-hub-dlq" })
}

resource "aws_sqs_queue" "hub" {
  name                       = "${var.project}-${var.environment}-hub-queue"
  visibility_timeout_seconds = 300
  message_retention_seconds  = 86400
  receive_wait_time_seconds  = 20
  kms_master_key_id          = var.kms_key_id

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.hub_dlq.arn
    maxReceiveCount     = 3
  })

  tags = merge(local.default_tags, { Name = "${var.project}-${var.environment}-hub-queue" })
}

resource "aws_sqs_queue_policy" "hub" {
  queue_url = aws_sqs_queue.hub.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat(
      [
        {
          Sid       = "AllowHubSend"
          Effect    = "Allow"
          Principal = { AWS = var.hub_role_arn }
          Action    = ["sqs:SendMessage"]
          Resource  = aws_sqs_queue.hub.arn
        }
      ],
      var.worker_role_arn != "" ? [
        {
          Sid       = "AllowWorkerConsume"
          Effect    = "Allow"
          Principal = { AWS = var.worker_role_arn }
          Action = [
            "sqs:ReceiveMessage",
            "sqs:DeleteMessage",
            "sqs:GetQueueAttributes",
            "sqs:ChangeMessageVisibility",
          ]
          Resource = aws_sqs_queue.hub.arn
        }
      ] : []
    )
  })
}

resource "aws_cloudwatch_metric_alarm" "dlq_depth" {
  count               = length(var.alarm_sns_arns) > 0 ? 1 : 0
  alarm_name          = "${var.project}-${var.environment}-dlq-messages"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "ApproximateNumberOfMessagesVisible"
  namespace           = "AWS/SQS"
  period              = 60
  statistic           = "Sum"
  threshold           = 0
  alarm_description   = "DLQ has messages - worker job failures require investigation"
  alarm_actions       = var.alarm_sns_arns

  dimensions = {
    QueueName = aws_sqs_queue.hub_dlq.name
  }

  tags = merge(local.default_tags, { Name = "${var.project}-${var.environment}-dlq-alarm" })
}
