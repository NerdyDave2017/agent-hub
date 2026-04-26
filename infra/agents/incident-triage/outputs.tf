output "ecr_repository_url" {
  value = aws_ecr_repository.agent.repository_url
}

output "ecr_repository_name" {
  value = aws_ecr_repository.agent.name
}

output "staging_service_url" {
  value = length(module.agent_staging) > 0 ? module.agent_staging[0].service_url : ""
}
