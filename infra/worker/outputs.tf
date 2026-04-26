output "worker_task_role_arn" {
  value = module.worker.task_role_arn
}

output "worker_cluster_arn" {
  value = module.worker.cluster_arn
}

output "worker_cluster_name" {
  value       = element(split("/", module.worker.cluster_arn), 1)
  description = "ECS cluster name for deployment commands."
}

output "worker_service_name" {
  value       = module.worker.service_name
  description = "ECS service name for deployment commands."
}

output "agent_instance_role_arn" {
  value = aws_iam_role.agent_instance.arn
}

output "agent_ecr_access_role_arn" {
  value = aws_iam_role.agent_ecr_access.arn
}

output "worker_app_runner_image_identifier" {
  value       = local.agent_app_runner_image_identifier
  description = "ECR URI:tag passed to the worker as APP_RUNNER_CREATE_IMAGE_IDENTIFIER."
}

output "agent_instance_policy_json" {
  value       = aws_iam_role_policy.agent_instance.policy
  description = "For agents/incident-triage staging App Runner module.instance_policy_json"
}

output "ecr_repository_name" {
  value = aws_ecr_repository.worker.name
}

output "ecr_repository_url" {
  value = aws_ecr_repository.worker.repository_url
}
