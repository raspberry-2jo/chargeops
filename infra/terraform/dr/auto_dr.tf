# ============================================================================
# 자동 DR 발동 (설계도 ⑫: 경보 → Lambda → EC2) — auto_dr=true 일 때만, 훈련 모드 고정
#   mgmt 생존 신호가 5분 끊김 → CloudWatch 경보(alerts 스택) → SNS chargeops-alerts → 이 Lambda → 서버 설계도로 DR-DB 1대
#   ⚠ 켜기 전에 생존 신호가 들어와 경보가 정상(OK)이어야 한다 (안 그러면 켜자마자 발동)
#   ⚠ 시험이 끝나면 auto_dr=false 로 — 켜 둔 채면 mgmt 가 잠깐만 끊겨도 서버가 뜬다
# ============================================================================
variable "auto_dr" {
  description = "자동 DR 발동 Lambda 를 켠다 (alerts 스택을 먼저 적용하고 생존 신호가 OK 일 때)"
  type        = bool
  default     = false
}

variable "alerts_topic_name" {
  description = "alerts 스택의 SNS 토픽 이름"
  type        = string
  default     = "chargeops-alerts"
}

variable "heartbeat_alarm_name" {
  type    = string
  default = "chargeops-mgmt-heartbeat"
}

data "aws_sns_topic" "alerts" {
  count = var.auto_dr ? 1 : 0
  name  = var.alerts_topic_name
}

data "archive_file" "auto_dr" {
  type        = "zip"
  source_file = "${path.module}/lambda/auto_dr.py"
  output_path = "${path.module}/.build/auto_dr.zip"
}

resource "aws_iam_role" "auto_dr" {
  count = var.auto_dr ? 1 : 0
  name  = "chargeops-dr-lambda"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy" "auto_dr" {
  count = var.auto_dr ? 1 : 0
  name  = "chargeops-dr-lambda"
  role  = aws_iam_role.auto_dr[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Sid = "Launch", Effect = "Allow", Action = ["ec2:RunInstances", "ec2:DescribeInstances", "ec2:DescribeLaunchTemplateVersions"],
      Resource = "*", Condition = { StringEquals = { "aws:RequestedRegion" = var.region } } },
      { Sid = "TagOnLaunch", Effect = "Allow", Action = "ec2:CreateTags", Resource = "*",
      Condition = { StringEquals = { "ec2:CreateAction" = "RunInstances" } } },
      { Sid = "PassDrRole", Effect = "Allow", Action = "iam:PassRole", Resource = aws_iam_role.dr.arn },
      { Sid = "Notify", Effect = "Allow", Action = "sns:Publish", Resource = data.aws_sns_topic.alerts[0].arn },
      { Sid = "Logs", Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"],
      Resource = "${aws_cloudwatch_log_group.auto_dr[0].arn}:*" }
    ]
  })
}

resource "aws_cloudwatch_log_group" "auto_dr" {
  count             = var.auto_dr ? 1 : 0
  name              = "/aws/lambda/chargeops-auto-dr"
  retention_in_days = 14
}

resource "aws_lambda_function" "auto_dr" {
  count            = var.auto_dr ? 1 : 0
  function_name    = "chargeops-auto-dr"
  role             = aws_iam_role.auto_dr[0].arn
  runtime          = "python3.12"
  handler          = "auto_dr.handler"
  filename         = data.archive_file.auto_dr.output_path
  source_code_hash = data.archive_file.auto_dr.output_base64sha256
  timeout          = 30
  environment {
    variables = {
      ALARM_NAME         = var.heartbeat_alarm_name
      LAUNCH_TEMPLATE_ID = aws_launch_template.dr[0].id
      SUBNET_ID          = aws_subnet.private_a.id
      TOPIC_ARN          = data.aws_sns_topic.alerts[0].arn
    }
  }
  lifecycle {
    precondition {
      condition     = var.make_ami
      error_message = "자동 DR 은 서버 설계도가 있어야 한다 — make_ami=true 와 함께."
    }
  }
  depends_on = [aws_iam_role_policy.auto_dr, aws_cloudwatch_log_group.auto_dr]
}

resource "aws_lambda_permission" "sns" {
  count         = var.auto_dr ? 1 : 0
  statement_id  = "AllowAlertsTopic"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.auto_dr[0].function_name
  principal     = "sns.amazonaws.com"
  source_arn    = data.aws_sns_topic.alerts[0].arn
}

resource "aws_sns_topic_subscription" "auto_dr" {
  count      = var.auto_dr ? 1 : 0
  topic_arn  = data.aws_sns_topic.alerts[0].arn
  protocol   = "lambda"
  endpoint   = aws_lambda_function.auto_dr[0].arn
  depends_on = [aws_lambda_permission.sns]
}

output "auto_dr_function" {
  value = try(aws_lambda_function.auto_dr[0].function_name, null)
}
