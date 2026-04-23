provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project   = "agent-hub"
      ManagedBy = "terraform"
      Stack     = var.stack_name
    }
  }
}
