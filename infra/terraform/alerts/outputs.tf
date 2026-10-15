output "sns_topic_arn" {
  value = aws_sns_topic.alerts.arn
}

output "heartbeat_user" {
  description = "콘솔 → IAM → 사용자 → 이 사용자 → 액세스 키 만들기 (사용 사례: AWS 외부 애플리케이션)"
  value       = aws_iam_user.heartbeat.name
}
