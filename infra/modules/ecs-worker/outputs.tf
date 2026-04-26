output "cluster_arn" {
  value = aws_ecs_cluster.worker.arn
}

output "task_role_arn" {
  value = aws_iam_role.task.arn
}

output "task_role_name" {
  value = aws_iam_role.task.name
}

output "execution_role_arn" {
  value = aws_iam_role.execution.arn
}

output "service_name" {
  value = aws_ecs_service.worker.name
}

output "log_group_name" {
  value = aws_cloudwatch_log_group.worker.name
}
