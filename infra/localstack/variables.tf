variable "aws_region" {
  type        = string
  description = "AWS region LocalStack emulates (must match app AWS_REGION)."
  default     = "us-east-1"
}

variable "localstack_endpoint" {
  type        = string
  description = "LocalStack edge URL reachable from the Terraform process (host: use http://127.0.0.1:4566)."
  default     = "http://127.0.0.1:4566"
}

variable "jobs_queue_name" {
  type        = string
  description = "Primary SQS queue name (hub → worker)."
  default     = "agent-hub-jobs"
}

variable "jobs_dlq_name" {
  type        = string
  description = "Dead-letter queue name for failed / poison messages."
  default     = "agent-hub-jobs-dlq"
}

variable "internal_service_token" {
  type        = string
  description = "Dev-only token mirrored into a Secrets Manager secret for parity with agent *SECRET_ARN patterns (not required for hub INTERNAL_SERVICE_TOKEN env)."
  default     = "local-dev-internal-token-change-me"
  sensitive   = true
}
