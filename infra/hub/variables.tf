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

variable "hub_image_uri" {
  type = string
}

variable "worker_role_arn" {
  type        = string
  default     = ""
  description = "ECS worker task role ARN — set after first worker apply, then re-apply hub to attach SQS consume policy."
}

variable "frontend_url" {
  type = string
}

variable "google_pubsub_topic" {
  type = string
}

variable "gcp_project_id" {
  type = string
}

variable "google_oauth_client_id" {
  type      = string
  sensitive = true
}

variable "google_oauth_client_secret" {
  type      = string
  sensitive = true
}

variable "google_webhook_secret" {
  type      = string
  sensitive = true
}

variable "slack_oauth_client_id" {
  type      = string
  sensitive = true
}

variable "slack_oauth_client_secret" {
  type      = string
  sensitive = true
}

variable "alarm_sns_arns" {
  type    = list(string)
  default = []
}

variable "langfuse_host" {
  type    = string
  default = "https://cloud.langfuse.com"
}
