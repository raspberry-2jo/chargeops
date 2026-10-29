-- ============================================================================
-- V20261027150000__mttr_count_grouped_incidents.sql  (WBS 3.8 — MTTR 에 여러 대 묶음 장애 포함)
--   왜: 일 통계 MTTR 이 incident.charger_id 로만 충전기를 찾아, 충전기 2대 이상이 함께 끊긴 장애
--       (notifier 가 charger_id 를 비우고 targets 에만 넣음)가 MTTR 에서 빠졌다.
--       → 10/23 · 10/26 MTTR 빈칸, 10/20 ~ 10/22 는 묶음 장애가 빠진 채 계산
--   바꾼 것: MTTR(mt) 만. targets 가 있으면 targets, 없으면 charger_id 로 대상 충전기를 찾고,
--       운영사마다 장애 1건으로 센다 (3대 묶음 장애가 3건으로 부풀지 않게 DISTINCT)
--   그대로: 가동률 · 고장 수 · 장기 방치 계산, 반환값, 멱등(UPSERT)
--   적용 뒤: 구체화 뷰 7일 창 안의 날짜만 다시 계산 가능 →
--       SELECT d::date, fn_refresh_stat_operator_daily(d::date) FROM generate_series(current_date - 6, current_date - 1, interval '1 day') d;
--   되돌리기: V20261014160100__stats_uptime.sql 의 같은 함수 정의로 다시 CREATE OR REPLACE
-- ============================================================================
SET client_min_messages = warning;

CREATE OR REPLACE FUNCTION fn_refresh_stat_operator_daily(p_day date DEFAULT (now() AT TIME ZONE 'Asia/Seoul')::date - 1)
RETURNS int LANGUAGE plpgsql AS $$
DECLARE n int;
  d0 timestamptz := p_day::timestamp AT TIME ZONE 'Asia/Seoul';
  d1 timestamptz := (p_day + 1)::timestamp AT TIME ZONE 'Asia/Seoul';
BEGIN
  INSERT INTO stat_operator_daily AS t (operator_id, day, chargers, uptime_pct, mttr_min, fault_cnt, long_fault_cnt)
  WITH sc AS (                 -- 통계 대상 충전기 (네 군데에서 다시 씀 → 한 번만 계산)
    SELECT charger_id, operator_id FROM v_stat_charger WHERE operator_id IS NOT NULL
  ), up AS (                   -- 가동률: 그날의 구체화 뷰 행
    SELECT sc.operator_id, count(DISTINCT u.charger_id) AS chargers, round(avg(u.uptime_pct), 3) AS uptime_pct
    FROM mv_daily_uptime u JOIN sc USING (charger_id)
    WHERE u.day = p_day
    GROUP BY sc.operator_id
  ), mt AS (                   -- MTTR: 그날 해결된 장애. 묶음 장애(targets)도 포함, 운영사마다 1건으로 셈 (2026-10-27)
    SELECT x.operator_id, round(avg(x.sec) / 60, 1) AS mttr_min
    FROM (
      SELECT DISTINCT sc.operator_id, i.incident_id,
             extract(epoch FROM i.resolved_at - i.detected_at)::numeric AS sec
      FROM incident i
      CROSS JOIN LATERAL unnest(CASE WHEN cardinality(i.targets) > 0 THEN i.targets
                                     ELSE ARRAY[i.charger_id] END) AS tg(charger_id)
      JOIN sc ON sc.charger_id = tg.charger_id
      WHERE i.resolved_at >= d0 AND i.resolved_at < d1
    ) x
    GROUP BY x.operator_id
  ), fc AS (                   -- 고장 건수: 그날 다운 상태로 바뀐 횟수
    SELECT sc.operator_id, count(*) AS fault_cnt
    FROM status_history h JOIN sc USING (charger_id) JOIN code_status cs ON cs.code = h.status_code
    WHERE h.changed_at >= d0 AND h.changed_at < d1 AND cs.is_down
    GROUP BY sc.operator_id
  ), lf AS (                   -- 장기 방치: 그날 끝 기준 24시간 넘게 다운 (현재 상태 표)
    SELECT sc.operator_id, count(*) AS long_fault_cnt
    FROM charger_status_current cur JOIN sc USING (charger_id) JOIN code_status cs ON cs.code = cur.status_code
    WHERE cs.is_down AND cur.changed_at <= d1 - interval '24 hours'
    GROUP BY sc.operator_id
  )
  SELECT up.operator_id, p_day, up.chargers, up.uptime_pct, mt.mttr_min,
         coalesce(fc.fault_cnt, 0), coalesce(lf.long_fault_cnt, 0)
  FROM up LEFT JOIN mt USING (operator_id) LEFT JOIN fc USING (operator_id) LEFT JOIN lf USING (operator_id)
  ON CONFLICT (operator_id, day) DO UPDATE
     SET chargers = EXCLUDED.chargers, uptime_pct = EXCLUDED.uptime_pct, mttr_min = EXCLUDED.mttr_min,
         fault_cnt = EXCLUDED.fault_cnt, long_fault_cnt = EXCLUDED.long_fault_cnt;
  GET DIAGNOSTICS n = ROW_COUNT;
  RETURN n;
END $$;
COMMENT ON FUNCTION fn_refresh_stat_operator_daily(date) IS '운영기관별 일 통계 (기본: 어제). 구체화 뷰가 7일 창이라 7일 안의 날짜만. MTTR 은 묶음 장애(targets) 포함 (2026-10-27)';

RESET client_min_messages;
