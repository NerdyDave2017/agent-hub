output "vpc_id" {
  value = module.vpc.vpc_id
}

output "private_subnet_ids" {
  value = module.vpc.private_subnet_ids
}

output "public_subnet_ids" {
  value = module.vpc.public_subnet_ids
}

output "worker_sg_id" {
  value = module.vpc.worker_sg_id
}

output "rds_sg_id" {
  value = module.vpc.rds_sg_id
}

output "app_runner_connector_sg_id" {
  value = module.vpc.app_runner_connector_sg_id
}

output "app_runner_vpc_connector_arn" {
  value = module.vpc.app_runner_vpc_connector_arn
}
