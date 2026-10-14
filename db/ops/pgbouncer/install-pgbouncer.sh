#!/usr/bin/env bash
# ============================================================================
# PgBouncer 설정 · 시작 — 각 DB 서버에서 root 로:  sudo bash db/ops/pgbouncer/install-pgbouncer.sh
#   Primary · Standby 둘 다 같은 명령. 앱은 두 서버의 6432 를 모두 알고, 쓰기 되는 쪽을 스스로 고른다
#   Primary 서버 이름이 db-01 이 아니면:  sudo PRIMARY_NAME=<이름> bash db/ops/pgbouncer/install-pgbouncer.sh
#   쓰기 풀 chargeops    = 자기 서버의 PostgreSQL(127.0.0.1) — Primary 든 Standby 든 그대로 (장애 전환 때 ini 를 고치지 않음)
#   읽기 풀 chargeops_ro = db-01 에서는 db-02(Standby), db-02 에서는 자기 자신(127.0.0.1)
#   이미 돌고 있으면 restart → 이 서버로 들어온 앱 연결이 한 번 끊긴다(앱 풀은 다시 붙음). 역할이 바뀐 뒤에도 restart 가 맞다
# ============================================================================
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "root 로 실행: sudo bash $0"; exit 1; }
HERE=$(cd "$(dirname "$0")" && pwd)
INI=/etc/pgbouncer/pgbouncer.ini
PRIMARY_NAME=${PRIMARY_NAME:-db-01}
command -v pgbouncer >/dev/null || { echo "먼저 패키지 설치: dnf install -y pgbouncer (PGDG, versions.md)"; exit 1; }
install -o pgbouncer -g pgbouncer -m 640 "$HERE/pgbouncer.ini" $INI
if [ "$(hostname -s)" != "$PRIMARY_NAME" ]; then
  sed -i -E 's/^(chargeops_ro *= *host=)[^ ]+/\1127.0.0.1/' $INI
fi
bash "$HERE/make-userlist.sh"                    # 인증 파일 (PostgreSQL 이 떠 있어야 함 — Standby 에서도 읽기는 됨)
systemctl enable pgbouncer >/dev/null 2>&1
systemctl restart pgbouncer
grep -E '^chargeops' $INI
echo "$(hostname -s) pgbouncer: $(systemctl is-active pgbouncer) · $(systemctl is-enabled pgbouncer)"
