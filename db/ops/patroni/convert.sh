#!/usr/bin/env bash
# ============================================================================
# 운영 DB 를 Patroni 가 넘겨받게 전환 (mgmt 에서, 사용자 승인 뒤):  bash db/ops/patroni/convert.sh
#   지금: postgresql-17 서비스 Primary 1 + Standby 1, 전환은 사람이 failover.sh  →  뒤: Patroni 가 감시 · 자동 전환
#   데이터는 그대로 쓴다 (복사 · 복원 없음). 앱 접속 문자열 · pgbouncer.ini 도 그대로
#   전제: ansible-playbook patroni.yml 끝남 (etcd 3대 healthy · watchdog · Patroni 설치 · 설정), 최근 백업 있음
#   쓰기가 멈추는 구간: ④ Primary 의 PostgreSQL 을 내렸다가 Patroni 로 다시 띄우는 동안 (기대 10~30초)
#   ① 점검 → ② 백업 시점 확인 → ③ Standby 내리고 정리 → ④ Primary 를 Patroni 로 다시 띄움(Leader)
#   → ⑤ PgBouncer restart + 쓰기 확인 → ⑥ Standby 를 Patroni 로(Replica) → ⑦ 옛 복제 슬롯 정리 → ⑧ 최종 확인
#   중간에 멈추면: 출력된 안내대로, 또는 되돌리기 bash db/ops/patroni/rollback.sh
#   비밀번호: mgmt 의 ~/.pgpass (app_writer 6432 두 줄 — failover.sh 와 같음). 스크립트에는 없다
# ============================================================================
set -uo pipefail
PGDATA=/var/lib/pgsql/17/data
PCTL="sudo -u postgres patronictl -c /etc/patroni/patroni.yml"
APP="host=db-01,db-02 port=6432 dbname=chargeops user=app_writer target_session_attrs=read-write connect_timeout=3"
T0=$(date +%s.%N)
ts()  { awk -v t0="$T0" -v now="$(date +%s.%N)" -v m="$*" 'BEGIN{printf "[%6.1fs] %s\n", now-t0, m}'; }
die() { echo "중단 — $*"; exit 1; }
on()  { ssh -n -o ConnectTimeout=5 -o BatchMode=yes "$@"; }
sq()  { on "$1" "sudo -u postgres psql -XAtc \"$2\"" 2>/dev/null; }
# 서버별 설정(conf.d/zz-local.conf — 예: 작은 Standby 의 shared_buffers)을 데이터 폴더 밖으로.
#   Patroni 의 자동 pg_rewind 는 conf.d 를 상대 서버 것으로 덮는다 (rejoin.sh 는 따로 보관했지만 Patroni 는 안 함).
#   postgresql.conf(= Patroni 가 postgresql.base.conf 로 보관 · 복원하는 파일) 맨 끝에서 /var/lib/pgsql/17/zz-local.conf 를 읽게 한다
local_conf() {
  on "$1" "sudo -u postgres bash -c 'cd $PGDATA && if [ -f conf.d/zz-local.conf ]; then mv conf.d/zz-local.conf ../zz-local.conf; fi; \
    grep -q zz-local.conf postgresql.conf || echo \"include_if_exists = '\''/var/lib/pgsql/17/zz-local.conf'\''\" >> postgresql.conf; \
    echo \"   $1: \$(tail -1 postgresql.conf) · 서버별 설정 파일 \$(ls ../zz-local.conf 2>/dev/null || echo 없음)\"'"
}
role_of() { on "$1" "$PCTL list -f json" 2>/dev/null | jq -r --arg m "$1" '.[] | select(.Member == $m) | "\(.Role)/\(.State)"'; }
write_ok() { psql "$APP" -w -XAt -c 'select txid_current()' >/dev/null 2>&1; }

command -v jq >/dev/null || die "mgmt 에 jq 가 없음 (sudo dnf install -y jq)"
for h in db-01 db-02; do
  grep -q "^$h:6432:chargeops:app_writer:" ~/.pgpass 2>/dev/null || die "~/.pgpass 에 $h:6432 app_writer 줄이 없음 (docs/setup/postgres-failover.md 2장)"
done

ts "① 점검 — 역할 · 서비스 · Patroni 준비 · etcd"
PRIMARY=; STANDBY=
for h in db-01 db-02; do
  case "$(sq "$h" 'select pg_is_in_recovery()')" in
    f) PRIMARY=$h ;; t) STANDBY=$h ;; *) die "$h 의 PostgreSQL 에 접속하지 못함" ;;
  esac
done
[ -n "$PRIMARY" ] && [ -n "$STANDBY" ] || die "Primary 1 · Standby 1 이 아님 (Primary='$PRIMARY' Standby='$STANDBY')"
echo "   Primary = $PRIMARY · Standby = $STANDBY"
for h in "$PRIMARY" "$STANDBY"; do
  st=$(on "$h" 'echo "$(systemctl is-active postgresql-17)|$(systemctl is-active patroni)|$(sudo test -f /etc/patroni/patroni.yml && echo yes)|$(stat -c %U /dev/watchdog 2>/dev/null)|$(rpm -q --quiet patroni-etcd && echo yes)"')
  IFS='|' read -r pg pat cfg wd pkg <<<"$st"
  echo "   $h: postgresql-17=$pg · patroni=$pat · 설정=${cfg:-없음} · watchdog 소유자=${wd:-없음} · patroni-etcd=${pkg:-없음}"
  [ "$pg" = active ]    || die "$h 의 postgresql-17 이 active 가 아님"
  [ "$pat" != active ]  || die "$h 에서 Patroni 가 이미 돌고 있음 — 이미 전환됨? (bash db/ops/patroni/status.sh)"
  [ "$cfg" = yes ] && [ "$wd" = postgres ] && [ "$pkg" = yes ] || die "$h 준비 안 됨 — ansible-playbook patroni.yml 먼저"
done
SCOPE=$(on "$PRIMARY" "sudo awk '/^scope:/{print \$2; exit}' /etc/patroni/patroni.yml")
healthy=$(on "$PRIMARY" '/usr/local/bin/etcdctl --endpoints=http://127.0.0.1:2379 endpoint health --cluster 2>&1' | grep -c 'is healthy')
echo "   etcd healthy: ${healthy}대 · 클러스터 이름: $SCOPE"
[ "$healthy" -ge 3 ] || die "etcd 3대가 healthy 가 아님 (ansible-playbook patroni.yml --tags etcd)"
left=$(on "$PRIMARY" "/usr/local/bin/etcdctl --endpoints=http://127.0.0.1:2379 get --prefix --keys-only /service/$SCOPE/" | grep -c .)
[ "$left" = 0 ] || die "etcd 에 이전 '$SCOPE' 기록 ${left}개가 남아 있음 (예전 전환을 되돌린 흔적이면 rollback.sh ⑦ 참고)"
rep=$(sq "$PRIMARY" "select state || ' ' || coalesce(pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn), 0) from pg_stat_replication where application_name = '$STANDBY'")
echo "   복제: ${rep:-없음} (state · 아직 반영 안 된 바이트)"
[ "${rep%% *}" = streaming ] || die "$STANDBY 가 streaming 이 아님 — 복제부터 정상화"
write_ok || die "앱 경로(6432)로 지금 쓰기가 안 됨 — 전환 전에 원인부터"

ts "② 백업 시점 확인 (되돌릴 기준점. db VM 에는 vagrant snapshot 을 쓰지 않는다)"
on "$PRIMARY" "sudo -u postgres pgbackrest --stanza=chargeops info --output=json" 2>/dev/null \
  | jq -r '.[0].backup[-3:][] | "   \(.type)\t\(.timestamp.stop | strftime("%Y-%m-%d %H:%M:%S")) (UTC)\t\(.label)"' 2>/dev/null \
  || echo "   (백업 목록을 읽지 못함)"
echo "   최근 백업이 오늘 것이 아니면 Ctrl+c 하고 먼저:  ssh $PRIMARY 'sudo systemctl start chargeops-backup && systemctl status chargeops-backup --no-pager | tail -3'"
read -rp "   백업 시점 확인 · 쓰기가 10~30초 멈추는 것에 동의하면 Enter (중단: Ctrl+c) " _

ts "③ $STANDBY: PostgreSQL 정지 · 자동 시작 해제 · 수동 Standby 설정 걷어 내기 (원본은 *.pre-patroni 로 보관)"
on "$STANDBY" "sudo systemctl disable --now postgresql-17 >/dev/null 2>&1; sudo systemctl mask postgresql-17 >/dev/null 2>&1; pgrep -u postgres -xc postgres; true" | grep -qx 0 \
  || die "$STANDBY 의 postgresql-17 을 멈추지 못함"
on "$STANDBY" "sudo -u postgres bash -c 'cd $PGDATA && cp -p postgresql.auto.conf postgresql.auto.conf.pre-patroni \
  && sed -i -E \"/^(primary_conninfo|primary_slot_name|restore_command)[[:space:]]*=/d\" postgresql.auto.conf \
  && if [ -f conf.d/zz-standby.conf ]; then mv conf.d/zz-standby.conf ../zz-standby.conf.pre-patroni; fi; ls conf.d'"
#   primary_conninfo 를 지우는 이유: postgresql.auto.conf 는 Patroni 가 쓰는 postgresql.conf 보다 나중에 읽혀서, 남아 있으면 Patroni 의 값을 덮는다
local_conf "$STANDBY"

ts "④ $PRIMARY: PostgreSQL 정지 → Patroni 로 다시 시작 (지금부터 쓰기 멈춤)"
sq "$PRIMARY" 'checkpoint' >/dev/null      # 내려가는 시간을 줄임
on "$PRIMARY" "sudo -u postgres cp -p $PGDATA/postgresql.conf $PGDATA/../postgresql.conf.pre-patroni"
local_conf "$PRIMARY"
D0=$(date +%s.%N)
on "$PRIMARY" "sudo systemctl disable --now postgresql-17 >/dev/null 2>&1; sudo systemctl mask postgresql-17 >/dev/null 2>&1; pgrep -u postgres -xc postgres; true" | grep -qx 0 \
  || die "$PRIMARY 의 postgresql-17 을 멈추지 못함 — 손으로 확인: ssh $PRIMARY 'systemctl status postgresql-17'"
on "$PRIMARY" "sudo systemctl enable --now patroni" || die "$PRIMARY 의 Patroni 시작 실패 — ssh $PRIMARY 'sudo journalctl -u patroni -n 40 --no-pager'"
R=
for _ in $(seq 1 30); do R=$(role_of "$PRIMARY"); [ "$R" = Leader/running ] && [ "$(sq "$PRIMARY" 'select pg_is_in_recovery()')" = f ] && break; sleep 2; done
[ "$R" = Leader/running ] || { echo "   $PRIMARY 상태: '${R:-응답 없음}'"; on "$PRIMARY" 'sudo journalctl -u patroni -n 25 --no-pager'; \
  die "60초 안에 Leader 가 되지 못함. 되돌리기: bash db/ops/patroni/rollback.sh"; }
ts "   $PRIMARY = Leader (running)"

ts "⑤ $PRIMARY 의 PgBouncer restart → 앱 경로로 쓰기 확인"
on "$PRIMARY" "sudo systemctl restart pgbouncer"
OK=
for _ in $(seq 1 30); do write_ok && { OK=1; break; }; sleep 1; done
[ -n "$OK" ] || die "쓰기 재개 실패 (30초) — ssh $PRIMARY 'sudo journalctl -u pgbouncer -n 20 --no-pager'"
D1=$(date +%s.%N)
ts "   쓰기 재개 — 멈춘 시간 $(awk -v a="$D0" -v b="$D1" 'BEGIN{printf "%.1f", b-a}')초"

ts "⑥ $STANDBY: Patroni 시작 → Replica 로 따라붙음 (pg_rewind 필요 없으면 그대로 이어 받음)"
on "$STANDBY" "sudo systemctl enable --now patroni" || die "$STANDBY 의 Patroni 시작 실패"
R=
for _ in $(seq 1 60); do R=$(role_of "$STANDBY"); [ "$R" = Replica/streaming ] && break; sleep 2; done
[ "$R" = Replica/streaming ] || { on "$STANDBY" 'sudo journalctl -u patroni -n 25 --no-pager'; \
  die "$STANDBY 가 120초 안에 streaming 이 되지 못함 ('${R:-응답 없음}'). Primary 는 정상 — 원인 확인 뒤: ssh $STANDBY \"$PCTL reinit $SCOPE $STANDBY --force\""; }
on "$STANDBY" "sudo systemctl restart pgbouncer"
ts "   $STANDBY = Replica (streaming)"

ts "⑦ 옛 수동 복제 슬롯 정리 (전환 동안 ignore_slots 로 지켜 둔 db01 · db02. Patroni 는 db_01 · db_02 를 쓴다)"
sq "$PRIMARY" "select slot_name || ' 정리' from (select slot_name, pg_drop_replication_slot(slot_name) from pg_replication_slots
  where slot_name in ('db01','db02') and not active) s"
sq "$PRIMARY" "select '   슬롯 ' || slot_name || ' active=' || active from pg_replication_slots"

ts "⑧ 최종 확인"
on "$PRIMARY" "$PCTL list"
on "$PRIMARY" "$PCTL show-config" | grep -E '^(ttl|loop_wait|retry_timeout|maximum_lag_on_failover):' | tr '\n' ' '; echo
on "$PRIMARY" "sudo journalctl -u patroni --since '-10min' --no-pager | grep -m1 -i 'watchdog activated'" \
  || echo "   ⚠ $PRIMARY 의 watchdog 활성 기록이 안 보임 — ssh $PRIMARY 'sudo journalctl -u patroni | grep -i watchdog'"
on "$PRIMARY" "sudo -u postgres pgbackrest --stanza=chargeops check >/dev/null 2>&1 && echo '   pgBackRest check OK (WAL 보관 정상)' || echo '   ⚠ pgBackRest check 실패'"
ts "✅ 전환 완료 — 이제 장애 전환은 Patroni 가 한다. failover.sh · switchover.sh · rejoin.sh 는 Patroni 가 돌면 스스로 멈춘다"
echo "다음: bash db/ops/patroni/status.sh  →  시험(사용자 확인 뒤) MODE=isolate bash db/ops/patroni/drill.sh  →  bash deploy/final-server/check-all.sh"
