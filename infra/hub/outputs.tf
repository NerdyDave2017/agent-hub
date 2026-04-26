output "hub_service_url" {
  value = module.apprunner.service_url
}

output "hub_service_arn" {
  value = module.apprunner.service_arn
}

output "hub_role_arn" {
  value = aws_iam_role.hub.arn
}

output "hub_queue_url" {
  value = module.sqs.hub_queue_url
}

output "hub_queue_arn" {
  value = module.sqs.hub_queue_arn
}

output "ecr_repository_name" {
  value = aws_ecr_repository.hub.name
}

output "ecr_repository_url" {
  value = aws_ecr_repository.hub.repository_url
}
