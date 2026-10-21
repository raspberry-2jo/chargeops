variable "alert_email" {
  description = "경보 받을 메일 (terraform.tfvars 에만 — 공개 저장소에 넣지 않음). 만든 뒤 받은 메일의 Confirm 링크를 눌러야 옴"
  type        = string
}

variable "monthly_budget_usd" {
  description = "월 예산 (보고서 7장: 10 USD, 80% · 100% 에서 경보)"
  type        = number
  default     = 10
}

variable "heartbeat_minutes" {
  description = "mgmt 생존 신호가 이 시간 동안 끊기면 경보 (설계도: 5분)"
  type        = number
  default     = 5
}

variable "enable_guardduty" {
  description = "GuardDuty 켜고 위협(중간 이상)을 SNS 로. 30일 무료 뒤 요금 — 기본 꺼짐"
  type        = bool
  default     = false
}

variable "region" {
  type    = string
  default = "ap-northeast-2"
}
