terraform {
  required_version = ">= 1.5.0"

  backend "s3" {
    bucket         = "agent-hub-terraform-state"
    key            = "rds/terraform.tfstate"
    region         = "us-east-1"
    encrypt        = true
    dynamodb_table = "agent-hub-terraform-locks"
  }

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}
