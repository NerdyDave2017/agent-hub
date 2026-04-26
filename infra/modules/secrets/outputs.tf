output "kms_key_arn" {
  value = aws_kms_key.main.arn
}

output "kms_key_id" {
  value = aws_kms_key.main.key_id
}

output "langfuse_secret_arn" {
  value = aws_secretsmanager_secret.langfuse.arn
}

output "internal_service_token_arn" {
  value = aws_secretsmanager_secret.internal_service_token.arn
}

output "jwt_secret_key_arn" {
  value = aws_secretsmanager_secret.jwt_secret_key.arn
}
