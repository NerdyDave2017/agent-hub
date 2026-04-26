variable "project" {
  type = string
}

variable "environment" {
  type = string
}

variable "aws_region" {
  type = string
}

variable "aws_account_id" {
  type = string
}

variable "image_uri" {
  type = string
}

variable "cpu" {
  type    = string
  default = "512"
}

variable "memory" {
  type    = string
  default = "1024"
}

variable "private_subnet_ids" {
  type = list(string)
}

variable "worker_sg_id" {
  type = string
}

variable "hub_queue_url" {
  type = string
}

variable "hub_queue_arn" {
  type = string
}

variable "db_secret_arn" {
  type = string
}

variable "kms_key_arn" {
  type = string
}

variable "internal_service_token_arn" {
  type = string
}

variable "hub_base_url" {
  type = string
}

variable "gcp_project_id" {
  type = string
}

variable "gmail_pubsub_topic" {
  type = string
}

variable "ecr_registry" {
  type = string
}

variable "agent_ecr_access_role_arn" {
  type = string
}

variable "agent_instance_role_arn" {
  type        = string
  default     = ""
  description = "IAM role ARN passed to worker for agent App Runner provisioning."
}

variable "extra_environment" {
  type        = list(map(string))
  default     = []
  description = "Additional {name,value} pairs for the worker container (e.g. APP_RUNNER_CREATE_* from the worker root)."
}
