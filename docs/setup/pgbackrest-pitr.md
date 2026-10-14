# pgBackRest 백업 · 특정 시각 복구(PITR) 설치 기록

- 담당: 강영수 (리뷰 조인성)
- 관련 WBS: 2.8 pgBackRest 백업 · S3 · PITR 절차 — 완료 기준: 특정 시각 복원 성공
- 버전: versions.md 참고 (pgBackRest 2.59, PostgreSQL 17)
- 파일: `db/ops/pgbackrest/`, 서버 설정은 `db/ops/postgresql/chargeops.conf`(2.7에서 올림)
- 앞 단계: [`postgres-replication.md`](postgres-replication.md) (Primary · Standby · PgBouncer)

## 1. 왜 / 무엇을

- 복제(2.7)는 **서버가 죽는 것**을 막지만, 잘못된 `DELETE` · `UPDATE`는 Standby에도 그대로 복제됩니다. 그래서 "실수 직전 시각"으로 되돌릴 **백업 + WAL 보관**이 따로 필요합니다.
- Primary는 WAL을 최대 60초마다 S3로 올리고(`archive_timeout = 60` → 데이터 손실 목표 RPO 1분), 전체 백업은 가끔 받습니다. **전체 백업 + 그 뒤의 WAL**로 원하는 시각까지 복원합니다.
- 저장소는 아키텍처의 "데이터망 → S3 443" 흐름. 실습은 관리망의 S3 호환 저장소(MinIO), 운영은 AWS S3(`.env.example`의 `S3_BUCKET_BACKUP`)로 바꿀 수 있습니다.

```text
db-01 (Primary) ── archive_command: WAL 을 60초 안에 ──┐
                └─ 전체 백업 (가끔, 승격 뒤에는 꼭)  ───┤──▶ S3 버킷 pgbackrest /chargeops (zst 압축, 전체 백업 2세트 보관)
db-02 (Standby) ── 같은 설정 파일: 승격되면 이어서 올림 ┘          │
                                                                  ▼
훈련: db-02 의 다른 폴더 · 포트 5433 에 "T 시각"으로 복원 → 운영 DB 는 그대로
```

## 2. 설치 명령어

전제: 2.7까지 끝남(`chargeops.conf`에 `archive_mode = on`, `archive_command = 'pgbackrest …'`가 이미 있음), 두 서버에 `pgbackrest` 패키지(PGDG), S3 버킷 `pgbackrest`가 있고 DB 서버에서 443으로 닿음, 사설 CA 인증서가 `/etc/pki/ca-trust/source/anchors/`에 있음(실습 4.3).

```bash
# ===== 실행할 명령어 =====
# ① 두 서버 모두(db-01 · db-02) — 설정 놓기. S3 키 두 개를 물어봄 (값은 화면 · 셸 기록에 안 남음)
sudo bash db/ops/pgbackrest/install-pgbackrest.sh

# ② Primary(db-01) — 저장소 자리 만들기 · WAL 이 실제로 올라가는지 끝까지 확인
sudo -u postgres pgbackrest --stanza=chargeops stanza-create
sudo -u postgres pgbackrest --stanza=chargeops check

# ③ Primary — 첫 전체 백업 · 목록
sudo -u postgres pgbackrest --stanza=chargeops --type=full backup
sudo -u postgres pgbackrest --stanza=chargeops info

# ④ 관리 서버(mgmt) — PITR 훈련 (yes 를 입력해야 시작. 운영 DB 는 건드리지 않음)
bash db/ops/pgbackrest/pitr-drill.sh
ssh db-02 "sudo -u postgres /usr/pgsql-17/bin/pg_ctl -D /var/lib/pgsql/17/pitr stop -m fast"   # 훈련본 끄기
```

| 단계 | 왜 |
|---|---|
| `install-pgbackrest.sh` | 양식(`.example`)의 키 두 칸만 채워 `/etc/pgbackrest/pgbackrest.conf`(root:postgres **640**)로. 키가 있어 postgres 그룹만 읽음. Standby에도 두는 이유: 승격되면 그 서버가 WAL을 이어서 올리고, PITR 훈련도 그 서버에서 복원함 |
| `install -D` (스크립트 안) | PGDG 패키지는 `/etc/pgbackrest.conf`만 깔고 `/etc/pgbackrest/` 폴더는 안 만듦. pgBackRest는 `/etc/pgbackrest/pgbackrest.conf`를 먼저 읽음 |
| `stanza-create` | 버킷 안에 이 클러스터의 자리(`/chargeops`)를 만듦. stanza = 백업 대상 클러스터 하나의 이름. **Primary에서 한 번만** |
| `check` | WAL 하나를 실제로 전환해 `archive_command`가 S3까지 올리는지 확인. `archive_command`는 2.7부터 켜져 있어서, 이게 안 되면 WAL이 `pg_wal`에 쌓이므로 ①②를 바로 이어서 함 |
| `--type=full backup` | 데이터 폴더 전체를 zst로 압축해 S3로. 이 백업 + 이후 WAL = PITR이 가능한 범위의 시작점 |
| `info` | 백업 목록 · 크기 · WAL 범위. `repo1-retention-full=2`라 전체 백업은 최근 2세트만 남음 |
| `pitr-drill.sh` | 표시 행 BEFORE → 기준 시각 T → 표시 행 AFTER(실수라고 가정) → 70초 대기(WAL 업로드) → db-02의 `/var/lib/pgsql/17/pitr`, 포트 5433에 T로 복원 → 승격까지 기다린 뒤 BEFORE만 있는지 확인 + RTO 출력 |

## 3. 설정 파일

| 파일 | 바꾼 것 |
|---|---|
| `db/ops/pgbackrest/pgbackrest.conf.example` | S3 저장소(`repo1-type=s3`, 엔드포인트 · 버킷 · 경로 `/chargeops`), 사설 CA 경로, `repo1-retention-full=2`, `compress-type=zst`, `process-max=2`, `start-fast=y`(체크포인트를 바로 → 백업 시작이 빠름). 키 두 칸은 `<S3 접근 키>` · `<S3 비밀 키>`. AWS S3로 바꿀 줄은 파일 끝 주석 |
| `/etc/pgbackrest/pgbackrest.conf` | 위 양식에 키를 채운 진짜 파일. root:postgres 640, **커밋 안 함**(.gitignore, README S2-1) |
| `db/ops/postgresql/chargeops.conf` (2.7) | `archive_mode = on`, `archive_command = 'pgbackrest --stanza=chargeops archive-push %p'`, `archive_timeout = 60` — 이번 PR에서 바꾸지 않음 |

## 4. 확인 방법

```bash
# ===== 실행할 명령어 =====
ssh db-01 'sudo -u postgres pgbackrest --stanza=chargeops check'
ssh db-01 'sudo -u postgres pgbackrest --stanza=chargeops info'
ssh db-01 'sudo -u postgres psql -Atc "SELECT archived_count, failed_count, last_archived_time FROM pg_stat_archiver"'
bash db/ops/pgbackrest/pitr-drill.sh
```
```
# ===== 실행 결과 ===== (실습 PC 2026-10-12 19:51, 장애 전환 훈련 뒤 새로 받은 전체 백업으로 — 줄별 요약)
check command end: completed successfully
full backup: 20261012-194956F   458.6MB → 저장소 81.9MB (zst), 13.5초   ← 이전 전체 백업 20261010-165942F 는 retention-full=2 로 정리
① 복구 기준 시각 T = 2026-10-12 19:51:48+0900
 … PITR-BEFORE (이번 훈련) 있음 · PITR-AFTER (이번 훈련) 없음             ← 특정 시각 복원 성공 (완료 기준)
RTO(복원 시작 → T 까지 재생 · 승격) = 15초                                  ← restore 10.5초 + 재생 · 승격
```

- 결과 표에 10/10 훈련의 `PITR-BEFORE` · `PITR-AFTER` 두 줄도 보이는데, T보다 앞선 지난 기록이라 있는 것이 맞습니다.
- 복원 로그(`/var/lib/pgsql/17/pitr/log/`)의 `recovery stopping before commit of transaction …, time <AFTER 시각>`도 같은 뜻입니다.
- `pg_stat_archiver`의 `failed_count`가 늘고 있으면 WAL이 S3로 못 올라가는 중 → 5장 첫 두 줄.

## 5. 막혔던 점

| 증상 | 원인 · 해결 |
|---|---|
| `check`에서 `archive … timed out` | SELinux가 PostgreSQL의 외부(443) 연결을 막음. db-01에서 `curl -s https://<S3 주소>/minio/health/live -o /dev/null -w '%{http_code}'`이 200이면 SELinux → `sudo ausearch -m avc -ts recent \| audit2allow -M pgbackrest_local && sudo semodule -i pgbackrest_local.pp` |
| VM을 다시 켠 뒤 `HostConnectError … [113] No route to host` | 켤 때 실습망 NIC가 기본 방화벽 존(ssh만 허용)으로 돌아감 → 방화벽 설정을 다시 적용 (2.7 기록과 같은 원인, 인프라 구성 자동화에 포함 필요) |
| `install: cannot create regular file '/etc/pgbackrest/pgbackrest.conf'` | 패키지가 `/etc/pgbackrest/` 폴더를 만들지 않음 → `install -D` (스크립트에 반영) |
| PITR 복원 직후 조회하면 표시 행이 `0 rows` | `pg_ctl -w`는 접속을 받기 시작하면 돌아오지만 T까지의 WAL 재생은 계속되는 중 → 승격(`pg_is_in_recovery() = f`)을 기다린 뒤 검증하도록 스크립트 수정 (2026-10-10) |
| 장애 전환 뒤 PITR이 오래 걸림 | RTO = "마지막 전체 백업 이후 쌓인 WAL"을 다시 재생하는 시간. 승격 · 원복으로 타임라인이 바뀌면 **새 Primary에서 전체 백업을 바로 다시** 받음 (10/12: 새 백업 뒤 RTO 15초) |

## 6. 팀에서 정할 것

- ~~전체 백업 주기~~ → **정함 (2026-10-14)**: 두 DB 서버의 systemd 타이머가 매일 03:40, 일요일 전체 · 다른 날 차등, Primary 에서만, 저장소마다 — [`aws-s3-backup.md`](aws-s3-backup.md) 2-6. 보관 수는 그대로 `retention-full=2`
- ~~운영 저장소~~ → **둘 다 (2026-10-14)**: repo1 MinIO(빠른 복구) 유지 + repo2 AWS S3(원격 · DR) 추가 — [`aws-s3-backup.md`](aws-s3-backup.md). 지금은 개인 계정 시험, 팀 계정은 2.2 IAM 뒤 같은 절차
