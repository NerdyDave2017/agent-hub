variable "project" {
  type = string
}

variable "environment" {
  type = string
}

variable "kms_key_id" {
  type        = string
  description = "KMS key id or alias for SSE on queues."
}

variable "hub_role_arn" {
  type = string
}

variable "worker_role_arn" {
  type        = string
  default     = ""
  description = "Leave empty on first hub apply; re-apply hub after worker to attach consume policy."
}

variable "alarm_sns_arns" {
  type    = list(string)
  default = []
}
