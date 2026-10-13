-- ============================================================================
-- V20261013113500__roles_and_grants.sql  (WBS 2.6 DDL v1 — 역할 분리)
--   DB 계정과 권한 = 최소 권한 원칙. 출처: 실습 스키마 v1.1 의 14_security.sql
--   실습판과 다른 점
--     · 비밀번호를 넣지 않는다. 역할은 LOGIN 으로 만들지만 비밀번호가 없어 아직 접속할 수 없다
--       → 적용한 뒤 db/migrations/README.md 의 "비밀번호 설정" 대로 사람이 직접 넣는다 (저장소 S1 · S6)
--     · 뷰 · 함수(통계 마트 · 이상치 · 품질)의 권한은 그 객체를 만드는 파일(db/views · db/quality)에서 준다
--     · DB 이름을 적지 않고 current_database() 로 (어떤 이름의 DB 에도 적용되게)
--     · psql 전용 명령(\set · \if · \gexec) 없이 일반 SQL 만
--
--   계정
--     app_writer         CSMS · 수집기 · 봇 · 알림: 읽기 + 쓰기, 감사 기록은 추가만 (UPDATE/DELETE 불가)
--     app_reader         이상치 감지 잡 · AI 게이트웨이: 조회 전용 (+ AI 기록 남기기, 장애 요약 한 칸 쓰기)
--     report_ro          Grafana · 리포트: Standby 에서 통계 · 조회만
--     dba                스키마 · 파티션 관리
--     replicator         Standby 복제 전용
--     postgres_exporter  모니터링 지표 수집 전용
--   postgres(소유자)로 실행한다. 역할은 DB 가 아니라 클러스터 전체에 있으므로, 이미 있으면 만들지 않고 속성만 맞춘다
-- ============================================================================
SET client_min_messages = warning;
SET password_encryption = 'scram-sha-256';     -- 나중에 넣을 비밀번호를 SCRAM 으로 저장

-- 역할 만들기 (없을 때만)
DO $$
DECLARE r text;
BEGIN
  FOREACH r IN ARRAY ARRAY['app_writer', 'app_reader', 'report_ro', 'dba', 'replicator', 'postgres_exporter'] LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
      EXECUTE format('CREATE ROLE %I LOGIN', r);
    END IF;
  END LOOP;
END $$;

ALTER ROLE dba        LOGIN CREATEROLE;
ALTER ROLE replicator LOGIN REPLICATION;
ALTER ROLE app_writer CONNECTION LIMIT 60;     -- PgBouncer 풀(20 + 예비 5) × 여유. 폭주해도 DB 전체를 막지 않게
GRANT pg_monitor TO report_ro, postgres_exporter, dba;

-- 스키마 · DB: 아무나 객체를 만들 수 없게
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO app_writer, app_reader, report_ro, dba, postgres_exporter;
GRANT CREATE ON SCHEMA public TO dba;
DO $$
BEGIN
  EXECUTE format('REVOKE CREATE ON DATABASE %I FROM PUBLIC', current_database());
  EXECUTE format('GRANT CREATE ON DATABASE %I TO dba', current_database());
END $$;

-- app_writer: 읽기 + 쓰기 (지우기 없음). 적재 대기 테이블은 비우기(TRUNCATE)까지
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO app_writer;
GRANT TRUNCATE ON stg_charger_info, stg_charger_status TO app_writer;
REVOKE UPDATE ON action_audit FROM app_writer;              -- 감사 기록은 추가만
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO app_writer;

-- app_reader: 조회 전용 + AI 기록 남기기 + 장애 요약 컬럼 하나만 쓰기
GRANT SELECT ON ALL TABLES IN SCHEMA public TO app_reader;
GRANT INSERT ON llm_call_log TO app_reader;
GRANT INSERT, UPDATE ON incident_embedding TO app_reader;
GRANT UPDATE (summary) ON incident TO app_reader;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO app_reader;

-- report_ro: 통계 · 조회용 테이블만 (통계 뷰 · 구체화 뷰는 db/views 에서 따로 GRANT)
GRANT SELECT ON stat_operator_daily, stat_scope, dq_result, anomaly_event, incident, charger, station, operator,
                charger_status_current, code_status, code_region, code_charger_type, collect_log
      TO report_ro;

-- dba: 전부
GRANT ALL ON ALL TABLES IN SCHEMA public TO dba;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO dba;

-- 앞으로 postgres 가 만드는 테이블 · 뷰에도 같은 기본 권한 (report_ro 는 필요한 것만 따로 준다)
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT SELECT, INSERT, UPDATE ON TABLES TO app_writer;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT SELECT ON TABLES TO app_reader;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT ALL ON TABLES TO dba;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT USAGE ON SEQUENCES TO app_writer, app_reader;

-- 감사 테이블은 권한이 있어도(소유자 · dba) 수정 · 삭제 불가 — 트리거로 한 번 더 막는다
CREATE OR REPLACE FUNCTION fn_block_audit_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'action_audit is append-only (% blocked)', TG_OP;
END $$;
DROP TRIGGER IF EXISTS trg_audit_append_only ON action_audit;
CREATE TRIGGER trg_audit_append_only BEFORE UPDATE OR DELETE ON action_audit
  FOR EACH ROW EXECUTE FUNCTION fn_block_audit_change();
