#!/usr/bin/env bash
# 계획된 역할 교대 (mgmt 에서, 사용자 승인 뒤):  TO=db-01 bash db/ops/patroni/switchover.sh
#   Patroni 가 지금 Leader 를 깨끗하게 내리고 TO 를 승격 → 옛 Leader 는 스스로 Replica 로 붙는다 (rejoin 불필요)
#   양쪽 PgBouncer 는 콜백이 restart. 예전 switchover.sh + rejoin.sh 를 대신한다
#   서버째 죽은 장애는 자동 전환이라 사람이 할 일이 없다. Leader 가 없는데 자동 승격이 안 되면(예: 지연 1MB 초과):
#     ssh db-0X "sudo -u postgres patronictl -c /etc/patroni/patroni.yml failover --candidate <서버> --force"  (데이터 손실 가능 — 판단 뒤)
set -uo pipefail
PCTL="sudo -u postgres patronictl -c /etc/patroni/patroni.yml"
APP="host=db-01,db-02 port=6432 dbname=chargeops user=app_writer target_session_attrs=read-write connect_timeout=3"
TO=${TO:-db-01}
on() { ssh -n -o ConnectTimeout=5 -o BatchMode=yes "$@"; }
J=$(on "$TO" "$PCTL list -f json") || { echo "$TO 접속 안 됨"; exit 1; }
LEADER=$(echo "$J" | jq -r '.[] | select(.Role == "Leader") | .Member')
STATE=$(echo "$J" | jq -r --arg m "$TO" '.[] | select(.Member == $m) | .State')
on "$TO" "$PCTL list"
[ "$LEADER" != "$TO" ] || { echo "$TO 가 이미 Leader — 할 일 없음"; exit 0; }
[ "$STATE" = streaming ] || { echo "중단 — $TO 가 streaming 이 아님 ('$STATE')"; exit 1; }
read -rp "$LEADER → $TO 로 역할 교대 (쓰기 수 초 멈춤). Enter (중단: Ctrl+c) " _
T0=$(date +%s.%N)
on "$TO" "$PCTL switchover --leader $LEADER --candidate $TO --force"
for _ in $(seq 1 60); do
  printf '%s\n' 'select txid_current();' '\echo :HOST' | psql "$APP" -w -XAt 2>/dev/null | tail -1 | grep -q "^$TO" && break; sleep 0.5
done
awk -v a="$T0" -v b="$(date +%s.%N)" 'BEGIN{printf "명령부터 %s 쓰기 확인까지 %.1f초\n", "'"$TO"'", b-a}'
sleep 15; on "$TO" "$PCTL list"
