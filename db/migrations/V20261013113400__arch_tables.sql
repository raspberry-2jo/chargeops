-- ============================================================================
-- V20261013113400__arch_tables.sql  (WBS 2.6 DDL v1)
--   계측 · 장애 · 감사 · 승인 · 권한 · 알림 · AI · 이상치 · 품질 · 통계 테이블
--   출처: 실습 스키마 v1.1 의 11_arch_tables.sql (내용 같음, psql 전용 명령만 뺌). 표 정의는 docs/db/table-definitions.md
--   적용 순서 · 방법은 db/migrations/README.md
-- ============================================================================
-- ============================================================================
-- ChargeOps 스키마 v1.1 · 아키텍처 확장 — 11_arch_tables.sql
--   데이터팀 v1 이 "해당 실습 뒤에 정한다"고 비워 둔 부분: 계측 · 장애 · 조치(감사) · 승인 · 권한 · 알림 · AI · 품질 · 통계
--   착수보고서 ERD(incident · action_audit · approval · ops_role · notification_log · llm_call_log)를 v1 테이블에 연결
--   빈 DB 에서 01~03, 10 다음에 한 번 실행
-- ============================================================================
SET client_min_messages = warning;
SET TIME ZONE 'Asia/Seoul';

-- ───────── 1. v1 테이블에 컬럼 더하기 (모형 · 가상 충전기 = OCPP 경로에서만 채워짐) ─────────
ALTER TABLE charger ADD COLUMN rated_current_a real;
COMMENT ON COLUMN charger.rated_current_a IS '정격 전류(A). C-03 과전류 판단 기준(정격의 120%). 공공 API 에는 없어 공공 충전기는 비움';
ALTER TABLE charger_status_current ADD COLUMN last_heartbeat_at timestamptz;
ALTER TABLE charger_status_current ADD COLUMN error_code text;
COMMENT ON COLUMN charger_status_current.last_heartbeat_at IS 'OCPP Heartbeat 를 마지막으로 받은 시각 (60초 넘으면 C-01 통신 두절 후보)';
COMMENT ON COLUMN charger_status_current.error_code IS '현재 상태의 OCPP errorCode (NoError 면 비움)';

-- ───────── 2. 계측(MeterValues): 10초마다 → 월 파티션 ─────────
CREATE TABLE meter_sample (
  charger_id text NOT NULL REFERENCES charger,
  ts         timestamptz NOT NULL,
  current_a  real,
  voltage_v  real,
  temp_c     real,
  energy_wh  bigint,
  PRIMARY KEY (charger_id, ts)
) PARTITION BY RANGE (ts);
CREATE INDEX meter_sample_brin ON meter_sample USING brin (ts);
CREATE TABLE meter_sample_default PARTITION OF meter_sample DEFAULT;
COMMENT ON TABLE meter_sample IS '충전기 계측값 (OCPP MeterValues). 이상 징후 감지의 입력. ts 기준 월 파티션';

-- 월 파티션 자동 준비 (status_history · meter_sample). pg_cron 이 매달 25일에 부른다
--   그 달의 행이 이미 기본 파티션에 있으면 파티션을 만들 수 없으므로 건너뛰고 알린다 (v1 문서 6.2 의 함정)
CREATE OR REPLACE FUNCTION fn_ensure_partitions(p_months_ahead int DEFAULT 2) RETURNS text[]
LANGUAGE plpgsql AS $$
DECLARE m date; t text; name text; lo timestamptz; hi timestamptz; made text[] := '{}'; n bigint;
BEGIN
  FOR m IN SELECT generate_series(date_trunc('month', now() AT TIME ZONE 'Asia/Seoul'),
                                  date_trunc('month', now() AT TIME ZONE 'Asia/Seoul') + make_interval(months => p_months_ahead),
                                  interval '1 month')::date LOOP
    FOREACH t IN ARRAY ARRAY['status_history', 'meter_sample'] LOOP
      name := format('%s_%s', t, to_char(m, 'YYYY_MM'));
      CONTINUE WHEN to_regclass(name) IS NOT NULL;
      lo := m::timestamp AT TIME ZONE 'Asia/Seoul';
      hi := (m + interval '1 month')::timestamp AT TIME ZONE 'Asia/Seoul';
      EXECUTE format('SELECT count(*) FROM %I WHERE %I >= $1 AND %I < $2', t || '_default',
                     CASE t WHEN 'meter_sample' THEN 'ts' ELSE 'changed_at' END,
                     CASE t WHEN 'meter_sample' THEN 'ts' ELSE 'changed_at' END) INTO n USING lo, hi;
      IF n > 0 THEN
        RAISE WARNING '% 를 만들 수 없음: 기본 파티션에 그 달의 행 %건이 이미 있음 (가이드 문제 해결 표 참고)', name, n;
        CONTINUE;
      END IF;
      EXECUTE format('CREATE TABLE %I PARTITION OF %I FOR VALUES FROM (%L) TO (%L)', name, t, lo, hi);
      made := made || name;
    END LOOP;
  END LOOP;
  RETURN made;
END $$;
COMMENT ON FUNCTION fn_ensure_partitions(int) IS '이번 달 ~ N개월 뒤의 월 파티션을 없으면 만든다 (status_history, meter_sample)';
SELECT fn_ensure_partitions(2) AS "새로 만든 파티션";

-- ───────── 3. 장애 · 조치(감사) · 승인 ─────────
CREATE TABLE incident (
  incident_id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  fingerprint     text NOT NULL,
  charger_id      text REFERENCES charger,
  station_id      text REFERENCES station,
  targets         text[],
  type_code       text NOT NULL,
  severity        text NOT NULL CHECK (severity IN ('SEV1','SEV2','SEV3','SEV4')),
  title           text NOT NULL,
  status          text NOT NULL DEFAULT 'open' CHECK (status IN ('open','acked','resolved')),
  detected_at     timestamptz NOT NULL DEFAULT now(),
  acked_at        timestamptz,
  resolved_at     timestamptz,
  slack_channel   text,
  slack_thread_ts text,
  root_cause      text,
  summary         text,
  CHECK (resolved_at IS NULL OR resolved_at >= detected_at)
);
CREATE UNIQUE INDEX incident_open_fp ON incident (fingerprint) WHERE status <> 'resolved';   -- 열린 장애는 원인당 1건 → 스레드 1개
CREATE INDEX incident_detected_idx ON incident (detected_at DESC);
COMMENT ON TABLE incident IS '장애. 같은 원인(fingerprint)의 열린 장애는 1건만. type_code: C-01~C-05, A-01, P-01~P-05';
COMMENT ON COLUMN incident.fingerprint IS '같은 원인 묶음 키 (예: C-05:CH-01, Alertmanager groupKey)';
COMMENT ON COLUMN incident.targets IS '묶인 충전기 목록 (같은 충전소 동시 장애)';

CREATE TABLE action_audit (
  action_id       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  incident_id     bigint REFERENCES incident,
  charger_id      text REFERENCES charger,
  idempotency_key text NOT NULL UNIQUE,
  step            text NOT NULL,
  requested_by    text NOT NULL,
  approved_by     text,
  role            text,
  result          text NOT NULL CHECK (result IN ('requested','pending_approval','executed','succeeded','failed',
                                                  'denied','rejected','expired','blocked','rate_limited','timeout')),
  reason          text,
  created_at      timestamptz NOT NULL DEFAULT now(),
  executed_at     timestamptz
);
CREATE INDEX action_audit_charger_idx ON action_audit (charger_id, created_at DESC);
COMMENT ON TABLE action_audit IS '조치 감사 기록. 추가만 허용 (수정 · 삭제는 트리거와 권한으로 막음). 결과가 바뀌면 새 행';
COMMENT ON COLUMN action_audit.idempotency_key IS '같은 버튼을 두 번 눌러도 한 번만 (두 번째는 UNIQUE 위반으로 거부)';
COMMENT ON COLUMN action_audit.step IS 'soft_reset · hard_reset · disable · enable · watch · false_positive · role_grant · dr_drill ...';

CREATE TABLE approval (
  approval_id  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  action_id    bigint NOT NULL REFERENCES action_audit,
  requested_by text NOT NULL,
  approver_id  text,
  role         text,
  decision     text NOT NULL DEFAULT 'pending' CHECK (decision IN ('pending','approved','rejected','expired')),
  payload      jsonb,
  created_at   timestamptz NOT NULL DEFAULT now(),
  expires_at   timestamptz NOT NULL DEFAULT now() + interval '10 minutes',
  decided_at   timestamptz,
  CHECK (approver_id IS NULL OR approver_id <> requested_by)          -- 자기 요청을 자기가 승인할 수 없음
);
CREATE UNIQUE INDEX approval_one_vote ON approval (action_id, approver_id) WHERE approver_id IS NOT NULL;  -- 한 사람 한 표
COMMENT ON TABLE approval IS '승인. 요청 1건(decision=pending, approver_id 없음) + 승인자마다 1행. action_id → 승인 대상 감사 기록';

-- ───────── 4. 권한 · 알림 · AI ─────────
CREATE TABLE ops_role (
  slack_user_id text NOT NULL,
  valid_from    timestamptz NOT NULL DEFAULT now(),
  role          text NOT NULL CHECK (role IN ('Viewer','Operator','Lead','Admin')),
  valid_to      timestamptz,
  granted_by    text NOT NULL,
  PRIMARY KEY (slack_user_id, valid_from)
);
COMMENT ON TABLE ops_role IS '운영 역할 (이력형: valid_to 가 비어 있으면 지금 유효)';

CREATE TABLE notification_log (
  id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  incident_id bigint REFERENCES incident,
  channel     text NOT NULL,
  target      text,
  status      text NOT NULL,
  detail      text,
  sent_at     timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE notification_log IS '알림 발송 기록 (slack · discord · mail)';

CREATE TABLE llm_call_log (
  call_id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  incident_id bigint REFERENCES incident,
  feature     text NOT NULL,
  provider    text NOT NULL,
  model_id    text,
  tokens_in   int,
  tokens_out  int,
  latency_ms  int,
  fallback    boolean NOT NULL DEFAULT false,
  feedback    int,
  created_at  timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE llm_call_log IS 'AI 호출 기록 (summary · ask · embed, ollama · template)';

CREATE EXTENSION IF NOT EXISTS vector;               -- pgvector: 유사 장애 검색
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;   -- 느린 쿼리 분석 (shared_preload_libraries 에 있어야 동작)
CREATE TABLE incident_embedding (
  incident_id bigint PRIMARY KEY REFERENCES incident,
  content     text NOT NULL,
  embedding   vector(768)
);
CREATE INDEX incident_embedding_hnsw ON incident_embedding USING hnsw (embedding vector_cosine_ops);
COMMENT ON TABLE incident_embedding IS '유사 장애 검색용 임베딩 (nomic-embed-text 768차원, HNSW 근사 검색)';

-- ───────── 5. 이상 징후 · 품질 · 통계 ─────────
CREATE TABLE anomaly_config (
  id         int PRIMARY KEY DEFAULT 1 CHECK (id = 1),
  watch      real NOT NULL DEFAULT 0.6,
  warn       real NOT NULL DEFAULT 0.8,
  critical   real NOT NULL DEFAULT 0.9,
  crit_temp  real NOT NULL DEFAULT 55,
  slope_base real NOT NULL DEFAULT 0.25
);
INSERT INTO anomaly_config DEFAULT VALUES;
COMMENT ON TABLE anomaly_config IS '이상 징후 임계값 (한 행). 오탐을 보고 UPDATE 한 줄로 주간 조정';

CREATE TABLE anomaly_event (
  anomaly_id  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  charger_id  text NOT NULL REFERENCES charger,
  incident_id bigint REFERENCES incident,
  score       real NOT NULL,
  level       text NOT NULL CHECK (level IN ('WATCH','WARN','CRITICAL')),
  feature     jsonb,
  verdict     text NOT NULL DEFAULT 'open' CHECK (verdict IN ('open','false_positive','confirmed','cleared')),
  detected_at timestamptz NOT NULL DEFAULT now(),
  notified_at timestamptz
);
CREATE INDEX anomaly_event_charger_idx ON anomaly_event (charger_id, detected_at DESC);

CREATE TABLE dq_result (
  check_date date NOT NULL,
  check_name text NOT NULL,
  total      bigint,
  failed     bigint,
  score      numeric(6,4),
  detail     jsonb,
  PRIMARY KEY (check_date, check_name)
);
COMMENT ON TABLE dq_result IS '데이터 품질 점검 결과 (하루 1회, 항목별 점수 0~1)';

CREATE TABLE stat_scope (
  region_code text PRIMARY KEY REFERENCES code_region,
  note        text
);
INSERT INTO stat_scope VALUES ('50', '실습 기본 범위 (제주, 2026-10-12 결정)');
COMMENT ON TABLE stat_scope IS '가동률 · 통계를 계산할 공공 충전기의 시도 범위 (모형 · 가상 충전기는 항상 포함). 전국 52만 대를 10분마다 계산하지 않으려고';

CREATE TABLE stat_operator_daily (
  operator_id    text NOT NULL REFERENCES operator,
  day            date NOT NULL,
  chargers       int,
  uptime_pct     numeric(6,3),
  mttr_min       numeric(10,1),
  fault_cnt      int,
  long_fault_cnt int,
  PRIMARY KEY (operator_id, day)
);
COMMENT ON TABLE stat_operator_daily IS '운영기관별 일 통계 마트 (주 · 월은 이 표를 다시 묶는 뷰)';

RESET client_min_messages;
