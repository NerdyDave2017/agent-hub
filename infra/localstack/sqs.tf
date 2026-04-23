resource "aws_sqs_queue" "jobs_dlq" {
  name = var.jobs_dlq_name

  tags = {
    Service = "agent-hub"
    Stack   = "localstack"
  }
}

resource "aws_sqs_queue" "jobs" {
  name                       = var.jobs_queue_name
  visibility_timeout_seconds = 60
  receive_wait_time_seconds  = 20

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.jobs_dlq.arn
    maxReceiveCount     = 5
  })

  tags = {
    Service = "agent-hub"
    Stack   = "localstack"
  }
}
