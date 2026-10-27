#!/bin/bash
# ============================================================================
# ChargeOps — pgBackRest 정기 백업. systemd 타이머(chargeops-backup.timer)가 매일 부른다
#   두 DB 서버(db-01 · db-02)에 같은 파일을 둔다 → 이 서버가 Primary 일 때만 백업, Standby 면 조용히 끝
#   (장애 전환으로 Primary 가 바뀌어도 설정을 바꿀 필요가 없다)
#   일요일 = 전체(full), 다른 날 = 차등(diff, 마지막 전체 이후 바뀐 것만)
#   설정된 저장소(repo1 MinIO · repo2 AWS S3 …)마다 차례로 — pgBackRest 는 백업을 저장소별로 따로 받는다
#   백업이 끝나면 보관 개수(repoN-retention-full)를 넘은 옛 백업 · WAL 정리(expire)가 자동으로 돈다
#   설치 · 확인: docs/setup/aws-s3-backup.md 2-6 (스크립트 · 서비스 · 타이머 3개를 두 DB 서버에)
# ============================================================================
set -uo pipefail
STANZA=chargeops
CONFS=(/etc/pgbackrest/pgbackrest.conf /etc/pgbackrest/conf.d/*.conf)

role=$(runuser -u postgres -- psql -XAtc 'select pg_is_in_recovery()' 2>/dev/null) \
  || { echo "PostgreSQL 에 접속하지 못함 — 이번 회차는 건너뜀"; exit 1; }
if [ "$role" != f ]; then
  echo "$(hostname -s) 는 Standby — 백업은 Primary 에서 받는다"; exit 0
fi

type="diff"
[ "$(date +%u)" = 7 ] && type="full"

repos=$(grep -ho '^repo[0-9]\+-type' "${CONFS[@]}" 2>/dev/null | grep -o '[0-9]\+' | sort -un)
[ -n "$repos" ] || { echo "설정된 저장소가 없음 — /etc/pgbackrest 확인"; exit 1; }

rc=0
for r in $repos; do
  echo "== repo$r: $type 백업 시작"
  runuser -u postgres -- pgbackrest --stanza="$STANZA" --repo="$r" --type="$type" backup || { echo "== repo$r: 실패"; rc=1; }
done
exit $rc
