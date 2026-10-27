-- ============================================================================
-- V20261014160200__anomaly_score.sql  (WBS 3.10 — 이상 징후 점수 · 감지 함수)
--   출처: 실습 스키마 13_anomaly_dq.sql 앞부분 (v_anomaly_score ~ v_anomaly_weekly_review)
--   실습판과 다른 점: psql 전용 명령 없음 · 감지 함수 실행 권한을 필요한 계정에만
-- ============================================================================
SET client_min_messages = warning;

CREATE OR REPLACE VIEW v_anomaly_score AS
WITH cfg AS (SELECT * FROM anomaly_config),
b AS (   -- ① 3분 버킷: 잡음을 줄이고 추세만 남긴다
  SELECT charger_id, date_bin('3 minutes', ts, timestamptz '2000-01-01') AS bucket,
         avg(temp_c) AS temp, max(current_a) - min(current_a) AS cur_range, count(*) AS n
  FROM meter_sample WHERE ts > now() - interval '3 hours' AND ts < now() + interval '1 hour'   -- 위 경계도 줘서 월 파티션을 2개만 본다 (강영수 튜닝)
  GROUP BY 1, 2
), f AS (   -- ② 직전 20버킷(=1시간) 이동 평균 · 표준편차, 직전 버킷 대비 기울기
  SELECT b.*,
         avg(temp) OVER w AS temp_ma, stddev_samp(temp) OVER w AS temp_sd,
         avg(cur_range) OVER w AS cr_ma, stddev_samp(cur_range) OVER w AS cr_sd,
         count(*) OVER w AS hist_n,
         temp - lag(temp) OVER (PARTITION BY charger_id ORDER BY bucket) AS slope
  FROM b
  WINDOW w AS (PARTITION BY charger_id ORDER BY bucket ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING)
)
SELECT f.charger_id, f.bucket, round(f.temp::numeric, 2) AS temp, round(f.slope::numeric, 2) AS slope,
       z.z_temp, z.z_slope, z.z_cur, z.z_hb,
       -- ③ 가중합을 1-exp(-x) 로 0~1 점수로 압축 (z 가 클수록 1에 가까움)
       round((1 - exp(-(0.40 * z.z_temp + 0.30 * z.z_slope + 0.15 * z.z_cur + 0.15 * z.z_hb) / 1.5))::numeric, 3) AS score
FROM f
LEFT JOIN charger_status_current cur USING (charger_id)
CROSS JOIN cfg
CROSS JOIN LATERAL (SELECT
   least(10, greatest(0, (f.temp - f.temp_ma) / greatest(f.temp_sd, 0.5)))          AS z_temp,    -- 평소보다 얼마나 뜨거운가
   least(10, greatest(0, coalesce(f.slope, 0) / cfg.slope_base))                       AS z_slope,   -- 얼마나 빨리 오르는가
   least(10, greatest(0, (f.cur_range - f.cr_ma) / greatest(f.cr_sd, 0.5)))          AS z_cur,     -- 전류가 얼마나 출렁이는가
   CASE WHEN f.bucket >= date_bin('3 minutes', now(), timestamptz '2000-01-01') - interval '3 minutes'
        THEN least(10, greatest(0, extract(epoch FROM now() - cur.last_heartbeat_at) / 30 - 1))
        ELSE 0 END                                                                      AS z_hb       -- Heartbeat 가 늦는가
) z
WHERE f.hist_n >= 5;   -- 기준선(과거 15분)이 없으면 판정하지 않음

-- 매 1분: 완료된 버킷만 보고 연속 조건을 만족하면 anomaly_event 기록
CREATE OR REPLACE FUNCTION fn_detect_anomaly() RETURNS int
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE r record; cfg anomaly_config; lvl text; n int := 0;
BEGIN
  SELECT * INTO cfg FROM anomaly_config;
  -- 먼저 끝난 이벤트를 닫는다 → 아래 "같은 등급 이상의 열린 이벤트가 있으면 건너뜀" 이 옛 이벤트에 막히지 않게.
  --   WATCH 는 24시간 무응답이면, WARN · CRITICAL 은 연결된 장애가 해소되면 (2026-10-14 시뮬레이션: 10/13 의 WARN 이
  --   열린 채 남아 CH-02 가열(점수 0.90)을 하루 넘게 못 잡았다)
  UPDATE anomaly_event e SET verdict = 'cleared'
   WHERE e.verdict = 'open'
     AND ((e.level = 'WATCH' AND e.detected_at < now() - interval '24 hours')
          OR EXISTS (SELECT 1 FROM incident i WHERE i.incident_id = e.incident_id AND i.status = 'resolved'));
  FOR r IN
    WITH s AS (
      SELECT v.*, row_number() OVER (PARTITION BY charger_id ORDER BY bucket DESC) AS rn
      FROM v_anomaly_score v
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
    CONTINUE WHEN EXISTS (   -- 같은 충전기에 같은 등급 이상의 열린 이벤트가 있으면 다시 기록하지 않음
      SELECT 1 FROM anomaly_event e
      WHERE e.charger_id = r.charger_id AND e.verdict = 'open'
        AND array_position(ARRAY['WATCH','WARN','CRITICAL'], e.level) >= array_position(ARRAY['WATCH','WARN','CRITICAL'], lvl));
    INSERT INTO anomaly_event (charger_id, score, level, feature) VALUES (r.charger_id, r.last_score, lvl, r.feature);
    n := n + 1;
  END LOOP;
  RETURN n;
END $$;
COMMENT ON FUNCTION fn_detect_anomaly() IS '이상 징후 감지 (1분마다). SECURITY DEFINER: app_reader 가 실행만 할 수 있게';

CREATE OR REPLACE VIEW v_anomaly_weekly_review AS
SELECT date_trunc('week', detected_at)::date AS week, level, count(*) AS events,
       count(*) FILTER (WHERE verdict = 'false_positive') AS false_positive,
       round(100.0 * count(*) FILTER (WHERE verdict = 'false_positive') / count(*), 1) AS fp_pct
FROM anomaly_event GROUP BY 1, 2 ORDER BY 1 DESC, 2;

-- 권한: 감지 함수(SECURITY DEFINER)는 필요한 계정만 실행
REVOKE EXECUTE ON FUNCTION fn_detect_anomaly() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION fn_detect_anomaly() TO app_reader, app_writer, dba;
GRANT SELECT ON v_anomaly_weekly_review TO report_ro;

RESET client_min_messages;
