variable "aws_region" {
  type        = string
  description = "AWS region for all resources."
  default     = "us-east-1"
}

variable "stack_name" {
  type        = string
  description = "Short name prefix for resources (e.g. dev, staging)."
  default     = "dev"
}

variable "vpc_id" {
  type        = string
  description = "VPC ID for ALB + ECS. Leave empty to use the account default VPC."
  default     = null
}

variable "public_subnet_ids" {
  type        = list(string)
  description = "At least two public subnet IDs (different AZs) for the ALB and Fargate tasks. Leave null to auto-pick subnets from the chosen/default VPC."
  default     = null
}

# --- Hub container (defaults prove wiring with nginx; switch to your ECR hub image) ---

variable "hub_image" {
  type        = string
  description = "Container image URI for the hub task (your ECR URL after first push, or a public image to validate infra)."
  default     = "public.ecr.aws/docker/library/nginx:alpine"
}

variable "hub_container_port" {
  type        = number
  description = "Container port the hub listens on (FastAPI hub uses 8000; nginx default uses 80)."
  default     = 80
}

variable "hub_health_check_path" {
  type        = string
  description = "ALB target group health check path (use /health for the real FastAPI hub on port 8000)."
  default     = "/"
}

variable "hub_desired_count" {
  type        = number
  description = "Fargate desired task count for the hub service."
  default     = 1
}

variable "enable_http_api_gateway" {
  type        = bool
  description = "When true, create an API Gateway HTTP API (HTTPS invoke URL) that proxies to the ALB — central entry for clients."
  default     = true
}

variable "ecr_hub_name" {
  type        = string
  default     = "agent-hub-backend"
}

variable "ecr_worker_name" {
  type        = string
  default     = "agent-hub-worker"
}

variable "ecr_agent_incident_triage_name" {
  type        = string
  default     = "agent-hub-incident-triage"
}

variable "hub_sqs_queue_arns" {
  type        = list(string)
  description = "SQS queue ARNs the hub may SendMessage to (main jobs queue). Leave empty until queues exist in another stack."
  default     = []
}

variable "hub_allow_secrets_manager" {
  type        = bool
  description = "Attach broad Secrets Manager permissions for Gmail/Slack OAuth flows (tighten to named secrets for production)."
  default     = true
}
