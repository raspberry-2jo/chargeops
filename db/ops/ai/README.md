# db/ops/ai — AI 게이트웨이 · RAG 용 DB (WBS 3.16 · 3.17 DB 지원)

인프라팀 AI 게이트웨이(LiteLLM)와 런북 검색(RAG)이 쓰는 DB · 계정을 만드는 SQL 입니다. 운영 DB(db-01 Primary · db-02 Standby)에 같이 둡니다.

- 담당: 윤하빈 (2026-10-22 운영 서버 적용)
- 근거: 인프라팀 「DB 팀 요청 사항」(10/21) + 10/22 회의 결정

## 1. 결정 사항 (10/22 회의)

| 항목 | 결정 | 이유 |
|---|---|---|
| 접속 경로 | **5432 직접** (PgBouncer 안 거침) | PgBouncer 가 transaction 모드라 LiteLLM(Prisma)의 미리 준비한 쿼리 · 기동 잠금이 깨짐. PgBouncer 설정을 안 바꾸니 CSMS 영향 없음 |
| 암호화 | **sslmode=require** | 다른 환경(인프라 D)에서 네트워크를 건너 들어옴. 서버 SSL 은 [`docs/setup/postgres-ssl.md`](../../../docs/setup/postgres-ssl.md) |
| 접근 | **계정별로 분리** | 계정마다 자기 DB 만, 필요한 권한만 |
| 임베딩 | bge-m3 **1024차원** | 런북과 장애 임베딩이 같은 모델 → `V20261022130000__ai_embedding_1024.sql` |

## 2. 만드는 것

| 종류 | 이름 | 내용 |
|---|---|---|
| DB | `litellm` | LiteLLM 사용자 키 · 사용량 기록. 주인 = `litellm` (테이블은 LiteLLM 이 직접 만듦) |
| DB | `ragdb` | 런북 조각 + 벡터. 주인 = postgres |
| 계정 | `litellm` | litellm DB 만. 연결 상한 20 |
| 계정 | `rag_indexer` | ragdb 의 `runbook_chunks` 쓰기 (색인 CronJob). 연결 상한 3 |
| 계정 | `rag_reader` | ragdb 의 `runbook_chunks` 읽기 (RAG 앱). 연결 상한 7 |
| 테이블 | `ragdb.runbook_chunks` | id · runbook_id · version · chunk_index · content · embedding `vector(1024)` · updated_at, `UNIQUE (runbook_id, version, chunk_index)` |

연결 상한 합계 30 = 서버 최대 100 − 예약 3 − 고정 접속 약 4 − PgBouncer 최대 약 60 안에서 정함.

## 3. 적용 순서 (Primary 기준, 전부 mgmt 에서)

1. 서버 SSL 켜기 — [`docs/setup/postgres-ssl.md`](../../../docs/setup/postgres-ssl.md) (db-02 → db-01)
2. DB · 계정 · 테이블
   ```bash
   # ===== 실행할 명령어 =====
   ssh db-01 "sudo -u postgres psql -X -v ON_ERROR_STOP=1 -d postgres" < db/ops/ai/ai_rag_setup.sql
   ssh db-01 "sudo -u postgres psql -X -v ON_ERROR_STOP=1 -d chargeops" < db/migrations/V20261022130000__ai_embedding_1024.sql
   ```
3. 권한 확인 (16줄 모두 OK)
   ```bash
   # ===== 실행할 명령어 =====
   ssh db-01 "sudo -u postgres psql -X -d ragdb" < db/ops/ai/ai_rag_verify.sql
   ```
4. 접속 허용 — `db/ops/postgresql/pg_hba.conf` 의 `hostssl` 두 줄을 db-01 · db-02 모두 거절 줄 위에 넣고 `SELECT pg_reload_conf();` (재시작 없음)
   - 출발지 `10.10.0.1/32`: 인프라 VM 이 VirtualBox NAT 로 나가서 DB 에는 Windows 호스트 주소로 보임 (방화벽은 이 주소의 5432 가 이미 열려 있음)
5. 비밀번호 — **쓰는 쪽 담당자가 직접 입력** (DB팀은 모름). C 환경 mgmt 에서:
   ```bash
   # ===== 실행할 명령어 =====
   ssh -t db-01 "sudo -u postgres psql -X"
   ```
   ```
   SET pgaudit.log = 'none';   -- 이 접속에서만 감사 로그를 꺼서 비밀번호 해시도 로그에 안 남김
   \password litellm
   \password rag_indexer
   \password rag_reader
   \q
   ```
6. 쓰는 쪽(인프라)이 Secret 생성 · 접속 확인 → DB 쪽 확인 (5장)

## 4. 접속 정보 (쓰는 쪽에 전달)

- 주소 `10.10.30.11:5432` (db-01) / PostgreSQL 17 / `sslmode=require` (인증서까지 검사하려면 CA 공개 인증서 `~/chargeops/pki/ca.crt` + `verify-full`)
- LiteLLM: `postgresql://litellm:<각자 설정>@10.10.30.11:5432/litellm?sslmode=require&connection_limit=10` (비밀번호는 영문 · 숫자만, 파드 2개까지)
- RAG(psycopg3): `host=10.10.30.11 port=5432 dbname=ragdb user=rag_reader | rag_indexer sslmode=require` — 5432 직접이라 prepare 끄는 설정 불필요
- 런북 다시 색인: `INSERT ... ON CONFLICT (runbook_id, version, chunk_index) DO UPDATE SET content = EXCLUDED.content, embedding = EXCLUDED.embedding, updated_at = now()` / id 는 넣지 않음 / version · runbook_id 는 문자

## 5. 확인 (읽기만)

```bash
# ===== 실행할 명령어 =====
# 비밀번호 들어갔는지 (내용은 안 보임)
ssh -n db-01 "sudo -u postgres psql -XAtc \"SELECT rolname, rolpassword LIKE 'SCRAM-SHA-256\$%' FROM pg_authid WHERE rolname IN ('litellm','rag_indexer','rag_reader') ORDER BY 1\""
# 접속이 SSL 로, 예상 주소에서 들어오는지
psql -h db-01 -U dba -d chargeops -Atc "SELECT a.usename, a.datname, a.client_addr, s.ssl, count(*) FROM pg_stat_activity a JOIN pg_stat_ssl s USING (pid) WHERE a.usename IN ('litellm','rag_indexer','rag_reader') GROUP BY 1,2,3,4"
```
```
# ===== 실행 결과 ===== (2026-10-22)
litellm|t
rag_indexer|t
rag_reader|t
litellm|litellm|10.10.0.1|t|3
```
- litellm DB 테이블 98개 (LiteLLM 이 처음 뜨면서 생성), `runbook_chunks` 0행 (색인 전)

## 6. 알려진 한계

- LiteLLM 은 주소를 하나(db-01)만 받음 → db-02 가 Primary 로 승격되면 쓰는 쪽 주소를 바꿔야 함
- 서버 인증서 · 키가 데이터 폴더 안에 있어 Standby 를 `pg_basebackup` · `pg_rewind` 로 다시 만들면 상대 서버 것으로 덮임 → `postgres-ssl.md` 의 설치 단계 다시
- 리포트 잡의 Standby 읽기(요청서 13번) · 검색용 Standby(14번) · 벡터 인덱스(6번)는 나중에
