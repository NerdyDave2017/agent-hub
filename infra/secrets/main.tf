locals {
  project     = var.project
  environment = var.environment
}

module "secrets" {
  source = "../modules/secrets"

  project         = local.project
  environment     = local.environment
  aws_account_id  = data.aws_caller_identity.current.account_id
  aws_region      = var.aws_region
  hub_role_arn    = var.hub_role_arn
  worker_role_arn = var.worker_role_arn
  agent_role_arns = var.agent_role_arns
}
