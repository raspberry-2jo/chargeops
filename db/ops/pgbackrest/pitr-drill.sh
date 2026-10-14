#!/usr/bin/env bash
# ============================================================================
# 특정 시각 복구(PITR) 훈련 — WBS 2.8 완료 기준 "특정 시각 복원 성공" + RTO 측정
#   실행 위치: 관리 서버(mgmt). 저장소 루트에서  bash db/ops/pgbackrest/pitr-drill.sh
#   운영 DB 는 건드리지 않는다: Standby 서버(기본 db-02)의 다른 폴더 · 다른 포트(5433)에 임시로 복원
#   ① 표시 행 BEFORE → 기준 시각 T → 표시 행 AFTER(T 뒤의 '실수'라고 가정)  ② T 시점으로 복원  ③ BEFORE 만 있으면 성공 + RTO
#   표시 행은 감사 기록(action_audit, step='pitr_marker')에 — 지울 수 없는 테이블이라 AFTER 행 자체를 '실수'로 본다
#
#   전제: 전체 백업이 하나 이상 있음(pgbackrest info) · mgmt 의 ~/.pgpass 에 app_writer(6432) 줄 ·
#         mgmt → 복원 서버 SSH(sudo) · 복원 서버에 /etc/pgbackrest/pgbackrest.conf
#   바꿀 수 있는 값: RESTORE_HOST(기본 db-02) · PITR_PORT(기본 5433) · DB_HOSTS(기본 db-01,db-02)
#                    PITR_REPO(비우면 pgBackRest 기본 = repo1 부터 찾음. AWS 원격 저장소로 복원 훈련은 PITR_REPO=2)
# ============================================================================
set -euo pipefail
RESTORE_HOST=${RESTORE_HOST:-db-02}
PITR_PORT=${PITR_PORT:-5433}
DB_HOSTS=${DB_HOSTS:-db-01,db-02}
PITR_REPO=${PITR_REPO:-}
case "$PITR_REPO" in ""|[1-9]) ;; *) echo "PITR_REPO 는 저장소 번호(예: 2)"; exit 1;; esac
REPO_OPT=${PITR_REPO:+--repo=$PITR_REPO}                                 # 비면 아무것도 안 붙음
REPO_TXT=${PITR_REPO:+repo$PITR_REPO}; REPO_TXT=${REPO_TXT:-기본(repo1 부터)}
APP="host=$DB_HOSTS port=6432 dbname=chargeops user=app_writer target_session_attrs=read-write connect_timeout=3"   # 앱과 같은 길 (지금 Primary 쪽)
DIR=/var/lib/pgsql/17/pitr; PG=/usr/pgsql-17/bin
case "$DIR" in */17/data|*/17/data/) echo "복원 폴더가 운영 데이터 폴더와 같음 — 중단"; exit 1;; esac
[ "$PITR_PORT" != 5432 ] || { echo "PITR_PORT 가 5432(운영)와 같음 — 중단"; exit 1; }

echo "$RESTORE_HOST 의 $DIR 을 지우고 포트 $PITR_PORT 에 복원합니다(저장소: $REPO_TXT). 운영 DB(5432)는 그대로입니다."
read -rp "계속하려면 yes: " ok; [ "$ok" = yes ] || { echo "취소"; exit 0; }

mark() { psql "$APP" -w -XAtc "INSERT INTO action_audit (idempotency_key, step, requested_by, result, reason)
             VALUES ('pitr-$1-' || extract(epoch FROM clock_timestamp())::bigint, 'pitr_marker', 'mgmt-script', 'executed', 'PITR-$1')
             RETURNING action_id"; }

mark BEFORE >/dev/null
sleep 2; T=$(date '+%Y-%m-%d %H:%M:%S%z'); echo "① 복구 기준 시각 T = $T"; sleep 2
mark AFTER >/dev/null
echo "② archive_timeout(60초)이 지나 T 이후 WAL 까지 S3 로 올라가길 기다림 (70초)"; sleep 70

T0=$(date +%s)
echo "③ $RESTORE_HOST 의 $DIR 에 T 시점으로 복원 (포트 $PITR_PORT)"
ssh "$RESTORE_HOST" "sudo -u postgres $PG/pg_ctl -D $DIR stop -m fast 2>/dev/null || true; sudo rm -rf $DIR && sudo install -d -o postgres -g postgres -m 700 $DIR"
ssh "$RESTORE_HOST" "sudo -u postgres pgbackrest --stanza=chargeops --pg1-path=$DIR --type=time --target='$T' \
  --target-action=promote --archive-mode=off $REPO_OPT restore"          # archive-mode=off: 복원본이 WAL 을 저장소에 올리지 않게
# 복원본은 Standby 가 아님: standby.signal 과 복제 접속 정보(zz-standby.conf, 재편입 뒤 생김)를 지워 운영 Primary 에 붙지 않게.
# 서버별 메모리 덮어쓰기(zz-local.conf)도 뺀다 (백업을 받은 서버의 값이 따라올 수 있음)
ssh "$RESTORE_HOST" "sudo -u postgres bash -c \"echo 'port = $PITR_PORT' >> $DIR/postgresql.auto.conf; rm -f $DIR/standby.signal $DIR/conf.d/zz-standby.conf $DIR/conf.d/zz-local.conf\""
# pg_cron · pgaudit 를 빼고 시작 → 복원본에서 예약 작업이 돌지 않게. 메모리는 작게
ssh "$RESTORE_HOST" "sudo -u postgres $PG/pg_ctl -D $DIR -l /tmp/pitr.log -o '-c shared_buffers=128MB -c shared_preload_libraries=pg_stat_statements' start -w -t 300"
# pg_ctl -w 는 "접속을 받기 시작"하면 돌아온다. hot_standby = on 이라 그때도 WAL 재생(T 까지)은 계속되는 중 →
# 바로 조회하면 표시 행이 아직 없는 중간 상태를 본다(실습 PC 2026-10-10: 0 rows). 승격(= T 까지 재생 끝)을 기다린다
echo "   T 까지 재생이 끝나 승격될 때까지 기다림 (최대 300초)"
done_ok=no
for _ in $(seq 1 300); do
  [ "$(ssh "$RESTORE_HOST" "sudo -u postgres psql -X -p $PITR_PORT -d postgres -Atc 'SELECT pg_is_in_recovery()'" 2>/dev/null)" = f ] && { done_ok=yes; break; }
  sleep 1
done
[ $done_ok = yes ] || echo "   ⚠ 300초 안에 승격되지 않음 — $RESTORE_HOST 의 $DIR/log/ 에서 recovery 줄 확인"
echo "④ 검증 (이번 BEFORE 는 있고 이번 AFTER 는 없어야 함. T 보다 앞선 지난 훈련 기록은 있어도 정상)"
ssh "$RESTORE_HOST" "sudo -u postgres psql -X -p $PITR_PORT chargeops -c \"SELECT action_id, created_at, reason FROM action_audit WHERE step = 'pitr_marker' ORDER BY action_id DESC LIMIT 4\""
echo "RTO(복원 시작 → T 까지 재생 · 승격) = $(( $(date +%s) - T0 ))초  /  T 직전의 PITR-BEFORE 만 있고 T 뒤의 PITR-AFTER 가 없으면 성공"
echo "정리(메모리 반납): ssh $RESTORE_HOST \"sudo -u postgres $PG/pg_ctl -D $DIR stop -m fast\""
