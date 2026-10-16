#!/usr/bin/env bash
# 구 Primary(db-01)를 pg_rewind 로 새 Primary(db-02)의 Standby 로 재편입 — mgmt 에서 (failover.sh · switchover.sh 다음)
#   구 Primary 가 서버째 꺼졌던 경우: 먼저 docs/setup/postgres-failover.md 3장 ③ 격리 부팅(망 끊고 켜서 두 서비스를 끈 뒤 망 연결)을 하고 실행
#   pg_rewind = 갈라진 뒤 바뀐 블록만 되감아, 전체 복사(pg_basebackup) 없이 다시 따라가게 함
#   비밀번호(postgres · replicator)는 mgmt 의 ~/.pgpass 에서 읽어 표준입력으로 보낸다 (파일 · 명령 줄에 쓰지 않음, docs/setup/postgres-failover.md 2장)
#   pg_rewind 는 설정 파일(postgresql.auto.conf · conf.d)까지 새 Primary 의 것으로 덮어쓴다 → 아래 ④ · ⑤ 에서 이 서버 것으로 되돌린다
set -euo pipefail
# (2026-10-16) Patroni 가 DB 를 관리하게 바꿨으면 이 스크립트는 쓰지 않는다 — 둘이 동시에 손대면 Primary 가 둘이 될 수 있다
#   한 대라도 patroni 가 enabled 면 중단. 두 대 모두 접속이 안 되면 판단할 수 없으므로 중단
_seen=0
for _h in db-01 db-02; do
  _e=$(ssh -n -o ConnectTimeout=5 -o BatchMode=yes "$_h" 'systemctl is-enabled patroni 2>/dev/null || true' 2>/dev/null) || continue
  _seen=1
  if [ "$_e" = enabled ]; then
    echo "중단 — $_h 에 Patroni 가 설치 · 사용 중(enabled). 상태: bash db/ops/patroni/status.sh · 역할 교대: TO=… bash db/ops/patroni/switchover.sh"
    echo "       (장애 전환은 Patroni 가 자동으로 한다. 예전 방식으로 돌아가려면 bash db/ops/patroni/rollback.sh)"; exit 1
  fi
done
[ "$_seen" = 1 ] || { echo "중단 — db-01 · db-02 모두 접속 안 됨 (Patroni 사용 여부를 확인할 수 없음)"; exit 1; }
OLD=${OLD:-db-01}; NEW=${NEW:-db-02}; PGDATA=/var/lib/pgsql/17/data   # 되돌릴 때: OLD=db-02 NEW=db-01 bash …
SLOT=${OLD//-/}                                                        # db-01 → db01 (postgres-replication.md ④ 의 슬롯 db02 와 같은 규칙)
KEEP=$PGDATA/../zz-local.keep                                          # ④ 에서 이 서버의 zz-local.conf 를 잠시 보관하는 곳
pw_of() { awk -F: -v u="$1" '$4 == u {print $5; exit}' ~/.pgpass 2>/dev/null || true; }
SUPER_PW=$(pw_of postgres); REPL_PW=$(pw_of replicator)

# 방향 확인 — 거꾸로 실행하면 살아 있는 Primary 를 되감아 버린다
[ "$(ssh -n -o ConnectTimeout=5 "$NEW" "sudo -u postgres psql -XAtc 'SELECT pg_is_in_recovery()'" 2>/dev/null)" = f ] \
  || { echo "중단 — $NEW 의 상태가 Primary 가 아님(응답 없음 또는 Standby). 방향 확인: OLD=… NEW=… bash $0"; exit 1; }
[ -n "$SUPER_PW" ] || { echo "중단 — $HOME/.pgpass 에 postgres 줄이 없음 (docs/setup/postgres-failover.md 2장)"; exit 1; }
[ -n "$REPL_PW" ]  || { echo "중단 — $HOME/.pgpass 에 replicator 줄이 없음 (docs/setup/postgres-failover.md 2장)"; exit 1; }

echo "① $OLD 의 PgBouncer · PostgreSQL 중지 (failover.sh · 격리 부팅에서 이미 멈췄으면 그대로)"
#   PgBouncer 도 멈추는 이유: 뒤의 PostgreSQL 이 멈춘 동안 PgBouncer 가 예전 '쓰기 가능' 상태를 기억한 채 살아 있으면
#   앱이 그 문을 골랐다가 쿼리가 대기(최대 120초)에 걸린다
ssh -n "$OLD" "sudo systemctl stop pgbouncer postgresql-17 || true"

echo "② $NEW 에 $OLD 용 복제 슬롯 $SLOT (없거나 못 쓰게 됐으면 새로) — 따라가는 동안 필요한 WAL 을 지우지 않게"
# shellcheck disable=SC2087  # $SLOT 은 일부러 mgmt 쪽에서 채워 보낸다 (서버에는 이 변수가 없음)
ssh "$NEW" "sudo -u postgres psql -X -q -At -d postgres -v ON_ERROR_STOP=1" <<SQL
SELECT pg_drop_replication_slot(slot_name) FROM pg_replication_slots WHERE slot_name = '$SLOT' AND wal_status = 'lost';
SELECT 'slot ' || (pg_create_physical_replication_slot('$SLOT', true)).slot_name || ' 만듦'
 WHERE NOT EXISTS (SELECT 1 FROM pg_replication_slots WHERE slot_name = '$SLOT');
SQL

echo "③ $OLD 의 접속 비밀번호 파일 (postgres: pg_rewind, replicator: 복제)"
printf '%s\n' "$NEW:5432:*:postgres:$SUPER_PW" "$NEW:5432:replication:replicator:$REPL_PW" \
  | ssh "$OLD" "sudo -u postgres tee /var/lib/pgsql/.pgpass >/dev/null && sudo chmod 600 /var/lib/pgsql/.pgpass"
unset SUPER_PW REPL_PW

echo "④ pg_rewind ($OLD ← $NEW). 이 서버만의 설정 conf.d/zz-local.conf 는 따로 보관했다가 되돌린다"
ssh "$OLD" "sudo -u postgres bash -c 'rm -f $KEEP; [ -f $PGDATA/conf.d/zz-local.conf ] && cp -p $PGDATA/conf.d/zz-local.conf $KEEP || true'"
ssh "$OLD" "sudo -u postgres /usr/pgsql-17/bin/pg_rewind --target-pgdata=$PGDATA \
  --source-server='host=$NEW port=5432 user=postgres dbname=chargeops' --progress"

echo "⑤ Standby 설정: 복사돼 온 primary_conninfo · primary_slot_name 지우기 → conf.d/zz-standby.conf 하나로"
ssh "$OLD" "sudo -u postgres bash -c \"sed -i -E '/^(primary_conninfo|primary_slot_name)[[:space:]]*=/d' $PGDATA/postgresql.auto.conf; \
  if [ -f $KEEP ]; then mv $KEEP $PGDATA/conf.d/zz-local.conf; else rm -f $PGDATA/conf.d/zz-local.conf; fi; \
  touch $PGDATA/standby.signal; \
  printf '%s\n' \\\"primary_conninfo = 'host=$NEW port=5432 user=replicator application_name=$OLD'\\\" \\\"primary_slot_name = '$SLOT'\\\" \
    > $PGDATA/conf.d/zz-standby.conf\""

echo "⑥ $OLD 시작 → Primary 시절의 안 쓰는 슬롯 정리 (남겨 두면 WAL 을 최대 4GB 붙잡음)"
ssh -n "$OLD" "sudo systemctl start postgresql-17"
ssh "$OLD" "sudo -u postgres psql -X -q -At -d postgres" <<'SQL'
SELECT 'slot ' || slot_name || ' 정리' FROM (SELECT slot_name, pg_drop_replication_slot(slot_name)
  FROM pg_replication_slots WHERE NOT active AND slot_type = 'physical') s;
SQL

echo "⑦ 확인 ($NEW 의 복제 상태 — application_name $OLD 줄의 state 가 streaming 이면 성공)"
sleep 3
ssh -n "$NEW" "sudo -u postgres psql -X -c 'SELECT application_name, client_addr, state, replay_lag FROM pg_stat_replication'"

echo "⑧ $OLD 자동 시작 되살리기 + PgBouncer 새로 시작 (새로 뜬 PgBouncer 는 'Standby' 를 정확히 기억 → 앱은 이 문을 건너뜀)"
ssh -n "$OLD" "sudo systemctl enable postgresql-17 pgbouncer 2>/dev/null; sudo systemctl restart pgbouncer && \
  echo \"$OLD: PgBouncer \$(systemctl is-active pgbouncer) · Standby \$(sudo -u postgres psql -XAtc 'SELECT pg_is_in_recovery()')\""
echo "역할을 다시 바꾸려면(평소 = db-01 Primary): OLD=$NEW NEW=$OLD bash db/ops/failover/switchover.sh (docs/setup/postgres-failover.md 3장 ⑤)"
