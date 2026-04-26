locals {
  project     = var.project
  environment = var.environment
}

module "vpc" {
  source = "../modules/vpc"

  project            = local.project
  environment        = local.environment
  aws_region         = var.aws_region
  vpc_cidr           = var.vpc_cidr
  availability_zones = var.availability_zones
}
