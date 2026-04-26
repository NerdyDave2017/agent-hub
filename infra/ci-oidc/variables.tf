variable "aws_region" {
  type        = string
  description = "AWS region for the IAM OIDC provider and role."
}

variable "github_organization" {
  type        = string
  description = "GitHub org or user that owns the repository (e.g. andela-ai)."
}

variable "github_repository" {
  type        = string
  description = "Repository name only, without org (e.g. agent-hub)."
}

variable "create_github_oidc_provider" {
  type        = bool
  default     = true
  description = "Set false if this AWS account already has the GitHub Actions OIDC provider (token.actions.githubusercontent.com)."
}

variable "existing_github_oidc_provider_arn" {
  type        = string
  default     = ""
  description = "When create_github_oidc_provider is false, set to the existing OIDC provider ARN (arn:aws:iam::ACCOUNT:oidc-provider/token.actions.githubusercontent.com)."
}

variable "github_subject_claims" {
  type        = list(string)
  default     = null
  description = "Optional list of token.actions.githubusercontent.com:sub values allowed to assume the role. Defaults to repo:ORG/REPO:*"
}

variable "attach_policy_arns" {
  type        = list(string)
  default     = []
  description = "IAM managed policy ARNs to attach to the GitHub Actions role (e.g. AdministratorAccess for Terraform apply in a dedicated account)."
}

variable "role_name" {
  type        = string
  default     = "github-actions-terraform"
  description = "IAM role name GitHub Actions will assume via OIDC."
}

variable "tags" {
  type        = map(string)
  default     = {}
  description = "Tags for IAM resources."
}

check "github_oidc_provider_arn" {
  assert {
    condition = var.create_github_oidc_provider || (
      var.existing_github_oidc_provider_arn != "" &&
      startswith(var.existing_github_oidc_provider_arn, "arn:aws:iam::")
    )
    error_message = "When create_github_oidc_provider is false, set existing_github_oidc_provider_arn to a valid IAM OIDC provider ARN."
  }
}
