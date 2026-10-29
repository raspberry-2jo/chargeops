-- mv_daily_uptime: 10분마다 7일 전체 재계산(REFRESH MATERIALIZED VIEW) → 날짜 단위 증분 갱신
--
-- 왜: 10/29 부하 시험에서 정각(:00, :10 …)마다 응답 시간이 튐 (10,500대 p95 최대 3.76초).
--     갱신 시간이 부하에 따라 4.3초 → 5.6초로 늘었고, 공공 API 수집기(:00, :05)와 같은 분에 겹침.
--
-- 바꾸는 것
--   1) mv_daily_uptime 을 같은 이름 · 같은 컬럼 5개의 일반 표로 바꿈 (PK charger_id, day)
--      → 읽는 곳(대시보드, fn_refresh_stat_operator_daily, ai-gateway, 리포트)은 수정 없음
--   2) fn_refresh_daily_uptime(p_from, p_to): 지정한 날짜(Asia/Seoul)만 다시 계산해 덮어씀
--   3) pg_cron refresh-uptime: 10분마다 "어제 + 오늘"만, 실행 분 :03 · :13 … (수집기와 안 겹침)
--      refresh-uptime-7d: 매일 00:07 최근 7일 재계산 + 7일 지난 행 삭제 (늦게 들어온 기록 · 정정 반영)
--   4) v_charger_card 는 표에 묶여 있어 같은 정의로 다시 만듦
--
-- 계산식은 기존 MV와 같음. 차이 1가지: 창 시작 기준 행(직전 상태)을 changes(상태 변경 수)에 세지 않음.
--   기존 MV는 7일 중 가장 오래된 날의 changes가 충전기마다 1 많았음.
-- 자정 경계: 00:03 · 00:13 실행이 어제를 마감 상태로 다시 계산 → 00:20 fn_refresh_stat_operator_daily 가 완성된 값을 읽음.
-- 수동 재계산: SELECT fn_refresh_daily_uptime('2026-10-26', '2026-10-26');
-- 되돌리기: 이 파일 아래 "되돌리기" 주석 참고.

-- 1) 현재 결과를 그대로 옮긴 일반 표
CREATE TABLE mv_daily_uptime_new AS SELECT * FROM mv_daily_uptime;

-- 2) 묶여 있는 뷰와 MV 교체 (CASCADE 없음: 다른 의존 객체가 있으면 여기서 멈추고 전체 취소)
DROP VIEW v_charger_card;
DROP MATERIALIZED VIEW mv_daily_uptime;
ALTER TABLE mv_daily_uptime_new RENAME TO mv_daily_uptime;
ALTER TABLE mv_daily_uptime ALTER COLUMN charger_id SET NOT NULL, ALTER COLUMN day SET NOT NULL;
ALTER TABLE mv_daily_uptime ADD CONSTRAINT mv_daily_uptime_pk PRIMARY KEY (charger_id, day);
COMMENT ON TABLE mv_daily_uptime IS '충전기별 일 가동률 (최근 7일, Asia/Seoul). fn_refresh_daily_uptime 으로 날짜 단위 갱신: 10분마다 어제+오늘, 매일 00:07 7일. 다운 = code_status.is_down';

-- 3) 날짜 단위 계산 함수
CREATE FUNCTION fn_refresh_daily_uptime(p_from date, p_to date)
RETURNS integer
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $fn$
DECLARE
  v_w0 timestamptz := p_from::timestamp AT TIME ZONE 'Asia/Seoul';
  v_w1 timestamptz := LEAST((p_to + 1)::timestamp AT TIME ZONE 'Asia/Seoul', now());
  v_rows integer;
BEGIN
  IF p_from > p_to THEN
    RAISE EXCEPTION 'p_from(%) > p_to(%)', p_from, p_to;
  END IF;

  DELETE FROM mv_daily_uptime WHERE day BETWEEN p_from AND p_to;

  INSERT INTO mv_daily_uptime (charger_id, day, uptime_pct, observed_h, changes)
  WITH ev AS (
    SELECT h.charger_id, h.changed_at, h.status_code, false AS is_seed
      FROM status_history h JOIN v_stat_charger USING (charger_id)
     WHERE h.changed_at >= v_w0 AND h.changed_at < v_w1
    UNION ALL
    SELECT sc.charger_id, v_w0, prev.status_code, true
      FROM v_stat_charger sc
     CROSS JOIN LATERAL (SELECT h.status_code FROM status_history h
                          WHERE h.charger_id = sc.charger_id AND h.changed_at < v_w0
                          ORDER BY h.changed_at DESC LIMIT 1) prev
  ), span AS (
    SELECT charger_id, status_code, is_seed, changed_at AS s0,
           COALESCE(lead(changed_at) OVER (PARTITION BY charger_id ORDER BY changed_at, is_seed DESC), v_w1) AS s1
      FROM ev
  ), days AS (
    SELECT (d AT TIME ZONE 'Asia/Seoul')::date AS day, d AS d0, LEAST(d + interval '1 day', v_w1) AS d1
      FROM generate_series(v_w0, v_w1 - interval '1 microsecond', interval '1 day') d
  )
  SELECT sp.charger_id, days.day,
         round(100::numeric * (1::numeric
               - COALESCE(sum(EXTRACT(epoch FROM LEAST(sp.s1, days.d1) - GREATEST(sp.s0, days.d0))) FILTER (WHERE cs.is_down), 0::numeric)
               / NULLIF(sum(EXTRACT(epoch FROM LEAST(sp.s1, days.d1) - GREATEST(sp.s0, days.d0))), 0::numeric)), 3),
         round(sum(EXTRACT(epoch FROM LEAST(sp.s1, days.d1) - GREATEST(sp.s0, days.d0))) / 3600::numeric, 2),
         count(*) FILTER (WHERE sp.s0 >= days.d0 AND NOT sp.is_seed)
    FROM span sp
    JOIN days ON sp.s0 < days.d1 AND sp.s1 > days.d0
    JOIN code_status cs ON cs.code = sp.status_code
   GROUP BY sp.charger_id, days.day;

  GET DIAGNOSTICS v_rows = ROW_COUNT;
  RETURN v_rows;
END
$fn$;
COMMENT ON FUNCTION fn_refresh_daily_uptime(date, date) IS 'mv_daily_uptime 의 p_from ~ p_to (Asia/Seoul 날짜) 를 다시 계산해 덮어씀. 반환: 넣은 행 수';

-- 4) 최근 7일을 새 방식으로 다시 채움 (changes 기준 행 차이 반영)
SELECT fn_refresh_daily_uptime(((now() AT TIME ZONE 'Asia/Seoul')::date - 6), (now() AT TIME ZONE 'Asia/Seoul')::date);

-- 5) v_charger_card 다시 만들기 (기존 정의 그대로)
CREATE VIEW v_charger_card AS
 SELECT c.charger_id,
    fn_status_group(cur.status_code) ||
        CASE
            WHEN cur.error_code IS NOT NULL THEN ' · '::text || fn_cause_label(cur.error_code)
            ELSE ''::text
        END AS state,
    m.current_a,
    m.temp_c,
    COALESCE(c.rated_current_a, 32::real) AS rated_a,
    a.score AS anomaly,
    u.uptime_pct AS uptime_today,
    EXTRACT(epoch FROM now() - cur.last_heartbeat_at)::integer AS hb_age_sec
   FROM charger c
     LEFT JOIN charger_status_current cur USING (charger_id)
     LEFT JOIN LATERAL ( SELECT ms.current_a,
            ms.temp_c
           FROM meter_sample ms
          WHERE ms.charger_id = c.charger_id AND ms.ts > (now() - '00:10:00'::interval)
          ORDER BY ms.ts DESC
         LIMIT 1) m ON true
     LEFT JOIN LATERAL ( SELECT v.score
           FROM v_anomaly_score v
          WHERE v.charger_id = c.charger_id
          ORDER BY v.bucket DESC
         LIMIT 1) a ON true
     LEFT JOIN mv_daily_uptime u ON u.charger_id = c.charger_id AND u.day = CURRENT_DATE
  WHERE c.source = 'model'::text AND NOT c.is_deleted;

-- 6) 권한: 읽기 전용 계정은 SELECT만. app_writer 는 기본 권한 설정으로 붙는 쓰기 권한을 빼고 SELECT만 (10/29 권한 회수 방향)
REVOKE ALL ON mv_daily_uptime, v_charger_card FROM PUBLIC, app_writer, app_reader, report_ro, grafana_aws;
GRANT SELECT ON mv_daily_uptime, v_charger_card TO app_writer, app_reader, report_ro, grafana_aws;
GRANT ALL ON mv_daily_uptime, v_charger_card TO dba;

-- 7) 예약 작업 (같은 이름이면 pg_cron 이 덮어씀)
SELECT cron.schedule('refresh-uptime', '3-59/10 * * * *',
  $c$SELECT fn_refresh_daily_uptime((now() AT TIME ZONE 'Asia/Seoul')::date - 1, (now() AT TIME ZONE 'Asia/Seoul')::date)$c$);
SELECT cron.schedule('refresh-uptime-7d', '7 0 * * *',
  $c$SELECT fn_refresh_daily_uptime((now() AT TIME ZONE 'Asia/Seoul')::date - 6, (now() AT TIME ZONE 'Asia/Seoul')::date); DELETE FROM mv_daily_uptime WHERE day < (now() AT TIME ZONE 'Asia/Seoul')::date - 6$c$);

-- 되돌리기 (필요할 때만, 한 트랜잭션으로):
--   SELECT cron.unschedule('refresh-uptime-7d');
--   DROP VIEW v_charger_card; DROP TABLE mv_daily_uptime; DROP FUNCTION fn_refresh_daily_uptime(date, date);
--   db/views/V20261014160100__stats_uptime.sql 의 mv_daily_uptime 정의 · 인덱스 · 권한,
--   db/views/V20261014160400__ops_views.sql 의 v_charger_card 정의 · 권한 다시 실행 후
--   SELECT cron.schedule('refresh-uptime', '*/10 * * * *', 'REFRESH MATERIALIZED VIEW CONCURRENTLY mv_daily_uptime');
