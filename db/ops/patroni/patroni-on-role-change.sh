#!/bin/bash
# /usr/local/sbin/patroni-on-role-change.sh — Patroni 콜백 (on_start · on_stop · on_role_change). 인자: <동작> <역할> <클러스터 이름>
#   이 서버의 PgBouncer 를 PostgreSQL 상태에 맞춘다. postgres 계정으로 돈다 (sudo 는 /etc/sudoers.d/patroni-pgbouncer 의 3개 명령만)
#   ① PostgreSQL 이 내려가면(on_stop) PgBouncer 를 멈춘다
#      — 켜 두면 앱 접속을 받아 놓고 서버를 최대 120초 기다려서, 앱이 다음 주소(다른 DB 서버)로 넘어가지 못한다
#        (2026-10-16 drill isolate: 강등된 db-01 의 PgBouncer 가 앱을 붙잡음)
#   ② 뜨거나 역할이 바뀌면 PostgreSQL 이 그 역할이 될 때까지(최대 60초) 기다린 뒤 PgBouncer restart
#      — Patroni 는 승격 명령 직후(아직 읽기 전용일 때) 콜백을 부른다. 그때 restart 하면 PgBouncer 1.26 이 '읽기 전용' 을
#        기억해 앱 쓰기가 계속 거절된다 (2026-10-16 drill: db-02 승격 뒤 쓰기 실패 지속). reload 가 아니라 restart 인 이유도 같음
action=${1:-?} role=${2:-?} scope=${3:-?}
log() { logger -t patroni-callback "$scope: $*"; }
state() { psql -h /run/postgresql -p 5432 -d postgres -XAtqc 'select pg_is_in_recovery()' 2>/dev/null; }   # f=Primary t=Standby 빈값=내려감
pgb() { sudo -n /usr/bin/systemctl "$1" pgbouncer; }

if [ "$action" = on_stop ]; then
  pgb stop && log "$action ($role) — PostgreSQL 내려감 → PgBouncer stop (앱은 다른 주소로)" || log "PgBouncer stop 실패"
  exit 0
fi
case "$role" in
  primary|promoted|master)        want=f ;;
  replica|standby|standby_leader) want=t ;;
  *)                              want=any ;;
esac
s=
for _ in $(seq 1 60); do
  s=$(state)
  if [ -n "$s" ] && { [ "$want" = any ] || [ "$s" = "$want" ]; }; then break; fi
  sleep 1
done
if [ -z "$s" ]; then
  pgb stop; log "$action → $role — 60초 안에 PostgreSQL 응답 없음 → PgBouncer stop"
  exit 0
fi
pgb restart && log "$action → $role — PostgreSQL $( [ "$s" = f ] && echo Primary || echo Standby ) 확인 → PgBouncer restart 완료" \
            || log "$action → $role — PgBouncer restart 실패 (손으로: sudo systemctl restart pgbouncer)"
