-- ============================================================================
-- V20261014160300__dq_check.sql  (WBS 3.9 — 데이터 품질 점검)
--   출처: 실습 스키마 13_anomaly_dq.sql 90줄부터 (fn_dq_check)
--   v_stat_charger(V…160100) 를 쓰므로 그 뒤에 적용
-- ============================================================================
SET client_min_messages = warning;

-- ───────── 데이터 품질: 누락 · 중복 · 이상값 · 지연 · 일관성 · 좌표 → dq_result ─────────
CREATE OR REPLACE FUNCTION fn_dq_check(p_day date DEFAULT (now() AT TIME ZONE 'Asia/Seoul')::date - 1) RETURNS numeric
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE d0 timestamptz := p_day::timestamp AT TIME ZONE 'Asia/Seoul';
        d1 timestamptz := (p_day + 1)::timestamp AT TIME ZONE 'Asia/Seoul';
        overall numeric;
BEGIN
  DELETE FROM dq_result WHERE check_date = p_day;            -- 다시 실행해도 같은 결과 (멱등)

  -- ① 누락: 상태 수집 성공 회차 / 기대 회차 288 (5분 주기). collect_log 를 근거로
  INSERT INTO dq_result
  SELECT p_day, 'missing_collect', 288, greatest(0, 288 - count(*) FILTER (WHERE status = 'ok')),
         round(least(1, count(*) FILTER (WHERE status = 'ok') / 288.0), 4),
         jsonb_build_object('runs', count(*), 'errors', count(*) FILTER (WHERE status = 'error'),
                            'api_calls', coalesce(sum(api_calls), 0))
  FROM collect_log WHERE operation = 'getChargerStatus' AND started_at >= d0 AND started_at < d1;

  -- ② 중복: 같은 충전기의 바로 앞 상태와 같은 상태(의미 없는 변경)
  INSERT INTO dq_result
  SELECT p_day, 'duplicate_status', count(*), count(*) FILTER (WHERE status_code = prev),
         round(1 - count(*) FILTER (WHERE status_code = prev)::numeric / greatest(count(*), 1), 4), NULL
  FROM (SELECT status_code, lag(status_code) OVER (PARTITION BY charger_id ORDER BY changed_at) AS prev
        FROM status_history WHERE changed_at >= d0 AND changed_at < d1) t;

  -- ③ 이상값: 물리적으로 불가능한 계측값 (온도 -40~150℃, 전류 0~정격 2배, 전압 0~1000V)
  INSERT INTO dq_result
  SELECT p_day, 'outlier_meter', count(*), count(*) FILTER (WHERE bad),
         round(1 - count(*) FILTER (WHERE bad)::numeric / greatest(count(*), 1), 4), NULL
  FROM (SELECT m.temp_c NOT BETWEEN -40 AND 150 OR m.current_a < 0
               OR m.current_a > coalesce(c.rated_current_a, 32) * 2 OR m.voltage_v NOT BETWEEN 0 AND 1000 AS bad
        FROM meter_sample m JOIN charger c USING (charger_id) WHERE m.ts >= d0 AND m.ts < d1) t;

  -- ④ 지연: OCPP 실시간 경로는 5분, 공공 상태 수집은 15분을 넘으면 늦은 것
  INSERT INTO dq_result
  SELECT p_day, 'late_arrival', count(*), count(*) FILTER (WHERE late),
         round(1 - count(*) FILTER (WHERE late)::numeric / greatest(count(*), 1), 4),
         jsonb_build_object('p95_sec', round(percentile_cont(0.95) WITHIN GROUP (ORDER BY lag_sec)::numeric, 1))
  FROM (SELECT extract(epoch FROM h.received_at - h.changed_at) AS lag_sec,
               (h.received_at - h.changed_at) > CASE WHEN h.source = 'public' THEN interval '15 minutes' ELSE interval '5 minutes' END AS late
        FROM status_history h LEFT JOIN collect_log l ON l.run_id = h.run_id
        WHERE h.changed_at >= d0 AND h.changed_at < d1
          AND (h.source <> 'public' OR l.operation = 'getChargerStatus')) t;

  -- ⑤ 일관성: 현재 상태가 이력의 마지막 상태와 다른 충전기 (통계 대상만)
  INSERT INTO dq_result
  SELECT p_day, 'consistency', count(*), count(*) FILTER (WHERE lst.status_code IS DISTINCT FROM cur.status_code),
         round(1 - count(*) FILTER (WHERE lst.status_code IS DISTINCT FROM cur.status_code)::numeric / greatest(count(*), 1), 4), NULL
  FROM charger_status_current cur JOIN v_stat_charger USING (charger_id)
  LEFT JOIN LATERAL (SELECT h.status_code FROM status_history h WHERE h.charger_id = cur.charger_id
                     ORDER BY h.changed_at DESC LIMIT 1) lst ON true;

  -- ⑥ 좌표: 통계 대상 충전소 중 좌표가 없거나 한국 밖
  INSERT INTO dq_result
  SELECT p_day, 'bad_coordinates', count(*),
         count(*) FILTER (WHERE s.lat IS NULL OR NOT (s.lat BETWEEN 33 AND 39 AND s.lng BETWEEN 124 AND 132)),
         round(1 - count(*) FILTER (WHERE s.lat IS NULL OR NOT (s.lat BETWEEN 33 AND 39 AND s.lng BETWEEN 124 AND 132))::numeric
                   / greatest(count(*), 1), 4), NULL
  FROM station s WHERE s.source = 'public' AND s.region_code IN (SELECT region_code FROM stat_scope);

  SELECT round(avg(score), 4) INTO overall FROM dq_result WHERE check_date = p_day AND total > 0;
  INSERT INTO dq_result VALUES (p_day, 'overall', NULL, NULL, overall, NULL);
  RETURN overall;
END $$;
COMMENT ON FUNCTION fn_dq_check(date) IS '데이터 품질 점검 (기본: 어제). 항목별 점수 0~1, overall = 평균 (건수 0인 항목 제외)';

REVOKE EXECUTE ON FUNCTION fn_dq_check(date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION fn_dq_check(date) TO app_reader, app_writer, dba;

RESET client_min_messages;
