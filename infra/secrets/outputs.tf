output "kms_key_arn" {
  value = module.secrets.kms_key_arn
}

output "kms_key_id" {
  value = module.secrets.kms_key_id
}

output "langfuse_secret_arn" {
  value = module.secrets.langfuse_secret_arn
}

output "internal_service_token_arn" {
  value = module.secrets.internal_service_token_arn
}
