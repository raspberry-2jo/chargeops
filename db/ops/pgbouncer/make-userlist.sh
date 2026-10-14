#!/usr/bin/env bash
# PgBouncer 인증 파일 만들기 (PgBouncer 가 있는 서버 — db-01 · db-02 — 에서 root 로): PostgreSQL 에 저장된 SCRAM 비밀을 그대로 복사
#   Standby 에서도 된다(읽기만 함). install-pgbouncer.sh 가 부른다. 비밀번호를 바꾸면 두 서버에서 각각 다시 실행
#   평문 비밀번호를 파일에 쓰지 않는다. 역할 비밀번호를 바꾸면 이 스크립트를 다시 실행
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "root 로 실행: sudo bash $0"; exit 1; }
# shellcheck disable=SC2024  # root 로 실행 — 파일은 root 가 쓰고 아래에서 pgbouncer 소유 · 600 으로 바꾼다
sudo -u postgres psql -Atc "SELECT format('\"%s\" \"%s\"', rolname, rolpassword) FROM pg_authid
  WHERE rolname IN ('app_writer','app_reader','report_ro','dba','postgres','postgres_exporter')
    AND rolpassword IS NOT NULL ORDER BY rolname" > /etc/pgbouncer/userlist.txt
chown pgbouncer:pgbouncer /etc/pgbouncer/userlist.txt
chmod 600 /etc/pgbouncer/userlist.txt
systemctl reload pgbouncer 2>/dev/null || systemctl restart pgbouncer
echo "userlist: $(wc -l < /etc/pgbouncer/userlist.txt) 계정 ($(cut -d'"' -f2 /etc/pgbouncer/userlist.txt | xargs))"
