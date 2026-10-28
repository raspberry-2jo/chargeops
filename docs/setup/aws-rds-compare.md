# AWS RDS 비교 훈련 기록 (EC2 물리 복원 vs RDS 덤프 복원)

- 담당: 황채연 (AWS)
- 관련 WBS: 4.5 DR 훈련
- 버전: versions.md 참고 (Terraform · AWS provider · PostgreSQL 17)
- 상태: **2026-10-27 멘토 서버 실데이터로 비교 완료 → DR 은 EC2 + pgBackRest 유지** · 2026-10-28 백업 정상화 후 EC2 DR 재측정 (복원 143초)
- 관련: [`aws-dr-vpc.md`](aws-dr-vpc.md) (DR VPC · `enable_rds` 코드), [`aws-s3-backup.md`](aws-s3-backup.md) (repo2 = S3 백업)

## 0. 결과 요약

| 항목 | EC2 + pgBackRest (현재 DR) | RDS (덤프 → 넣기) |
|---|---|---|
| 서버 준비 | **12초** (골든 이미지) | **6분 17초** (사용 가능까지 439초) |
| 데이터 넣기 | S3 물리 복원 320초 (10/27, 오래된 백업) → **143초** (10/28, 백업 정상화 후) | 덤프 12.3초 (54MB) + 넣기 81.8초 + ANALYZE 4.0초 |
| 데이터 일치 | 기준 | charger 535,286 · station 100,998 · status_history 1,225,081 **모두 같음** · 오류 0 |
| 같은 쿼리 (가동률 집계 뷰 갱신, 1회 → 2회) | 2.1초 → 2.1초 (t3.small, 2GB) | 4.8초 → 3.5초 (db.t4g.micro, 1GB) |
| RPO | 수 초 ~ 1분 (WAL 연속 보관, 실측 10/26 약 12초 · 10/28 약 17초) | 덤프 주기 (하루 1번이면 최대 24시간) |
| 우리 백업과 호환 | pgBackRest 백업 · WAL 그대로 | 외부 물리 백업을 못 받음 → 덤프만 가능 |
| 계정 · 권한 · 예약 작업 | 그대로 | 소유자 · 권한을 빼고 넣음 → 다시 만들어야 함, pg_cron 예약 작업 6개 해제됨 |

**결론** — DR 은 EC2 + pgBackRest 를 유지한다. EC2 는 서버 준비부터 복원 끝까지 약 2분 35초(10/28: 12초 + 143초)인데, RDS 는 서버 생성만 6분 17초 걸리고, RPO 가 덤프 주기에 묶인다. RDS 의 장점(자동 백업 · 패치 · Multi-AZ)은 AWS 에서 상시 운영할 때 의미가 있고, 장애 때만 켜는 DR 에서는 살릴 것이 적다.

### 이전 측정과 비교

| 항목 | 10/15 사전 실측 (참고) | 10/21 (실습 DB) | 10/27 (멘토 서버 실데이터) |
|---|---|---|---|
| 데이터 | 미확인 | S3 `/chargeops` | S3 `/chargeops-mentor` |
| DR-DB(EC2) 생성 | 12초 | 13초 | 12초 |
| RDS 생성 (Terraform) | 6분 17초 | 6분 27초 | 6분 17초 |
| RDS 사용 가능까지 (aws.sh) | 미확인 | 470초 | 439초 |
| 덤프 · 넣기 · ANALYZE | 미확인 | 미기록 | 12.3초 · 81.8초 · 4.0초 |
| 쿼리 | ANALYZE 없으면 RDS 3배 느림 | 미기록 | EC2 2.1초 vs RDS 3.5초 |

- RDS 생성 시간은 데이터 양과 상관없이 세 번 모두 6분대 → RDS 경로의 고정 비용
- 10/15 값은 aws.sh 안내 문구에 적힌 참고 수치. 10/21 은 비교 스크립트 결과 파일을 남기지 않아 덤프 · 쿼리 수치가 없음

### EC2 DR 재측정 (10/28, 백업 정상화 후)

| 항목 | 10/27 | 10/28 |
|---|---|---|
| 복원에 쓴 백업 | 10/26 백업 (약 28시간 전) | 10/28 03:41 증분 백업 (약 6시간 전) |
| 내려받기 | 42초 | 43초 |
| WAL 재생 · 승격 | 275초 | **68초** |
| 합계 (`dr-restore.log` 의 "걸린 시간") | 320초 | **143초** |
| RPO (되살린 마지막 트랜잭션 ~ 확인 시각) | 미기록 | 약 17초 |

- 내려받기는 같고 WAL 재생만 줄었다 → 10/27 이 느렸던 원인은 "오래된 백업 → 재생할 WAL 이 많음"으로 확인
- 실제 RTO = 서버 준비(EC2 생성 12초) + 복원 143초 + 앱 접속 전환 → 앱 전환 전까지 약 2분 35초
- 10/28 합계 143초 = 패키지 3초 + 저장소 확인 1초 + 내려받기 43초 + 재생 · 승격 68초 + 목록에 없는 단계 약 28초 (내역 미확인)
- 10/26(160초)은 단계별 시간을 남기지 않아 이 표에서 뺐다

## 1. 왜 / 무엇을

- "DR 을 왜 RDS 가 아니라 EC2 로 했나"를 실데이터 숫자로 확인한다 (설계 근거: `aws-dr-vpc.md` 1장)
- 같은 DR VPC 에 DR-DB(EC2) 와 RDS 를 함께 띄운다. DR-DB 가 S3 백업으로 복원되면, DR-DB 안에서 `pg_dump` → RDS 로 `pg_restore` 하고 시간 · 행 수 · 쿼리 속도를 비교한다
- 멘토 서버(운영 DB)에는 접속하지 않는다. S3 백업만 읽는다

| 구성 | 값 |
|---|---|
| DR-DB (EC2) | t3.small, 골든 이미지, 첫 부팅에 S3 자동 복원 (훈련 모드) |
| RDS | `chargeops-dr-compare` · PostgreSQL 17 · db.t4g.micro (ARM) · gp3 20GB · 단일 AZ · 외부 공개 꺼짐 · 자동 백업 0 (비교용) |
| 네트워크 | DR VPC 프라이빗 서브넷. RDS 는 DB 서브넷 그룹(A + C, AZ 2개 필수) |
| RDS 보안 그룹 | 5432 를 DR-DB 보안 그룹에서만 허용 |

## 2. 설치 명령어

`aws.sh` · `rds-compare.sh` 는 실습 묶음(내 PC mgmt 의 `~/chargeops/deploy/aws/`)에 있고 팀 저장소에는 아직 없다. Terraform 코드는 팀 저장소 `infra/terraform/dr` 와 같다.

**▶ 내 PC mgmt — 시작 전 확인 (읽기만)**

```bash
# ===== 실행할 명령어 =====
bash ~/chargeops/deploy/aws/aws.sh status
grep -n "repo_path" ~/chargeops/infra/terraform/dr/terraform.tfvars
```

- `drill=false` · `enable_rds=false` (켜진 것 없음), `repo_path` 가 `/chargeops-mentor` 면 진행. `/chargeops` 면 실습 DB 로 비교하게 됨

**▶ 내 PC mgmt — RDS 와 DR-DB 만들기 (여기부터 요금)**

```bash
# ===== 실행할 명령어 =====
bash ~/chargeops/deploy/aws/aws.sh rds start
```

- RDS 관리자 비밀번호 = `<각자 설정>` (12자 이상, 화면에 안 보임. 다음 단계에서 한 번 더 입력)
- 계획이 **8 to add · 0 to destroy** 인지 보고 `yes`. RDS 생성 5 ~ 10분 동안 **Ctrl+C 금지** (상태가 꼬이면 `aws.sh off` 로도 안 지워짐)
- 끝에 나오는 RDS 주소는 화면에서만 본다 (문서 · Git · 캡처에 넣지 않음)

**▶ AWS 콘솔 → EC2 → `chargeops-dr-db` → 연결 → Session Manager**

```bash
# ===== 실행할 명령어 =====
sudo /usr/local/sbin/rds-compare.sh
sudo cat /tmp/rds-compare.txt
```

- DR-DB 의 S3 복원이 끝날 때까지 기다린 뒤 RDS 주소 · 비밀번호(`<각자 설정>`)를 묻는다

**▶ 내 PC mgmt — 끝 (꼭)**

```bash
# ===== 실행할 명령어 =====
bash ~/chargeops/deploy/aws/aws.sh off
bash ~/chargeops/deploy/aws/aws.sh status
```

- 계획이 **8 to destroy** 인지 보고 `yes`. 남는 것: DR 망 · 골든 이미지 · 서버 설계도 · 경보 (월 1달러 미만)

## 3. 설정 파일

| 파일 | 무엇 | git |
|---|---|---|
| `infra/terraform/dr/main.tf` ④ | RDS · DB 서브넷 그룹 · 파라미터 그룹 · RDS 보안 그룹 (`drill && enable_rds` 일 때만) | ✅ |
| `infra/terraform/dr/terraform.tfvars` | `repo_path = "/chargeops-mentor"` (멘토 서버 백업) | ✗ (.gitignore) |
| 환경 변수 `TF_VAR_rds_password` | RDS 관리자 비밀번호 = `<각자 설정>`, `read -rsp` 로만 입력 | ✗ |

파라미터 그룹 `chargeops-dr-pg17` — `shared_preload_libraries = pg_stat_statements,pg_cron,pgaudit`, `cron.database_name = chargeops` (덤프에 있는 확장을 RDS 가 받을 수 있게)

## 4. 확인 방법

**Terraform (내 PC mgmt)** — `Apply complete! Resources: 8 added` 와 `aws_db_instance.compare[0]: Creation complete after …` 줄의 시간이 RDS 생성 시간

**비교 결과 (DR-DB)**

```bash
# ===== 실행할 명령어 =====
sudo cat /tmp/rds-compare.txt
```
```
# ===== 실행 결과 요약 (2026-10-27, 주소는 뺌) =====
Terraform    Plan 8 to add · 0 to destroy / DR-DB 12s / RDS 6m17s / 적용 끝 381초 / RDS 생성까지 439초
DR-DB 복원   S3 320초 (내려받기 42초 + WAL 재생 · 승격 275초)
덤프         pg_dump 12.3초 · 54MB
넣기         pg_restore 81.8초 · 오류 0 (소유자 · 권한 제외)
통계         ANALYZE 4.0초
행 수        charger 535286 / station 100998 / status_history 1225081 → EC2 · RDS 모두 같음
쿼리         가동률 집계 뷰 갱신  EC2 2.1 → 2.1초 / RDS 4.8 → 3.5초
기타         pg_cron 예약 작업 6개 해제
정리         aws.sh off → 8 destroyed · 적용 끝 364초
```

**EC2 DR 재측정 (10/28)** — 내 PC mgmt 에서 `aws.sh drill start` → DR-DB 에서 아래 명령 → 내 PC mgmt 에서 `aws.sh off`

```bash
# ===== 실행할 명령어 =====
sudo tail -n 25 /var/log/dr-restore.log
```
```
# ===== 실행 결과 요약 (2026-10-28, 버킷 · 주소는 뺌) =====
복원 백업    10/28 03:41 증분(diff) 백업 = 10/26 풀백업 + 증분
걸린 시간    패키지 3초 · 저장소 확인 1초 · 내려받기 43초 · 재생 · 승격 68초 = 합계 143초 · 종료 코드 0
확인         복구 중 f · 타임라인 4 · 되살린 마지막 트랜잭션 09:31:29 / 확인 시각 09:31:46
정리         aws.sh off → DR-DB + SSM 엔드포인트 3개 삭제 (엔드포인트 삭제에 약 3분)
```

## 5. 막혔던 점

| 증상 | 원인 · 해결 |
|---|---|
| 내 PC mgmt VM 이 "완전히 꼬임"(Guru Meditation)으로 안 켜짐 | VirtualBox 가 꼬인 상태 → Windows 재부팅 후 `vagrant up mgmt`. Terraform 상태 파일은 VM 디스크에 그대로 있었음 |
| 10/27 EC2 복원이 10/26(2분 40초)의 약 2배(320초) | 10/27 03:40 · 11:18 백업이 실패해 마지막 성공 백업(10/26) 이후 약 28시간치 WAL 을 재생. 원인(데이터 폴더 안 root 소유 사본)은 DB 팀이 정리 → **10/28 재측정으로 확인**: 같은 단계(WAL 재생 · 승격)가 275초 → 68초, 합계 143초 |
| `aws.sh off` 가 멈춘 것처럼 보임 | SSM 엔드포인트 삭제에 약 3분 걸리는데 진행 문구가 안 나옴. 중간에 Ctrl+C 를 눌러 첫 실행이 끊김(DR-DB 는 이미 삭제) → 다시 `aws.sh off` 로 남은 엔드포인트 3개 삭제. Terraform 실행 중에는 Ctrl+C 금지 |
| RDS 에 넣을 때 소유자 · 권한이 빠짐 | RDS 는 관리자도 슈퍼유저가 아니라 원본 역할을 그대로 못 만듦 → `pg_restore` 를 소유자 · 권한 없이 실행. RDS 로 운영하려면 역할 · 권한 · pg_cron 작업을 다시 만들어야 함 |
| 화면 출력에 RDS 주소가 나옴 | 외부 공개는 꺼져 있어 접속은 안 되지만, 문서 · Git · 캡처에는 넣지 않음 |

## 6. 비용 (서울, 근사치 — 청구서로 확인)

| 무엇 | 언제 | 대략 |
|---|---|---|
| DR-DB t3.small + RDS db.t4g.micro + SSM 엔드포인트 3 | 훈련 때만 | 시간당 약 0.1달러 — **끝나면 꼭 `aws.sh off`** |
| RDS 스토리지 gp3 20GB | 훈련 때만 (삭제와 함께 사라짐) | 몇 시간이면 1센트 미만 |

## 7. 다음

- ~~백업이 정상화되면 EC2 DR 을 다시 재서 2분대로 돌아오는지 확인~~ → 10/28 완료 (143초)
- 복원 합계 중 단계 목록에 없는 약 28초의 내역 확인 (다음 훈련 때 `sudo head -n 40 /var/log/dr-restore.log`)
- (선택) 같은 크기(메모리 2GB)로 RDS 를 띄워 쿼리 차이를 다시 비교 — 이번 1.7배 차이에는 서버 크기 차이가 섞여 있음
- RDS 로 옮길 일이 생기면: 정기 덤프를 S3 에 쌓는 작업, 역할 · 권한 스크립트, pg_cron 작업 다시 등록이 먼저 필요
