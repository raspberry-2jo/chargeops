#!/usr/bin/env bash
# ============================================================================
# 자동 장애 전환 시험 (mgmt 에서, 사용자 승인 뒤):  MODE=<아래 넷 중 하나> bash db/ops/patroni/drill.sh
#   2026-10-16 시험 클러스터(5433)에서 한 시험을 운영 DB(5432 · 앱 경로 6432)에서 다시 한다. 대상은 지금 Leader
#   MODE=poweroff      서버째 꺼짐   — 사람이 PowerShell 에서 VM 전원을 끈다(스크립트가 명령을 보여 줌) → 자동 승격
#   MODE=freeze-patroni 감시가 멈춤 — Leader 의 Patroni 를 얼림(SIGSTOP, 멈춘 프로그램 흉내) → watchdog 이 약 25초 뒤 그 VM 재부팅
#                       → 그다음 Replica 승격 (2026-10-16 시험 ③ 과 같은 상황: PostgreSQL 은 살아 있는데 감시가 없음)
#   MODE=kill-all      프로세스 장애 — Patroni + PostgreSQL 강제 종료 → systemd 가 5초 뒤 Patroni 를 다시 띄움.
#                       Leader 열쇠(ttl 30초)가 남아 있으면 같은 서버가 스스로 복구, 아니면 Replica 승격 — 둘 다 PASS
#   MODE=isolate       망 고립      — Leader 에서 etcd(2379)로 나가는 연결 차단 → 스스로 강등 (ISOLATE_SEC 뒤 자동 해제)
#   측정: 앱과 같은 길(6432, 두 주소 + read-write)로 0.5초마다 쓰기 확인(txid_current — 표에는 아무것도 쓰지 않음)
#         + 두 서버에 직접(5432, dba) 'Primary 인가' 를 1초마다 → 같은 순간 Primary 두 대였는지
#   판정 PASS: 쓰기 받는 서버가 한 번만 바뀌고(옛 서버로 돌아가지 않음) Primary 두 대가 한 번도 없음
#        PASS (스스로 복구): Replica 승격 없이 옛 Leader 가 다시 쓰기를 받음 + Primary 두 대 0회
#          kill-all = systemd 재시작 · freeze-patroni = watchdog 재부팅이 확인된 경우만
#          (2026-10-17 운영 시험: 옛 Leader 가 etcd 리더도 겸하면 etcd 리더 교체 때 Leader 열쇠가 ttl 만큼 연장 →
#           재부팅된 옛 Leader 가 먼저 돌아와 열쇠를 다시 잡음. 안전하고 데이터 손실 없음 — 문서 11장)
#   쓰기 멈춘 시간 = 옛 Leader 마지막 쓰기 → 장애 뒤 첫 쓰기(어느 서버든). poweroff 의 '장애 시각' 은 Enter 를 누른 시각이라
#                   실제보다 몇 초 늦을 수 있어 이 값을 기준으로 본다
#   기록: ~/patroni-drill-<MODE>-<시각>.log (쓰기) · .roles.log (역할)
#   재부팅된 서버는 Patroni 가 부팅 때 저절로 떠서 Replica 로 되감겨 붙는다 → 끝나고 bash db/ops/patroni/status.sh
#   비밀번호: ~/.pgpass 의 app_writer(6432) · dba(5432) 줄
# ============================================================================
set -uo pipefail
MODE=${MODE:-}; ISOLATE_SEC=${ISOLATE_SEC:-60}; WATCH_SEC=${WATCH_SEC:-90}
PCTL="sudo -u postgres patronictl -c /etc/patroni/patroni.yml"
APP="host=db-01,db-02 port=6432 dbname=chargeops user=app_writer target_session_attrs=read-write connect_timeout=2"
STAMP=$(date +%Y%m%d-%H%M%S); LOG=~/patroni-drill-$MODE-$STAMP.log; RLOG=${LOG%.log}.roles.log
on() { ssh -n -o ConnectTimeout=5 -o BatchMode=yes "$@"; }
now() { date +%T.%3N; }
etcd_leader() {   # etcd 리더 서버 이름 (Leader 열쇠 연장 여부를 읽는 데 씀)
  local h ip; for h in db-01 db-02; do
    ip=$(on "$h" '/usr/local/bin/etcdctl --endpoints=http://127.0.0.1:2379 endpoint status --cluster -w simple 2>/dev/null' 2>/dev/null \
         | awk -F', ' '$5=="true"{sub(/^http:\/\//,"",$1); sub(/:.*/,"",$1); print $1}') && [ -n "$ip" ] && break
  done
  for h in db-01 db-02 mgmt; do [ -n "$ip" ] && getent hosts "$h" | grep -qw "$ip" && { echo "$h"; return; }; done   # IP 는 적지 않고 이름으로
  echo "${ip:-?}"
}
die() { echo "중단 — $*"; exit 1; }

case "$MODE" in poweroff|freeze-patroni|kill-all|isolate) ;; *) awk 'NR>2 && /^# ====/{exit} NR>2' "$0"; exit 1 ;; esac
if [ "${CO_PROFILE:-}" = server ] && [ "${DRILL_ON_SERVER:-}" != yes ]; then
  die "멘토 서버(CO_PROFILE=server)에서는 DRILL_ON_SERVER=yes 를 붙여야 실행 (빠른 실행표 30 — 사용자 확인 뒤)"
fi
for h in db-01 db-02; do
  grep -q "^$h:6432:chargeops:app_writer:" ~/.pgpass 2>/dev/null || die "~/.pgpass 에 $h:6432 app_writer 줄 없음"
  grep -q "^$h:5432:\*:dba:" ~/.pgpass 2>/dev/null || die "~/.pgpass 에 $h:5432 dba 줄 없음"
done

J=
for h in db-01 db-02; do J=$(on "$h" "$PCTL list -f json" 2>/dev/null) && break; done
[ -n "$J" ] || die "Patroni 상태를 읽지 못함 (bash db/ops/patroni/status.sh)"
LEADER=$(echo "$J" | jq -r '.[] | select(.Role == "Leader" and .State == "running") | .Member')
REPLICA=$(echo "$J" | jq -r '.[] | select(.Role == "Replica" and .State == "streaming") | .Member')
[ -n "$LEADER" ] && [ -n "$REPLICA" ] || { on db-01 "$PCTL list" 2>/dev/null; die "Leader running + Replica streaming 이 아님 — 정상 상태에서만 시험"; }
WD=$(on "$LEADER" 'sudo find /proc/[0-9]*/fd -lname /dev/watchdog 2>/dev/null | wc -l')
[ "$WD" -ge 1 ] || die "$LEADER 에서 watchdog 을 잡은 프로세스가 없음 — Patroni 설정 확인(watchdog: required)"

echo "대상: Leader = $LEADER → 기대: $REPLICA 가 자동 승격"
EL=$(etcd_leader)
if [ "$EL" = "$LEADER" ]; then
  echo "  etcd 리더: $EL (DB Leader 와 같은 서버) — 서버째 멈추는 시험(poweroff · freeze-patroni)은 etcd 리더 교체로"
  echo "             Leader 열쇠가 약 30초 연장되어 전환이 늦어지거나, 재부팅된 $LEADER 가 다시 Leader 가 될 수 있음"
else
  echo "  etcd 리더: $EL"
fi
case "$MODE" in
  poweroff)     echo "  사람이 할 일: 아래 Enter 뒤 안내가 나오면 PowerShell 에서 $LEADER VM 전원 끄기" ;;
  freeze-patroni) echo "  ⚠ $LEADER 의 Patroni 를 얼림 → 약 25초 뒤 $LEADER VM 이 watchdog 으로 재부팅됨" ;;
  kill-all)     echo "  ⚠ $LEADER 의 Patroni + PostgreSQL 강제 종료 → 같은 서버가 스스로 복구하거나 Replica 승격" ;;
  isolate)      echo "  $LEADER 에서 etcd(2379)로 나가는 연결을 ${ISOLATE_SEC}초 막음 (runtime 규칙, 끝나면 자동 해제)" ;;
esac
echo "  쓰기는 수십 초 멈춘다. 운영 앱(CSMS)은 재시도로 다시 붙는다"
read -rp "진행하려면 Enter (중단: Ctrl+c) " _

# --- 측정 시작 ------------------------------------------------------------------
( while :; do
    o=$(printf '%s\n' 'select txid_current();' '\echo :HOST' | timeout 4 psql "$APP" -w -XAt 2>&1); w=$(echo "$o" | tail -1)
    case "$w" in db-0*) ;; *) w="실패 $(echo "$o" | grep -m1 -oE '(FATAL|ERROR|error):.*' | cut -c1-80)"; [ "$w" = "실패 " ] && w="실패 (4초 응답 없음)" ;; esac
    echo "$(now) $w"; sleep 0.5
  done ) > "$LOG" 2>&1 &
WPID=$!
( while :; do
    r=; for h in db-01 db-02; do
      x=$(timeout 3 psql "host=$h port=5432 dbname=postgres user=dba connect_timeout=2" -w -XAtc 'select pg_is_in_recovery()' 2>/dev/null)
      r="$r $h=${x:--}"
    done
    echo "$(now)$r"; sleep 1
  done ) > "$RLOG" 2>&1 &
RPID=$!
UNBLOCK=
cleanup() {
  kill "$WPID" "$RPID" 2>/dev/null
  if [ -n "$UNBLOCK" ]; then on "$LEADER" "sudo firewall-cmd --direct --remove-rule ipv4 filter OUTPUT 0 -p tcp --dport 2379 -j REJECT" >/dev/null 2>&1 && echo "(차단 해제됨)"; fi
}
trap cleanup EXIT INT TERM
sleep 5
grep -q " $LEADER" "$LOG" || die "시험 전 쓰기가 $LEADER 로 가지 않음 — $LOG 확인"

# --- 장애 만들기 ------------------------------------------------------------------
case "$MODE" in
  poweroff)
    echo; echo "  ▶ 지금 PowerShell(영수 PC 또는 멘토 서버)에서:  VBoxManage controlvm chargeops-$LEADER poweroff"
    read -rp "  전원을 끈 직후 Enter " _; TK=$(now); TKE=$(date +%s) ;;
  freeze-patroni)
    TK=$(now); TKE=$(date +%s); on "$LEADER" 'sudo pkill -STOP -f "[p]atroni /etc/patroni/patroni.yml"; sleep 1; echo "  Patroni 상태 $(pgrep -f "[p]atroni /etc/patroni" | head -1 | xargs -r ps -o stat= -p) (T = 얼어 있음) · postgres $(pgrep -u postgres -xc postgres)개 (살아 있어야 이 시험의 의미가 있음)"' ;;
  kill-all)
    TK=$(now); TKE=$(date +%s); on "$LEADER" 'sudo pkill -9 -f "[p]atroni /etc/patroni/patroni.yml"; sudo pkill -9 -u postgres -x postgres; sleep 1; echo "  남은 patroni $(pgrep -fc "[p]atroni /etc/patroni")개 · postgres $(pgrep -u postgres -xc postgres)개"' ;;
  isolate)
    TK=$(now); TKE=$(date +%s); UNBLOCK=1
    on "$LEADER" "sudo firewall-cmd --direct --add-rule ipv4 filter OUTPUT 0 -p tcp --dport 2379 -j REJECT" >/dev/null || die "차단 규칙 추가 실패"
    WATCH_SEC=$((ISOLATE_SEC + 30)) ;;
esac
echo "  장애 시각 $TK — ${WATCH_SEC}초 지켜봄"
if [ "$MODE" = isolate ]; then
  sleep "$ISOLATE_SEC"; on "$LEADER" "sudo firewall-cmd --direct --remove-rule ipv4 filter OUTPUT 0 -p tcp --dport 2379 -j REJECT" >/dev/null; UNBLOCK=
  echo "  $(now) 차단 해제 → $LEADER 는 Replica 로 되감겨 붙어야 함"; sleep 30
else
  sleep "$WATCH_SEC"
fi
kill "$WPID" "$RPID" 2>/dev/null; trap - EXIT INT TERM

# --- 판정 -----------------------------------------------------------------------
echo; echo "== 쓰기 받는 서버가 바뀐 순서 (시각 · 서버)"
awk '{h=($2 ~ /^db-0/)?$2:"실패"; if(h!=p){print "  " $1, h; p=h}}' "$LOG"
SEQ=$(awk '{h=($2 ~ /^db-0/)?$2:"실패"; if(h!=p){printf "%s ", h; p=h}}' "$LOG")
FAIL1=$(awk '$2 !~ /^db-0/{print $1; exit}' "$LOG")                                   # 첫 실패 (poweroff 는 Enter 보다 앞설 수 있음)
LAST_OLD=$(awk -v o="$LEADER" -v f="${FAIL1:-99}" '$1<f && $2==o{t=$1} END{print t}' "$LOG")   # 장애 직전 옛 Leader 마지막 쓰기
RESUME=$( [ -n "$FAIL1" ] && awk -v f="$FAIL1" '$1>f && $2 ~ /^db-0/{print $1, $2; exit}' "$LOG")  # 장애 뒤 첫 쓰기 (시각 서버)
FIRST_NEW=$(awk -v n="$REPLICA" '$2==n{print $1; exit}' "$LOG")
BACK=$(awk -v o="$LEADER" -v n="$REPLICA" '$2==n{s=1} s && $2==o{c++} END{print c+0}' "$LOG")
DUAL=$(grep -c 'db-01=f db-02=f' "$RLOG")
sec() { awk -F: '{print $1*3600+$2*60+$3}' <<<"$1"; }
GAP=$( [ -n "$RESUME" ] && [ -n "$LAST_OLD" ] && awk -v a="$(sec "$LAST_OLD")" -v b="$(sec "${RESUME% *}")" 'BEGIN{printf "%.1f", b-a}')
FROMK=$( [ -n "$RESUME" ] && awk -v a="$(sec "$TK")" -v b="$(sec "${RESUME% *}")" 'BEGIN{printf "%.1f", b-a}')
REBOOT=
if [ "$MODE" = freeze-patroni ]; then   # watchdog 재부팅 확인: 옛 Leader 의 부팅 시각이 장애 뒤인가
  BE=$(on "$LEADER" 'date -d "$(uptime -s)" +%s' 2>/dev/null)
  [ -n "$BE" ] && [ "$BE" -gt "$TKE" ] && REBOOT="$(date -d "@$BE" +%T) (장애 뒤 $((BE - TKE))초)"
fi
echo "== 결과 ($MODE)"
echo "  장애 시각              $TK$([ "$MODE" = poweroff ] && echo ' (Enter 기준 — 실제 꺼진 시각은 첫 실패 근처)')"
echo "  첫 실패                ${FAIL1:-없음}"
echo "  옛 Leader 마지막 쓰기   ${LAST_OLD:-없음} ($LEADER)"
echo "  장애 뒤 첫 쓰기         ${RESUME:-없음}"
[ "$MODE" = freeze-patroni ] && echo "  $LEADER 재부팅(watchdog)  ${REBOOT:-확인 안 됨}"
echo "  쓰기 멈춘 시간          ${GAP:-?}초 (옛 Leader 마지막 쓰기부터 · 장애 시각부터 ${FROMK:-?}초)"
echo "  새 Leader 뒤 옛 서버 쓰기 ${BACK}회 · 같은 순간 Primary 두 대 ${DUAL}회 · etcd 리더(시험 전) $EL"
SELF=$( [ -n "$RESUME" ] && [ "${RESUME#* }" = "$LEADER" ] && echo "${RESUME% *}")   # 장애 뒤 첫 쓰기가 옛 Leader (스스로 복구)
if [ -n "$FIRST_NEW" ] && [ "$BACK" = 0 ] && [ "$DUAL" = 0 ]; then
  echo "  판정: PASS — $REPLICA 자동 승격. 바뀐 순서: $SEQ"
elif [ "$MODE" = kill-all ] && [ -z "$FIRST_NEW" ] && [ -n "$SELF" ] && [ "$DUAL" = 0 ]; then
  echo "  판정: PASS (스스로 복구) — systemd 가 Patroni 를 다시 띄워 $LEADER 에서 $SELF 에 쓰기 재개. 바뀐 순서: $SEQ"
elif [ "$MODE" = freeze-patroni ] && [ -z "$FIRST_NEW" ] && [ -n "$SELF" ] && [ -n "$REBOOT" ] && [ "$DUAL" = 0 ]; then
  echo "  판정: PASS (재부팅 뒤 스스로 복구) — watchdog 이 $LEADER 를 재부팅, 열쇠가 남아 있어 $LEADER 가 $SELF 에 다시 Leader. 바뀐 순서: $SEQ"
  [ "$EL" = "$LEADER" ] && echo "        (시험 전 etcd 리더도 $LEADER — etcd 리더 교체로 열쇠가 연장된 경우. 안전 · 손실 없음)"
else
  echo "  판정: FAIL — 바뀐 순서: $SEQ (기록: $LOG · $RLOG)"
fi
echo "== 지금 상태"
on "$REPLICA" "$PCTL list" 2>&1 | grep -v '^$'
case "$MODE" in
  poweroff)     echo "다음: PowerShell 에서 VM 을 다시 켠다 (vagrant up $LEADER 또는 VBoxManage startvm chargeops-$LEADER --type headless)."
                echo "      격리 부팅 필요 없음 — Patroni 가 부팅 때 떠서 Leader 가 따로 있는 것을 보고 Replica 로 되감긴다. 2~3분 뒤 status.sh" ;;
  freeze-patroni) echo "다음: $LEADER 는 watchdog 으로 재부팅 중 → 2~3분 뒤 bash db/ops/patroni/status.sh 에서 $LEADER Replica streaming 확인"
                echo "      (재부팅이 안 됐다면 watchdog 이상 — 얼린 Patroni 풀기: ssh $LEADER 'sudo pkill -CONT -f \"[p]atroni /etc/patroni\"')" ;;
  kill-all)     echo "다음: bash db/ops/patroni/status.sh — 두 서버가 Leader running · Replica streaming 인지" ;;
  isolate)      echo "다음: bash db/ops/patroni/status.sh 에서 $LEADER Replica streaming 확인 (재부팅 없음)" ;;
esac
echo "원래대로(평소 Leader = db-01): TO=db-01 bash db/ops/patroni/switchover.sh"
echo "기록: $LOG · $RLOG"
