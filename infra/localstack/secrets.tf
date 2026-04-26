resource "aws_secretsmanager_secret" "internal_service_token" {
  name = "agent-hub/local/internal-service-token"
  tags = {
    Service = "agent-hub"
    Stack   = "localstack"
  }
}

resource "aws_secretsmanager_secret_version" "internal_service_token" {
  secret_id     = aws_secretsmanager_secret.internal_service_token.id
  secret_string = var.internal_service_token
}

resource "aws_secretsmanager_secret" "oauth_placeholder" {
  name = "agent-hub/local/oauth-placeholder"
  tags = {
    Service = "agent-hub"
    Stack   = "localstack"
  }
}

resource "aws_secretsmanager_secret_version" "oauth_placeholder" {
  secret_id     = aws_secretsmanager_secret.oauth_placeholder.id
  secret_string = jsonencode({})
}
