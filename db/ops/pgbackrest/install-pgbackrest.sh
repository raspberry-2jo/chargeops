#!/usr/bin/env bash
# ============================================================================
# pgBackRest 설정 놓기 — 두 DB 서버(db-01 · db-02)에서 각각 한 번. 저장소 루트에서:
#   sudo bash db/ops/pgbackrest/install-pgbackrest.sh
#   ① pgbackrest.conf.example 의 <S3 접근 키> · <S3 비밀 키> 를 입력받아 채운다 (입력은 화면 · 셸 기록에 남지 않음)
#   ② /etc/pgbackrest/pgbackrest.conf 로 (root:postgres 640 — 키가 있어 postgres 그룹만 읽게)
#   stanza-create · check · 백업은 이 스크립트가 하지 않는다 → docs/setup/pgbackrest-pitr.md 2장
# ============================================================================
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "root 로 실행: sudo bash $0"; exit 1; }
HERE=$(cd "$(dirname "$0")" && pwd)
CONF=/etc/pgbackrest/pgbackrest.conf
command -v pgbackrest >/dev/null || { echo "먼저 패키지 설치: dnf install -y pgbackrest (PGDG, versions.md)"; exit 1; }
getent group postgres >/dev/null || { echo "postgres 그룹이 없음 — PostgreSQL 17 패키지를 먼저 설치"; exit 1; }

read -rp  "S3 접근 키: " S3_KEY
read -rsp "S3 비밀 키 (화면에 안 보임): " S3_SECRET; echo
[ -n "$S3_KEY" ] && [ -n "$S3_SECRET" ] || { echo "빈 값 — 중단"; exit 1; }

# 키에 / · & 같은 문자가 있어도 깨지지 않게 sed 대신 awk 로 '그 줄 통째로' 바꾼다. 값은 환경변수로만 넘김(명령 줄에 안 보임)
TMP=$(mktemp); trap 'rm -f "$TMP"' EXIT
S3_KEY="$S3_KEY" S3_SECRET="$S3_SECRET" awk '
  /^repo1-s3-key=/        { print "repo1-s3-key=" ENVIRON["S3_KEY"]; next }
  /^repo1-s3-key-secret=/ { print "repo1-s3-key-secret=" ENVIRON["S3_SECRET"]; next }
  { print }' "$HERE/pgbackrest.conf.example" > "$TMP"
unset S3_KEY S3_SECRET
if grep -q '<S3 ' "$TMP"; then echo "빈자리가 남음 — pgbackrest.conf.example 확인"; exit 1; fi

# PGDG 패키지는 /etc/pgbackrest.conf 만 깔고 /etc/pgbackrest/ 폴더는 안 만든다 → install -D 로 폴더까지
install -D -o root -g postgres -m 640 "$TMP" "$CONF"
ls -l "$CONF"
grep -E '^(repo1-(type|s3-endpoint|s3-bucket|path|retention-full)|pg1-path)=' "$CONF"   # 키 두 줄은 화면에 내지 않음
echo "$(hostname -s) pgbackrest $(pgbackrest version | awk '{print $2}'): 설정 놓음. 다음은 docs/setup/pgbackrest-pitr.md 2장 ②"
