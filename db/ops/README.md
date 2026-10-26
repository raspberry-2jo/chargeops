# db/ops — 복제 · 연결 풀 · 백업 · 장애 전환

운영 DB(PostgreSQL 17, Primary 1 + Standby 1)를 **돌리는 데 필요한 설정과 스크립트**입니다. 테이블 · 계정은 `db/migrations/`에 있습니다.

| 폴더 | 내용 | WBS | 상태 |
|---|---|---|---|
| `postgresql/` | 서버 설정(`conf.d/chargeops.conf`: 복제 · WAL · 확장 · 로그), 접속 규칙(`pg_hba.conf`) | 2.7 | 있음 |
| `pgbouncer/` | 연결 풀 설정(`pgbouncer.ini`), 설치 · 인증 파일 스크립트 | 2.7 | 있음 |
| `pgbackrest/` | 백업 · WAL 보관(S3) 설정 양식(`pgbackrest.conf.example`), 설치 스크립트, 특정 시각 복구(PITR) 훈련(`PITR_REPO=2` 로 AWS 저장소에서), AWS S3 원격 저장소(repo2) 최소 권한 정책 양식(`aws-s3-policy.example.json`), 정기 백업 타이머(`chargeops-backup.*`, 매일 · Primary 에서만), 빈 새 서버에서 AWS 백업만으로 되살리기(`dr-restore.sh`) | 2.8 · 4.5 | 있음 |
| `patroni/` | **자동** 장애 전환(Patroni + etcd + watchdog): 전환(`convert.sh`) · 상태(`status.sh`) · 시험(`drill.sh`) · 역할 교대(`switchover.sh`) · 되돌리기(`rollback.sh`) · PgBouncer 콜백. 설치는 `infra/ansible/patroni.yml` | 4.4 확장 | 있음 (2026-10-16) |
| `failover/` | 장애 전환(`failover.sh`) · 구 Primary 재편입(`rejoin.sh`) · 계획 전환 · 원복(`switchover.sh`) | 4.4 | 있음 (쿼리 튜닝은 통계 뷰 3.8 뒤) |
| `ai/` | AI 게이트웨이(LiteLLM) · RAG 용 DB(`litellm` · `ragdb`) · 계정 3개 · 런북 테이블(`ai_rag_setup.sql`), 권한 확인(`ai_rag_verify.sql`), 적용 순서 · 접속 정보(`README.md`). 서버 SSL 은 [`docs/setup/postgres-ssl.md`](../../docs/setup/postgres-ssl.md) | 3.16 · 3.17 | 있음 (2026-10-22, 담당 윤하빈) |

- 설치 순서 · 확인 방법 · 막혔던 점: 복제 · 연결 풀 [`docs/setup/postgres-replication.md`](../../docs/setup/postgres-replication.md), 백업 · PITR [`docs/setup/pgbackrest-pitr.md`](../../docs/setup/pgbackrest-pitr.md), AWS 원격 백업 · 정기 일정 [`docs/setup/aws-s3-backup.md`](../../docs/setup/aws-s3-backup.md), DR 복원 [`docs/setup/dr-restore.md`](../../docs/setup/dr-restore.md) · 온프렘 복귀 [`docs/setup/dr-failback.md`](../../docs/setup/dr-failback.md), 장애 전환 훈련 [`docs/setup/postgres-failover.md`](../../docs/setup/postgres-failover.md), 자동 장애 전환 [`docs/setup/postgres-patroni.md`](../../docs/setup/postgres-patroni.md)
- 담당: 강영수 (리뷰 조인성). 같은 폴더를 함께 쓰는 분(조인성 · 윤하빈)은 파일을 더하기 전에 Slack에 알려 주세요 (K2 · K8)
- 비밀 파일(`userlist.txt` · `pgbackrest.conf` · `.pgpass`)은 `.gitignore`로 막혀 있습니다. 값이 필요한 설정은 값을 지운 `파일명.example`만 올립니다 (S2-1)
- 스크립트는 DB 서버에서 root로 실행합니다(`pitr-drill.sh` · `failover/` 는 관리 서버에서, `dr-restore.sh` 는 빈 새 서버에서. 장애 전환 · 원복 · PITR 은 **승인 뒤에만** 실행). 비밀번호는 명령 줄 · 파일에 쓰지 않고, PostgreSQL에 저장된 SCRAM 비밀이나 `~/.pgpass`(600)에서 읽습니다
