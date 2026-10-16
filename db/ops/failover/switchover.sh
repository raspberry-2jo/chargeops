#!/usr/bin/env bash
# ============================================================================
# 계획된 역할 교대 (mgmt 에서, Lead 승인 뒤):  bash db/ops/failover/switchover.sh
#   기본값 = 원래대로 되돌리기: 지금 Primary 인 db-02 → db-01 (장애 전환 · 재편입 다음에)
#   두 서버가 모두 살아 있을 때 쓴다. 서버째 다운이면 failover.sh
#   ① 역할 확인 → ② 지금 Primary 의 PgBouncer · PostgreSQL 중지 + 자동 시작 해제 (깨끗한 종료 = 마지막 WAL 까지 Standby 로)
#   → ③ Standby 가 그 마지막 WAL 까지 받았는지 확인 → ④ 승격 + 그 서버의 PgBouncer restart → ⑤ 쓰기 재개 확인
#   → ⑥ 멈춘 쪽을 rejoin.sh 로 Standby 재편입 (깨끗하게 멈췄으면 pg_rewind 가 'no rewind required')
#   PgBouncer 를 reload 가 아니라 restart 하는 이유는 failover.sh 머리말 (1.26 의 '읽기 전용' 기억)
#   변수: OLD = 지금 Primary(기본 db-02), NEW = 승격할 Standby(기본 db-01), DB_HOSTS = 앱 주소 목록(기본 db-01,db-02)
# ============================================================================
set -uo pipefail
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
OLD=${OLD:-db-02}; NEW=${NEW:-db-01}; PGDATA=/var/lib/pgsql/17/data
DB_HOSTS=${DB_HOSTS:-db-01,db-02}                                     # 앱 접속 문자열의 주소 목록 (앞이 평소 Primary)
APP="host=$DB_HOSTS port=6432 dbname=chargeops user=app_writer target_session_attrs=read-write connect_timeout=3"
T0=$(date +%s.%N)
ts() { awk -v t0="$T0" -v now="$(date +%s.%N)" -v m="$*" 'BEGIN{printf "[%6.1fs] %s\n", now-t0, m}'; }
role() { ssh -n -o ConnectTimeout=5 "$1" "sudo -u postgres psql -XAtc 'SELECT pg_is_in_recovery()'" 2>/dev/null; }

[ "$OLD" != "$NEW" ] || { echo "OLD 와 NEW 가 같음 ($OLD)"; exit 1; }
for h in ${DB_HOSTS//,/ }; do
  grep -q "^$h:6432:chargeops:app_writer:" ~/.pgpass 2>/dev/null \
    || { echo "$HOME/.pgpass 에 $h:6432 의 app_writer 줄이 없음 — docs/setup/postgres-failover.md 2장의 pgpass 줄을 먼저"; exit 1; }
done

ts "① 역할 확인: $OLD = Primary(f), $NEW = Standby(t) 이고 $OLD 에서 streaming 이어야 함"
RO=$(role "$OLD"); RN=$(role "$NEW")
echo "$OLD=$RO $NEW=$RN"
[ "$RO" = f ] && [ "$RN" = t ] || { echo "중단 — 역할이 기대와 다름. 방향 확인: OLD=<지금 Primary> NEW=<Standby> bash $0"; exit 1; }
#   (이름 대신 개수: pg_basebackup -R 로 처음 만든 Standby(postgres-replication.md ④) 는 application_name 이 'walreceiver', rejoin.sh 를 거치면 서버 이름)
ST=$(ssh -n "$OLD" "sudo -u postgres psql -XAtc \"SELECT count(*) FILTER (WHERE state = 'streaming') FROM pg_stat_replication\"")
[ "${ST:-0}" -ge 1 ] || { echo "중단 — $OLD 에 streaming 중인 Standby 가 없음 (rejoin.sh 로 $NEW 부터 붙이기)"; exit 1; }

ts "② $OLD 의 PgBouncer · PostgreSQL 중지 + 자동 시작 해제 — 여기서부터 쓰기가 멈춤"
ssh -n "$OLD" "sudo systemctl disable --now pgbouncer postgresql-17 && systemctl is-active pgbouncer postgresql-17 | xargs" \
  | grep -qx 'inactive inactive' || { echo "중단 — $OLD 의 서비스를 멈추지 못함 (ssh $OLD 'systemctl status pgbouncer postgresql-17')"; exit 1; }

ts "③ $OLD 의 마지막 WAL(종료 체크포인트)을 $NEW 에서 받았는지"
CKPT=$(ssh -n "$OLD" "sudo -u postgres env LC_ALL=C /usr/pgsql-17/bin/pg_controldata -D $PGDATA" | awk -F': *' '/^Latest checkpoint location/ {print $2}')
GOT=
for _ in $(seq 1 10); do
  [ -n "$CKPT" ] || break                    # 위치를 못 읽었으면 아래 경고로
  GOT=$(ssh -n "$NEW" "sudo -u postgres psql -XAtc \"SELECT pg_wal_lsn_diff(pg_last_wal_receive_lsn(), '$CKPT') > 0\"")
  [ "$GOT" = t ] && break; sleep 1
done
echo "   종료 체크포인트 $CKPT · $NEW 수신 여부 $GOT"
if [ "$GOT" != t ]; then
  echo "   ⚠ $NEW 에 마지막 WAL 이 다 오지 않음 — 계속하면 그 차이만큼 손실되고 $OLD 의 데이터는 되감긴다(rewind)"
  read -rp "   그래도 계속하려면 Enter (중단: Ctrl+c, 중단하면 ssh $OLD 'sudo systemctl enable --now postgresql-17 pgbouncer') " _
fi

ts "④ $NEW 승격 + PgBouncer restart"
ssh -n "$NEW" "sudo -u postgres psql -XAtc 'SELECT pg_promote(wait => true, wait_seconds => 30)'" | grep -qx t \
  || { echo "승격 실패 — ssh $NEW 'sudo journalctl -u postgresql-17 -n 30'"; exit 1; }
ssh -n "$NEW" "sudo systemctl enable pgbouncer >/dev/null 2>&1; sudo systemctl restart pgbouncer && systemctl is-active pgbouncer"

ts "⑤ 쓰기 재개 확인 (앱과 같은 접속 문자열)"
OK=
for _ in $(seq 1 20); do
  if OUT=$(psql "$APP" -w -XAt -c \
      "INSERT INTO action_audit (idempotency_key, step, requested_by, role, result, reason)
       VALUES ('drill-switchover-' || extract(epoch FROM now())::bigint || '-$$', 'db_switchover', 'mgmt-script', 'Lead', 'executed',
               '역할 교대 $OLD → $NEW')
       RETURNING action_id" -c '\conninfo' 2>&1); then OK=1; break; fi
  sleep 1
done
[ -n "$OK" ] || { echo "쓰기 재개 실패 (20회): $OUT"; exit 1; }
echo "action_id $(echo "$OUT" | head -1) · $(echo "$OUT" | grep -o 'on host "[^"]*"')"
ts "✅ 교대 완료 — ② 부터 여기까지가 쓰기가 멈춘 시간 (실제 PC: 명령 사이 간격 포함 약 19초, 승격 뒤 약 3초)"

ts "⑥ $OLD 재편입 → Standby (rejoin.sh)"
OLD=$OLD NEW=$NEW bash "$(dirname "$0")/rejoin.sh"
