# LocalStack: single AWS provider with custom endpoints (no real AWS calls).
# https://docs.localstack.cloud/user-guide/integrations/terraform/

provider "aws" {
  region = var.aws_region

  access_key = "test"
  secret_key = "test"

  skip_credentials_validation = true
  skip_metadata_api_check     = true
  skip_requesting_account_id  = true

  endpoints {
    sqs            = var.localstack_endpoint
    iam            = var.localstack_endpoint
    sts            = var.localstack_endpoint
    secretsmanager = var.localstack_endpoint
  }
}

data "aws_caller_identity" "current" {}

data "aws_region" "current" {}
