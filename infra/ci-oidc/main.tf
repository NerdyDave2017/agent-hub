locals {
  default_subject_claims = [
    "repo:${var.github_organization}/${var.github_repository}:*",
  ]
  subject_claims = coalesce(var.github_subject_claims, local.default_subject_claims)
}

# AWS provider >= 5.81: thumbprint_list optional for GitHub; IAM uses trusted CAs.
resource "aws_iam_openid_connect_provider" "github" {
  count = var.create_github_oidc_provider ? 1 : 0

  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]

  tags = merge(var.tags, { Name = "github-actions-oidc" })
}

locals {
  github_oidc_provider_arn = var.create_github_oidc_provider ? aws_iam_openid_connect_provider.github[0].arn : var.existing_github_oidc_provider_arn
}

data "aws_iam_policy_document" "github_actions_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [local.github_oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = local.subject_claims
    }
  }
}

resource "aws_iam_role" "github_actions" {
  name                 = var.role_name
  assume_role_policy   = data.aws_iam_policy_document.github_actions_trust.json
  max_session_duration = 3600

  tags = merge(var.tags, { Name = var.role_name })
}

resource "aws_iam_role_policy_attachment" "github_actions" {
  for_each = toset(var.attach_policy_arns)

  role       = aws_iam_role.github_actions.name
  policy_arn = each.value
}
