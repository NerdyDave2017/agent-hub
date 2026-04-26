output "hub_queue_url" {
  value = aws_sqs_queue.hub.id
}

output "hub_queue_arn" {
  value = aws_sqs_queue.hub.arn
}

output "hub_dlq_url" {
  value = aws_sqs_queue.hub_dlq.id
}

output "hub_dlq_arn" {
  value = aws_sqs_queue.hub_dlq.arn
}
