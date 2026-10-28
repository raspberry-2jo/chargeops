# DR VPC (WBS 4.5) — 황채연 AWS 설계의 "DR VPC 10.20.0.0/16 · DR 준비물" 을 코드로
#   Terraform 1.6+ 또는 OpenTofu 1.6+ (mgmt 에서 실행). 상태 파일은 S3 전용 버킷에 둔다 (backend.tf · S3 잠금은 Terraform 1.10+)
#   버킷 이름은 git 에 넣지 않고 mgmt 의 ~/.chargeops-dr.tfbackend 에서 읽는다 — docs/setup/aws-dr-mentor.md
terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.70, < 7.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = ">= 2.4, < 3.0"
    }
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = { Project = "chargeops", Stack = "dr", ManagedBy = "terraform" }
  }
}
