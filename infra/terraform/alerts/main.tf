data "aws_caller_identity" "me" {}

# ── 경보 모이는 곳 (SNS) ─────────────────────────────────────────────────────
resource "aws_sns_topic" "alerts" {
  name = "chargeops-alerts"
}

resource "aws_sns_topic_policy" "alerts" {
  arn = aws_sns_topic.alerts.arn
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Sid = "Owner", Effect = "Allow", Principal = { AWS = data.aws_caller_identity.me.account_id },
      Action = ["sns:Publish", "sns:Subscribe", "sns:GetTopicAttributes"], Resource = aws_sns_topic.alerts.arn },
      { Sid = "Budgets", Effect = "Allow", Principal = { Service = "budgets.amazonaws.com" },
      Action = "sns:Publish", Resource = aws_sns_topic.alerts.arn },
      { Sid = "CloudWatch", Effect = "Allow", Principal = { Service = "cloudwatch.amazonaws.com" },
      Action = "sns:Publish", Resource = aws_sns_topic.alerts.arn },
      { Sid = "EventBridge", Effect = "Allow", Principal = { Service = "events.amazonaws.com" },
      Action = "sns:Publish", Resource = aws_sns_topic.alerts.arn }
    ]
  })
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}
# Slack: AWS 콘솔 → Amazon Q Developer in chat applications(옛 Chatbot) 에서 워크스페이스 연결 뒤 이 토픽을 #chargeops-alerts 에 구독
#   (워크스페이스 승인이 브라우저에서만 돼 Terraform 으로 하지 않음 — docs/setup/aws-dr-vpc.md 7장 "다음")

# ── ③ mgmt 생존 신호: 1분마다 ChargeOps/Heartbeat 값 1 → 5분 끊기면 경보 ────────────────────
resource "aws_cloudwatch_metric_alarm" "mgmt_heartbeat" {
  alarm_name          = "chargeops-mgmt-heartbeat"
  alarm_description   = "온프레미스 mgmt 의 생존 신호가 끊김 — PC · 네트워크 · 관제 센터 전체 장애 의심 (DR 판단의 시작)"
  namespace           = "ChargeOps"
  metric_name         = "Heartbeat"
  dimensions          = { Host = "mgmt" }
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = var.heartbeat_minutes
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching" # 안 오는 것 자체가 장애
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
}

# 생존 신호 보내는 프로그램 전용 사용자 — 이 지표 하나만 쓸 수 있음. 키는 콘솔에서 만들어 mgmt 에서 read -rsp 로 (Terraform 상태에 키가 남지 않게)
resource "aws_iam_user" "heartbeat" {
    name          = "chargeops-heartbeat"
  force_destroy = true # 콘솔에서 만든 액세스 키가 있어도 지울 수 있게 (destroy)
}

resource "aws_iam_user_policy" "heartbeat" {
  name = "chargeops-heartbeat-put"
  user = aws_iam_user.heartbeat.name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "cloudwatch:PutMetricData", Resource = "*",
    Condition = { StringEquals = { "cloudwatch:namespace" = "ChargeOps" } } }]
  })
}

# ── ⑥ 예산 경보: 실제 80% · 100%, 예측 100% ─────────────────────────────────────
resource "aws_budgets_budget" "monthly" {
  # AWS 가 채우는 기본값(청구 화면 · 비용 계산 방식) — 코드에 없어 plan 에 매번 차이로 뜸
  lifecycle {
    ignore_changes = [billing_view_arn, metrics]
  }
  name         = "chargeops-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"
  dynamic "notification" {
    for_each = [
      { t = 80, type = "ACTUAL" }, { t = 100, type = "ACTUAL" }, { t = 100, type = "FORECASTED" }
    ]
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value.t
      threshold_type             = "PERCENTAGE"
      notification_type          = notification.value.type
      subscriber_email_addresses = [var.alert_email]
      subscriber_sns_topic_arns  = [aws_sns_topic.alerts.arn]
    }
  }
}

# ── ⑤ (선택) GuardDuty → EventBridge → SNS ───────────────────────────────────
resource "aws_guardduty_detector" "this" {
  count  = var.enable_guardduty ? 1 : 0
  enable = true
}

resource "aws_cloudwatch_event_rule" "guardduty" {
  count       = var.enable_guardduty ? 1 : 0
  name        = "chargeops-guardduty-medium-up"
  description = "GuardDuty 위협 (심각도 4 이상)"
  event_pattern = jsonencode({
    source        = ["aws.guardduty"]
    "detail-type" = ["GuardDuty Finding"]
    detail        = { severity = [{ numeric = [">=", 4] }] }
  })
}

resource "aws_cloudwatch_event_target" "guardduty" {
  count = var.enable_guardduty ? 1 : 0
  rule  = aws_cloudwatch_event_rule.guardduty[0].name
  arn   = aws_sns_topic.alerts.arn
}
