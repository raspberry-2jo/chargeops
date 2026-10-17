#!/usr/bin/env bash
# Patroni 클러스터 상태 한눈에 (mgmt 에서, 읽기만):  bash db/ops/patroni/status.sh
#   ① 구성원 표 ② 서버별 서비스 · watchdog ③ etcd ④ 복제 ⑤ 앱 경로 쓰기 ⑥ 최근 역할 변경 기록
set -uo pipefail
PCTL="sudo -u postgres patronictl -c /etc/patroni/patroni.yml"
APP="host=db-01,db-02 port=6432 dbname=chargeops user=app_writer target_session_attrs=read-write connect_timeout=3"
on() { ssh -n -o ConnectTimeout=5 -o BatchMode=yes "$@"; }
ANY=
for h in db-01 db-02; do on "$h" true 2>/dev/null && { ANY=$h; break; }; done
[ -n "$ANY" ] || { echo "db-01 · db-02 모두 접속 안 됨"; exit 1; }

echo "== ① 구성원 (Leader = 쓰기 받는 서버, Replica streaming = 따라오는 중)"
on "$ANY" "$PCTL list" 2>&1 | grep -v '^$'

echo "== ② 서버별 (patroni · pgbouncer · watchdog 을 연 프로세스)"
for h in db-01 db-02; do
  printf '   %s: ' "$h"
  on "$h" 'echo "patroni=$(systemctl is-active patroni) pgbouncer=$(systemctl is-active pgbouncer) postgresql-17=$(systemctl is-enabled postgresql-17 2>/dev/null) watchdog=$(sudo find /proc/[0-9]*/fd -lname /dev/watchdog 2>/dev/null | wc -l)개"' 2>/dev/null || echo "접속 안 됨 (꺼졌거나 재부팅 중)"
done
echo "   (watchdog 1개 = Leader 가 잡고 있음. Replica 는 0 이 정상. postgresql-17 은 masked 가 정상)"

echo "== ③ etcd"
on "$ANY" '/usr/local/bin/etcdctl --endpoints=http://127.0.0.1:2379 endpoint health --cluster 2>&1' | sed 's/^/   /'
EL=$(on "$ANY" '/usr/local/bin/etcdctl --endpoints=http://127.0.0.1:2379 endpoint status --cluster -w simple 2>/dev/null' | awk -F', ' '$5=="true"{print $1}')
echo "   etcd 리더: ${EL:-?}  (DB Leader 와 같은 서버면, 그 서버가 통째로 죽을 때 전환이 약 30초 늦어질 수 있음 — 문서 11장)"

echo "== ④ 복제 (Leader 에서)"
for h in db-01 db-02; do
  on "$h" "sudo -u postgres psql -XAtc \"select '   ' || application_name || ' ' || state || ' 지연 ' || coalesce(replay_lag::text, '0') from pg_stat_replication\"" 2>/dev/null
done

echo "== ⑤ 앱 경로(6432, 쓰기 되는 쪽) 쓰기"
if out=$(printf '%s\n' 'select txid_current();' '\echo :HOST' | psql "$APP" -w -XAt 2>&1); then
  echo "   OK → $(echo "$out" | tail -1)"
else
  echo "   실패: $(echo "$out" | tail -1)"
fi

echo "== ⑥ 최근 역할 변경 (PgBouncer restart 콜백 기록)"
for h in db-01 db-02; do
  on "$h" "sudo journalctl -t patroni-callback --since '-1day' --no-pager -o short-iso 2>/dev/null | tail -3" 2>/dev/null | sed "s/^/   /"
done
