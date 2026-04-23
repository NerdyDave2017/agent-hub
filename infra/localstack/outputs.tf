output "sqs_queue_url" {
  description = "Main jobs queue URL — set SQS_QUEUE_URL (hub + worker)."
  value       = aws_sqs_queue.jobs.id
}

output "sqs_queue_arn" {
  value = aws_sqs_queue.jobs.arn
}

output "sqs_dlq_url" {
  description = "DLQ URL — optional SQS_DLQ_URL for ops / debugging."
  value       = aws_sqs_queue.jobs_dlq.id
}

output "sqs_dlq_arn" {
  value = aws_sqs_queue.jobs_dlq.arn
}

output "iam_role_arn_hub" {
  description = "ECS task role ARN for hub (prod-style); local apps typically still use static test creds."
  value       = aws_iam_role.hub.arn
}

output "iam_role_arn_worker" {
  value = aws_iam_role.worker.arn
}

output "iam_role_arn_agent_incident_triage" {
  value = aws_iam_role.agent.arn
}

output "secrets_internal_service_token_arn" {
  value       = aws_secretsmanager_secret.internal_service_token.arn
  sensitive   = false
  description = "Optional: point tooling at this ARN to test Secrets Manager reads (hub still uses INTERNAL_SERVICE_TOKEN env by default)."
}

output "secrets_oauth_placeholder_arn" {
  value = aws_secretsmanager_secret.oauth_placeholder.arn
}

output "aws_region" {
  value = var.aws_region
}

output "localstack_endpoint" {
  value = var.localstack_endpoint
}
