locals {
  project     = var.project
  environment = var.environment
}

data "terraform_remote_state" "vpc" {
  backend = "s3"
  config = {
    bucket = "agent-hub-terraform-state"
    key    = "vpc/terraform.tfstate"
    region = var.aws_region
  }
}

data "terraform_remote_state" "secrets" {
  backend = "s3"
  config = {
    bucket = "agent-hub-terraform-state"
    key    = "secrets/terraform.tfstate"
    region = var.aws_region
  }
}

module "rds" {
  source = "../modules/rds"

  project            = local.project
  environment        = local.environment
  private_subnet_ids = data.terraform_remote_state.vpc.outputs.private_subnet_ids
  rds_sg_id          = data.terraform_remote_state.vpc.outputs.rds_sg_id
  kms_key_arn        = data.terraform_remote_state.secrets.outputs.kms_key_arn
  instance_class     = var.instance_class
  db_name            = var.db_name
  db_username        = var.db_username
  db_password        = var.db_password
}
