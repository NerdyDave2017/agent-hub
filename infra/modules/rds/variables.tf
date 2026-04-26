variable "project" {
  type = string
}

variable "environment" {
  type = string
}

variable "private_subnet_ids" {
  type = list(string)
}

variable "rds_sg_id" {
  type = string
}

variable "kms_key_arn" {
  type = string
}

variable "instance_class" {
  type    = string
  default = "db.t4g.micro"
}

variable "db_name" {
  type    = string
  default = "agent_hub"
}

variable "db_username" {
  type    = string
  default = "agent_hub_admin"
}

variable "db_password" {
  type      = string
  sensitive = true
}
