variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "project" {
  type    = string
  default = "agent-hub"
}

variable "environment" {
  type = string
}

variable "hub_role_arn" {
  type    = string
  default = ""
}

variable "worker_role_arn" {
  type    = string
  default = ""
}

variable "agent_role_arns" {
  type    = list(string)
  default = []
}
