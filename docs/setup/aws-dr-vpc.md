# AWS DR VPC · 자동 DR 설치 기록 (Terraform)

- 담당: 강영수 (설계: 황채연 「ChargeOps AWS 아키텍처」의 DR VPC · DR 준비물 · 자동 DR)
- 관련 WBS: 4.5 DR 훈련, 2.2 후속(AWS 경보)
- 버전: versions.md 참고 (Terraform · AWS provider)
- 상태: **개인 AWS 계정으로 시험 완료 (2026-10-15)** — 수동 훈련 RTO **4분 55초**, 자동 DR(사람 손 0) RTO **7분 49초**. 팀 계정은 `terraform.tfvars` 의 버킷 · 경로 · 메일만 바꾼다
- 2026-10-28: 팀 계정 · 멘토 서버 실데이터로 **멘토 서버 mgmt 에서 실행** (복원 124초, RTO 약 2분 17초). 장부(상태 파일)는 S3 전용 버킷으로 옮김 → [`aws-dr-mentor.md`](aws-dr-mentor.md)
- 앞 단계: [`aws-s3-backup.md`](aws-s3-backup.md) (repo2 = AWS S3 에 백업이 쌓이고 있어야 함), [`dr-restore.md`](dr-restore.md) (빈 VM 복원 — 같은 스크립트를 이번엔 AWS 안에서)

## 0. 결과 요약

| | 수동 훈련 (15:17) | 자동 DR (15:54) |
|---|---|---|
| 시작 | 사람이 `terraform apply` | mgmt 생존 신호를 일부러 끊음 (센터 전체 장애 가정) |
| 감지 | — | 경보까지 **4분 15초** |
| 서버 생성 | 1분 33초 (엔드포인트 포함, 서버만 13초) | Lambda 가 경보 후 1분 안에 |
| 부팅 → 복원 시작 | 48초 | 약 50초 |
| 패키지 설치 | **0초** (골든 이미지. 빈 VM 은 54초) | 0초 |
| S3 에서 내려받기 (513MB · 1,522개) | 27초 | 24초 |
| WAL 재생 · 승격 | 125초 | 131초 |
| **RTO** | **4분 55초** | **7분 49초** (목표 30분) |
| 사람 손 | 명령 1번 | **0** (메일 2통으로 알게 됨) |
| 확인 | 충전기 527,266 · 상태 이력 753,062 · 확장 4개 · 미리 올린 라이브러리 3개 · `복구 중 f` · WAL 보관 꺼짐 | 같음 (상태 이력 755,686) |

- RPO: 운영 DB 가 계속 돌던 시험이라 복원이 끝 무렵(수십 초 전)까지 따라왔다. 실제로 센터가 통째로 멈추면 **아직 S3 로 못 올린 마지막 1분 안쪽**(WAL 60초)만 잃는다
- 훈련 모드라 복원본은 저장소에 아무것도 올리지 않았다 (db-01 의 `pgbackrest --repo=2 info` 의 WAL 이 타임라인 9 그대로)

## 1. 왜 / 무엇을

- 빈 VM 복원(10/14, 63초)은 "같은 PC 안의 새 VM" 이었다. 이번에는 **관제 센터가 통째로 사라졌을 때 AWS 안에서** 백업만으로 DB 를 되살린다
- DR 서버는 **들어오는 길 0 · 인터넷 0** 인 사설 망에 두고, S3 백업만 전용 길로 읽는다. 사람은 포트 없이 Session Manager 로 들어간다
- RDS 가 아니라 EC2 + PostgreSQL 직접 설치: 우리 물리 백업(pgBackRest) · WAL 을 그대로 복원할 수 있어 RPO 가 1분. RDS 는 외부 물리 백업을 받지 않아 덤프로만 가능 → 비교 실험으로만 둠

| 설계도 | 코드 | 상태 |
|---|---|---|
| DR VPC 10.20.0.0/16 · 프라이빗 서브넷 A(2a) · C(2c) | `infra/terraform/dr` 상시 | ✅ 시험 |
| 보안 그룹 "들어오는 길 0" | 들어오는 규칙 0, 나가는 것은 S3 · SSM 엔드포인트 · RDS 만 | ✅ |
| S3 게이트웨이 엔드포인트 (무료) | 정책: 백업 버킷의 우리 경로 읽기만 | ✅ |
| 골든 이미지 (AMI) | 임시 서버가 PG17 · 확장 3개 · pgBackRest 2.59 · SSM 에이전트 · `dr-restore.sh` 설치 후 스스로 꺼짐 → 이미지 | ✅ (설치 약 3분) |
| 서버 설계도 (Launch Template) · IAM 역할 | 역할: 백업 경로 읽기 + SSM. 첫 부팅에 훈련 모드 자동 복원 | ✅ |
| SSM 엔드포인트 · EC2 DR-DB | 훈련 때만 (`drill=true`) | ✅ 수동 RTO 4분 55초 |
| ⑫ 경보 → Lambda → EC2 (자동 DR) | `auto_dr=true` 때만, 훈련 모드 고정 | ✅ 자동 RTO 7분 49초 |
| ③ mgmt 생존 신호 → CloudWatch → SNS → 메일 | `infra/terraform/alerts` + `aws-heartbeat.py` (1분 cron) | ✅ |
| ⑦ Budgets | 월 10달러, 실제 80 · 100% · 예측 100% | ✅ |
| ⑤ GuardDuty → EventBridge → SNS | `enable_guardduty` (기본 끔) | 코드만 |
| RDS 비교 실험 · DB 서브넷 그룹 (A+C) | `enable_rds` | ✅ 비교 완료, DR 은 EC2 유지 → [`aws-rds-compare.md`](aws-rds-compare.md) |
| CloudTrail 기록 · 보안 경보 4종 · Config → EventBridge · Slack | 콘솔 (황채연) | ✅ [`aws-security-alerts.md`](aws-security-alerts.md) |
| S3 백업 감시 (Lambda · 매일 07:00) | 콘솔 · CloudShell (황채연) | ✅ [`aws-backup-watch.md`](aws-backup-watch.md) |
| ECR 이미지 사본 | 콘솔 · Jenkinsfile | ✅ [`aws-ecr-mirror.md`](aws-ecr-mirror.md) |
| Grafana AWS 상황판 | EC2 `chargeops-metrics` | ✅ [`aws-grafana-ec2.md`](aws-grafana-ec2.md) |

## 2. 설치 명령어

**▶ AWS 콘솔 (한 번)** — Terraform 전용 사용자 (루트 키는 만들지 않는다)

1. IAM → 정책 → 정책 생성 → JSON → `infra/terraform/terraform-user-policy.example.json` → 이름 `chargeops-terraform`
2. IAM → 사용자 생성 `chargeops-terraform` (콘솔 액세스 체크 안 함) → 위 정책 직접 연결 → 액세스 키 ("AWS 외부에서 실행되는 애플리케이션")

**▶ mgmt (한 번)**

```bash
# ===== 실행할 명령어 =====
sudo dnf install -y dnf-plugins-core && sudo dnf config-manager --add-repo https://rpm.releases.hashicorp.com/RHEL/hashicorp.repo && sudo dnf install -y terraform

# 키는 화면 · 셸 기록에 안 남게 → ~/.chargeops.env(600) 에만
read -rsp "Terraform Access key ID: " K; echo; read -rsp "Terraform Secret access key: " S; echo; printf "export AWS_TF_KEY_ID='%s'\nexport AWS_TF_SECRET='%s'\n" "$K" "$S" >> ~/.chargeops.env && unset K S && chmod 600 ~/.chargeops.env

# 창마다 한 번: MinIO 용 AWS_* · 사설 CA 설정이 섞이지 않게 감싼 terraform
tf() { ( source ~/.chargeops.env; unset AWS_CA_BUNDLE AWS_ENDPOINT_URL AWS_ENDPOINT_URL_S3 S3_ENDPOINT_URL AWS_SESSION_TOKEN AWS_PROFILE; export AWS_ACCESS_KEY_ID="$AWS_TF_KEY_ID" AWS_SECRET_ACCESS_KEY="$AWS_TF_SECRET"; terraform "$@" ); }

# 백업이 S3 에 있는지 (status: ok · full backup 1개 이상 · WAL 이 오늘까지)
ssh db-01 'sudo -u postgres pgbackrest --stanza=chargeops --repo=2 info' | grep -E 'status|full backup|wal archive min/max'
```

### 2-1. DR VPC · 골든 이미지 (한 번)

```bash
# ===== 실행할 명령어 =====
cd infra/terraform/dr && cp terraform.tfvars.example terraform.tfvars   # backup_bucket = "<백업 버킷>", repo_path = "/test-chargeops"(개인) | "/chargeops"(팀)
tf init -input=false -backend-config=$HOME/.chargeops-dr.tfbackend && tf validate   # 장부 위치(S3) 파일: aws-dr-mentor.md 2장

tf apply                                                   # 0. 상시 망 13개 (무료)
tf apply -var build_ami=true                               # 1. 임시 서버 등 6개 → 설치 후 스스로 꺼짐 (약 3~8분)
tf apply -refresh-only -auto-approve -var build_ami=true >/dev/null && tf state show 'aws_instance.builder[0]' | grep instance_state   # "stopped" 이면 다음
tf apply -var build_ami=true -var make_ami=true            # 2. 이미지 · 서버 설계도 2개
tf apply -var make_ami=true                                # 임시 서버 · 공인 서브넷 · 인터넷 게이트웨이 6개 지움 → 인터넷 길 0
```

### 2-2. 수동 훈련

```bash
# ===== 실행할 명령어 =====
date '+시작 %H:%M:%S'; tf apply -auto-approve -var make_ami=true -var drill=true; date '+생성 끝 %H:%M:%S'   # SSM 엔드포인트 3 · DR-DB 1
# 콘솔 EC2 → chargeops-dr-db → 연결 → Session Manager →  sudo tail -n 25 /var/log/dr-restore.log
tf apply -var make_ami=true                                # 끝: DR-DB · 엔드포인트 4개 지움 (비용 멈춤)
```

### 2-3. 경보 (상시)

```bash
# ===== 실행할 명령어 =====
cd ../alerts && cp terraform.tfvars.example terraform.tfvars   # alert_email = "<경보 메일>"
tf init -input=false && tf apply                           # SNS · 메일 구독 · 생존 신호 경보 · 생존 신호 사용자 · 예산 = 7개
# 메일함의 "Subscription Confirmation" → Confirm (안 누르면 안 옴)
# 콘솔 IAM → 사용자 chargeops-heartbeat → 액세스 키 만들기
read -rsp "Heartbeat Access key ID: " K; echo; read -rsp "Heartbeat Secret: " S; echo; printf "export AWS_HB_KEY_ID='%s'\nexport AWS_HB_SECRET='%s'\n" "$K" "$S" >> ~/.chargeops.env && unset K S && chmod 600 ~/.chargeops.env
bash -c 'source ~/.chargeops.env && python3 <저장소>/infra/terraform/alerts/aws-heartbeat.py --verbose'
(crontab -l 2>/dev/null | grep -v aws-heartbeat.py; echo "* * * * * bash -c 'source ~/.chargeops.env && python3 <저장소>/infra/terraform/alerts/aws-heartbeat.py' >/dev/null 2>&1") | crontab -
# 콘솔 CloudWatch → 경보 → chargeops-mgmt-heartbeat 가 정상(OK) 될 때까지 (2~5분)
```

### 2-4. 자동 DR 시험 (경보가 OK 인 뒤에만)

```bash
# ===== 실행할 명령어 =====
cd ../dr && tf init -upgrade -input=false -backend-config=$HOME/.chargeops-dr.tfbackend
tf apply -var make_ami=true -var auto_dr=true              # Lambda · 역할 · 로그 · 권한 · SNS 구독 6 + SSM 엔드포인트 3 = 9개

crontab -l | sed 's|^\(.*aws-heartbeat.py.*\)$|#\1|' | crontab - && date '+신호 끊음 %H:%M:%S'   # 사고 발생 가정
# 약 5분 뒤 메일 2통 (ALARM · "[ChargeOps] 자동 DR 발동 (훈련 모드)") → 3~4분 뒤 Session Manager 로 복원 로그

crontab -l | sed 's|^#\(.*aws-heartbeat.py.*\)$|\1|' | crontab -   # 신호 되살림 → 몇 분 뒤 OK 메일 (Lambda 는 OK 를 무시)
# ⚠ 콘솔에서 Lambda 가 띄운 chargeops-dr-db (태그 LaunchedBy=auto-dr-lambda) 종료 — Terraform 밖에서 생긴 서버
tf apply -var make_ami=true                                # ⚠ 자동 DR · 엔드포인트 9개 지움 (켜 둔 채면 mgmt 가 잠깐만 끊겨도 서버가 뜬다)
```

## 3. 설정 파일

| 파일 | 무엇 | git |
|---|---|---|
| `infra/terraform/dr/*.tf` · `scripts/*.tftpl` · `lambda/auto_dr.py` | DR VPC · 골든 이미지 · 서버 설계도 · 훈련 · 자동 DR · RDS 비교 | ✅ |
| `infra/terraform/alerts/*.tf` · `aws-heartbeat.py` | 경보 · 예산 · (선택) GuardDuty · 생존 신호 보내기 | ✅ |
| `infra/terraform/terraform-user-policy.example.json` | Terraform 사용자 권한 (`chargeops-*` 이름만, 루트 키 없이) | ✅ |
| `infra/terraform/*/terraform.tfvars` | `backup_bucket` · `repo_path` · `alert_email` | ✗ (.gitignore) |
| `infra/terraform/dr/backend.tf` | 장부(상태 파일)는 S3 전용 버킷 `dr/terraform.tfstate` (버전 보관 · 잠금). 버킷 이름은 넣지 않음 | ✅ |
| `infra/terraform/alerts/terraform.tfstate` · `*/.terraform/` · `.build/` | 상태 · 생성물 (버킷 이름 · 리소스 ID 가 들어 있음) | ✗ — **mgmt 에만** |
| mgmt `~/.chargeops.env` | `AWS_TF_KEY_ID` · `AWS_TF_SECRET` · `AWS_HB_KEY_ID` · `AWS_HB_SECRET` = `<각자 설정>` | ✗ |
| 멘토 서버 mgmt `~/.chargeops-dr.env` · `~/.chargeops-dr.tfbackend` | DR 전용 Terraform 키 · 장부 위치 (공용 `.chargeops.env` 와 분리) — [`aws-dr-mentor.md`](aws-dr-mentor.md) | ✗ |
| mgmt crontab | 생존 신호 1분마다 | ✗ |

단계 스위치 (`-var`): `build_ami`(임시 서버) → `make_ami`(이미지 · 설계도, 한 번 켜면 계속 true) → `drill`(수동 훈련) / `auto_dr`(자동 DR) → `enable_rds`(RDS 비교, drill 과 함께)

## 4. 확인 방법

```bash
# ===== 실행할 명령어 =====
# DR-DB 의 Session Manager 창에서
sudo tail -n 25 /var/log/dr-restore.log
# mgmt 에서 — 훈련본이 저장소에 아무것도 안 남겼는지 (WAL 타임라인 앞자리가 운영 그대로)
ssh db-01 'sudo -u postgres pgbackrest --stanza=chargeops --repo=2 info' | grep -E 'status|wal archive min/max'
```
```
# ===== 실행 결과 (2026-10-15 자동 DR, 개인 계정) =====
④ 복원 (내려받기)   repo2: 백업 세트 20261014-110915F_20261015-034025D 를 복원 … 복원 크기 = 513.3MB, 파일 총 개수 = 1522 … 24초
⑤ shared_buffers = 428MB  # DR 서버 메모리 1713MB 에 맞춤 · 로그 폴더 /var/log/postgresql 만듦
⑥ 시작 → 마지막 WAL 까지 재생 → 승격   131초
⑦ 복구 중 f | 타임라인 10 | WAL 보관 꺼짐
   충전기 527266 | 상태 이력 755686 | 수집기 마지막 회차 10-15 16:00:01
   확장 pg_cron 1.6, pg_stat_statements 1.11, plpgsql 1.0, vector 0.8.7 | 미리 올린 라이브러리 pg_stat_statements,pg_cron,pgaudit
== 걸린 시간: 패키지 1초 · 저장소 확인 1초 · 내려받기 24초 · 재생 · 승격 131초 = 합계 161초 · 종료 코드 0
```

경보 쪽 확인: CloudWatch → 경보 → `chargeops-mgmt-heartbeat` 의 상태 막대 (데이터 부족 → 처음 경보 → 정상 → 일부러 끊은 경보 → 정상), Lambda 가 한 일은 로그 그룹 `/aws/lambda/chargeops-auto-dr` 의 `자동 DR 발동: i-…`

## 5. 막혔던 점

| 증상 | 원인 · 해결 |
|---|---|
| 생존 신호 스크립트가 `CERTIFICATE_VERIFY_FAILED` | `~/.chargeops.env` 의 MinIO 용 `AWS_CA_BUNDLE`(사설 CA)이 AWS 호출에 섞임 → 스크립트가 시작할 때 MinIO 설정을 지우게 고침. Terraform 은 `tf()` 함수가 같은 일을 함 |
| 경보를 처음 만들자마자 ALARM 메일 | 아직 신호가 없어서(누락 = 위반). 정상. **그래서 자동 DR 은 경보가 OK 가 된 뒤에 켠다** |
| 경보가 5분이 아니라 4분 15초에 울림 | 1분 단위 구간으로 세기 때문. 정상 |
| 엔드포인트 지우는 데 4분 | 인터페이스 엔드포인트 삭제가 원래 느림. 기다리면 됨 |
| (주의) Lambda 가 띄운 서버는 `terraform destroy` 로 안 지워짐 | Terraform 상태 밖에서 생긴 서버 → 콘솔에서 종료 |
| (주의) PC · VM 을 끄면 ALARM 메일 | 의도대로. 번거로우면 경보 → 작업 → **경보 작업 비활성화** (구독 취소 대신), 시험 전 다시 활성화 |

## 6. 비용 (서울, 근사치 — 청구서로 확인)

| 무엇 | 언제 | 대략 |
|---|---|---|
| VPC · 서브넷 · 보안 그룹 · S3 게이트웨이 엔드포인트 · IAM · 서버 설계도 | 상시 | 0 |
| 골든 이미지 스냅샷 (약 3GB) | 상시 | 월 0.2달러 안팎 |
| 경보 1 · 지표 1 · SNS 메일 · 예산 | 상시 | 월 0.4달러 이하 |
| 훈련 (DR-DB t3.small + SSM 엔드포인트 3) | 훈련 때만 | 시간당 약 0.07달러 — **끝나면 꼭 지움** (엔드포인트만 하루 약 1달러) |
| Lambda · SNS → Lambda | 발동 때 | 무료 범위 |

## 7. 다음

- ✅ RDS 비교 (`enable_rds=true`) — 2026-10-28 멘토 서버 실행: RDS 생성 6분 17초, DR 은 EC2 유지 → [`aws-rds-compare.md`](aws-rds-compare.md)
- ✅ 보안 · 감사: CloudTrail 기록(S3 + CloudWatch Logs), Config(규칙 4개) → EventBridge → SNS, 보안 경보 4종. GuardDuty 는 보류 → [`aws-security-alerts.md`](aws-security-alerts.md)
- ✅ Jenkins → ECR 이미지 사본, Grafana 에 CloudWatch 데이터 소스, SNS → Slack(#chargeops-alerts) → [`aws-ecr-mirror.md`](aws-ecr-mirror.md) · [`aws-grafana-ec2.md`](aws-grafana-ec2.md)
- **자동 DR 을 실제 운영에서 켤지** — 사이트 인터넷만 끊겨도 발동하면 Primary 가 둘이 될 수 있음. 추천: 자동으로 서버까지 띄우고 승격(`DR_MODE=real`)은 사람이 확인 뒤
- ✅ 팀 계정 전환: 같은 코드, `terraform.tfvars` 만 (버킷 · `/chargeops-mentor` · 메일). 개인 계정 자원은 프로젝트 끝에 `terraform destroy` (AWS 도입 보고서 8장 ④)
