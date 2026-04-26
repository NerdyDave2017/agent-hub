output "db_endpoint" {
  value = aws_db_instance.postgres.endpoint
}

output "db_name" {
  value = aws_db_instance.postgres.db_name
}

output "db_secret_arn" {
  value = aws_secretsmanager_secret.db_url.arn
}

output "db_instance_id" {
  value = aws_db_instance.postgres.id
}
