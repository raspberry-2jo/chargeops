# Terraform 장부(상태 파일)는 S3 전용 버킷에 둔다 — 버전 보관 · 잠금(use_lockfile, Terraform 1.10+)
#   버킷 이름 · 경로 · 리전은 git 에 넣지 않고 mgmt 의 ~/.chargeops-dr.tfbackend 에서 읽는다
#     terraform init -backend-config=$HOME/.chargeops-dr.tfbackend
#   (멘토 서버 mgmt 는 ~/.chargeops-dr.env 의 TF_CLI_ARGS_init 로 자동) — docs/setup/aws-dr-mentor.md
terraform {
  backend "s3" {}
}
