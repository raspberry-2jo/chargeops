-- ============================================================================
-- lab_chargers.sql  (실습 · 시연용 — V 체인 밖, 운영 DB 에는 넣지 않음)
--   출처: 실습 스키마 16_seed_lab.sql
--   CSMS 는 여기 등록된 충전기만 받는다 → 실습 · 시연 DB 에서 마이그레이션 뒤 손으로 한 번:
--     sudo -u postgres psql -X -q -d chargeops -v ON_ERROR_STOP=1 < db/seed/lab_chargers.sql
--   모형 CH-01~03 · 가상 LT-0001~1000 · DMZ 시험 PB-01~03. 다시 실행해도 된다
-- ============================================================================
INSERT INTO operator (operator_id, name, operating_org, in_guide)
VALUES ('LAB', 'ChargeOps 실습', 'ChargeOps 실습 운영사', false)
ON CONFLICT (operator_id) DO NOTHING;

INSERT INTO station (station_id, operator_id, name, addr, lat, lng, region_code, use_time, source) VALUES
  ('CH', 'LAB', '실습실 모형 충전소', '세종특별자치시 실습실 3층', 36.4800000, 127.2890000, '36', '24시간 이용가능', 'model'),
  ('LT', 'LAB', '가상 부하 충전소',   '시험망 loadgen', NULL, NULL, NULL, '24시간 이용가능', 'virtual'),
  ('PB', 'LAB', 'DMZ 시험 접속용',    '시험망 loadgen', NULL, NULL, NULL, '24시간 이용가능', 'virtual')
ON CONFLICT (station_id) DO NOTHING;

INSERT INTO charger (charger_id, station_id, charger_no, source, charger_type_code, output_kw, power_type, rated_current_a, install_year)
SELECT 'CH-' || lpad(n::text, 2, '0'), 'CH', lpad(n::text, 2, '0'), 'model', '02', 7, '완속', 32, 2026
FROM generate_series(1, 3) n
ON CONFLICT (charger_id) DO NOTHING;

INSERT INTO charger (charger_id, station_id, charger_no, source, charger_type_code, output_kw, power_type, rated_current_a)
SELECT 'LT-' || lpad(n::text, 4, '0'), 'LT', lpad(n::text, 4, '0'), 'virtual', '02', 7, '완속', 32
FROM generate_series(1, 1000) n
ON CONFLICT (charger_id) DO NOTHING;

INSERT INTO charger (charger_id, station_id, charger_no, source, charger_type_code, output_kw, power_type, rated_current_a)
SELECT 'PB-' || lpad(n::text, 2, '0'), 'PB', lpad(n::text, 2, '0'), 'virtual', '02', 7, '완속', 32
FROM generate_series(1, 3) n
ON CONFLICT (charger_id) DO NOTHING;

-- 최초 Admin (선택, 손으로): <Slack 멤버 ID> 를 바꿔서 실행
-- INSERT INTO ops_role (slack_user_id, role, granted_by) SELECT '<Slack 멤버 ID>', 'Admin', 'seed'
--   WHERE NOT EXISTS (SELECT 1 FROM ops_role WHERE slack_user_id = '<Slack 멤버 ID>' AND valid_to IS NULL);

SELECT station_id AS "충전소", source AS "출처", count(*) AS "충전기" FROM charger WHERE station_id IN ('CH', 'LT', 'PB') GROUP BY 1, 2 ORDER BY 1;
