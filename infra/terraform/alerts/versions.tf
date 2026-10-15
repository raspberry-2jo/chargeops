# AWS 경보 (황채연 AWS 설계의 3 · 4 · 5 · 6): mgmt 생존 신호 → CloudWatch → SNS → 메일 · (Slack), 예산 경보, (선택) GuardDuty
#   상시 비용: 거의 0 (경보 1개 · SNS 메일 · 예산 2개까지 무료). GuardDuty 는 30일 무료 뒤 요금 → 기본 꺼짐
terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.70, < 7.0"
    }
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = { Project = "chargeops", Stack = "alerts", ManagedBy = "terraform" }
  }
}
