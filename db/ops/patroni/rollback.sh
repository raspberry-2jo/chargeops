#!/usr/bin/env bash
# ============================================================================
# Patroni → 예전 방식(postgresql-17 서비스 + 수동 failover.sh)으로 되돌리기 (mgmt 에서, 사용자 승인 뒤)
#   bash db/ops/patroni/rollback.sh
#   데이터는 그대로. Leader 였던 서버가 Primary 로 남고, 다른 쪽은 rejoin.sh 로 Standby 재편입
#   ① 역할 확인 → ② Replica 의 Patroni 정상 종료 → ③ Leader 의 Patroni 정상 종료 (watchdog 도 정상 해제 — kill 금지: 재부팅됨)
#   → ④ 두 서버: Patroni 가 바꿔 둔 설정 파일을 원래대로 · postgresql-17 되살리기 · patroni 자동 시작 해제
#   → ⑤ Leader 였던 서버를 postgresql-17 로 시작 + PgBouncer restart → 쓰기 확인
#   → ⑥ Patroni 슬롯(db_01 · db_02) 정리 → ⑦ etcd 안의 클러스터 기록 지움(다음에 convert.sh 를 다시 할 수 있게)
#   다음(안내가 나옴): OLD=<Replica 였던 서버> NEW=<Leader 였던 서버> bash db/ops/failover/rejoin.sh
#   etcd · watchdog 모듈 · Patroni 패키지는 그대로 둔다 (다시 전환할 때 씀. watchdog 은 아무도 열지 않으면 동작하지 않음)
# ============================================================================
set -uo pipefail
PGDATA=/var/lib/pgsql/17/data
PCTL="sudo -u postgres patronictl -c /etc/patroni/patroni.yml"
APP="host=db-01,db-02 port=6432 dbname=chargeops user=app_writer target_session_attrs=read-write connect_timeout=3"
on() { ssh -n -o ConnectTimeout=5 -o BatchMode=yes "$@"; }
die() { echo "중단 — $*"; exit 1; }

echo "① 역할 확인"
J=
for h in db-01 db-02; do J=$(on "$h" "$PCTL list -f json" 2>/dev/null) && break; done
on db-01 "$PCTL list" 2>/dev/null || on db-02 "$PCTL list"
LEADER=$(echo "$J" | jq -r '.[] | select(.Role == "Leader") | .Member')
[ -n "$LEADER" ] || die "Leader 가 없음 — 이 상태에서는 되돌리지 않는다 (status.sh 로 원인 먼저)"
REPLICA=$([ "$LEADER" = db-01 ] && echo db-02 || echo db-01)
SCOPE=$(on "$LEADER" "sudo awk '/^scope:/{print \$2; exit}' /etc/patroni/patroni.yml")
echo "   Leader = $LEADER (Primary 로 남음) · 다른 쪽 = $REPLICA (rejoin.sh 로 Standby)"
read -rp "예전 방식으로 되돌림 (쓰기 10~30초 멈춤). Enter (중단: Ctrl+c) " _

echo "② $REPLICA: Patroni 정상 종료 — 확실히 멈춘 것을 확인해야 다음으로 (살아 있으면 ③ 에서 승격돼 Primary 두 대)"
if on "$REPLICA" true 2>/dev/null; then
  on "$REPLICA" "sudo systemctl disable --now patroni; sleep 2"
  st=$(on "$REPLICA" 'echo "$(systemctl is-active patroni) $(pgrep -u postgres -xc postgres)"')
  echo "   $REPLICA: patroni · postgres 프로세스 = $st"
  [ "$st" = "inactive 0" ] || die "$REPLICA 의 Patroni · PostgreSQL 이 아직 살아 있음 — ssh $REPLICA 'sudo journalctl -u patroni -n 30'"
else
  echo "   ⚠ $REPLICA 에 접속할 수 없음. 켜진 채 망만 끊긴 것이면 ③ 에서 그쪽이 승격된다"
  echo "     PowerShell 에서 chargeops-$REPLICA 가 '전원 꺼짐' 인지 확인 (VBoxManage list runningvms 에 없어야 함)"
  read -rp "     꺼진 것을 확인했으면 Enter (중단: Ctrl+c) " _
fi

echo "③ $LEADER: Patroni 정상 종료 (PostgreSQL 도 같이 내려감 — 지금부터 쓰기 멈춤)"
on "$LEADER" "sudo systemctl disable --now patroni; sleep 2; echo \"   patroni \$(systemctl is-active patroni) · postgres \$(pgrep -u postgres -xc postgres)개\""
[ "$(on "$LEADER" 'pgrep -u postgres -xc postgres')" = 0 ] || die "$LEADER 에 PostgreSQL 이 남아 있음 — ssh $LEADER 'sudo journalctl -u patroni -n 30'"

echo "④ 두 서버: 설정 파일 원래대로 (Patroni 의 postgresql.conf → postgresql.conf.patroni, postgresql.base.conf → postgresql.conf)"
for h in "$LEADER" "$REPLICA"; do
  on "$h" "sudo -u postgres bash -c 'cd $PGDATA && if [ -f postgresql.base.conf ]; then mv postgresql.conf postgresql.conf.patroni && mv postgresql.base.conf postgresql.conf; fi; \
    rm -f patroni.dynamic.json'; sudo systemctl unmask postgresql-17 >/dev/null 2>&1; echo \"   $h: \$(sudo -u postgres grep -c include_dir $PGDATA/postgresql.conf)개 include_dir · postgresql-17 \$(systemctl is-enabled postgresql-17)\"" \
    || echo "   $h 접속 안 됨 — 켜진 뒤 이 단계만 손으로 (문서 7장)"
done

echo "⑤ $LEADER: postgresql-17 로 시작 (Primary) + PgBouncer restart → 쓰기 확인"
on "$LEADER" "sudo -u postgres rm -f $PGDATA/standby.signal; sudo systemctl enable --now postgresql-17 && sudo systemctl restart pgbouncer" \
  || die "$LEADER 의 postgresql-17 시작 실패 — ssh $LEADER 'sudo journalctl -u postgresql-17 -n 30 --no-pager'"
OK=
for _ in $(seq 1 30); do psql "$APP" -w -XAt -c 'select txid_current()' >/dev/null 2>&1 && { OK=1; echo "   쓰기 재개"; break; }; sleep 1; done
[ -n "$OK" ] || die "30초 안에 쓰기가 돌아오지 않음 — ssh $LEADER 'systemctl status postgresql-17 pgbouncer --no-pager'"

echo "⑥ Patroni 가 쓰던 복제 슬롯 정리"
on "$LEADER" "sudo -u postgres psql -XAtc \"select '   슬롯 ' || slot_name || ' 정리' from (select slot_name, pg_drop_replication_slot(slot_name)
  from pg_replication_slots where slot_name in ('db_01','db_02') and not active) s\""

echo "⑦ etcd 안의 '$SCOPE' 기록 지움 (다시 convert.sh 할 때 깨끗하게)"
on "$LEADER" "/usr/local/bin/etcdctl --endpoints=http://127.0.0.1:2379 del --prefix /service/$SCOPE/" | sed 's/^/   지운 키 /'

echo "✅ 되돌림 완료 — $LEADER = Primary (postgresql-17). 다음(사용자 확인 뒤):"
echo "   OLD=$REPLICA NEW=$LEADER bash db/ops/failover/rejoin.sh      ← $REPLICA 를 Standby 로 (zz-standby.conf · 슬롯 ${REPLICA//-/} 를 다시 만듦)"
echo "   평소 방향(db-01 Primary)이 아니면 그다음: OLD=$LEADER NEW=$REPLICA bash db/ops/failover/switchover.sh"
