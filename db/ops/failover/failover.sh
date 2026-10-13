#!/usr/bin/env bash
# ============================================================================
# DB 장애 전환 (mgmt 에서, Lead 승인 뒤):  bash db/ops/failover/failover.sh
#   구조: PgBouncer 가 db-01 · db-02 에 하나씩(각자 자기 서버의 PostgreSQL 로). 앱은 주소 두 개 +
#         target_session_attrs=read-write 로 접속해 "쓰기 되는 쪽"을 스스로 고른다 → 전환 때 앱 설정 · ini 를 바꾸지 않는다
#   ① 확인 → ② 펜싱(구 Primary 의 PgBouncer · PostgreSQL 중지 + 자동 시작 해제) → ③ 승격
#   → ④ 새 Primary 의 PgBouncer restart → ⑤ 쓰기 재개 확인 (감사 기록 action_audit 에 한 줄 — 훈련도 "조치" 라서)
#   ④ 가 reload 가 아니라 restart 인 이유: PgBouncer 1.26 은 reload 로 서버의 "읽기 전용" 상태 기억을 지우지 않는다
#      → 앱이 두 문 모두에서 'session is read-only' 로 거절당해 18분 멈췄다(실제 PC 2026-10-12). restart 하면 바로 풀린다
#   구 Primary 가 응답하지 않으면(서버째 다운): 전원이 꺼졌는지 사람이 확인한 뒤 진행한다.
#      그 서버를 다시 켤 때는 반드시 "격리 부팅"(docs/setup/postgres-failover.md 3장 ③) — 그냥 켜면 Primary 로 떠서, 앱이 첫 주소인 그쪽에 쓴다(Primary 둘)
#   비밀번호는 mgmt 의 ~/.pgpass (두 서버의 6432 app_writer 줄이 모두 있어야 함 — docs/setup/postgres-failover.md 2장)
#   변수: OLD = 구 Primary(기본 db-01), NEW = 승격할 Standby(기본 db-02), DB_HOSTS = 앱 주소 목록(기본 db-01,db-02)
#         OLD_IS_OFF=yes → 구 Primary 의 전원이 꺼진 것을 이미 확인했으면 Enter 확인을 건너뜀
#   계획된 역할 교대(둘 다 살아 있음, 원래대로 되돌리기)는 switchover.sh
# ============================================================================
set -uo pipefail
OLD=${OLD:-db-01}; NEW=${NEW:-db-02}; DOWN=
DB_HOSTS=${DB_HOSTS:-db-01,db-02}                                     # 앱 접속 문자열의 주소 목록 (앞이 평소 Primary)
APP="host=$DB_HOSTS port=6432 dbname=chargeops user=app_writer target_session_attrs=read-write connect_timeout=3"
T0=$(date +%s.%N)
ts() { awk -v t0="$T0" -v now="$(date +%s.%N)" -v m="$*" 'BEGIN{printf "[%6.1fs] %s\n", now-t0, m}'; }

[ "$OLD" != "$NEW" ] || { echo "OLD 와 NEW 가 같음 ($OLD)"; exit 1; }
for h in ${DB_HOSTS//,/ }; do                               # 없으면 ⑤ 에서 비밀번호를 물으며 멈춘다
  grep -q "^$h:6432:chargeops:app_writer:" ~/.pgpass 2>/dev/null \
    || { echo "$HOME/.pgpass 에 $h:6432 의 app_writer 줄이 없음 — docs/setup/postgres-failover.md 2장의 pgpass 줄을 먼저"; exit 1; }
done

ts "① $NEW 확인 (Standby 여야 함. 둘째 값 = 마지막으로 받은 쓰기가 몇 초 전인지)"
R=$(ssh -n -o ConnectTimeout=5 "$NEW" "sudo -u postgres psql -XAtc \"SELECT pg_is_in_recovery(), coalesce((now() - pg_last_xact_replay_timestamp())::text, '-')\"") \
  || { echo "$NEW 응답 없음 — 중단"; exit 1; }
echo "$R"
[ "${R%%|*}" = t ] || { echo "중단 — $NEW 의 상태가 Standby 가 아님(이미 Primary?). 방향 확인: OLD=… NEW=… bash $0"; exit 1; }

ts "② 펜싱: $OLD 의 PgBouncer · PostgreSQL 중지 + 자동 시작 해제 (Primary 가 둘이 되는 것을 막음)"
if ssh -n -o ConnectTimeout=5 -o BatchMode=yes "$OLD" true 2>/dev/null; then
  ssh -n "$OLD" "sudo systemctl disable --now pgbouncer postgresql-17 && systemctl is-active pgbouncer postgresql-17 | xargs" \
    | grep -qx 'inactive inactive' || { echo "중단 — $OLD 의 서비스를 멈추지 못함 (ssh $OLD 'systemctl status pgbouncer postgresql-17')"; exit 1; }
  ts "   $OLD 중지 완료 — 다시 켜져도 자동으로 뜨지 않음"
else
  DOWN=1
  ts "   ⚠ $OLD 에 접속할 수 없음 = 서버째 다운으로 봄"
  echo "     가상화 관리 화면(실습: VirtualBox 의 chargeops-$OLD)에서 '전원 꺼짐' 인지 확인 (켜져 있으면 먼저 강제로 끈다)"
  echo "     켜진 채로 망만 끊긴 것이면 승격하지 말 것 — 망이 돌아오는 순간 Primary 가 둘이 된다"
  if [ "${OLD_IS_OFF:-}" = yes ]; then echo "     OLD_IS_OFF=yes → 확인 생략"; else read -rp "     꺼진 것을 확인했으면 Enter (중단: Ctrl+c) " _; fi
fi

ts "③ $NEW 승격 (pg_promote)"
ssh -n "$NEW" "sudo -u postgres psql -XAtc 'SELECT pg_promote(wait => true, wait_seconds => 30)'" | grep -qx t \
  || { echo "승격 실패 — ssh $NEW 'sudo journalctl -u postgresql-17 -n 30'"; exit 1; }

ts "④ $NEW 의 PgBouncer restart (reload 로는 '읽기 전용' 기억이 남는다 — PgBouncer 1.26 실측)"
ssh -n "$NEW" "sudo systemctl enable pgbouncer >/dev/null 2>&1; sudo systemctl restart pgbouncer && systemctl is-active pgbouncer"

ts "⑤ 쓰기 재개 확인 (앱과 같은 접속 문자열: 두 주소 중 쓰기 되는 쪽)"
OK=
for _ in $(seq 1 20); do
  if OUT=$(psql "$APP" -w -XAt -c \
      "INSERT INTO action_audit (idempotency_key, step, requested_by, role, result, reason)
       VALUES ('drill-failover-' || extract(epoch FROM now())::bigint || '-$$', 'db_failover', 'mgmt-script', 'Lead', 'executed',
               '장애 전환 $OLD → $NEW')
       RETURNING action_id" -c '\conninfo' 2>&1); then OK=1; break; fi
  sleep 1
done
[ -n "$OK" ] || { echo "쓰기 재개 실패 (20회): $OUT"; exit 1; }
echo "action_id $(echo "$OUT" | head -1) · $(echo "$OUT" | grep -o 'on host "[^"]*"')"
ts "✅ 전환 완료 — 위 시간이 '승인 후 쓰기 재개' (목표 60초. 실제 PC 서버째 다운: 승격 명령 뒤 약 4초)"
if [ -n "$DOWN" ]; then
  echo "   $OLD 의 전원이 꺼져 있는 동안은 새 접속이 그 주소를 connect_timeout(3초)만큼 기다린 뒤 다음 주소로 간다 (${DB_HOSTS%%,*} 이 목록의 첫 주소)."
  echo "   앱 풀은 연결을 들고 있어 평소에는 영향이 없다. 격리 부팅으로 서비스를 끈 채 망에 붙이면 바로 '거부'가 와서 사라진다"
  echo "다음: docs/setup/postgres-failover.md 3장 ③ 격리 부팅(망을 끊고 켜서 두 서비스를 끈 뒤 망 연결) → OLD=$OLD NEW=$NEW bash db/ops/failover/rejoin.sh"
else
  echo "다음: OLD=$OLD NEW=$NEW bash db/ops/failover/rejoin.sh"
fi
