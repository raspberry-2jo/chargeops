-- ============================================================================
-- V20261013113100__load_functions.sql  (WBS 2.6 DDL v1)
--   적재 대기(stg_*) → 본 테이블 적재 함수 (수집기 apps/collector 가 부름)
--   출처: 실습 스키마 v1.1 의 02_load_functions.sql (내용 같음, psql 전용 명령만 뺌). 표 정의는 docs/db/table-definitions.md
--   적용 순서 · 방법은 db/migrations/README.md
-- ============================================================================
-- ============================================================================
-- ChargeOps 운영 DB 스키마 v1.1 — 02_load_functions.sql
--   적재 대기(stg_*) → 본 테이블로 옮기는 함수. 변환 규칙을 DB 안에 두어, 수집기를 바꾸거나
--   손으로 넣어도 같은 규칙이 적용된다. 값이 이상한 행이 와도 멈추지 않고 그 값만 비운다
--   CREATE OR REPLACE 라서 고친 뒤 다시 실행해도 된다 (데이터는 그대로)
--   v1.1: charger_type → charger_type_code, kst14 등을 예외 없는 SQL 함수로 (전국 52만 행 속도),
--         _register_codes 를 공통 함수로 뺌
-- ============================================================================

-- 14자리 한국 시간 글자(YYYYMMDDHHMMSS) → 시각. 형식이 틀리거나 없는 날짜(2월 30일 등)면 NULL
--   정규식으로 시 · 분 · 초 범위를 보고, 날짜는 pg_input_is_valid(PostgreSQL 16+)로 "바꿀 수 있는가"만 검사
--   → 행마다 예외 처리(EXCEPTION 블록)를 쓰지 않아 전국 52만 행에서도 빠르다
--   '20261001T090000' 처럼 가운데 T 를 넣으면 PostgreSQL 이 ISO 8601 기본 형식으로 읽는다
--   앞뒤 공백까지 깎으면(btrim) 52만 행 기준 1초 넘게 더 걸려, 14자리 숫자가 아니면 그대로 "형식 오류"로 본다
CREATE OR REPLACE FUNCTION kst14(p text) RETURNS timestamptz
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT CASE
    WHEN p ~ '^\d{8}([01]\d|2[0-3])[0-5]\d[0-5]\d$'
     AND pg_input_is_valid(left(p, 8) || 'T' || right(p, 6), 'timestamp')
    THEN (left(p, 8) || 'T' || right(p, 6))::timestamp AT TIME ZONE 'Asia/Seoul'
  END
$$;
COMMENT ON FUNCTION kst14(text) IS '14자리 한국 시간 글자를 시각으로. 형식이 틀리면 NULL';

-- 글자 → 숫자. 숫자가 아니거나 범위를 벗어나면 NULL (예: 위도 -90~90, 용량 0~99999)
CREATE OR REPLACE FUNCTION num_in_range(p text, p_min numeric, p_max numeric) RETURNS numeric
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT CASE
    WHEN pg_input_is_valid(btrim(p), 'numeric') AND btrim(p) <> 'NaN'
     AND btrim(p)::numeric BETWEEN p_min AND p_max
    THEN btrim(p)::numeric
  END
$$;
COMMENT ON FUNCTION num_in_range(text, numeric, numeric) IS '글자를 숫자로. 숫자가 아니거나 범위 밖이면 NULL';

-- Y → true, N → false, 그 밖(빈 값 포함) → NULL
CREATE OR REPLACE FUNCTION yn(p text) RETURNS boolean
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT CASE upper(btrim(p)) WHEN 'Y' THEN true WHEN 'N' THEN false END
$$;
COMMENT ON FUNCTION yn(text) IS 'Y는 true, N은 false, 그 밖은 NULL';

-- 처음 보는 코드를 '(가이드 미등록)'으로 등록하고, 새로 등록한 코드 목록을 돌려준다
-- (등록하지 않으면 외래키 오류로 수집 전체가 실패한다)
CREATE OR REPLACE FUNCTION _register_codes(p_table text, p_codes text[]) RETURNS text[]
LANGUAGE plpgsql AS $$
DECLARE r text[];
BEGIN
  EXECUTE format($q$
    WITH ins AS (
      INSERT INTO %I (code, name, in_guide)
      SELECT DISTINCT c, '(가이드 미등록)', false
      FROM unnest($1) AS c WHERE nullif(c, '') IS NOT NULL
      ON CONFLICT (code) DO NOTHING
      RETURNING code)
    SELECT coalesce(array_agg(code ORDER BY code), '{}') FROM ins$q$, p_table)
  INTO r USING p_codes;
  RETURN r;
END $$;
COMMENT ON FUNCTION _register_codes(text, text[]) IS '(내부용) 처음 보는 코드를 (가이드 미등록)으로 등록하고 그 목록을 돌려줌';

-- ───────── 기본정보 적재: 코드 등록 → 운영기관 → 충전소 → 충전기 → 상태 ─────────
CREATE OR REPLACE FUNCTION load_charger_info(p_run_id bigint DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql
SET work_mem = '64MB'      -- 이 함수 안에서만: 52만 행 정렬 · 해시를 메모리에서 (서버 기본값은 그대로)
AS $$
DECLARE
  v_rows int; v_valid int; v_unique int;
  v_ops int; v_st int; v_ch int; v_ch_new int; v_hist int; v_cur int; v_st_skip int;
  v_new jsonb;
BEGIN
  SELECT count(*) INTO v_rows FROM stg_charger_info;

  -- 0) 충전기 ID 를 만들 수 있는 행만 (statId · chgerId 필수). 같은 충전기가 두 번 오면 뒤 쪽의 값
  IF to_regclass('pg_temp._info') IS NOT NULL THEN DROP TABLE _info; END IF;   -- 같은 트랜잭션에서 두 번 부를 때
  CREATE TEMP TABLE _info ON COMMIT DROP AS
    SELECT DISTINCT ON (btrim(stat_id), btrim(chger_id))
           s.*, btrim(stat_id) AS sid, btrim(chger_id) AS cid
    FROM stg_charger_info s
    WHERE nullif(btrim(stat_id), '') IS NOT NULL AND nullif(btrim(chger_id), '') IS NOT NULL
    ORDER BY btrim(stat_id), btrim(chger_id), page_no DESC;
  GET DIAGNOSTICS v_unique = ROW_COUNT;
  SELECT count(*) INTO v_valid FROM stg_charger_info
   WHERE nullif(btrim(stat_id), '') IS NOT NULL AND nullif(btrim(chger_id), '') IS NOT NULL;
  CREATE INDEX ON _info (sid, cid);   -- 충전소마다 "번호가 가장 작은 충전기" 를 정렬 없이 고르려고
  ANALYZE _info;                      -- 임시 테이블은 통계가 없어 조인 계획이 나빠질 수 있다

  -- 1) 코드: 외래키가 걸린 컬럼에 들어갈 값 중 처음 보는 것을 먼저 등록
  v_new := jsonb_strip_nulls(jsonb_build_object(
    'code_status',              to_jsonb(nullif(_register_codes('code_status',              ARRAY(SELECT DISTINCT btrim(stat)        FROM _info)), '{}')),
    'code_charger_type',        to_jsonb(nullif(_register_codes('code_charger_type',        ARRAY(SELECT DISTINCT btrim(chger_type)  FROM _info)), '{}')),
    'code_region',              to_jsonb(nullif(_register_codes('code_region',              ARRAY(SELECT DISTINCT btrim(zcode)       FROM _info)), '{}')),
    'code_district',            to_jsonb(nullif(_register_codes('code_district',            ARRAY(SELECT DISTINCT btrim(zscode)      FROM _info)), '{}')),
    'code_station_kind',        to_jsonb(nullif(_register_codes('code_station_kind',        ARRAY(SELECT DISTINCT btrim(kind)        FROM _info)), '{}')),
    'code_station_kind_detail', to_jsonb(nullif(_register_codes('code_station_kind_detail', ARRAY(SELECT DISTINCT btrim(kind_detail) FROM _info)), '{}'))));

  -- 2) 운영기관: 기관 ID 마다 한 행. 새 기관이거나 값이 실제로 바뀐 기관만 넣고 고친다
  --    (바뀌지 않은 행까지 ON CONFLICT 로 보내면 행 잠금 · 쓰기가 생겨 느려지고 updated_at 이 의미를 잃는다)
  WITH src AS (
    SELECT DISTINCT ON (btrim(busi_id)) btrim(busi_id) AS operator_id,
           nullif(btrim(bnm), '') AS name, nullif(btrim(busi_nm), '') AS operating_org,
           nullif(btrim(busi_call), '') AS contact_tel
    FROM _info WHERE nullif(btrim(busi_id), '') IS NOT NULL
    ORDER BY btrim(busi_id), sid, cid
  ), chg AS (
    SELECT src.* FROM src LEFT JOIN operator o USING (operator_id)
    WHERE o.operator_id IS NULL
       OR (o.name, o.operating_org, o.contact_tel)
          IS DISTINCT FROM (coalesce(src.name, o.name), coalesce(src.operating_org, o.operating_org),
                            coalesce(src.contact_tel, o.contact_tel))
  ), up AS (
    INSERT INTO operator AS o (operator_id, name, operating_org, contact_tel)
    SELECT * FROM chg
    ON CONFLICT (operator_id) DO UPDATE
       SET name = coalesce(EXCLUDED.name, o.name),
           operating_org = coalesce(EXCLUDED.operating_org, o.operating_org),
           contact_tel = coalesce(EXCLUDED.contact_tel, o.contact_tel),
           updated_at = now()
    RETURNING 1)
  SELECT (SELECT count(*) FROM src) INTO v_ops;

  -- 3) 충전소: 충전소마다 한 행. 15개 필드는 충전기 번호가 가장 작은 행의 값 (stg_info_conflicts 로 점검)
  --    좌표는 저장 자릿수(소수 7자리)로 미리 반올림 → 같은 값을 "바뀜"으로 보지 않게
  WITH pick AS (                          -- 먼저 고르고(17만 행) 나서 변환한다 (52만 행 모두 변환하지 않게)
    SELECT DISTINCT ON (sid) * FROM _info ORDER BY sid, cid
  ), src AS (
    SELECT sid AS station_id, nullif(btrim(busi_id), '') AS operator_id,
           nullif(btrim(stat_nm), '') AS name, nullif(btrim(addr), '') AS addr,
           round(num_in_range(lat, -90, 90), 7) AS lat, round(num_in_range(lng, -180, 180), 7) AS lng,
           nullif(btrim(zcode), '') AS region_code, nullif(btrim(zscode), '') AS district_code,
           nullif(btrim(kind), '') AS kind_code, nullif(btrim(kind_detail), '') AS kind_detail_code,
           nullif(btrim(use_time), '') AS use_time, yn(parking_free) AS parking_free,
           nullif(btrim(note), '') AS note, yn(limit_yn) AS is_restricted,
           nullif(btrim(limit_detail), '') AS restrict_reason, yn(traffic_yn) AS traffic_yn
    FROM pick
  ), chg AS (
    SELECT src.* FROM src LEFT JOIN station s USING (station_id)
    WHERE s.station_id IS NULL
       OR (s.operator_id, s.name, s.addr, s.lat, s.lng, s.region_code, s.district_code, s.kind_code,
           s.kind_detail_code, s.use_time, s.parking_free, s.note, s.is_restricted, s.restrict_reason, s.traffic_yn)
          IS DISTINCT FROM
          (src.operator_id, src.name, src.addr, src.lat, src.lng, src.region_code, src.district_code, src.kind_code,
           src.kind_detail_code, src.use_time, src.parking_free, src.note, src.is_restricted, src.restrict_reason,
           src.traffic_yn)
  ), up AS (
    INSERT INTO station AS s (station_id, operator_id, name, addr, lat, lng, region_code, district_code,
                              kind_code, kind_detail_code, use_time, parking_free, note, is_restricted,
                              restrict_reason, traffic_yn, source)
    SELECT chg.*, 'public' FROM chg
    ON CONFLICT (station_id) DO UPDATE
       SET operator_id = EXCLUDED.operator_id, name = EXCLUDED.name, addr = EXCLUDED.addr,
           lat = EXCLUDED.lat, lng = EXCLUDED.lng, region_code = EXCLUDED.region_code,
           district_code = EXCLUDED.district_code, kind_code = EXCLUDED.kind_code,
           kind_detail_code = EXCLUDED.kind_detail_code, use_time = EXCLUDED.use_time,
           parking_free = EXCLUDED.parking_free, note = EXCLUDED.note, is_restricted = EXCLUDED.is_restricted,
           restrict_reason = EXCLUDED.restrict_reason, traffic_yn = EXCLUDED.traffic_yn, updated_at = now()
    RETURNING 1)
  SELECT (SELECT count(*) FROM src) INTO v_st;

  -- 4) 충전기: 매번 last_seen_at 을 새로 → "오래 안 보인 충전기 = API 에서 사라진 충전기"
  WITH up AS (
    INSERT INTO charger AS c (charger_id, station_id, charger_no, source, charger_type_code, output_kw, power_type,
                              charge_method, maker, install_year, addr_detail, location_detail, floor_no,
                              floor_type, is_deleted, delete_reason, last_seen_at)
    SELECT sid || '-' || cid, sid, cid, 'public', nullif(btrim(chger_type), ''),
           num_in_range(output, 0, 99999), nullif(btrim(power_type), ''), nullif(btrim(method), ''),
           nullif(btrim(maker), ''),
           CASE WHEN btrim(year) ~ '^\d{4}$' THEN btrim(year)::smallint END,
           nullif(btrim(addr_detail), ''), nullif(btrim(location), ''), nullif(btrim(floor_num), ''),
           nullif(btrim(floor_type), ''), coalesce(yn(del_yn), false), nullif(btrim(del_detail), ''), now()
    FROM _info
    ON CONFLICT (charger_id) DO UPDATE
       SET charger_type_code = EXCLUDED.charger_type_code, output_kw = EXCLUDED.output_kw,
           power_type = EXCLUDED.power_type, charge_method = EXCLUDED.charge_method, maker = EXCLUDED.maker,
           install_year = EXCLUDED.install_year, addr_detail = EXCLUDED.addr_detail,
           location_detail = EXCLUDED.location_detail, floor_no = EXCLUDED.floor_no,
           floor_type = EXCLUDED.floor_type, is_deleted = EXCLUDED.is_deleted,
           delete_reason = EXCLUDED.delete_reason, last_seen_at = now()
    RETURNING (xmax = 0) AS inserted)      -- xmax = 0 이면 새로 넣은 행, 아니면 고친 행
  SELECT count(*), count(*) FILTER (WHERE inserted) INTO v_ch, v_ch_new FROM up;

  -- 5) 상태: 충전기마다 "지금 상태" 한 건 → 이력의 출발점 + 상태 수집이 놓친 변경을 하루 한 번 메움
  IF to_regclass('pg_temp._info_st') IS NOT NULL THEN DROP TABLE _info_st; END IF;   -- 같은 트랜잭션에서 두 번 부를 때
  CREATE TEMP TABLE _info_st ON COMMIT DROP AS
    SELECT sid || '-' || cid AS charger_id, nullif(btrim(stat), '') AS status_code, kst14(stat_upd_dt) AS changed_at,
           kst14(last_tsdt) AS a, kst14(last_tedt) AS b, kst14(now_tsdt) AS c
    FROM _info;
  DELETE FROM _info_st WHERE status_code IS NULL OR changed_at IS NULL;   -- 상태나 시각이 없으면 상태만 건너뜀
  GET DIAGNOSTICS v_st_skip = ROW_COUNT;
  ANALYZE _info_st;

  -- 이미 있는 (충전기, 시각, 상태)는 미리 걸러 낸다 (대부분이 "어제와 같은 상태"라 이 쪽이 훨씬 빠름).
  -- ON CONFLICT 는 그 사이 다른 곳에서 같은 행을 넣었을 때를 위한 안전장치로 남겨 둔다
  WITH ins AS (
    INSERT INTO status_history (changed_at, charger_id, source, status_code,
                                last_charge_start_at, last_charge_end_at, charging_start_at, run_id)
    SELECT n.changed_at, n.charger_id, 'public', n.status_code, n.a, n.b, n.c, p_run_id FROM _info_st n
    WHERE NOT EXISTS (SELECT 1 FROM status_history h
                      WHERE h.charger_id = n.charger_id AND h.changed_at = n.changed_at
                        AND h.status_code = n.status_code)
    ON CONFLICT (charger_id, changed_at, status_code) DO NOTHING
    RETURNING 1)
  SELECT count(*) INTO v_hist FROM ins;

  WITH chg AS (           -- 현재 상태가 없거나, 받은 변경이 더 새로운 충전기만
    SELECT n.* FROM _info_st n LEFT JOIN charger_status_current cur USING (charger_id)
    WHERE cur.charger_id IS NULL OR n.changed_at > cur.changed_at
  ), up AS (
    INSERT INTO charger_status_current AS cur (charger_id, status_code, changed_at, last_charge_start_at,
                                               last_charge_end_at, charging_start_at, source)
    SELECT charger_id, status_code, changed_at, a, b, c, 'public' FROM chg
    ON CONFLICT (charger_id) DO UPDATE
       SET status_code = EXCLUDED.status_code, changed_at = EXCLUDED.changed_at,
           last_charge_start_at = EXCLUDED.last_charge_start_at, last_charge_end_at = EXCLUDED.last_charge_end_at,
           charging_start_at = EXCLUDED.charging_start_at, source = EXCLUDED.source, received_at = now()
     WHERE EXCLUDED.changed_at > cur.changed_at          -- 더 새로운 변경일 때만 (과거로 되돌아가지 않게)
    RETURNING 1)
  SELECT count(*) INTO v_cur FROM up;

  RETURN jsonb_build_object(
    'rows', v_rows, 'skipped_invalid', v_rows - v_valid, 'duplicates', v_valid - v_unique,
    'operators', v_ops, 'stations', v_st, 'chargers', v_ch, 'chargers_new', v_ch_new,
    'history_inserted', v_hist, 'current_updated', v_cur, 'status_skipped', v_st_skip,
    'new_codes', v_new);
END $$;
COMMENT ON FUNCTION load_charger_info(bigint) IS '기본정보 적재. 코드 등록 → 운영기관 → 충전소 → 충전기 → 상태 순서로 넣고 건수를 JSON으로 돌려줌';

-- ───────── 상태 적재: 새 변경만 이력에 쌓고 현재 상태를 고친다 ─────────
CREATE OR REPLACE FUNCTION load_charger_status(p_run_id bigint DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql
SET work_mem = '64MB'
AS $$
DECLARE v_rows int; v_valid int; v_unknown int; v_hist int; v_cur int; v_new jsonb;
BEGIN
  SELECT count(*) INTO v_rows FROM stg_charger_status;

  IF to_regclass('pg_temp._st') IS NOT NULL THEN DROP TABLE _st; END IF;   -- 같은 트랜잭션에서 두 번 부를 때
  CREATE TEMP TABLE _st ON COMMIT DROP AS
    SELECT btrim(stat_id) || '-' || btrim(chger_id) AS charger_id, btrim(stat) AS status_code,
           kst14(stat_upd_dt) AS changed_at, kst14(last_tsdt) AS a, kst14(last_tedt) AS b, kst14(now_tsdt) AS c
    FROM stg_charger_status
    WHERE nullif(btrim(stat_id), '') IS NOT NULL AND nullif(btrim(chger_id), '') IS NOT NULL
      AND nullif(btrim(stat), '') IS NOT NULL AND kst14(stat_upd_dt) IS NOT NULL;
  GET DIAGNOSTICS v_valid = ROW_COUNT;

  -- 기본정보에 없는 충전기는 건너뛴다 (이력이 charger 를 참조하므로 넣을 수 없음). 건수는 알려 준다
  DELETE FROM _st WHERE NOT EXISTS (SELECT 1 FROM charger c WHERE c.charger_id = _st.charger_id);
  GET DIAGNOSTICS v_unknown = ROW_COUNT;

  v_new := jsonb_strip_nulls(jsonb_build_object(
    'code_status', to_jsonb(nullif(_register_codes('code_status', ARRAY(SELECT DISTINCT status_code FROM _st)), '{}'))));

  WITH ins AS (
    INSERT INTO status_history (changed_at, charger_id, source, status_code,
                                last_charge_start_at, last_charge_end_at, charging_start_at, run_id)
    SELECT changed_at, charger_id, 'public', status_code, a, b, c, p_run_id FROM _st
    ON CONFLICT (charger_id, changed_at, status_code) DO NOTHING      -- 이미 받은 변경 → 0건 (변경분만 적재)
    RETURNING 1)
  SELECT count(*) INTO v_hist FROM ins;

  WITH latest AS (       -- 한 번에 같은 충전기의 변경이 여러 건이면 가장 늦은 것이 현재 상태
    SELECT DISTINCT ON (charger_id) * FROM _st ORDER BY charger_id, changed_at DESC
  ), up AS (
    INSERT INTO charger_status_current AS cur (charger_id, status_code, changed_at, last_charge_start_at,
                                               last_charge_end_at, charging_start_at, source)
    SELECT charger_id, status_code, changed_at, a, b, c, 'public' FROM latest
    ON CONFLICT (charger_id) DO UPDATE
       SET status_code = EXCLUDED.status_code, changed_at = EXCLUDED.changed_at,
           last_charge_start_at = EXCLUDED.last_charge_start_at, last_charge_end_at = EXCLUDED.last_charge_end_at,
           charging_start_at = EXCLUDED.charging_start_at, source = EXCLUDED.source, received_at = now()
     WHERE EXCLUDED.changed_at > cur.changed_at
    RETURNING 1)
  SELECT count(*) INTO v_cur FROM up;

  RETURN jsonb_build_object('rows', v_rows, 'history_inserted', v_hist, 'current_updated', v_cur,
                            'skipped_invalid', v_rows - v_valid, 'unknown_charger', v_unknown,
                            'new_codes', v_new);
END $$;
COMMENT ON FUNCTION load_charger_status(bigint) IS '상태 적재. 새 변경만 이력에 쌓고 현재 상태를 고침';

-- ───────── 설계 가정 점검: 충전소(운영기관) 단위로 묶은 필드가 실제로 한 가지 값인가 ─────────
-- 결과 {} = 가정이 맞음. {"station.useTime": 12} = 충전소 12곳에서 충전기마다 값이 달랐음
CREATE OR REPLACE FUNCTION stg_info_conflicts() RETURNS jsonb
LANGUAGE sql STABLE AS $$
  WITH per_key AS (
    SELECT jsonb_build_object(
      'station.statNm',      count(DISTINCT coalesce(btrim(stat_nm), '')),
      'station.addr',        count(DISTINCT coalesce(btrim(addr), '')),
      'station.lat',         count(DISTINCT coalesce(num_in_range(lat, -90, 90)::text, '')),
      'station.lng',         count(DISTINCT coalesce(num_in_range(lng, -180, 180)::text, '')),
      'station.useTime',     count(DISTINCT coalesce(btrim(use_time), '')),
      'station.busiId',      count(DISTINCT coalesce(btrim(busi_id), '')),
      'station.zcode',       count(DISTINCT coalesce(btrim(zcode), '')),
      'station.zscode',      count(DISTINCT coalesce(btrim(zscode), '')),
      'station.kind',        count(DISTINCT coalesce(btrim(kind), '')),
      'station.kindDetail',  count(DISTINCT coalesce(btrim(kind_detail), '')),
      'station.parkingFree', count(DISTINCT coalesce(btrim(parking_free), '')),
      'station.note',        count(DISTINCT coalesce(btrim(note), '')),
      'station.limitYn',     count(DISTINCT coalesce(btrim(limit_yn), '')),
      'station.limitDetail', count(DISTINCT coalesce(btrim(limit_detail), '')),
      'station.trafficYn',   count(DISTINCT coalesce(btrim(traffic_yn), ''))) AS j
    FROM stg_charger_info WHERE nullif(btrim(stat_id), '') IS NOT NULL
    GROUP BY btrim(stat_id)
    UNION ALL
    SELECT jsonb_build_object(
      'operator.bnm',      count(DISTINCT coalesce(btrim(bnm), '')),
      'operator.busiNm',   count(DISTINCT coalesce(btrim(busi_nm), '')),
      'operator.busiCall', count(DISTINCT coalesce(btrim(busi_call), '')))
    FROM stg_charger_info WHERE nullif(btrim(busi_id), '') IS NOT NULL
    GROUP BY btrim(busi_id)
  )
  SELECT coalesce(jsonb_object_agg(key, n ORDER BY key), '{}'::jsonb)
  FROM (SELECT e.key, count(*) AS n FROM per_key, jsonb_each(per_key.j) AS e
        WHERE (e.value)::int > 1 GROUP BY e.key) x
$$;
COMMENT ON FUNCTION stg_info_conflicts() IS '충전소·운영기관 단위로 묶은 필드가 실제로 한 가지 값인지 점검. {}이면 문제없음';
