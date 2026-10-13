-- ============================================================================
-- V20261013113300__arch_codes.sql  (WBS 2.6 DDL v1)
--   공공 API 상태 코드 ↔ OCPP 상태 대응, 고장 코드
--   출처: 실습 스키마 v1.1 의 10_arch_codes.sql (내용 같음, psql 전용 명령만 뺌). 표 정의는 docs/db/table-definitions.md
--   적용 순서 · 방법은 db/migrations/README.md
-- ============================================================================
-- ============================================================================
-- ChargeOps 스키마 v1.1 · 아키텍처 확장 — 10_arch_codes.sql
--   공공 API 상태 코드와 OCPP(모형 · 가상 충전기) 상태를 "한 어휘"로 맞춘다
--   → 공공 · 모형 · 가상 충전기의 가동률 · 장애를 같은 계산식으로 볼 수 있게 (v1 문서 7장 9번 결정의 연장)
--   데이터팀 v1 테이블은 지우거나 이름을 바꾸지 않고 컬럼 · 행만 더한다. 다시 실행해도 된다
-- ============================================================================
ALTER TABLE code_status ADD COLUMN IF NOT EXISTS is_down boolean NOT NULL DEFAULT false;
ALTER TABLE code_status ADD COLUMN IF NOT EXISTS ocpp_status text[];
COMMENT ON COLUMN code_status.is_down     IS '가동률 계산에서 "다운"으로 보는 상태면 true (통신이상 · 운영중지 · 점검중 · 고장)';
COMMENT ON COLUMN code_status.ocpp_status IS '이 코드로 바꿔 저장할 OCPP 1.6 StatusNotification 값들 (CSMS 가 사용)';

-- 공공 API 에 없는 "고장" — OCPP Faulted 를 담을 자리. 가이드 코드가 아니므로 in_guide = false
INSERT INTO code_status (code, name, in_guide) VALUES ('F', '고장 (OCPP Faulted · 아키텍처 확장)', false)
ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name;

UPDATE code_status SET is_down = (code IN ('1', '4', '5', 'F'));
UPDATE code_status SET ocpp_status = CASE code
    WHEN '2' THEN ARRAY['Available', 'Reserved']
    WHEN '3' THEN ARRAY['Preparing', 'Charging', 'SuspendedEV', 'SuspendedEVSE', 'Finishing']
    WHEN '4' THEN ARRAY['Unavailable']
    WHEN 'F' THEN ARRAY['Faulted']
    WHEN '1' THEN ARRAY['Offline']             -- OCPP 값이 아니라 CSMS 가 Heartbeat 끊김을 이렇게 기록
  END;

-- OCPP 상태 → 코드. 모르는 값은 9(상태미확인)
CREATE OR REPLACE FUNCTION fn_status_code(p_ocpp text) RETURNS text
LANGUAGE sql STABLE AS $$
  SELECT coalesce((SELECT code FROM code_status WHERE p_ocpp = ANY (ocpp_status) ORDER BY code LIMIT 1), '9')
$$;
COMMENT ON FUNCTION fn_status_code(text) IS 'OCPP 상태 이름 → code_status.code (모르면 9)';

-- 다운 상태인가 (가동률 · 장기 방치 판단). 정의를 코드가 아니라 테이블(code_status.is_down)에 둔다
CREATE OR REPLACE FUNCTION fn_is_down(p_code text) RETURNS boolean
LANGUAGE sql STABLE AS $$
  SELECT coalesce((SELECT is_down FROM code_status WHERE code = p_code), false)
$$;

SELECT code, name, is_down, ocpp_status FROM code_status ORDER BY code;
