# infra/terraform

| 폴더 | 무엇 | 상태 |
|---|---|---|
| `dr/` | DR VPC 10.20.0.0/16 (들어오는 길 0 · S3 전용 길) · 골든 이미지 · 서버 설계도 · DR 훈련 · 자동 DR(Lambda) · RDS 비교 | 개인 계정 시험 완료 (2026-10-15) |
| `alerts/` | mgmt 생존 신호 → CloudWatch → SNS → 메일, 월 예산, (선택) GuardDuty | 개인 계정 시험 완료 (2026-10-15) |
| `terraform-user-policy.example.json` | Terraform 을 돌릴 IAM 사용자 권한 예시 (`chargeops-*` 이름만) | |

- 순서 · 결과 · 비용: [`docs/setup/aws-dr-vpc.md`](../../docs/setup/aws-dr-vpc.md)
- 계정마다 다른 값(버킷 · 경로 · 메일)은 각 폴더의 `terraform.tfvars` 에만 — git 에 올리지 않음 (`.gitignore`). 상태 파일도 실행한 mgmt 에만
- 설계: 황채연 「ChargeOps AWS 아키텍처」
