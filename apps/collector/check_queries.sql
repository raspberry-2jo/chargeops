-- ============================================================================
-- 공공 API 수집기 확인 쿼리 (WBS 3.6) — DBeaver · psql 공용, 전부 SELECT (데이터를 바꾸지 않음)
--
--   DBeaver : 관리 연결(dba · db-01:5432 · chargeops)에서 이 파일을 열고, 보고 싶은 쿼리 안에 커서를 둔 뒤 Ctrl+Enter
--             (Alt+X 는 파일 전체를 차례로 실행 → 결과 탭이 쿼리마다 하나씩 생김)
--             연결 설정 → 일반(General) → "읽기 전용 연결(Read-only connection)"을 켜 두면 실수로 고칠 일이 없음
--   psql    : psql -h db-01 -U dba chargeops -f apps/collector/check_queries.sql      (관리 서버, 비밀번호는 ~/.pgpass)
--
--   시각은 모두 한국 시간으로 바꿔서 보여 줌 (AT TIME ZONE 'Asia/Seoul') → DBeaver · psql 의 시간대 설정과 상관없음
--   수집 중에 실행해도 됨. 쿼리 안에는 빈 줄을 두지 않음 (DBeaver 가 빈 줄을 쿼리의 끝으로 봄)
-- ============================================================================


-- [1] 지금 수집이 살아 있나 — '마지막 성공 뒤 지난 분'이 5 안쪽이면 정상 (상태 수집은 5분마다)
SELECT to_char(max(started_at) AT TIME ZONE 'Asia/Seoul', 'MM-DD HH24:MI:SS')        AS "마지막 성공 수집",
       round(extract(epoch FROM now() - max(started_at)) / 60, 1)                      AS "마지막 성공 뒤 지난 분",
       (SELECT to_char(max(received_at) AT TIME ZONE 'Asia/Seoul', 'MM-DD HH24:MI:SS')
          FROM status_history WHERE source = 'public')                                 AS "이력 마지막 적재",
       (SELECT count(*) FROM status_history
         WHERE source = 'public' AND received_at > now() - interval '30 minutes')      AS "최근 30분 적재 건수"
FROM collect_log
WHERE operation = 'getChargerStatus' AND status = 'ok';


-- [2] 최근 수집 20회 — 5분마다 한 줄씩 늘고, 상태 수집은 '호출' 1 · '결과' ok 면 정상
SELECT run_id,
       CASE operation WHEN 'getChargerStatus' THEN '상태' WHEN 'getChargerInfo' THEN '기본정보' ELSE operation END AS "종류",
       to_char(started_at AT TIME ZONE 'Asia/Seoul', 'MM-DD HH24:MI:SS') AS "시작",
       round(extract(epoch FROM finished_at - started_at)::numeric, 1)    AS "걸린 초",
       period_min AS "조회 범위(분)", status AS "결과", api_calls AS "호출",
       rows_fetched AS "받음", rows_loaded AS "적재", left(error_message, 60) AS "오류"
FROM collect_log
ORDER BY run_id DESC
LIMIT 20;


-- [3] 최근 24시간 실적 — 상태 수집은 하루 288회가 기준. '빠진 회차'(대략)는 기록조차 못 남긴 회차
--     (DB 에 못 붙은 회차 · PC 나 VM 이 꺼져 있던 시간) → 어느 시간대인지는 [4]
SELECT CASE operation WHEN 'getChargerStatus' THEN '상태' WHEN 'getChargerInfo' THEN '기본정보' ELSE operation END AS "종류",
       count(*) FILTER (WHERE status = 'ok')      AS "성공",
       count(*) FILTER (WHERE status = 'error')   AS "실패",
       count(*) FILTER (WHERE status = 'running') AS "실행 중 · 강제 종료",
       CASE WHEN operation = 'getChargerStatus' THEN 288 - count(*) END AS "빠진 회차",
       sum(api_calls)    AS "API 호출",
       sum(rows_fetched) AS "받은 건수",
       sum(rows_loaded)  AS "적재 건수",
       sum(rows_fetched) FILTER (WHERE operation = 'getChargerStatus') - sum(rows_loaded) FILTER (WHERE operation = 'getChargerStatus') AS "이미 받은 변경(건너뜀)"
FROM collect_log
WHERE started_at > now() - interval '24 hours'
GROUP BY operation
ORDER BY operation DESC;


-- [4] 빠진 구간 찾기 — 상태 수집 기록 사이가 7분 넘게 벌어진 곳 (맨 아래 줄이 '지금'이면 지금 멈춰 있는 것)
WITH runs AS (
  SELECT started_at FROM collect_log
  WHERE operation = 'getChargerStatus' AND started_at > now() - interval '24 hours'
  UNION ALL
  SELECT now()
), gaps AS (
  SELECT lag(started_at) OVER (ORDER BY started_at) AS prev_at, started_at AS next_at FROM runs
)
SELECT to_char(prev_at AT TIME ZONE 'Asia/Seoul', 'MM-DD HH24:MI') AS "이 기록 뒤로",
       to_char(next_at AT TIME ZONE 'Asia/Seoul', 'MM-DD HH24:MI') AS "다음 기록까지",
       round(extract(epoch FROM next_at - prev_at) / 60)          AS "간격(분)",
       round(extract(epoch FROM next_at - prev_at) / 300) - 1     AS "빠진 회차"
FROM gaps
WHERE next_at - prev_at > interval '7 minutes'
ORDER BY prev_at;


-- [5] 실패 · 강제 종료된 회차 (최근 20건) — 오류 문구로 원인 구분 (API 응답 문제인지, DB 문제인지)
SELECT run_id,
       CASE operation WHEN 'getChargerStatus' THEN '상태' WHEN 'getChargerInfo' THEN '기본정보' ELSE operation END AS "종류",
       to_char(started_at AT TIME ZONE 'Asia/Seoul', 'MM-DD HH24:MI:SS') AS "시작",
       status AS "결과", api_calls AS "호출",
       coalesce(left(error_message, 200), '(강제 종료 · 실행 중)')        AS "오류"
FROM collect_log
WHERE status = 'error' OR (status = 'running' AND started_at < now() - interval '10 minutes')
ORDER BY run_id DESC
LIMIT 20;


-- [6] 오늘(한국 시간 0시부터) 쓴 API 호출 수 — 하루 한도 1,000회 (같은 인증키로 다른 곳에서 부른 호출은 여기 안 보임)
SELECT coalesce(CASE operation WHEN 'getChargerStatus' THEN '상태' WHEN 'getChargerInfo' THEN '기본정보' ELSE operation END, '합계') AS "API",
       sum(api_calls) AS "호출",
       CASE WHEN operation IS NULL THEN 1000 - sum(api_calls) END AS "남은 호출"
FROM collect_log
WHERE started_at >= date_trunc('day', now() AT TIME ZONE 'Asia/Seoul') AT TIME ZONE 'Asia/Seoul'
GROUP BY ROLLUP (operation)
ORDER BY operation NULLS LAST;


-- [7] 최근 3시간, 5분 구간별 적재 건수 — 0 이거나 줄이 빠진 구간이 수집이 끊긴 곳
--     (DBeaver 결과 창 오른쪽의 '차트' 버튼으로 막대그래프로 보면 한눈에 보임)
SELECT to_char(date_bin('5 minutes', received_at, timestamptz '2000-01-01') AT TIME ZONE 'Asia/Seoul', 'MM-DD HH24:MI') AS "5분 구간",
       count(*) AS "적재 건수"
FROM status_history
WHERE source = 'public' AND received_at > now() - interval '3 hours'
GROUP BY 1
ORDER BY 1;


-- [8] 수집 지연 — 충전기 상태가 바뀐 뒤 DB 에 들어오기까지 (최근 24시간 · 상태 수집분만)
SELECT count(*) AS "건수",
       percentile_cont(0.5)  WITHIN GROUP (ORDER BY h.received_at - h.changed_at) AS "중앙값",
       percentile_cont(0.95) WITHIN GROUP (ORDER BY h.received_at - h.changed_at) AS "95%",
       max(h.received_at - h.changed_at) AS "최대"
FROM status_history h
JOIN collect_log l ON l.run_id = h.run_id
WHERE h.received_at > now() - interval '24 hours'
  AND l.operation = 'getChargerStatus';


-- [9] 원본 보관 확인 — 종류별 마지막 원본 파일 위치 (s3://chargeops-raw/… 면 S3 에 보관 중)
SELECT DISTINCT ON (operation)
       CASE operation WHEN 'getChargerStatus' THEN '상태' WHEN 'getChargerInfo' THEN '기본정보' ELSE operation END AS "종류",
       to_char(started_at AT TIME ZONE 'Asia/Seoul', 'MM-DD HH24:MI:SS') AS "시작",
       raw_key AS "원본 위치"
FROM collect_log
WHERE status = 'ok' AND raw_key IS NOT NULL
ORDER BY operation, run_id DESC;


-- [10] 테이블별 건수 — 들어갔는지 한눈에
SELECT 'operator (운영기관)' AS "테이블", count(*) AS "건수" FROM operator
UNION ALL SELECT 'station (충전소)', count(*) FROM station
UNION ALL SELECT 'charger (충전기)', count(*) FROM charger
UNION ALL SELECT 'charger_status_current (현재 상태)', count(*) FROM charger_status_current
UNION ALL SELECT 'status_history (상태 이력)', count(*) FROM status_history
UNION ALL SELECT 'collect_log (수집 기록)', count(*) FROM collect_log;


-- [11] 지금 상태별 충전기 수 — 대시보드의 기본 숫자
SELECT c.code AS "코드", c.name AS "상태", count(*) AS "충전기",
       round(100.0 * count(*) / sum(count(*)) OVER (), 1) AS "비율(%)"
FROM charger_status_current cur
JOIN code_status c ON c.code = cur.status_code
GROUP BY c.code, c.name
ORDER BY 3 DESC;


-- [12] 운영기관별 충전기 수 상위 10곳 (충전기 → 충전소 → 운영기관)
SELECT o.operator_id AS "기관 ID", o.name AS "기관명", count(*) AS "충전기"
FROM charger ch
JOIN station s USING (station_id)
JOIN operator o ON o.operator_id = s.operator_id
WHERE NOT ch.is_deleted
GROUP BY o.operator_id, o.name
ORDER BY 3 DESC
LIMIT 10;


-- [13] 충전기 한 대의 최근 이력 20건 — 최근 24시간 동안 가장 자주 바뀐 공공 충전기를 자동으로 고름
--      특정 충전기를 보려면 pick 안의 SELECT 를  SELECT '충전기ID'::text AS charger_id  로 바꿔서 실행
WITH pick AS (
  SELECT charger_id FROM status_history
  WHERE source = 'public' AND received_at > now() - interval '24 hours'
  GROUP BY charger_id ORDER BY count(*) DESC, charger_id LIMIT 1
)
SELECT h.charger_id AS "충전기",
       to_char(h.changed_at AT TIME ZONE 'Asia/Seoul', 'MM-DD HH24:MI:SS')  AS "바뀐 시각",
       h.status_code AS "코드", c.name AS "상태",
       to_char(h.received_at AT TIME ZONE 'Asia/Seoul', 'MM-DD HH24:MI:SS') AS "받은 시각",
       h.run_id
FROM status_history h
JOIN pick USING (charger_id)
JOIN code_status c ON c.code = h.status_code
ORDER BY h.changed_at DESC
LIMIT 20;
