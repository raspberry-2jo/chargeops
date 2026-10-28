# AWS RDS 비교 훈련 기록 (EC2 물리 복원 vs RDS 덤프 복원)

- 담당: 황채연 (AWS)
- 관련 WBS: 4.5 DR 훈련
- 버전: versions.md 참고 (Terraform · AWS provider · PostgreSQL 17)
- 상태: **2026-10-28 멘토 서버 mgmt 에서 실데이터로 비교 → DR 은 EC2 + pgBackRest 유지** (10/27 담당자 PC 실행 결과와 같은 경향)
- 관련: [`aws-dr-vpc.md`](aws-dr-vpc.md) (DR VPC · `enable_rds` 코드), [`aws-dr-mentor.md`](aws-dr-mentor.md) (멘토 서버 실행 · DR 훈련), [`aws-s3-backup.md`](aws-s3-backup.md) (repo2 = S3 백업)

## 0. 결과 요약 (2026-10-28 멘토 서버 실행)

| 항목 | EC2 + pgBackRest (현재 DR) | RDS (덤프 → 넣기) |
|---|---|---|
| 서버 준비 | **13초** (골든 이미지) | **6분 17초** (사용 가능까지 412초) |
| 데이터 넣기 | S3 물리 복원 140초 (내려받기 42초 + WAL 재생·승격 95초) | 덤프 14.9초 (61MB) + 넣기 77.3초 + ANALYZE 5.6초 |
| 서비스 가능까지 (같은 회차) | 13초 + 140초 = **약 2분 33초** | 412초 + 98초 = **약 8분 30초** |
| 데이터 일치 (10/28 12시대) | 기준 | charger 535,290 · station 101,000 · status_history 1,315,990 **모두 같음** · 오류 0 |
| 같은 쿼리 (가동률 집계 뷰 갱신, 1회 → 2회) | 2.1초 → 2.0초 (t3.small, 2GB) | 4.2초 → 3.6초 (db.t4g.micro, 1GB) |
| RPO | 수 초 ~ 1분 (WAL 연속 보관) | 덤프 주기 (하루 1번이면 최대 24시간) |
| 우리 백업과 호환 | pgBackRest 백업 · WAL 그대로 | 외부 물리 백업을 못 받음 → 덤프만 가능 |
| 계정 · 권한 · 예약 작업 | 그대로 | 소유자 · 권한을 빼고 넣음 → 다시 만들어야 함, pg_cron 예약 작업 6개 해제됨 |

**결론** — DR 은 EC2 + pgBackRest 를 유지한다.

### RDS 를 DR 에 쓰지 않는 이유

1. **복구가 느리다.** RDS 는 서버를 만드는 데만 6분 17초가 고정으로 든다(10/21 · 10/27 · 10/28 세 번 모두 6분대). 데이터를 넣는 시간까지 더하면 약 8분 30초로, EC2(약 2분 33초)의 3배가 넘는다
2. **잃는 데이터가 늘어난다.** RDS 는 pgBackRest 물리 백업을 받지 못해 덤프로만 옮길 수 있다. 덤프를 하루 1번 뜨면 최대 24시간치를 잃는다. EC2 는 WAL 을 계속 보관해서 수 초 ~ 1분이다
3. **그대로 쓸 수 없다.** RDS 는 관리자도 슈퍼유저가 아니라 원본의 소유자 · 권한을 그대로 만들 수 없다. 이번에도 소유자 · 권한을 빼고 넣었고 pg_cron 예약 작업 6개가 해제됐다
4. **RDS 의 장점을 살리기 어렵다.** 자동 백업 · 패치 · Multi-AZ 는 AWS 에서 DB 를 상시 운영할 때 의미가 있다. 장애 때만 켜는 DR 에서는 살릴 것이 적다

- 쿼리 속도(EC2 2.0초, RDS 3.6초)는 서버 크기 차이(메모리 2GB vs 1GB)가 섞여 있어 이유에서 뺐다
- AWS 에서 DB 를 상시 운영한다면 RDS 가 더 맞는 선택이다

### 회차별 비교

| 항목 | 10/15 사전 실측 (참고) | 10/21 (실습 DB) | 10/27 (멘토 서버 실데이터) | **10/28 (멘토 서버 실행)** |
|---|---|---|---|---|
| 실행 위치 | 개인 PC | 담당자 PC mgmt | 담당자 PC mgmt | **멘토 서버 mgmt** |
| 데이터 | 미확인 | S3 `/chargeops` | S3 `/chargeops-mentor` | S3 `/chargeops-mentor` |
| DR-DB(EC2) 생성 | 12초 | 13초 | 12초 | 13초 |
| RDS 생성 (Terraform) | 6분 17초 | 6분 27초 | 6분 17초 | 6분 17초 |
| RDS 사용 가능까지 (aws.sh) | 미확인 | 470초 | 439초 | 412초 |
| DR-DB S3 복원 | 미확인 | 미기록 | 320초 (오래된 백업) | 140초 |
| 덤프 · 넣기 · ANALYZE | 미확인 | 미기록 | 12.3초 · 81.8초 · 4.0초 | 14.9초 · 77.3초 · 5.6초 |
| 쿼리 (2회차) | ANALYZE 없으면 RDS 3배 느림 | 미기록 | EC2 2.1초 vs RDS 3.5초 | EC2 2.0초 vs RDS 3.6초 |

- RDS 생성 시간은 데이터 양 · 실행 위치와 상관없이 네 번 모두 6분대 → RDS 경로의 고정 비용
- 10/27 DR-DB 복원(320초)이 느렸던 건 10/27 03:40 · 11:18 백업이 실패해 약 28시간치 WAL 을 재생했기 때문 (5장). 백업이 정상화된 10/28 에는 140초
- 10/15 값은 aws.sh 안내 문구에 적힌 참고 수치. 10/21 은 비교 스크립트 결과 파일을 남기지 않아 덤프 · 쿼리 수치가 없음

## 1. 왜 / 무엇을

- "DR 을 왜 RDS 가 아니라 EC2 로 했나"를 실데이터 숫자로 확인한다 (설계 근거: `aws-dr-vpc.md` 1장)
- 같은 DR VPC 에 DR-DB(EC2) 와 RDS 를 함께 띄운다. DR-DB 가 S3 백업으로 복원되면, DR-DB 안에서 `pg_dump` → RDS 로 `pg_restore` 하고 시간 · 행 수 · 쿼리 속도를 비교한다
- 멘토 서버(운영 DB)에는 접속하지 않는다. S3 백업만 읽는다

```text
S3 백업 ─▶ ① DR-DB(EC2) 물리 복원 ─▶ ② DR-DB 에서 pg_dump ─▶ ③ RDS 로 pg_restore · ANALYZE ─▶ ④ 행 수 · 쿼리 비교
```

| 구성 | 값 |
|---|---|
| DR-DB (EC2) | t3.small, 골든 이미지, 첫 부팅에 S3 자동 복원 (훈련 모드) |
| RDS | `chargeops-dr-compare` · PostgreSQL 17 · db.t4g.micro (ARM) · gp3 20GB · 단일 AZ · 외부 공개 꺼짐 · 자동 백업 0 (비교용) |
| 네트워크 | DR VPC 프라이빗 서브넷. RDS 는 DB 서브넷 그룹(A + C, AZ 2개 필수) |
| RDS 보안 그룹 | 5432 를 DR-DB 보안 그룹에서만 허용 |

## 2. 설치 명령어

멘토 서버 mgmt 에서 실행한다 (`~/aws-dr.sh` = DR 전용 키를 읽고 `aws.sh` 를 실행하는 파일 → [`aws-dr-mentor.md`](aws-dr-mentor.md) 2-3). `aws.sh` · `rds-compare.sh` 는 실습 묶음(mgmt 의 `~/chargeops/deploy/aws/`)에 있고, Terraform 코드는 팀 저장소 `infra/terraform/dr` 와 같다.

**▶ 멘토 서버 mgmt — 시작 전 확인 (읽기만)**

```bash
# ===== 실행할 명령어 =====
~/aws-dr.sh status
grep -n "repo_path" ~/chargeops/infra/terraform/dr/terraform.tfvars
```

- `drill=false` · `enable_rds=false` · No changes, `repo_path` 가 `/chargeops-mentor` 면 진행. `/chargeops` 면 실습 DB 로 비교하게 됨

**▶ 멘토 서버 mgmt — RDS 와 DR-DB 만들기 (여기부터 요금)**

```bash
# ===== 실행할 명령어 =====
date; ~/aws-dr.sh rds start
```

- RDS 관리자 비밀번호 = `<각자 설정>` (12자 이상, 화면에 안 보임. 다음 단계에서 한 번 더 입력)
- 계획이 **8 to add · 0 to destroy** 인지 보고 `yes`. RDS 생성 6 ~ 7분 동안 **Ctrl+C 금지** (상태가 꼬이면 `off` 로도 안 지워짐)
- 끝에 나오는 RDS 주소는 화면에서만 본다 (문서 · Git · 캡처에 넣지 않음)

**▶ AWS 콘솔 → EC2 → `chargeops-dr-db` → 연결 → Session Manager**

```bash
# ===== 실행할 명령어 =====
sudo /usr/local/sbin/rds-compare.sh
sudo cat /tmp/rds-compare.txt
```

- DR-DB 의 S3 복원이 끝날 때까지 기다린 뒤 RDS 주소 · 비밀번호(`<각자 설정>`)를 묻는다

**▶ 멘토 서버 mgmt — 끝 (꼭)**

```bash
# ===== 실행할 명령어 =====
date; ~/aws-dr.sh off
~/aws-dr.sh status
```

- 계획이 **0 to add · 8 to destroy** 인지 보고 `yes`. 5분쯤 걸림. 남는 것: DR 망 · 골든 이미지 · 서버 설계도 · 경보 (월 1달러 미만)

## 3. 설정 파일

| 파일 | 무엇 | git |
|---|---|---|
| `infra/terraform/dr/main.tf` ④ | RDS · DB 서브넷 그룹 · 파라미터 그룹 · RDS 보안 그룹 (`drill && enable_rds` 일 때만) | ✅ |
| `infra/terraform/dr/terraform.tfvars` | `repo_path = "/chargeops-mentor"` (멘토 서버 백업) | ✗ (.gitignore) |
| 환경 변수 `TF_VAR_rds_password` | RDS 관리자 비밀번호 = `<각자 설정>`, `read -rsp` 로만 입력 | ✗ |

파라미터 그룹 `chargeops-dr-pg17` — `shared_preload_libraries = pg_stat_statements,pg_cron,pgaudit`, `cron.database_name = chargeops` (덤프에 있는 확장을 RDS 가 받을 수 있게)

## 4. 확인 방법

**Terraform (멘토 서버 mgmt)** — `Apply complete! Resources: 8 added` 와 `aws_db_instance.compare[0]: Creation complete after …` 줄의 시간이 RDS 생성 시간

**비교 결과 (DR-DB)**

```bash
# ===== 실행할 명령어 =====
sudo cat /tmp/rds-compare.txt
```
```
# ===== 실행 결과 요약 (2026-10-28 멘토 서버 실행, ID · 주소는 뺌) =====
Terraform    12:27:46 시작 · Plan 8 to add · 0 to destroy / DR-DB 13s / SSM 엔드포인트 32~52s / RDS 6m17s / 적용 끝 386초 / RDS 생성까지 412초
DR-DB 복원   S3 140초 (패키지 1초 · 저장소 확인 0초 · 내려받기 42초 · 재생 · 승격 95초)
덤프         pg_dump 14.9초 · 61MB
넣기         pg_restore 77.3초 · 오류 0 (소유자 · 권한 제외)
통계         ANALYZE 5.6초
행 수        charger 535290 / station 101000 / status_history 1315990 → EC2 · RDS 모두 같음
쿼리         가동률 구체화 뷰 갱신  EC2 2.1 → 2.0초 / RDS 4.2 → 3.6초
기타         pg_cron 예약 작업 6개 해제
정리         12:47:54 off → 8 destroyed · 적용 끝 309초 → status No changes
```

## 5. 막혔던 점

| 증상 | 원인 · 해결 |
|---|---|
| 10/27 EC2 복원이 10/26(2분 40초)의 약 2배(320초) | 10/27 03:40 · 11:18 백업이 실패해 마지막 성공 백업(10/26) 이후 약 28시간치 WAL 을 재생. 원인(데이터 폴더 안 root 소유 사본)은 DB 팀이 정리. 10/28 재측정으로 확인 — 같은 단계(WAL 재생 · 승격)가 275초 → 68~95초. 이후 백업 감시 추가 → [`aws-backup-watch.md`](aws-backup-watch.md) |
| 백업 뒤 시간이 길수록 복원이 느려짐 | 10/28 WAL 재생 · 승격 68초(백업 후 약 6시간) → 78초(약 7시간) → 95초(약 9시간). 매일 백업이 필요한 근거 |
| RDS 에 넣을 때 소유자 · 권한이 빠짐 | RDS 는 관리자도 슈퍼유저가 아니라 원본 역할을 그대로 못 만듦 → `pg_restore` 를 소유자 · 권한 없이 실행. RDS 로 운영하려면 역할 · 권한 · pg_cron 작업을 다시 만들어야 함 |
| RDS 비밀번호가 12자 미만이면 시작 전에 멈춤 | `aws.sh` 가 길이를 검사함 → 다시 실행해서 12자 이상 입력 |
| `off` 가 멈춘 것처럼 보임 | SSM 엔드포인트 삭제에 3~5분. 진행 문구가 안 나옴. Ctrl+C 금지 |
| 화면 출력에 RDS 주소가 나옴 | 외부 공개는 꺼져 있어 접속은 안 되지만, 문서 · Git · 캡처에는 넣지 않음 |
| (10/27) 담당자 PC mgmt VM 이 "완전히 꼬임"(Guru Meditation)으로 안 켜짐 | VirtualBox 가 꼬인 상태 → Windows 재부팅 후 `vagrant up mgmt`. 이후 실행 위치를 멘토 서버로 옮김 → [`aws-dr-mentor.md`](aws-dr-mentor.md) |

## 6. 비용 (서울, 근사치 — 청구서로 확인)

| 무엇 | 언제 | 대략 |
|---|---|---|
| DR-DB t3.small + RDS db.t4g.micro + SSM 엔드포인트 3 | 훈련 때만 | 시간당 약 0.1달러 — **끝나면 꼭 `off`** |
| RDS 스토리지 gp3 20GB | 훈련 때만 (삭제와 함께 사라짐) | 몇 시간이면 1센트 미만 |

## 7. 다음

- (선택) 같은 크기(메모리 2GB)로 RDS 를 띄워 쿼리 차이를 다시 비교 — 이번 차이에는 서버 크기 차이가 섞여 있음
- RDS 로 옮길 일이 생기면: 정기 덤프를 S3 에 쌓는 작업, 역할 · 권한 스크립트, pg_cron 작업 다시 등록이 먼저 필요
