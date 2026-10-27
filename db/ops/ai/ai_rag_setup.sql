-- ============================================================================
-- db/ops/ai/ai_rag_setup.sql — AI 게이트웨이(LiteLLM) · RAG 용 DB 와 계정 (WBS 3.16 · 3.17 DB 지원)
--   DB 는 클러스터 전체에 하나씩이라 V 체인(chargeops DB 안) 밖에 둔다. psql 전용 명령(\gexec · \c)을 쓴다
--   실행: Primary(db-01)에서 postgres 로, 처음 접속 DB 는 postgres. Standby(db-02)는 복제로 자동 반영
--     ssh db-01 "sudo -u postgres psql -X -v ON_ERROR_STOP=1 -d postgres" < db/ops/ai/ai_rag_setup.sql
--   10/22 회의 결정: 5432 직접(PgBouncer 안 거침) · SSL 필수 · 계정별 접근 분리
--   비밀번호는 넣지 않는다 → 계정은 만들어져도 아직 로그인 불가. 비밀번호는 README 순서대로 쓰는 쪽 담당자가 직접 입력
--   다시 실행해도 안전: 이미 있는 것은 건너뛴다. 운영 서버 적용 2026-10-22
-- ============================================================================
SET password_encryption = 'scram-sha-256';

-- 1) 계정 3개 + 연결 상한 (AI 합계 30 — 서버 여유 약 33 = 97 - 고정 4 - PgBouncer 최대 60)
--    litellm 20 = LiteLLM 파드당 connection_limit=10 × 2파드 / rag_indexer 3 = 색인 CronJob 1개 / rag_reader 7 = RAG 앱 + 여유
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'litellm')     THEN CREATE ROLE litellm LOGIN;     END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rag_indexer') THEN CREATE ROLE rag_indexer LOGIN; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rag_reader')  THEN CREATE ROLE rag_reader LOGIN;  END IF;
END $$;
ALTER ROLE litellm     LOGIN CONNECTION LIMIT 20;
ALTER ROLE rag_indexer LOGIN CONNECTION LIMIT 3;
ALTER ROLE rag_reader  LOGIN CONNECTION LIMIT 7;

-- 2) DB 2개 (CREATE DATABASE 는 DO 안에서 못 쓰므로, 없을 때만 \gexec 로 실행)
--    litellm 주인 = litellm → LiteLLM(Prisma)이 처음 뜰 때 자기 테이블을 직접 만든다 (PG 15+ public 스키마 CREATE 는 DB 주인만)
SELECT 'CREATE DATABASE litellm OWNER litellm'
 WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'litellm') \gexec
SELECT 'CREATE DATABASE ragdb'
 WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'ragdb') \gexec

-- 3) 정해진 계정만 접속 (새 DB 는 기본으로 모든 계정이 접속 가능 → 회수 후 필요한 계정만)
REVOKE CONNECT, TEMP ON DATABASE litellm, ragdb FROM PUBLIC;
GRANT CONNECT ON DATABASE ragdb TO rag_indexer, rag_reader;
-- litellm 은 litellm DB 의 주인이라 따로 주지 않아도 접속 · 테이블 생성 가능

-- 4) ragdb: pgvector, 런북 조각 테이블, 권한 (테이블 주인 = postgres → 구조는 DB팀만 바꿈)
\c ragdb
CREATE EXTENSION IF NOT EXISTS vector;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO rag_indexer, rag_reader;
CREATE TABLE IF NOT EXISTS runbook_chunks (
  id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  runbook_id  text         NOT NULL,
  version     text         NOT NULL,
  chunk_index int          NOT NULL,
  content     text         NOT NULL,
  embedding   vector(1024) NOT NULL,
  updated_at  timestamptz  NOT NULL DEFAULT now(),
  UNIQUE (runbook_id, version, chunk_index)      -- 다시 색인할 때 같은 조각이 두 번 들어가지 않게 (덮어쓰기는 ON CONFLICT)
);
COMMENT ON TABLE runbook_chunks IS 'RAG runbook chunks (bge-m3 1024 dims). written by rag_indexer, read by rag_reader';
GRANT SELECT, INSERT, UPDATE, DELETE ON runbook_chunks TO rag_indexer;
GRANT SELECT ON runbook_chunks TO rag_reader;
-- 벡터 인덱스는 런북이 늘면 (지금은 수백 조각 수준이라 전체 비교가 더 빠름)
-- CREATE INDEX CONCURRENTLY runbook_chunks_hnsw ON runbook_chunks USING hnsw (embedding vector_cosine_ops);
