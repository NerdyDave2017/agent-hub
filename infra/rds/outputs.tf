output "db_endpoint" {
  value = module.rds.db_endpoint
}

output "db_name" {
  value = module.rds.db_name
}

output "db_secret_arn" {
  value = module.rds.db_secret_arn
}

output "db_instance_id" {
  value = module.rds.db_instance_id
}
