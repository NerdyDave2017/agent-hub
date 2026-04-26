output "service_url" {
  value = "https://${aws_apprunner_service.main.service_url}"
}

output "service_arn" {
  value = aws_apprunner_service.main.arn
}

output "service_id" {
  value = aws_apprunner_service.main.service_id
}

output "instance_role_arn" {
  value = var.create_instance_role ? aws_iam_role.instance[0].arn : var.instance_role_arn
}

output "instance_role_name" {
  value = var.create_instance_role ? aws_iam_role.instance[0].name : ""
}

output "ecr_access_role_arn" {
  value = aws_iam_role.ecr_access.arn
}
