-- ============================================================================
-- V20261026160000__grafana_ro_litellm.sql  (Grafana LLM 운영 대시보드 — litellm DB 읽기 전용 계정, 관측 인계 Step 15)
--   경로: 온프렘 Grafana 파드(노드 10.10.20.11 · .21 · .22) → db-01 · db-02:5432 직접 · SSL (require)
--   권한: litellm DB public 스키마 SELECT (LiteLLM 이 나중에 만드는 표도 기본 권한으로 SELECT)
--   비밀번호는 넣지 않는다 → 적용 뒤 Primary 에서 \password grafana_ro 로 사람이 직접 (저장소 S1 · S6)
--   pg_hba: db/ops/postgresql/pg_hba.conf 의 grafana_ro 3줄 · 방화벽: DB 서버 chargeops 영역에 Grafana 노드 3대 5432 허용
--   Primary(db-01)에서 postgres 로, DB litellm 에 적용. 운영 서버 적용 2026-10-26 (litellm 표 98개 SELECT 확인)
--   psql 전용 명령 없이 일반 SQL 만
-- ============================================================================
SET client_min_messages = warning;
SET password_encryption = 'scram-sha-256';

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grafana_ro') THEN
    CREATE ROLE grafana_ro LOGIN CONNECTION LIMIT 5;
  END IF;
END $$;
ALTER ROLE grafana_ro LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION CONNECTION LIMIT 5;
ALTER ROLE grafana_ro SET default_transaction_read_only = on;

DO $$
BEGIN
  EXECUTE format('GRANT CONNECT ON DATABASE %I TO grafana_ro', current_database());
END $$;
GRANT USAGE ON SCHEMA public TO grafana_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO grafana_ro;
ALTER DEFAULT PRIVILEGES FOR ROLE litellm IN SCHEMA public GRANT SELECT ON TABLES TO grafana_ro;
