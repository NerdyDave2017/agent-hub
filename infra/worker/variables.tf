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

variable "worker_image_uri" {
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

variable "agent_ecr_repository" {
  type        = string
  default     = ""
  description = "ECR repository path (without registry), e.g. agent-hub/staging/incident-triage. Empty = agent-hub/{environment}/incident-triage."
}

variable "agent_image_tag" {
  type        = string
  default     = "latest"
  description = "Image tag pushed to ECR for per-tenant App Runner CreateService."
}

variable "worker_extra_environment" {
  type        = list(map(string))
  default     = []
  description = "Extra worker container env {name,value} maps (merged with App Runner provisioning vars)."
}

variable "domain_name" {
  type    = string
  default = "agent-hub.io"
}

variable "langfuse_host" {
  type    = string
  default = "https://cloud.langfuse.com"
}

variable "langfuse_public_key" {
  type      = string
  sensitive = true
  default   = ""
}

variable "langfuse_secret_key" {
  type      = string
  sensitive = true
  default   = ""
}
