locals {
  default_tags = {
    Project     = var.project
    Environment = var.environment
  }
}

resource "aws_db_subnet_group" "main" {
  name       = "${var.project}-${var.environment}-db-subnet-group"
  subnet_ids = var.private_subnet_ids
  tags       = merge(local.default_tags, { Name = "${var.project}-${var.environment}-db-subnet-group" })
}

resource "aws_db_instance" "postgres" {
  identifier     = "${var.project}-${var.environment}-postgres"
  engine         = "postgres"
  engine_version = "16.4"
  instance_class = var.instance_class

  allocated_storage     = 20
  max_allocated_storage = 100
  storage_type          = "gp3"
  storage_encrypted     = true
  kms_key_id            = var.kms_key_arn

  db_name  = var.db_name
  username = var.db_username
  password = var.db_password

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [var.rds_sg_id]

  publicly_accessible = false

  backup_retention_period      = 7
  deletion_protection          = var.environment == "production" ? true : false
  skip_final_snapshot          = var.environment != "production"
  final_snapshot_identifier    = "${var.project}-${var.environment}-final-snapshot"
  performance_insights_enabled = true

  tags = merge(local.default_tags, { Name = "${var.project}-${var.environment}-postgres" })
}

resource "aws_secretsmanager_secret" "db_url" {
  name                    = "${var.project}/${var.environment}/database/url"
  kms_key_id              = var.kms_key_arn
  recovery_window_in_days = 7
  tags                    = merge(local.default_tags, { Name = "${var.project}-${var.environment}-db-url" })
}

resource "aws_secretsmanager_secret_version" "db_url" {
  secret_id     = aws_secretsmanager_secret.db_url.id
  secret_string = "postgresql+asyncpg://${var.db_username}:${var.db_password}@${aws_db_instance.postgres.endpoint}/${var.db_name}"
}
