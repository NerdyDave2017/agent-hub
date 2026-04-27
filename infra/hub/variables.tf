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

variable "hub_public_url" {
  type        = string
  default     = "http://127.0.0.1:8000"
  description = "Public base URL of the hub API (HUB_PUBLIC_URL). App Runner / CI should set this to the real https service URL (terraform output hub_service_url) or a stable custom domain; default is for targeted applies that skip the service."
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
