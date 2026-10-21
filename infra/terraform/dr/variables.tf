# ── 꼭 넣을 값 (terraform.tfvars — git 에 넣지 않음) ─────────────────────────────
variable "backup_bucket" {
  description = "pgBackRest repo2 가 쓰는 S3 버킷 이름 (공개 저장소 · 발표 자료에는 <백업 버킷> 으로)"
  type        = string
}

variable "repo_path" {
  description = "버킷 안의 pgBackRest 경로. 개인 계정 시험 = /test-chargeops, 팀 계정 = /chargeops"
  type        = string
  default     = "/test-chargeops"
}

# ── 단계 스위치 (README 의 순서대로 하나씩 켠다) ───────────────────────────────
variable "build_ami" {
  description = "① 골든 이미지 만들기용 임시 서버 · 임시 공인 서브넷 · 인터넷 게이트웨이를 만든다 (만든 뒤 false 로 끔)"
  type        = bool
  default     = false
}

variable "make_ami" {
  description = "② 임시 서버가 꺼진 뒤 골든 이미지(AMI)를 찍는다. 한 번 true 로 했으면 계속 true (false 면 AMI 를 지움)"
  type        = bool
  default     = false
}

variable "drill" {
  description = "③ DR 훈련: SSM 엔드포인트 + DR EC2 (켜지면 S3 백업으로 자동 복원). 끝나면 false 로 → 비용 멈춤"
  type        = bool
  default     = false
}

variable "enable_rds" {
  description = "④ (선택) RDS 비교 실험 — DR EC2 에서 덤프를 RDS 로 복원해 시간 비교. drill 과 함께만"
  type        = bool
  default     = false
}

variable "rds_password" {
  description = "RDS 관리자(dba) 비밀번호. enable_rds 때만 — 파일에 쓰지 말고 TF_VAR_rds_password 로 (read -rsp)"
  type        = string
  default     = ""
  sensitive   = true
}

# ── 바꿀 일이 거의 없는 값 ──────────────────────────────────────────────────
variable "region" {
  type    = string
  default = "ap-northeast-2"
}

variable "vpc_cidr" {
  description = "DR VPC. 실습망 10.10.0.0/16 과 겹치지 않게"
  type        = string
  default     = "10.20.0.0/16"
}

variable "instance_type" {
  description = "DR-DB 서버. 실습 DB 0.5GB 기준 t3.small(2GB) — dr-restore.sh 가 메모리 4GB 미만이면 shared_buffers 를 1/4 로 맞춤"
  type        = string
  default     = "t3.small"
}

variable "rocky_ami_id" {
  description = "골든 이미지의 바탕 OS. 비우면 Rocky Linux 공식 계정(792107900819)의 최신 Rocky 9 x86_64 를 찾음"
  type        = string
  default     = ""
}

variable "dr_restore_url" {
  description = "골든 이미지에 넣을 복원 스크립트 (팀 저장소 공개 원본)"
  type        = string
  default     = "https://raw.githubusercontent.com/raspberry-2jo/chargeops/dev/db/ops/pgbackrest/dr-restore.sh"
}

variable "auto_restore" {
  description = "DR EC2 가 켜지자마자 복원을 시작 (설계도의 '켜지면 자동 복원'). false 면 SSM 으로 들어가 사람이 실행"
  type        = bool
  default     = true
}
