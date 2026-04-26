variable "project" {
  type = string
}

variable "environment" {
  type = string
}

variable "aws_region" {
  type        = string
  description = "Used in VPC endpoint service names."
}

variable "vpc_cidr" {
  type    = string
  default = "10.0.0.0/16"
}

variable "availability_zones" {
  type = list(string)
}
