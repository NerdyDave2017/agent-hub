output "vpc_id" {
  value = aws_vpc.main.id
}

output "private_subnet_ids" {
  value = aws_subnet.private[*].id
}

output "public_subnet_ids" {
  value = aws_subnet.public[*].id
}

output "worker_sg_id" {
  value = aws_security_group.worker.id
}

output "rds_sg_id" {
  value = aws_security_group.rds.id
}

output "app_runner_connector_sg_id" {
  value = aws_security_group.app_runner_connector.id
}

output "app_runner_vpc_connector_arn" {
  value = aws_apprunner_vpc_connector.hub.arn
}
