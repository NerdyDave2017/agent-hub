variable "project" {
  type = string
}

variable "environment" {
  type = string
}

variable "service_name" {
  type = string
}

variable "image_uri" {
  type = string
}

variable "container_port" {
  type    = string
  default = "8080"
}

variable "cpu" {
  type    = string
  default = "1024"
}

variable "memory" {
  type    = string
  default = "2048"
}

variable "health_check_path" {
  type    = string
  default = "/health"
}

variable "vpc_connector_arn" {
  type    = string
  default = ""
}

variable "kms_key_arn" {
  type = string
}

variable "create_instance_role" {
  type        = bool
  default     = true
  description = "When false, pass instance_role_arn (hub root attaches IAM policy to that role)."
}

variable "instance_role_arn" {
  type        = string
  default     = ""
  description = "Required when create_instance_role is false."
}

variable "instance_policy_json" {
  type        = string
  default     = "{}"
  description = "IAM policy JSON for instance role; used only when create_instance_role is true."
}

variable "environment_variables" {
  type        = map(string)
  default     = {}
  description = "Non-secret environment variables injected into container."
}

variable "environment_secrets" {
  type        = map(string)
  default     = {}
  description = "Map of env var name to Secrets Manager secret ARN."
}
