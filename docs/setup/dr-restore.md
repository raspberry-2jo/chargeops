# DR 복원 런북 — 빈 새 서버에서 AWS 백업만으로 DB 되살리기

- 담당: 강영수
- 관련 WBS: 4.5 DR 훈련 (DB 부분) · 2.8 후속
- 버전: versions.md 참고 (PostgreSQL 17 · pgBackRest 2.59 · pg_cron · pgaudit · pgvector)
- 상태: **실습 훈련 완료 (2026-10-14)** — 빈 VM 에서 AWS 저장소만으로 63초 (+ 패키지 설치 54초). EC2 에서 하는 4.5 훈련은 Terraform 담당과 (6장)
- 2026-10-23: **왕복 훈련** — 클라우드 역할 서버에서 복원(75초) → 그 서버가 다시 백업 → 온프렘 Patroni 로 복귀, 잃은 데이터 0건. 이때 찾은 두 가지(SSL 인증서 · 체크포인트 대기)를 스크립트에 반영 (5장)
- 앞 단계: [`aws-s3-backup.md`](aws-s3-backup.md) (repo2 = AWS S3 · 정기 백업), [`pgbackrest-pitr.md`](pgbackrest-pitr.md) (PITR 훈련)
- 다음 단계: [`dr-failback.md`](dr-failback.md) (클라우드 → 온프렘 복귀)

## 1. 왜 / 무엇을

- 실습 PC 가 통째로 사라지면 DB 두 대와 MinIO(repo1)가 함께 없어진다. 남는 것은 **AWS S3 의 repo2** (매일 03:40 백업 + 최대 60초마다 WAL)
- [`db/ops/pgbackrest/dr-restore.sh`](../../db/ops/pgbackrest/dr-restore.sh) 하나로 빈 Rocky Linux 9 서버에서: ① 패키지 ② pgBackRest 설정 ③ 저장소 확인 ④ 내려받기 ⑤ 이 서버에 맞게 정리 ⑥ 마지막 WAL 까지 재생 · 승격 ⑦ 확인 + 단계별 걸린 시간(RTO)

```text
AWS S3 <백업 버킷>/test-chargeops ── 전체 · 차등 백업 + WAL ──▶ 빈 새 서버 (Rocky 9 — 실습 VM 또는 EC2)
                                                                sudo bash dr-restore.sh  →  새 Primary (훈련이면 훈련본)
```

| 모드 | 언제 | 복원본이 저장소에 WAL 을 | 재부팅 때 자동 시작 |
|---|---|---|---|
| `DR_MODE=drill` (기본) | 훈련 — 운영 DB 가 살아 있음 (`yes` 입력) | **올리지 않음** (`archive-mode=off`) | 안 함 |
| `DR_MODE=real` | 실제 재해 — 운영 Primary 가 멈춤 (`DR` 입력) | 올림 (새 타임라인으로 이어서) + `check` | 함 |

- 훈련본이 WAL 을 올리면 안 되는 이유: 복원본은 승격하면서 **새 타임라인**(이번 훈련 10)을 만든다. 이것이 운영과 같은 저장소에 섞이면 다음 복원(기본 = 최신 타임라인을 따라감)이 운영이 아니라 훈련본의 기록을 따라간다
- AWS 키: `/etc/pgbackrest/conf.d/repo2-aws.conf` 가 있으면 그대로 쓰고, 없으면 스크립트가 묻는다(키는 화면 · 기록에 안 남음). EC2 에 IAM 역할을 붙이면 키 없이(질문에 `2`). 훈련에는 읽기 권한(`ListBucket` · `GetObject`)만 있으면 된다
- 빈 서버가 아니면(데이터 폴더에 무엇이 있거나 PostgreSQL 이 돌면) 아무것도 바꾸지 않고 멈춘다 — 운영 DB 서버에서 잘못 실행해도 안전

## 2. 실행 명령어

### 2-1. 실습 훈련 (빈 VM — 2026-10-14 은 `loadgen`)

```bash
# ===== 실행할 명령어 =====
# 관리 서버(mgmt), 이 저장소 루트에서
# ① 빈 서버인지 · 인터넷(S3 · PGDG)이 되는지 — 'PostgreSQL 없음' 이고 응답 코드가 000 이 아니면 진행
ssh loadgen 'hostname -s; free -m | grep Mem; ls -d /var/lib/pgsql 2>/dev/null || echo "PostgreSQL 없음"; curl -s -o /dev/null -w "S3 %{http_code}\n" https://s3.ap-northeast-2.amazonaws.com; curl -s -o /dev/null -w "PGDG %{http_code}\n" https://download.postgresql.org/pub/repos/yum/'
# ② AWS 설정을 보낸다 (aws-s3-backup.md 2-5 ① 과 같은 방식 — 키는 표준입력으로. postgres 그룹이 아직 없어 root 600 → 스크립트가 권한을 맞춤)
source ~/.chargeops.env && printf '[global]\nrepo2-type=s3\nrepo2-s3-endpoint=s3.ap-northeast-2.amazonaws.com\nrepo2-s3-region=ap-northeast-2\nrepo2-s3-bucket=%s\nrepo2-s3-key=%s\nrepo2-s3-key-secret=%s\nrepo2-path=/test-chargeops\nrepo2-retention-full=2\n' "$AWS_TEST_BUCKET_BACKUP" "$AWS_TEST_KEY_ID" "$AWS_TEST_SECRET" | ssh loadgen 'sudo install -d -m 750 /etc/pgbackrest/conf.d && sudo sh -c "umask 077; cat > /etc/pgbackrest/conf.d/repo2-aws.conf" && echo "$(hostname -s) 놓음"'
# ③ 실행 (yes 입력). 끝에 '== 걸린 시간' 이 나오면 성공
scp -q db/ops/pgbackrest/dr-restore.sh loadgen:/tmp/ && ssh -t loadgen 'sudo bash /tmp/dr-restore.sh'
# ④ RPO: 운영의 마지막 적재 ↔ ⑦ 의 '이력 마지막 적재' · '되살린 마지막 트랜잭션'
psql -h db-01 -p 5432 -U dba chargeops -Atc "select to_char(max(received_at),'MM-DD HH24:MI:SS') from status_history"
```

### 2-2. 훈련 뒤 정리 (훈련 서버만 — 운영 DB · 저장소와 무관)

```bash
# ===== 실행할 명령어 =====
# 훈련본 · AWS 키 파일 · 로그 폴더 · 스크립트를 지운다 (키가 VM 에 남지 않게)
ssh loadgen 'sudo systemctl stop postgresql-17; sudo rm -rf /var/lib/pgsql/17/data /etc/pgbackrest /var/log/postgresql /tmp/dr-restore.sh && echo 정리'
# (선택) 패키지까지
ssh loadgen 'sudo dnf remove -y -q postgresql17\* pgvector_17 pg_cron_17 pgaudit_17 pgbackrest && echo 패키지 제거'
```

### 2-3. 실제 재해 (EC2 — 4.5)

- 서버: Rocky Linux 9 (실습과 같은 PGDG 패키지), 메모리 2GB 이상, 디스크 20GB, 밖으로 443 (S3 · PGDG)
- 권한: EC2 IAM 역할에 [`aws-s3-policy.example.json`](../../db/ops/pgbackrest/aws-s3-policy.example.json) 의 백업 칸 권한 (훈련만이면 `ListBucket` · `GetObject`)

```bash
# ===== 실행할 명령어 =====
# EC2 에서, 이 저장소를 받은 뒤 루트에서. DR 입력 → 버킷 · 경로 · 2(역할)
sudo DR_MODE=real bash db/ops/pgbackrest/dr-restore.sh
# 특정 시각으로 (예: 실수로 지운 데이터) — 그 시각 직전 백업을 골라 그 시각까지만 재생
sudo DR_MODE=real TARGET_TIME='2026-10-14 13:00:00+09' bash db/ops/pgbackrest/dr-restore.sh
```

`real` 은 승격 직후 **새 타임라인의 전체 백업(⑧, `--start-fast`)** 까지 받는다(건너뛰려면 `DR_BACKUP=0`). 이 백업이 있어야 클라우드 DB 도 사라졌을 때 다시 살릴 수 있고, 온프렘 복귀([`dr-failback.md`](dr-failback.md))의 출발점이 된다. 걸린 시간 줄에 따로 나오며 RTO 에는 넣지 않는다

그 뒤 (스크립트 끝에도 나옴): `pg_hba.conf` 에 새 앱 대역 → PgBouncer(`db/ops/pgbouncer/`) → 앱 접속 주소를 새 서버로 → Standby 다시 만들기

## 3. 설정 파일

| 위치 (새 서버) | 내용 | 저장소에 |
|---|---|---|
| `/etc/pgbackrest/pgbackrest.conf` | stanza · `pg1-path` · zst · `process-max=2` (비밀 없음) | 스크립트가 만듦 |
| `/etc/pgbackrest/conf.d/repo2-aws.conf` | repo2 = AWS S3 (키 2줄 또는 `repo2-s3-key-type=auto`), root:postgres 640 | ✗ (키 — 2-1 ② 또는 스크립트 질문) |
| SSL 인증서 · 키 (`ssl_cert_file` · `ssl_key_file` 위치) | 백업에 있으면 그대로(원래 서버 이름). 데이터 폴더 밖이라 없으면 **7일짜리 임시 자체 서명** — 정식 인증서로 교체 ([`postgres-ssl.md`](postgres-ssl.md)) | 스크립트가 만듦 (없을 때만) |
| 데이터 폴더 `conf.d/zz-local.conf` | 메모리 4GB 미만이면 `shared_buffers` = 메모리 1/4 (설정 파일의 1GB 는 4GB 기준) | 스크립트가 만듦 |
| `/var/log/postgresql` | 로그 폴더 — 원래 서버 설정을 따름. 백업에 들어 있지 않아 새로 만든다 (5장) | 스크립트가 만듦 |

## 4. 확인 방법

스크립트 ⑦ 이 함께 확인한다: `복구 중 f` · 타임라인 · 되살린 마지막 트랜잭션 · 건수 · 확장 · `WAL 보관 off`(훈련). 훈련본이 저장소에 아무것도 올리지 않았는지는 운영 서버에서:

```bash
# ===== 실행할 명령어 =====
# 훈련본의 새 타임라인(이번 10 = 16진수 0000000A) 기록이 repo1 · repo2 어디에도 없어야 함 → 둘 다 0
#   이름의 앞 8자리가 타임라인이라 ^ 로 앞에 붙여 찾는다 (옛 타임라인 폴더 000000050000000A 처럼 뒤쪽에 들어간 것과 헷갈리지 않게)
ssh db-01 'for r in 1 2; do echo "repo$r $(sudo -u postgres pgbackrest --repo=$r repo-ls archive/chargeops/17-1 | grep -c "^0000000A")"; done'
```
```
# ===== 실행 결과 (2026-10-14, 실습 loadgen = 빈 Rocky 9 VM · 메모리 1.2GB, 운영 DB 는 그대로 동작 중) =====
① 패키지           PostgreSQL 17.11 · pgBackRest 2.59.3 · pg_cron 1.6.8 · pgaudit 17.1 · pgvector 0.8.7 (versions.md 와 같음) — 54초
③ 저장소 확인       repo2 status: ok — 전체 20261014-110915F · 차등 20261014-110915F_20261014-122146D
④ 내려받기          restore size = 498.5MB, file total = 1522 — 28초 (앞선 시도 34 · 36초)
⑤ 이 서버에 맞게    shared_buffers = 318MB (메모리 1274MB) · 로그 폴더 /var/log/postgresql 만듦
⑥ 재생 · 승격       32초 (12:21 차등 이후 약 2시간치 WAL)
⑦ 확인             복구 중 f · 타임라인 10 · WAL 보관 off · 되살린 마지막 트랜잭션 14:28:40 (복원 끝 14:29:00)
                   충전기 527,245 · 상태 이력 683,376 · 이력 마지막 적재 14:25:01 · 수집 마지막 회차 14:25:01
                   확장 pg_cron 1.6 · pg_stat_statements 1.11 · vector 0.8.7 / 미리 올린 라이브러리 pg_stat_statements,pg_cron,pgaudit
합계               63초 (패키지가 이미 있을 때) — 처음부터는 + 54초 = 약 2분
RPO                마지막 트랜잭션이 복원 끝보다 20초 앞 (archive_timeout 60초 안). 수집 회차는 14:25 까지 모두 — 다음 14:30 회차는 복원 뒤
```

| 단계 | 이번 (실습) | 실제 DR 에서 달라지는 것 |
|---|---|---|
| 서버 준비 | VM 이 이미 있음 | EC2 생성 시간 — 4.5 에서 측정 |
| 패키지 | 54초 | 인터넷 속도 |
| 내려받기 | 28초 | DB 크기에 비례 (지금 0.5GB) |
| 재생 · 승격 | 32초 (2시간치 WAL) | 마지막 백업 이후 WAL 양에 비례 — 매일 03:40 차등이라 최대 하루치 |
| 앱 연결 전환 | — | `pg_hba` · PgBouncer · 앱 접속 주소 (4.5) |

- 스크립트 자체 시험(별도 환경, PostgreSQL 16 · 파일 저장소): 최신 시점 · 시각 지정(`TARGET_TIME`) · `real`(새 타임라인이 저장소에 올라가고 `check` 통과) · 빈 서버가 아니면 거부 · 시작 실패 안내

## 5. 막혔던 점

| 증상 | 원인 · 해결 |
|---|---|
| 1회차 ⑥ 시작 실패 — `FATAL: could not open log file "/var/log/postgresql/postgresql-Wed.log": No such file or directory` | 실습 DB 는 로그를 데이터 폴더 밖(`/var/log/postgresql`)에 쓰게 바꿔 두었다(비밀번호가 백업에 섞이지 않게). 그 **설정은 백업으로 따라오는데 폴더는 백업 밖**이라 새 서버에 없다 → 스크립트 ⑤ 가 복원된 설정(`postgres -C log_directory`)을 읽어 폴더를 만든다(SELinux 라벨 포함). **실제 재해 때 EC2 에서도 똑같이 막혔을 것** — 훈련으로 찾음 |
| 처음엔 SELinux 를 의심 | 실습 VM(db-01 · loadgen)은 `Permissive`. EC2 의 Rocky 가 `Enforcing` 이면 4.5 에서 다시 확인 (`getenforce`, 막히면 `ausearch -m avc`) |
| `Start request repeated too quickly` | 실패 뒤 systemd 가 5번 다시 시도하고 멈춘 상태 → 다시 시작하려면 `systemctl reset-failed` (스크립트가 함) |
| 확인 명령이 repo1 에서 5 | 처음 명령은 `grep -c 0000000A` 라 옛 타임라인의 WAL 폴더(`0000000?0000000A` = 타임라인 ?, WAL 번호 A)까지 셌다 → 앞에 붙여 `^0000000A` 로 (4장) |
| 2026-10-23 왕복 훈련 ⑥ 시작 실패 — `FATAL: could not load server certificate file "/var/lib/pgsql/ssl/server.crt"` | 로그 폴더와 같은 문제: 실습 DB 는 인증서를 데이터 폴더 밖에 둠 → `ssl=on` 설정만 따라옴. 그때는 손으로 임시 인증서를 만들어 띄움 → 스크립트 ⑤ 가 `postgres -C ssl_cert_file` 로 위치를 읽어 없으면 임시 발급 · 경고. 팀 서버는 인증서가 데이터 폴더 안([`postgres-ssl.md`](postgres-ssl.md))이라 그대로 따라오지만, 원래 서버 이름이라 `verify-full` 앱은 교체 필요 |
| 2026-10-23 살린 DB 의 첫 전체 백업이 95초 동안 진행 없음 (합계 145초) | 백업 시작 때 다음 체크포인트(`checkpoint_timeout`)까지 기다림 → `--start-fast` 로 바로 체크포인트 (스크립트 ⑧) |
| (예방) PGDG 저장소 404 | 옛 박스(9.6 이하)의 dnf 는 PGDG 주소를 못 만든다 → 먼저 `dnf -y update --exclude='kernel*'` |

## 6. 팀에서 정할 것 · 다음

- **4.5 EC2 DR (Terraform 담당과)**: AMI(Rocky 9 권장 — 실습과 같은 PGDG 패키지) · 크기 · 네트워크(밖으로 443) · IAM 역할(훈련은 읽기만) · 앱 연결 전환 방법(접속 주소를 이름으로 둘지)
- 목표 RTO 30분과 비교: EC2 생성 + 약 2분(이번 실측) + 앱 전환
- 팀 계정으로 옮기면 버킷 · 경로(예: `/chargeops`)만 바꿔 같은 스크립트로
- 온프렘 복귀: [`dr-failback.md`](dr-failback.md) — 실습 Patroni 로는 완료(2026-10-23), 멘토 서버(수동 전환)는 4.5 때
