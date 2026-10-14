#!/usr/bin/env bash
# ============================================================================
# DR 복원 — 빈 새 서버에서 AWS S3 저장소(repo2)의 백업 · WAL 만으로 chargeops DB 를 되살린다 (WBS 4.5)
#   실행 위치: 새 서버(Rocky Linux 9) root. 저장소 루트에서  sudo bash db/ops/pgbackrest/dr-restore.sh
#   ① 패키지(PGDG: PostgreSQL 17 · 확장 3개 · pgBackRest 2.59 — versions.md)  ② pgBackRest 설정  ③ 저장소 확인(info)
#   ④ 복원(내려받기)  ⑤ 이 서버에 맞게 설정 정리  ⑥ 시작 → 마지막 WAL 까지 재생 → 승격  ⑦ 확인 + 걸린 시간(RTO)
#
#   DR_MODE=drill (기본) 훈련. 복원본은 저장소에 WAL 을 올리지 않는다(archive-mode=off). 운영 Primary 가 같은 저장소를 쓰는 중이라
#                        훈련본의 새 타임라인이 섞이면, 다음 복원(최신 타임라인을 따라감)이 운영이 아닌 훈련본 쪽으로 간다
#   DR_MODE=real         실제 재해. 운영 Primary 가 확실히 멈춘 뒤에만. 이 서버가 새 Primary 가 되어 같은 저장소에 WAL 을 이어 올린다
#   AWS 설정: /etc/pgbackrest/conf.d/repo2-aws.conf 가 있으면 그대로 쓰고(실습: mgmt 에서 미리 보냄), 없으면 물어본다
#             EC2 에 IAM 역할을 붙였으면 키 없이(역할 방식). 훈련에는 읽기 권한(ListBucket · GetObject)만 있으면 된다
#   바꿀 수 있는 값: DR_MODE · TARGET_TIME(비우면 최신. 예 '2026-10-14 13:00:00+09') · REPO(기본 2) · PG_SHARED_BUFFERS
#                    PGDATA · PGBIN · PG_SERVICE (PGDG 기본 경로가 아닐 때만)
#   순서 · 결과 · 정리: docs/setup/dr-restore.md
# ============================================================================
set -euo pipefail
STANZA=chargeops
DB=chargeops
DR_MODE=${DR_MODE:-drill}
REPO=${REPO:-2}
TARGET_TIME=${TARGET_TIME:-}
PGDATA=${PGDATA:-/var/lib/pgsql/17/data}
PGBIN=${PGBIN:-/usr/pgsql-17/bin}
PG_SERVICE=${PG_SERVICE:-postgresql-17}
PG_SHARED_BUFFERS=${PG_SHARED_BUFFERS:-}
CONF_DIR=/etc/pgbackrest
AWS_CONF=$CONF_DIR/conf.d/repo$REPO-aws.conf
PKGS=(postgresql17-server postgresql17-contrib pgvector_17 pg_cron_17 pgaudit_17 'pgbackrest-2.59*')

[ "$(id -u)" -eq 0 ] || { echo "root 로 실행: sudo bash $0"; exit 1; }
case "$DR_MODE" in drill|real) ;; *) echo "DR_MODE 는 drill 또는 real"; exit 1;; esac
case "$REPO" in [1-9]) ;; *) echo "REPO 는 저장소 번호(예: 2)"; exit 1;; esac
pg()  { runuser -u postgres -- "$@"; }
sec() { echo $(( $(date +%s) - $1 )); }

# 빈 서버에만: 데이터 폴더에 무엇이 있거나 PostgreSQL 이 돌고 있으면 멈춘다 (지울지는 사람이 판단 — 운영 DB 서버 보호)
if [ -n "$(ls -A "$PGDATA" 2>/dev/null)" ]; then echo "$PGDATA 가 비어 있지 않음 — 빈 서버에서만 실행"; exit 1; fi
if systemctl is-active --quiet "$PG_SERVICE" 2>/dev/null; then echo "$PG_SERVICE 가 실행 중 — 빈 서버에서만 실행"; exit 1; fi

echo "== DR 복원 ($DR_MODE): $(hostname -s) 의 $PGDATA ← repo$REPO(AWS), ${TARGET_TIME:-마지막 WAL 까지(최신)}"
if [ "$DR_MODE" = real ]; then
  echo "   실제 재해 모드 — 이 서버가 새 Primary 가 되어 같은 저장소에 WAL 을 올린다."
  echo "   운영 Primary 가 멈춘 것을 확인했을 때만. 두 Primary 가 한 저장소에 올리면 복원 기준이 꼬인다"
  read -rp "   확인했으면 DR 입력: " ok; [ "$ok" = DR ] || { echo "취소"; exit 0; }
else
  echo "   훈련 모드 — 복원본은 저장소에 아무것도 올리지 않는다(archive-mode=off). 운영 DB · 저장소는 그대로"
  read -rp "   계속하려면 yes: " ok; [ "$ok" = yes ] || { echo "취소"; exit 0; }
fi

T0=$(date +%s)
echo "① 패키지 (PGDG)"
if [ -x "$PGBIN/postgres" ] && command -v pgbackrest >/dev/null; then
  echo "   이미 있음 — 건너뜀"
else
  echo "   설치 중 (인터넷 속도에 따라 1~3분)"
  dnf install -y -q dnf-plugins-core epel-release
  dnf config-manager --set-enabled crb                       # EPEL · CRB: pgBackRest 등이 쓰는 라이브러리 (가이드 5.1)
  rpm -q pgdg-redhat-repo >/dev/null || dnf install -y -q https://download.postgresql.org/pub/repos/yum/reporpms/EL-9-x86_64/pgdg-redhat-repo-latest.noarch.rpm
  dnf -qy module disable postgresql                          # 기본 저장소의 옛 postgresql 이 PGDG 를 가리지 않게
  dnf install -y -q "${PKGS[@]}"
fi
echo "   $("$PGBIN/postgres" --version) · $(pgbackrest version) — $(sec "$T0")초"
R_PKG=$(sec "$T0")

echo "② pgBackRest 설정"
install -d -m 750 "$CONF_DIR" "$CONF_DIR/conf.d"
[ -s "$CONF_DIR/pgbackrest.conf" ] || printf '[global]\ncompress-type=zst\nprocess-max=2\nlog-level-console=info\n\n[%s]\npg1-path=%s\n' "$STANZA" "$PGDATA" > "$CONF_DIR/pgbackrest.conf"
if [ ! -s "$AWS_CONF" ]; then
  echo "   $AWS_CONF 가 없음 → AWS 저장소 정보를 입력 (키는 화면 · 셸 기록에 남지 않음)"
  read -rp "   백업 버킷 이름: " B; [ -n "$B" ] || { echo "빈 값 — 중단"; exit 1; }
  read -rp "   저장소 경로 [/test-chargeops]: " P
  read -rp "   키 방식 — 1: 액세스 키  2: 이 EC2 에 붙인 IAM 역할 [1]: " K
  TMP=$(mktemp); trap 'rm -f "$TMP"' EXIT; chmod 600 "$TMP"
  printf '[global]\nrepo%s-type=s3\nrepo%s-s3-endpoint=s3.ap-northeast-2.amazonaws.com\nrepo%s-s3-region=ap-northeast-2\nrepo%s-s3-bucket=%s\nrepo%s-path=%s\nrepo%s-retention-full=2\n' \
    "$REPO" "$REPO" "$REPO" "$REPO" "$B" "$REPO" "${P:-/test-chargeops}" "$REPO" > "$TMP"
  if [ "${K:-1}" = 2 ]; then
    printf 'repo%s-s3-key-type=auto\n' "$REPO" >> "$TMP"
  else
    read -rsp "   Access key ID (화면에 안 보임): " AK; echo
    read -rsp "   Secret access key (화면에 안 보임): " SK; echo
    [ -n "$AK" ] && [ -n "$SK" ] || { echo "빈 값 — 중단"; exit 1; }
    printf 'repo%s-s3-key=%s\nrepo%s-s3-key-secret=%s\n' "$REPO" "$AK" "$REPO" "$SK" >> "$TMP"; unset AK SK
  fi
  install -m 640 "$TMP" "$AWS_CONF"
fi
chgrp -R postgres "$CONF_DIR"; chmod 640 "$CONF_DIR/pgbackrest.conf" "$CONF_DIR"/conf.d/*.conf   # 키가 있어 postgres 그룹만
grep -hE "^repo$REPO-(type|s3-endpoint|s3-region|path|s3-key-type)=" "$AWS_CONF" | sed 's/^/   /' || true   # 버킷 · 키 줄은 화면에 내지 않음

T1=$(date +%s)
echo "③ 저장소 확인 (repo$REPO)"
pg pgbackrest --stanza="$STANZA" --repo="$REPO" info
R_INFO=$(sec "$T1")

echo "④ 복원 (내려받기)"
install -d -o postgres -g postgres -m 700 "$PGDATA"
opts=(--stanza="$STANZA" --repo="$REPO" --pg1-path="$PGDATA")
[ -n "$TARGET_TIME" ] && opts+=(--type=time "--target=$TARGET_TIME" --target-action=promote)
[ "$DR_MODE" = drill ] && opts+=(--archive-mode=off)
T2=$(date +%s)
pg pgbackrest "${opts[@]}" restore
R_DL=$(sec "$T2")
echo "   $R_DL초"

echo "⑤ 이 서버에 맞게: 복제 설정 · 원래 서버의 메모리 설정 빼기"
# 백업을 받은 서버가 예전에 Standby 였으면 복제 접속 정보가 따라온다 → 이 서버는 혼자 서는 Primary
rm -f "$PGDATA/standby.signal" "$PGDATA/conf.d/zz-standby.conf" "$PGDATA/conf.d/zz-local.conf"
sed -i -E '/^(primary_conninfo|primary_slot_name)[[:space:]]*=/d' "$PGDATA/postgresql.auto.conf"
mem_mb=$(awk '/^MemTotal:/ {print int($2 / 1024)}' /proc/meminfo)
sb=$PG_SHARED_BUFFERS
[ -z "$sb" ] && [ $(( mem_mb / 4 )) -lt 1024 ] && sb="$(( mem_mb / 4 ))MB"      # 설정 파일의 1GB 는 메모리 4GB 기준
if [ -n "$sb" ]; then
  line="shared_buffers = $sb    # DR 서버 메모리 ${mem_mb}MB 에 맞춤 (dr-restore.sh)"
  if [ -d "$PGDATA/conf.d" ]; then echo "$line" > "$PGDATA/conf.d/zz-local.conf"; chown postgres:postgres "$PGDATA/conf.d/zz-local.conf"; chmod 600 "$PGDATA/conf.d/zz-local.conf"
  else echo "$line" >> "$PGDATA/postgresql.auto.conf"; fi
  echo "   $line"
fi
# 로그를 데이터 폴더 밖(/var/log/postgresql — 비밀번호가 백업에 섞이지 않게 옮김)에 쓰면 그 폴더는 백업에 없다
#   → 만들지 않으면 시작하자마자 FATAL "could not open log file" (실습 2026-10-14 첫 DR 훈련)
logdir=$(pg "$PGBIN/postgres" -D "$PGDATA" -C log_directory)
case "$logdir" in /*) logpath=$logdir ;; *) logpath=$PGDATA/$logdir ;; esac
case "$logdir" in
  /*) if [ ! -d "$logdir" ]; then
        install -d -o postgres -g postgres -m 700 "$logdir"
        command -v restorecon >/dev/null && restorecon -R "$logdir"    # SELinux 라벨 postgresql_log_t
        echo "   로그 폴더 $logdir 만듦 (원래 서버 설정 그대로)"
      fi ;;
esac

echo "⑥ 시작 → 마지막 WAL 까지 재생 → 승격"
T3=$(date +%s)
[ "$DR_MODE" = real ] && systemctl enable -q "$PG_SERVICE"           # 훈련본은 재부팅 때 저절로 켜지지 않게
systemctl reset-failed "$PG_SERVICE" 2>/dev/null || true              # 앞선 실패로 '너무 잦은 재시작' 상태면 풀기
if ! systemctl start "$PG_SERVICE"; then
  echo "   ⚠ 시작 실패 — 원인 줄:"
  journalctl -u "$PG_SERVICE" -n 30 --no-pager -o cat 2>/dev/null | grep -E 'FATAL|PANIC|ERROR' | tail -3 | sed 's/^/     /' || true
  echo "   (전체: journalctl -u $PG_SERVICE -n 30 · $logpath)"; exit 1
fi
# 접속을 받기 시작해도(hot_standby) WAL 재생은 계속되는 중 → 승격(= 재생 끝)을 기다린다 (pitr-drill.sh 와 같은 이유)
state=""
for _ in $(seq 1 900); do
  state=$(pg psql -XAtc 'SELECT pg_is_in_recovery()' 2>/dev/null || true)
  [ "$state" = f ] && break
  sleep 1
done
R_REPLAY=$(sec "$T3")
[ "$state" = f ] || { echo "   ⚠ 900초 안에 승격되지 않음 — journalctl -u $PG_SERVICE 와 $logpath 의 recovery 줄 확인"; exit 1; }
echo "   $R_REPLAY초"

echo "⑦ 확인"
pg psql -X -d "$DB" -P footer=off <<'SQL'
SELECT pg_is_in_recovery() AS "복구 중",
       (SELECT timeline_id FROM pg_control_checkpoint()) AS "타임라인",
       to_char(pg_last_xact_replay_timestamp(), 'MM-DD HH24:MI:SS') AS "되살린 마지막 트랜잭션",
       to_char(now(), 'MM-DD HH24:MI:SS') AS "지금",
       current_setting('archive_mode') AS "WAL 보관";
SELECT (SELECT count(*) FROM charger) AS "충전기",
       (SELECT count(*) FROM status_history) AS "상태 이력",
       to_char((SELECT max(received_at) FROM status_history), 'MM-DD HH24:MI:SS') AS "이력 마지막 적재",
       to_char((SELECT max(started_at) FROM collect_log), 'MM-DD HH24:MI:SS') AS "수집 마지막 회차";
SELECT string_agg(extname || ' ' || extversion, ', ' ORDER BY extname) AS "확장",
       current_setting('shared_preload_libraries') AS "미리 올린 라이브러리" FROM pg_extension;
SQL
if [ "$DR_MODE" = real ]; then
  echo "   새 Primary 의 WAL 이 저장소에 올라가는지"
  pg pgbackrest --stanza="$STANZA" check
fi

echo "== 걸린 시간: 패키지 ${R_PKG}초 · 저장소 확인 ${R_INFO}초 · 내려받기 ${R_DL}초 · 재생 · 승격 ${R_REPLAY}초 = 합계 $(sec "$T0")초"
echo "   (실제 DR 의 RTO = 서버 준비(EC2 생성) + 이 합계 + 앱 접속 전환. '되살린 마지막 트랜잭션' 과 사고 시각의 차이가 RPO)"
if [ "$DR_MODE" = drill ]; then
  echo "정리: systemctl stop $PG_SERVICE  — 다시 훈련하려면 $PGDATA 를 비운다(지우는 것은 사람이 판단). 순서는 docs/setup/dr-restore.md"
else
  echo "다음: pg_hba.conf 에 새 앱 대역 → PgBouncer → 앱 접속 주소를 이 서버로 → 전체 백업(--repo=$REPO --type=full) → Standby 다시 만들기 (docs/setup/dr-restore.md)"
fi
