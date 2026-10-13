# PostgreSQL 복제 · PgBouncer 설치 기록

- 담당: 강영수 (리뷰 조인성)
- 관련 WBS: 2.7 Standby 복제 · PgBouncer — 완료 기준: 복제 지연 1초 이내 · 쓰기/조회 분리
- 버전: versions.md 참고 (PostgreSQL 17, PgBouncer 1.26)
- 파일: `db/ops/postgresql/`, `db/ops/pgbouncer/`

## 1. 왜 / 무엇을

- Primary(`db-01`)가 서버째 멈춰도 Standby(`db-02`)를 승격해 이어 가려고 **스트리밍 복제**를 둡니다.
- 앱은 두 서버의 PgBouncer(6432) 주소를 모두 알고, **쓰기 되는 쪽(Primary)을 스스로 고릅니다**. 그래서 장애 전환 때 앱 설정이나 `pgbouncer.ini`를 바꾸지 않습니다.
- 조회(Grafana · 리포트)는 Standby의 읽기 풀(`chargeops_ro`)로 보내 Primary 부하를 덜어 줍니다.

```text
앱(CSMS · 수집기 · 봇) ─ "host=db-01,db-02 port=6432 … target_session_attrs=read-write"
   ├─ db-01:6432 PgBouncer ─ 127.0.0.1:5432   평소 Primary → 여기에 붙음
   └─ db-02:6432 PgBouncer ─ 127.0.0.1:5432   평소 Standby → '읽기 전용'이라 건너뜀
Grafana · 리포트(report_ro) ─ db-01:6432 의 chargeops_ro ─ db-02:5432 (Standby)
```

## 2. 설치 명령어

전제: 두 서버에 PostgreSQL 17과 확장(pgvector · pg_cron · pgaudit), PgBouncer 패키지가 깔려 있고(PGDG), 서로 이름(`db-01` · `db-02`)으로 찾을 수 있음. 저장소는 각 서버의 작업 폴더에 받아 둔 상태(아래 명령은 저장소 루트에서 실행).

```bash
# ===== 실행할 명령어 =====
# ① Primary(db-01) — 설정 놓기 (initdb 는 --data-checksums --encoding=UTF8 --locale=C.UTF-8 로 해 둔 상태)
D=/var/lib/pgsql/17/data
sudo -u postgres mkdir -p $D/conf.d
sudo install -o postgres -g postgres -m 600 db/ops/postgresql/chargeops.conf $D/conf.d/chargeops.conf
sudo install -o postgres -g postgres -m 600 db/ops/postgresql/pg_hba.conf $D/pg_hba.conf
grep -q "^include_dir = 'conf.d'" $D/postgresql.conf || echo "include_dir = 'conf.d'" | sudo -u postgres tee -a $D/postgresql.conf
sudo systemctl enable --now postgresql-17

# ② Primary — 스키마 · 계정 (db/migrations/README.md 의 반복문, 그다음 \password 로 계정 비밀번호)

# ③ Standby(db-02) — 복제 비밀번호를 postgres 계정의 .pgpass 에 (값은 각자, 파일은 600)
#    한 줄 형식:  db-01:5432:replication:replicator:<각자 설정>
sudo -u postgres bash -c 'umask 077; cat > /var/lib/pgsql/.pgpass'      # 붙여 넣고 Ctrl+D

# ④ Standby — Primary 를 통째로 복사해서 Standby 로 시작
sudo -u postgres /usr/pgsql-17/bin/pg_basebackup -h db-01 -U replicator -D /var/lib/pgsql/17/data \
     -X stream -C -S db02 -R -P --checkpoint=fast
echo "shared_buffers = 512MB" | sudo -u postgres tee /var/lib/pgsql/17/data/conf.d/zz-local.conf   # Standby 메모리가 작을 때만
sudo systemctl enable --now postgresql-17

# ⑤ 두 서버 모두 — PgBouncer (Primary 이름이 db-01 이 아니면 sudo PRIMARY_NAME=<이름> bash …)
sudo bash db/ops/pgbouncer/install-pgbouncer.sh
```

| 단계 | 왜 |
|---|---|
| `conf.d/chargeops.conf` | 기본 `postgresql.conf`는 그대로 두고 바꾼 값만 따로 → 무엇을 바꿨는지 한눈에 보임 |
| `pg_hba.conf` | 앱 계정은 PgBouncer에서만, 복제는 DB망 안에서만, dba는 관리망에서만, 나머지는 `reject` |
| `.pgpass` (Standby) | 비밀번호를 명령 줄에 쓰지 않음. 복제가 다시 붙을 때도 여기서 읽음 |
| `pg_basebackup -C -S db02 -R` | 복제 슬롯 `db02`를 만들어 Standby가 잠시 꺼져도 필요한 WAL을 남김(최대 4GB). `-R`로 시작하자마자 Standby로 동작 |
| `zz-local.conf` | 설정은 Primary 것이 복사되어 옴. 메모리가 다르면 이 파일만 덮어씀 (conf.d 는 이름 순) |
| `install-pgbouncer.sh` | 쓰기 풀은 두 서버 모두 자기 서버(127.0.0.1). 읽기 풀은 Primary에서는 Standby로, Standby에서는 자기 자신으로 |

`archive_command`(pgBackRest)는 켜진 상태라, 저장소 설정 전이면 WAL이 `pg_wal`에 쌓입니다 → 바로 이어서 [`pgbackrest-pitr.md`](pgbackrest-pitr.md)(WBS 2.8)의 ① · ②를 합니다.

## 3. 설정 파일

| 파일 | 바꾼 것 |
|---|---|
| `db/ops/postgresql/chargeops.conf` | `wal_level = replica`, `max_wal_senders = 5`, `max_replication_slots = 5`, `max_slot_wal_keep_size = 4GB`, `hot_standby = on`, `wal_log_hints = on`(pg_rewind), `archive_timeout = 60`(RPO 1분), 확장 3개 preload, `cron.use_background_workers = on`, 0.5초 넘는 쿼리 로그 |
| `db/ops/postgresql/pg_hba.conf` | 주소는 DB망 `10.10.30.0/24` · 관리망 `10.10.40.0/24` 기준. WBS 1.3 IP 대역표가 바뀌면 주소만 맞춤 |
| `db/ops/pgbouncer/pgbouncer.ini` | `pool_mode = transaction`, `default_pool_size = 20` + 예비 5, `max_client_conn = 2000`, `auth_type = scram-sha-256` |
| `/etc/pgbouncer/userlist.txt` | `make-userlist.sh`가 PostgreSQL의 SCRAM 비밀(평문 아님)을 복사해 만듦. 600, 커밋 안 함(.gitignore) |

앱 쪽 `.env` (루트 `.env.example` 양식): `DB_HOST=db-01,db-02`, `DB_PORT=6432`, 그리고 `PGTARGETSESSIONATTRS=read-write`, `PGCONNECT_TIMEOUT=3`.

## 4. 확인 방법

```bash
# ===== 실행할 명령어 =====
ssh db-01 'sudo -u postgres psql -c "SELECT application_name, state, replay_lag FROM pg_stat_replication"'
ssh db-02 'sudo -u postgres psql -Atc "SELECT pg_is_in_recovery()"'
psql "host=db-01,db-02 port=6432 dbname=chargeops user=app_writer target_session_attrs=read-write connect_timeout=3" -XAt -c "SELECT pg_is_in_recovery()" -c '\conninfo'
psql -h db-01 -p 6432 -U report_ro chargeops_ro -Atc "SELECT pg_is_in_recovery()"
psql -h db-02 -p 5432 -U dba chargeops -c "CREATE TABLE t_write_test(x int)"
```
```
# ===== 실행 결과 ===== (실습 PC. 첫 표는 2026-10-13 PC 강제 종료 뒤 다시 켜고 잰 값, 나머지는 실습 9.2 · 9.3 때 확인)
 application_name |   state   |   replay_lag
------------------+-----------+-----------------
 db-02            | streaming | 00:00:00.001464        ← 복제 지연 0.0015초 (완료 기준 1초 이내)
t                                                      ← db-02 는 Standby
f
You are connected to database "chargeops" as user "app_writer" on host "db-01" …   ← 쓰기는 Primary 로
t                                                      ← 조회 풀은 Standby 로 (쓰기/조회 분리)
ERROR:  cannot execute CREATE TABLE in a read-only transaction                     ← Standby 는 읽기 전용
```

## 5. 막혔던 점

| 증상 | 원인 · 해결 |
|---|---|
| 장애 전환 뒤 앱이 두 PgBouncer 모두에서 거절당함 | PgBouncer 1.26은 `reload`로는 서버의 '읽기 전용' 상태 기억을 지우지 않음 → 역할이 바뀐 서버는 **restart** (2026-10-12) |
| pg_cron 작업이 모두 `connection failed` | 기본(off)은 작업마다 127.0.0.1로 새로 접속해 scram 비밀번호를 요구함 → `cron.use_background_workers = on` (2026-10-11) |
| VM을 다시 켠 뒤 5432 · 6432 · S3가 `No route to host` | 켤 때 실습망 NIC가 기본 방화벽 존으로 돌아감 → 방화벽 설정을 다시 적용 (인프라 구성 자동화에 포함 필요) |
| PC가 강제로 꺼져 두 DB가 동시에 멈춤 | 다시 켜면 역할 그대로(Primary · Standby), 스스로 복구됨. 확인은 4장 명령 + `pgbackrest --stanza=chargeops check` (2026-10-13) |
| 새로 연결할 때 3초씩 늦음 (장애 중) | 꺼진 db-01에 먼저 붙어 보다가 `connect_timeout=3` 뒤 db-02로 넘어감. 정상 동작 |
