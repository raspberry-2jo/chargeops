# db/ops — 복제 · 연결 풀 · 백업 · 장애 전환

운영 DB(PostgreSQL 17, Primary 1 + Standby 1)를 **돌리는 데 필요한 설정과 스크립트**입니다. 테이블 · 계정은 `db/migrations/`에 있습니다.

| 폴더 | 내용 | WBS | 상태 |
|---|---|---|---|
| `postgresql/` | 서버 설정(`conf.d/chargeops.conf`: 복제 · WAL · 확장 · 로그), 접속 규칙(`pg_hba.conf`) | 2.7 | 있음 |
| `pgbouncer/` | 연결 풀 설정(`pgbouncer.ini`), 설치 · 인증 파일 스크립트 | 2.7 | 있음 |
| `pgbackrest/` | 백업 · WAL 보관(S3) 설정 양식(`pgbackrest.conf.example`), 설치 스크립트, 특정 시각 복구(PITR) 훈련 | 2.8 | 있음 |
| `failover/` | 장애 전환 · 계획 전환 · 구 Primary 재편입 | 4.4 | 다음 PR |

- 설치 순서 · 확인 방법 · 막혔던 점: 복제 · 연결 풀 [`docs/setup/postgres-replication.md`](../../docs/setup/postgres-replication.md), 백업 · PITR [`docs/setup/pgbackrest-pitr.md`](../../docs/setup/pgbackrest-pitr.md)
- 담당: 강영수 (리뷰 조인성). 같은 폴더를 함께 쓰는 분(조인성 · 윤하빈)은 파일을 더하기 전에 Slack에 알려 주세요 (K2 · K8)
- 비밀 파일(`userlist.txt` · `pgbackrest.conf` · `.pgpass`)은 `.gitignore`로 막혀 있습니다. 값이 필요한 설정은 값을 지운 `파일명.example`만 올립니다 (S2-1)
- 스크립트는 DB 서버에서 root로 실행합니다(`pitr-drill.sh`만 관리 서버에서, 시작 전에 yes 확인). 비밀번호는 명령 줄 · 파일에 쓰지 않고, PostgreSQL에 저장된 SCRAM 비밀이나 `~/.pgpass`(600)에서 읽습니다
