-- ============================================================================
-- V20261027190000__anomaly_detect_window_80m.sql  (WBS 4.4 — 쿼리 튜닝: 이상 징후 감지 함수)
--   왜: fn_detect_anomaly() 가 v_anomaly_score(최근 3시간)를 매분 통째로 계산했다.
--       10/27 9,000대 부하 시험에서 평균 2.5초/회(1분마다), DB 시간 1·2위.
--       판정에 쓰는 건 완료된 최근 5버킷과 각 버킷 앞 20버킷(=75분)뿐이다.
--   무엇: 함수 안에서만 v_anomaly_score 와 같은 식으로 최근 80분만 계산한다.
--         대시보드용 v_anomaly_score(3시간)는 바꾸지 않는다.
--   검증: 10/27 db-02 에서 3시간판과 80분판의 충전기별 판정 등급 비교 후 적용
-- ============================================================================
SET client_min_messages = warning;

CREATE OR REPLACE FUNCTION fn_detect_anomaly() RETURNS int
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE r record; cfg anomaly_config; lvl text; n int := 0;
BEGIN
  SELECT * INTO cfg FROM anomaly_config;
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
    )
    SELECT charger_id,
           min(score) FILTER (WHERE rn <= 3) AS min3, min(score) FILTER (WHERE rn <= 5) AS min5,
           max(score) FILTER (WHERE rn = 1) AS last_score, max(temp) FILTER (WHERE rn = 1) AS last_temp,
           max(slope) FILTER (WHERE rn = 1) AS last_slope, count(*) FILTER (WHERE rn <= 5) AS cnt,
           jsonb_agg(jsonb_build_object('bucket', bucket, 'score', score, 'temp', temp, 'slope', slope,
                     'z_temp', round(z_temp::numeric, 2), 'z_slope', round(z_slope::numeric, 2),
                     'z_cur', round(z_cur::numeric, 2), 'z_hb', round(z_hb::numeric, 2)) ORDER BY bucket)
             FILTER (WHERE rn <= 5) AS feature
    FROM s GROUP BY charger_id
  LOOP
    lvl := CASE
      WHEN (r.cnt >= 5 AND r.min5 >= cfg.critical) OR (r.last_temp >= cfg.crit_temp AND r.last_slope > 0) THEN 'CRITICAL'
      WHEN r.cnt >= 5 AND r.min5 >= cfg.warn  THEN 'WARN'
      WHEN r.cnt >= 3 AND r.min3 >= cfg.watch THEN 'WATCH'
    END;
    CONTINUE WHEN lvl IS NULL;
    CONTINUE WHEN EXISTS (
      SELECT 1 FROM anomaly_event e
      WHERE e.charger_id = r.charger_id AND e.verdict = 'open'
        AND array_position(ARRAY['WATCH','WARN','CRITICAL'], e.level) >= array_position(ARRAY['WATCH','WARN','CRITICAL'], lvl));
    INSERT INTO anomaly_event (charger_id, score, level, feature) VALUES (r.charger_id, r.last_score, lvl, r.feature);
    n := n + 1;
  END LOOP;
  RETURN n;
END $$;
COMMENT ON FUNCTION fn_detect_anomaly() IS '이상 징후 감지 (1분마다). 최근 80분만 계산(2026-10-27 튜닝). SECURITY DEFINER: app_reader 가 실행만 할 수 있게';

REVOKE EXECUTE ON FUNCTION fn_detect_anomaly() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION fn_detect_anomaly() TO app_reader, app_writer, dba;

RESET client_min_messages;
