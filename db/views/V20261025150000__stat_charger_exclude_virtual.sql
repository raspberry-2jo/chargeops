-- ============================================================================
-- V20261025150000__stat_charger_exclude_virtual.sql  (WBS 3.8 — 통계 대상에서 부하 시험용 가상 충전기 제외)
--   왜: v_stat_charger 가 public 이 아닌 충전기를 모두 통계에 넣어, 부하 시험용 가상 충전기(LT-, PB-)가
--       꺼진 채로 우리 운영사(LAB) 통계에 들어갔다.
--       → 일일 리포트(10/25 09:00) LAB 가동률 0.033% · 장기 방치 9,001, 10/21 MTTR 154분 · 고장 15,643건
--   바꾼 것: 첫째 갈래(모형 · 가상)를 모형(source = 'model')만으로. 공공 갈래는 그대로
--       장기 방치 감지(fn_detect_long_fault)는 이미 가상을 빼고 있었음 → 통계도 같은 기준으로 맞춤
--   영향: mv_daily_uptime · fn_refresh_stat_operator_daily · fn_dq_check · fn_detect_long_fault 가 이 뷰를 씀
--       열 이름 · 순서 · 형식이 같아 CREATE OR REPLACE 로 바뀌고, 기대는 객체 · 권한(report_ro)은 그대로
--       Soft Reset 훈련용 LT-0001 의 사건도 MTTR 에서 빠진다 (우리 MTTR = 실물 모형 CH- 기준)
--   기존 인덱스 charger_nonpublic_idx (WHERE source <> 'public') 를 계속 쓰도록 source <> 'public' 조건을 남김
--   되돌리기: 아래 WHERE 의 첫째 갈래를  NOT c.is_deleted AND c.source <> 'public'  로 다시 바꿔 실행
-- ============================================================================
SET client_min_messages = warning;

CREATE OR REPLACE VIEW v_stat_charger AS
SELECT c.charger_id, c.station_id, s.operator_id, c.source
FROM charger c JOIN station s USING (station_id)
WHERE NOT c.is_deleted AND c.source <> 'public' AND c.source = 'model'
UNION ALL
SELECT c.charger_id, c.station_id, s.operator_id, c.source
FROM station s JOIN charger c USING (station_id)
WHERE NOT c.is_deleted AND c.source = 'public' AND s.region_code IN (SELECT region_code FROM stat_scope);

COMMENT ON VIEW v_stat_charger IS '가동률 · 통계 대상 충전기 — 모형(CH-)과 stat_scope 범위의 공공. 부하 시험용 가상(LT- · PB-)은 제외 (2026-10-25)';

RESET client_min_messages;
