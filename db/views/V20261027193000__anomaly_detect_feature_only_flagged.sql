-- ============================================================================
-- V20261027193000__anomaly_detect_feature_only_flagged.sql  (WBS 4.4 — 쿼리 튜닝 2차: 이상 징후 감지 함수)
--   왜: 1차(V20261027190000, 3시간 → 80분) 후에도 2.2초. 단계별 EXPLAIN(10/27 db-02) 결과
--       마지막 집계(충전기 9,003대마다 판정 요약 jsonb 생성)가 약 580ms 로 가장 큰 단일 단계였다.
--       요약(feature)은 이벤트를 기록할 충전기에만 필요하다 (평소 0~수 대).
--   무엇: 등급(grade)을 SQL 안에서 먼저 정하고, 등급이 나온 충전기만 jsonb 요약을 만든다.
--         판정 기준 · 80분 범위 · 기록 내용은 1차와 같다.
--   검증: 같은 데이터에서 1차 함수와 2차 함수가 기록한 이상 이벤트(충전기 · 등급 · 점수 · 요약)가 같은지 비교
-- ============================================================================
SET client_min_messages = warning;

CREATE OR REPLACE FUNCTION fn_detect_anomaly() RETURNS int
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE r record; n int := 0;
BEGIN
  UPDATE anomaly_event e SET verdict = 'cleared'
   WHERE e.verdict = 'open'
     AND ((e.level = 'WATCH' AND e.detected_at < now() - interval '24 hours')
          OR EXISTS (SELECT 1 FROM incident i WHERE i.incident_id = e.incident_id AND i.status = 'resolved'));
  FOR r IN
    WITH c AS (SELECT * FROM anomaly_config),
    b AS (   -- v_anomaly_score 와 같은 3분 버킷. 기간만 80분 (5버킷 + 앞 20버킷 = 75분 + 진행 중 버킷)
      SELECT charger_id, date_bin('3 minutes', ts, timestamptz '2000-01-01') AS bucket,
             avg(temp_c) AS temp, max(current_a) - min(current_a) AS cur_range
      FROM meter_sample WHERE ts > now() - interval '80 minutes' AND ts < now() + interval '1 hour'
      GROUP BY 1, 2
    ), f AS (
      SELECT b.*,
             avg(temp) OVER w AS temp_ma, stddev_samp(temp) OVER w AS temp_sd,
             avg(cur_range) OVER w AS cr_ma, stddev_samp(cur_range) OVER w AS cr_sd,
             count(*) OVER w AS hist_n,
             temp - lag(temp) OVER (PARTITION BY charger_id ORDER BY bucket) AS slope
      FROM b
      WINDOW w AS (PARTITION BY charger_id ORDER BY bucket ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING)
    ), v AS (
      SELECT f.charger_id, f.bucket, round(f.temp::numeric, 2) AS temp, round(f.slope::numeric, 2) AS slope,
             z.z_temp, z.z_slope, z.z_cur, z.z_hb,
             round((1 - exp(-(0.40 * z.z_temp + 0.30 * z.z_slope + 0.15 * z.z_cur + 0.15 * z.z_hb) / 1.5))::numeric, 3) AS score
      FROM f
      LEFT JOIN charger_status_current cur USING (charger_id)
      CROSS JOIN c
      CROSS JOIN LATERAL (SELECT
         least(10, greatest(0, (f.temp - f.temp_ma) / greatest(f.temp_sd, 0.5)))  AS z_temp,
         least(10, greatest(0, coalesce(f.slope, 0) / c.slope_base))                AS z_slope,
         least(10, greatest(0, (f.cur_range - f.cr_ma) / greatest(f.cr_sd, 0.5)))  AS z_cur,
         CASE WHEN f.bucket >= date_bin('3 minutes', now(), timestamptz '2000-01-01') - interval '3 minutes'
              THEN least(10, greatest(0, extract(epoch FROM now() - cur.last_heartbeat_at) / 30 - 1))
              ELSE 0 END                                                             AS z_hb
      ) z
      WHERE f.hist_n >= 5
    ), s AS (
      SELECT v.*, row_number() OVER (PARTITION BY charger_id ORDER BY bucket DESC) AS rn
      FROM v
      WHERE bucket < date_bin('3 minutes', now(), timestamptz '2000-01-01')   -- 진행 중인 버킷 제외
    ), g AS (   -- 판정 재료: 충전기별 최근 5버킷 요약 (jsonb 없음)
      SELECT charger_id,
             min(score) FILTER (WHERE rn <= 3) AS min3, min(score) AS min5,
             max(score) FILTER (WHERE rn = 1) AS last_score, max(temp) FILTER (WHERE rn = 1) AS last_temp,
             max(slope) FILTER (WHERE rn = 1) AS last_slope, count(*) AS cnt
      FROM s WHERE rn <= 5
      GROUP BY charger_id
    ), j AS (   -- 등급 판정 (1차 함수의 CASE 와 같은 기준)
      SELECT g.*,
             CASE WHEN (g.cnt >= 5 AND g.min5 >= c.critical) OR (g.last_temp >= c.crit_temp AND g.last_slope > 0) THEN 'CRITICAL'
                  WHEN g.cnt >= 5 AND g.min5 >= c.warn  THEN 'WARN'
                  WHEN g.cnt >= 3 AND g.min3 >= c.watch THEN 'WATCH'
             END AS grade
      FROM g CROSS JOIN c
    )
    -- 등급이 나온 충전기만 판정 요약(jsonb)을 만든다
    SELECT j.charger_id, j.last_score, j.grade,
           jsonb_agg(jsonb_build_object('bucket', s.bucket, 'score', s.score, 'temp', s.temp, 'slope', s.slope,
                     'z_temp', round(s.z_temp::numeric, 2), 'z_slope', round(s.z_slope::numeric, 2),
                     'z_cur', round(s.z_cur::numeric, 2), 'z_hb', round(s.z_hb::numeric, 2)) ORDER BY s.bucket) AS feature
    FROM j JOIN s ON s.charger_id = j.charger_id AND s.rn <= 5
    WHERE j.grade IS NOT NULL
    GROUP BY j.charger_id, j.last_score, j.grade
  LOOP
    CONTINUE WHEN EXISTS (   -- 같은 충전기에 같은 등급 이상의 열린 이벤트가 있으면 다시 기록하지 않음
      SELECT 1 FROM anomaly_event e
      WHERE e.charger_id = r.charger_id AND e.verdict = 'open'
        AND array_position(ARRAY['WATCH','WARN','CRITICAL'], e.level) >= array_position(ARRAY['WATCH','WARN','CRITICAL'], r.grade));
    INSERT INTO anomaly_event (charger_id, score, level, feature) VALUES (r.charger_id, r.last_score, r.grade, r.feature);
    n := n + 1;
  END LOOP;
  RETURN n;
END $$;
COMMENT ON FUNCTION fn_detect_anomaly() IS '이상 징후 감지 (1분마다). 최근 80분만 계산 · 등급 나온 충전기만 요약 생성(2026-10-27 튜닝). SECURITY DEFINER: app_reader 가 실행만 할 수 있게';

REVOKE EXECUTE ON FUNCTION fn_detect_anomaly() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION fn_detect_anomaly() TO app_reader, app_writer, dba;

RESET client_min_messages;
