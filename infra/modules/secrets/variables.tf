variable "project" {
  type = string
}

variable "environment" {
  type = string
}

variable "aws_account_id" {
  type = string
}

variable "aws_region" {
  type = string
}

variable "hub_role_arn" {
  type        = string
  default     = ""
  description = "Optional; include after hub IAM exists to extend KMS key policy."
}

variable "worker_role_arn" {
  type        = string
  default     = ""
  description = "Optional; include after worker task role exists."
}

variable "agent_role_arns" {
  type    = list(string)
  default = []
}
