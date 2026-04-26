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
