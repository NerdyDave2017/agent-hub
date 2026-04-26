output "github_oidc_provider_arn" {
  description = "ARN of the GitHub Actions OIDC identity provider (for trust policies and debugging)."
  value       = local.github_oidc_provider_arn
}

output "github_actions_role_arn" {
  description = "Assume this role from GitHub Actions via OIDC (set as AWS_ROLE_ARN secret or role-to-assume in configure-aws-credentials)."
  value       = aws_iam_role.github_actions.arn
}

output "github_actions_role_name" {
  value = aws_iam_role.github_actions.name
}
