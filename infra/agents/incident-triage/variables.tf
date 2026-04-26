variable "environment" {
  type = string
}

variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "aws_account_id" {
  type = string
}

variable "staging_tenant_id" {
  type    = string
  default = ""
}

variable "staging_agent_id" {
  type    = string
  default = ""
}

variable "ecr_repository_name" {
  type        = string
  default     = ""
  description = "Optional ECR repository path without registry. Empty defaults to agent-hub/{environment}/incident-triage."
}

variable "gmail_poll_interval_seconds" {
  type        = number
  default     = 0
  description = "Incident-triage Gmail poll interval in seconds; 0 disables polling."
}
