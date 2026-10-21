-- ============================================================================
-- V20261014160100__stats_uptime.sql  (WBS 3.8 — 가동률 · MTTR · 운영기관 통계 · 장기 방치)
--   출처: 실습 스키마 12_stats.sql (강영수 PC 2026-10-14 11:03 판, md5 2a889f55863c5171247371de23b54391) + 2026-10-15 일 통계 함수 튜닝(상관 서브쿼리 → CTE)
--   실습판과 다른 점: psql 전용 명령(cho) 없음 · 인덱스 2개는 V20261014160000 으로 · report_ro 권한을 끝에서 줌
--   ⚠ 새로 세우는 DB 에만. 이미 운영 중인 DB 에 다시 돌리면 DROP MATERIALIZED VIEW … CASCADE 가 관제 화면 뷰까지 지운다
-- ============================================================================
SET client_min_messages = warning;

-- 통계 대상 충전기: 모형 · 가상은 항상, 공공은 stat_scope 의 시도만, 삭제 표시는 뺌
--   (2026-10-12 튜닝) 예전에는 WHERE (모형·가상 OR 범위 시도) 한 덩어리 → OR 때문에 인덱스를 못 써서 충전기 52만 · 충전소 11만을
--   매번 전부 읽었다. 두 갈래(UNION ALL)로 나누고 인덱스 2개를 더함 → 결과 같음(행 수 · md5), 작성 환경에서 뷰 0.41→0.07초,
--   가동률 갱신 4.1→1.8초, 일 통계 10.5→3.0초 (인덱스는 V20261014160000). 이 뷰를 쓰는 곳: mv_daily_uptime · fn_refresh_stat_operator_daily · fn_dq_check · fn_detect_long_fault
CREATE OR REPLACE VIEW v_stat_charger AS
SELECT c.charger_id, c.station_id, s.operator_id, c.source
FROM charger c JOIN station s USING (station_id)
WHERE NOT c.is_deleted AND c.source <> 'public'
UNION ALL
SELECT c.charger_id, c.station_id, s.operator_id, c.source
FROM station s JOIN charger c USING (station_id)
WHERE NOT c.is_deleted AND c.source = 'public' AND s.region_code IN (SELECT region_code FROM stat_scope);
COMMENT ON VIEW v_stat_charger IS '가동률 · 통계 대상 충전기 (stat_scope 로 범위 조절)';

-- 상태 구간: 한 상태가 [바뀐 시각, 다음 변경 시각) 동안 유지됐다고 본다 → lead() 윈도 함수
CREATE OR REPLACE VIEW v_status_span AS
SELECT h.charger_id, h.status_code, h.changed_at AS span_start,
       coalesce(lead(h.changed_at) OVER (PARTITION BY h.charger_id ORDER BY h.changed_at), now()) AS span_end
FROM status_history h;
COMMENT ON VIEW v_status_span IS '상태 구간 (전체 이력 기준. 큰 범위에서는 느리니 기간 조건과 함께 쓴다)';

DROP MATERIALIZED VIEW IF EXISTS mv_daily_uptime CASCADE;
CREATE MATERIALIZED VIEW mv_daily_uptime AS
WITH win AS (           -- 오늘 포함 7일 (한국 시간 자정 기준)
  SELECT (date_trunc('day', now() AT TIME ZONE 'Asia/Seoul') - interval '6 days') AT TIME ZONE 'Asia/Seoul' AS w0
), ev AS (              -- 창 안의 변경 + 창 시작 직전의 마지막 상태(창 시작 시각으로 당겨 붙임)
  SELECT h.charger_id, h.changed_at, h.status_code
  FROM status_history h JOIN v_stat_charger USING (charger_id), win
  WHERE h.changed_at >= win.w0
  UNION ALL
  SELECT sc.charger_id, win.w0, prev.status_code
  FROM v_stat_charger sc CROSS JOIN win
  CROSS JOIN LATERAL (SELECT status_code FROM status_history h
                      WHERE h.charger_id = sc.charger_id AND h.changed_at < win.w0
                      ORDER BY h.changed_at DESC LIMIT 1) prev
), span AS (
  SELECT ev.charger_id, ev.status_code, ev.changed_at AS s0,
         coalesce(lead(ev.changed_at) OVER (PARTITION BY ev.charger_id ORDER BY ev.changed_at), now()) AS s1
  FROM ev
), days AS (
  SELECT (d AT TIME ZONE 'Asia/Seoul')::date AS day, d AS d0, least(d + interval '1 day', now()) AS d1
  FROM win, generate_series(win.w0, now(), interval '1 day') AS d
)
SELECT sp.charger_id, days.day,
       -- 관측한 시간 중 다운이 아닌 시간의 비율 (처음 관측 전 시간은 분모에서 뺌)
       round((100 * (1 - coalesce(sum(extract(epoch FROM least(sp.s1, days.d1) - greatest(sp.s0, days.d0)))
                                      FILTER (WHERE cs.is_down), 0)
                         / nullif(sum(extract(epoch FROM least(sp.s1, days.d1) - greatest(sp.s0, days.d0))), 0)))::numeric, 3) AS uptime_pct,
       round((sum(extract(epoch FROM least(sp.s1, days.d1) - greatest(sp.s0, days.d0))) / 3600)::numeric, 2) AS observed_h,
       count(*) FILTER (WHERE sp.s0 >= days.d0) AS changes
FROM span sp
JOIN days ON sp.s0 < days.d1 AND sp.s1 > days.d0
JOIN code_status cs ON cs.code = sp.status_code
GROUP BY sp.charger_id, days.day;
CREATE UNIQUE INDEX mv_daily_uptime_pk ON mv_daily_uptime (charger_id, day);   -- CONCURRENTLY 갱신에 필요
COMMENT ON MATERIALIZED VIEW mv_daily_uptime IS '충전기별 일 가동률 (최근 7일, 10분마다 갱신). 다운 = code_status.is_down';

-- 충전기별 MTTR(평균 복구 시간, 분)
CREATE OR REPLACE VIEW v_mttr AS
SELECT charger_id, count(*) AS incidents,
       round(avg(extract(epoch FROM resolved_at - detected_at) / 60)::numeric, 1) AS mttr_min
FROM incident WHERE resolved_at IS NOT NULL AND charger_id IS NOT NULL
GROUP BY charger_id;

-- 운영기관별 일 통계 적재 (UPSERT → 다시 실행해도 결과가 같다 = 멱등)
CREATE OR REPLACE FUNCTION fn_refresh_stat_operator_daily(p_day date DEFAULT (now() AT TIME ZONE 'Asia/Seoul')::date - 1)
RETURNS int LANGUAGE plpgsql AS $$
DECLARE n int;
  d0 timestamptz := p_day::timestamp AT TIME ZONE 'Asia/Seoul';
  d1 timestamptz := (p_day + 1)::timestamp AT TIME ZONE 'Asia/Seoul';
BEGIN
  -- (2026-10-14 튜닝) 예전에는 운영사마다 서브쿼리 3개가 통계 대상 뷰를 다시 계산했다 (운영사 수 x 3번).
  --   지금은 대상 충전기를 한 번 구하고, 가동률 · MTTR · 고장 수 · 장기 방치 수를 운영사별로 한 번씩 묶어 붙인다.
  --   결과는 같다: 서브쿼리의 count 는 대상이 없으면 0 → coalesce(…, 0), avg 는 NULL → LEFT JOIN 의 NULL 그대로
  --   (2026-10-15 강영수 PC db-01 적용) 최근 6일 338행 바꾸기 전 · 뒤 md5 같음, 실행 1.06초 → 0.16초
  INSERT INTO stat_operator_daily AS t (operator_id, day, chargers, uptime_pct, mttr_min, fault_cnt, long_fault_cnt)
  WITH sc AS (                 -- 통계 대상 충전기 (네 군데에서 다시 씀 → 한 번만 계산)
    SELECT charger_id, operator_id FROM v_stat_charger WHERE operator_id IS NOT NULL
  ), up AS (                   -- 가동률: 그날의 구체화 뷰 행
    SELECT sc.operator_id, count(DISTINCT u.charger_id) AS chargers, round(avg(u.uptime_pct), 3) AS uptime_pct
    FROM mv_daily_uptime u JOIN sc USING (charger_id)
    WHERE u.day = p_day
    GROUP BY sc.operator_id
  ), mt AS (                   -- MTTR: 그날 해결된 장애
    SELECT sc.operator_id, round(avg(extract(epoch FROM i.resolved_at - i.detected_at) / 60)::numeric, 1) AS mttr_min
    FROM incident i JOIN sc USING (charger_id)
    WHERE i.resolved_at >= d0 AND i.resolved_at < d1
    GROUP BY sc.operator_id
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
COMMENT ON FUNCTION fn_refresh_stat_operator_daily(date) IS '운영기관별 일 통계 (기본: 어제). 구체화 뷰가 7일 창이라 7일 안의 날짜만';

-- 주 · 월 통계는 일 마트를 다시 묶는 뷰 (원본 이력을 다시 읽지 않음)
CREATE OR REPLACE VIEW stat_operator_weekly AS
SELECT operator_id, date_trunc('week', day)::date AS week, round(avg(uptime_pct), 3) AS uptime_pct,
       round(avg(mttr_min), 1) AS mttr_min, sum(fault_cnt) AS fault_cnt, max(long_fault_cnt) AS long_fault_cnt
FROM stat_operator_daily GROUP BY 1, 2;
CREATE OR REPLACE VIEW stat_operator_monthly AS
SELECT operator_id, date_trunc('month', day)::date AS month, round(avg(uptime_pct), 3) AS uptime_pct,
       round(avg(mttr_min), 1) AS mttr_min, sum(fault_cnt) AS fault_cnt, max(long_fault_cnt) AS long_fault_cnt
FROM stat_operator_daily GROUP BY 1, 2;

-- C-05 장기 방치: 다운 상태가 24시간 넘게 이어진 충전기를 SEV4 장애로 (하루 1회)
--   기본은 우리 충전기(모형 · 가상)만. 공공 충전기는 수백 대가 몇 달째 운영중지라 알림 폭탄이 됨 → 통계(long_fault_cnt)로만
CREATE OR REPLACE FUNCTION fn_detect_long_fault(p_include_public boolean DEFAULT false) RETURNS int
LANGUAGE plpgsql AS $$
DECLARE n int;
BEGIN
  INSERT INTO incident (fingerprint, charger_id, station_id, type_code, severity, title)
  SELECT 'C-05:' || c.charger_id, c.charger_id, c.station_id, 'C-05', 'SEV4',
         format('장기 방치: %s (%s 상태 %s시간)', c.charger_id, cs.name,
                round(extract(epoch FROM now() - cur.changed_at) / 3600))
  FROM charger_status_current cur
  JOIN v_stat_charger c USING (charger_id)
  JOIN code_status cs ON cs.code = cur.status_code
  WHERE cs.is_down AND cur.changed_at < now() - interval '24 hours'
    AND (p_include_public OR c.source <> 'public')
    AND c.source <> 'virtual'                    -- 부하 시험용 가상 충전기 제외 (2026-10-14, 17_ops_ui.sql 과 같음)
  ON CONFLICT (fingerprint) WHERE status <> 'resolved' DO NOTHING;
  GET DIAGNOSTICS n = ROW_COUNT;
  RETURN n;
END $$;

-- 권한: Grafana · 리포트(report_ro)는 통계 뷰 · 마트만 (팀 roles_and_grants 가 여기로 미뤄 둔 것)
GRANT SELECT ON mv_daily_uptime, stat_operator_weekly, stat_operator_monthly, v_mttr, v_stat_charger TO report_ro;

RESET client_min_messages;
