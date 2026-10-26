-- ============================================================================
-- V20261026170000__grafana_aws_readonly.sql  (AWS Grafana DB 패널 19개 — 읽기 전용 계정)
--   경로: AWS Grafana → WireGuard → mgmt(NAT, 10.10.40.10) → db-02:5432 직접 · sslmode=verify-full
--   권한: public 스키마 14개 객체 SELECT 만 (refined 스키마의 같은 이름 charger · station 은 제외)
--   비밀번호는 넣지 않는다 → 적용 뒤 Primary 에서 \password grafana_aws 로 사람이 직접 (저장소 S1 · S6)
--   pg_hba: db/ops/postgresql/pg_hba.conf 의 grafana_aws 줄 (db-02 · db-01 둘 다, reload)
--   Primary(db-01)에서 postgres 로, DB chargeops 에 적용. 운영 서버 적용 2026-10-26 · mgmt 에서 verify-full · 14개 SELECT · INSERT 거부 검증
--   default_transaction_read_only 는 사용자가 끌 수 있는 설정이라 보안 경계가 아님 → 실제 경계는 GRANT(SELECT 만)
--   psql 전용 명령 없이 일반 SQL 만
-- ============================================================================
SET client_min_messages = warning;
SET password_encryption = 'scram-sha-256';

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grafana_aws') THEN
    CREATE ROLE grafana_aws LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION CONNECTION LIMIT 5;
  END IF;
END $$;
ALTER ROLE grafana_aws LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION CONNECTION LIMIT 5;
ALTER ROLE grafana_aws SET default_transaction_read_only = on;
ALTER ROLE grafana_aws SET statement_timeout = '30s';

DO $$
BEGIN
  EXECUTE format('GRANT CONNECT ON DATABASE %I TO grafana_aws', current_database());
END $$;
GRANT USAGE ON SCHEMA public TO grafana_aws;
GRANT SELECT ON public.charger, public.charger_status_current, public.code_status, public.collect_log,
                public.mv_daily_uptime, public.operator, public.station,
                public.v_anomaly_score, public.v_charger_card, public.v_ops_actions, public.v_ops_incidents,
                public.v_ops_notify, public.v_ops_roles, public.v_ops_todo
  TO grafana_aws;
