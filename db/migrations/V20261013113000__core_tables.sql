-- ============================================================================
-- V20261013113000__core_tables.sql  (WBS 2.6 DDL v1)
--   코드표 · 마스터 · 상태 · 수집 테이블 14개 + 상태 이력 월 파티션
--   출처: 실습 스키마 v1.1 의 01_tables.sql (내용 같음, psql 전용 명령만 뺌). 표 정의는 docs/db/table-definitions.md
--   적용 순서 · 방법은 db/migrations/README.md
-- ============================================================================
-- ============================================================================
-- ChargeOps 운영 DB 스키마 v1.1 — 01_tables.sql
--   데이터팀 「운영 DB 스키마 v1 초안」(2026-10-08) 의 테이블 14개 + 상태 이력 파티션
--   v1 → v1.1 변경: charger.charger_type → charger.charger_type_code (다른 코드 컬럼과 이름 규칙 통일)
--   아키텍처 확장(장애 · 승인 · AI · 계측 · 권한)은 10_ 이후 파일이 ALTER/CREATE 로 덧붙인다
--
--   이 파일은 빈 DB 에 한 번만 실행한다 (다시 만들려면 DB 를 지우고 처음부터)
-- ============================================================================
SET client_min_messages = warning;   -- "이미 있음" 같은 잡음 대신 결과만 보이게
SET TIME ZONE 'Asia/Seoul';          -- 파티션 경계를 한국 시간 자정으로 계산

-- ───────── 1. 공통코드 6개 (구조가 모두 같다: code · name · in_guide) ─────────
CREATE TABLE code_status (
  code     text PRIMARY KEY,
  name     text NOT NULL,
  in_guide boolean NOT NULL DEFAULT true
);
CREATE TABLE code_charger_type        (LIKE code_status INCLUDING ALL);
CREATE TABLE code_region              (LIKE code_status INCLUDING ALL);
CREATE TABLE code_district            (LIKE code_status INCLUDING ALL);
CREATE TABLE code_station_kind        (LIKE code_status INCLUDING ALL);
CREATE TABLE code_station_kind_detail (LIKE code_status INCLUDING ALL);

COMMENT ON TABLE code_status              IS '충전기 상태 코드 (API 필드 stat). 예: 2 충전대기, 3 충전중';
COMMENT ON TABLE code_charger_type        IS '충전기 타입 코드 (API 필드 chgerType). 예: 02 AC완속, 04 DC콤보';
COMMENT ON TABLE code_region              IS '시도 코드 (API 필드 zcode). 예: 11 서울특별시, 36 세종특별자치시';
COMMENT ON TABLE code_district            IS '시군구 코드 (API 필드 zscode). 예: 36110 세종특별자치시';
COMMENT ON TABLE code_station_kind        IS '충전소 구분 코드 (API 필드 kind). 예: B0 주차시설';
COMMENT ON TABLE code_station_kind_detail IS '충전소 구분 상세 코드 (API 필드 kindDetail). 예: B001 공영주차장';
COMMENT ON COLUMN code_status.code     IS '코드';
COMMENT ON COLUMN code_status.name     IS '코드의 이름. 가이드에 없던 코드는 (가이드 미등록)';
COMMENT ON COLUMN code_status.in_guide IS '가이드 코드표에 있으면 true, 실제 데이터에서 처음 나와 자동 등록됐으면 false';
COMMENT ON COLUMN code_charger_type.code     IS '코드';
COMMENT ON COLUMN code_charger_type.name     IS '코드의 이름';
COMMENT ON COLUMN code_charger_type.in_guide IS '가이드 코드표에 있으면 true';
COMMENT ON COLUMN code_region.code     IS '코드';
COMMENT ON COLUMN code_region.name     IS '코드의 이름';
COMMENT ON COLUMN code_region.in_guide IS '가이드 코드표에 있으면 true';
COMMENT ON COLUMN code_district.code     IS '코드';
COMMENT ON COLUMN code_district.name     IS '코드의 이름';
COMMENT ON COLUMN code_district.in_guide IS '가이드 코드표에 있으면 true';
COMMENT ON COLUMN code_station_kind.code     IS '코드';
COMMENT ON COLUMN code_station_kind.name     IS '코드의 이름';
COMMENT ON COLUMN code_station_kind.in_guide IS '가이드 코드표에 있으면 true';
COMMENT ON COLUMN code_station_kind_detail.code     IS '코드';
COMMENT ON COLUMN code_station_kind_detail.name     IS '코드의 이름';
COMMENT ON COLUMN code_station_kind_detail.in_guide IS '가이드 코드표에 있으면 true';

-- ───────── 2. 기준 정보: 운영기관 1:N 충전소 1:N 충전기 ─────────
CREATE TABLE operator (
  operator_id   text PRIMARY KEY,
  name          text,
  operating_org text,
  contact_tel   text,
  in_guide      boolean NOT NULL DEFAULT false,
  updated_at    timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE  operator               IS '운영기관. 가이드의 기관 코드표로 먼저 채우고, 수집할 때 API 값으로 갱신한다';
COMMENT ON COLUMN operator.operator_id   IS '기관 ID (API 필드 busiId). 예: ME';
COMMENT ON COLUMN operator.name          IS '기관명 (API 필드 bnm). 예: 환경부';
COMMENT ON COLUMN operator.operating_org IS '운영기관명 (API 필드 busiNm). 예: 한국자동차환경협회';
COMMENT ON COLUMN operator.contact_tel   IS '운영기관 연락처 (API 필드 busiCall)';
COMMENT ON COLUMN operator.in_guide      IS '가이드의 기관 코드표에 있으면 true, 실제 데이터에서 처음 나왔으면 false';
COMMENT ON COLUMN operator.updated_at    IS '이 행을 마지막으로 고친 시각';

CREATE TABLE station (
  station_id       text PRIMARY KEY,
  operator_id      text REFERENCES operator,
  name             text,
  addr             text,
  lat              numeric(10,7),
  lng              numeric(10,7),
  region_code      text REFERENCES code_region,
  district_code    text REFERENCES code_district,
  kind_code        text REFERENCES code_station_kind,
  kind_detail_code text REFERENCES code_station_kind_detail,
  use_time         text,
  parking_free     boolean,
  note             text,
  is_restricted    boolean,
  restrict_reason  text,
  traffic_yn       boolean,
  source           text NOT NULL DEFAULT 'public' CHECK (source IN ('public','model','virtual')),
  first_seen_at    timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE  station                  IS '충전소. 한 행 = 충전소 1곳';
COMMENT ON COLUMN station.station_id       IS '충전소 ID (API 필드 statId). 예: PK000434';
COMMENT ON COLUMN station.operator_id      IS '운영기관 (API 필드 busiId) → operator.operator_id';
COMMENT ON COLUMN station.name             IS '충전소명 (API 필드 statNm)';
COMMENT ON COLUMN station.addr             IS '주소 (API 필드 addr)';
COMMENT ON COLUMN station.lat              IS '위도 (API 필드 lat). -90~90을 벗어난 값은 비운다';
COMMENT ON COLUMN station.lng              IS '경도 (API 필드 lng). -180~180을 벗어난 값은 비운다';
COMMENT ON COLUMN station.region_code      IS '시도 코드 (API 필드 zcode) → code_region.code';
COMMENT ON COLUMN station.district_code    IS '시군구 코드 (API 필드 zscode) → code_district.code';
COMMENT ON COLUMN station.kind_code        IS '충전소 구분 코드 (API 필드 kind) → code_station_kind.code';
COMMENT ON COLUMN station.kind_detail_code IS '충전소 구분 상세 코드 (API 필드 kindDetail) → code_station_kind_detail.code';
COMMENT ON COLUMN station.use_time         IS '이용 가능 시간 (API 필드 useTime). 예: 24시간 이용가능';
COMMENT ON COLUMN station.parking_free     IS '주차료 무료 여부 (API 필드 parkingFree). Y=true, N=false, 그 밖은 비움';
COMMENT ON COLUMN station.note             IS '충전소 안내 (API 필드 note)';
COMMENT ON COLUMN station.is_restricted    IS '이용자 제한 여부 (API 필드 limitYn). Y=true';
COMMENT ON COLUMN station.restrict_reason  IS '이용 제한 사유 (API 필드 limitDetail)';
COMMENT ON COLUMN station.traffic_yn       IS '편의제공 여부 (API 필드 trafficYn). 가이드에 뜻 설명이 없어 이름을 그대로 둠';
COMMENT ON COLUMN station.source           IS '데이터 출처. public 공공 API, model 모형 충전기, virtual 가상 충전기';
COMMENT ON COLUMN station.first_seen_at    IS '이 충전소가 처음 들어온 시각';
COMMENT ON COLUMN station.updated_at       IS '이 행을 마지막으로 고친 시각';

CREATE TABLE charger (
  charger_id        text PRIMARY KEY,
  station_id        text NOT NULL REFERENCES station,
  charger_no        text NOT NULL,
  source            text NOT NULL DEFAULT 'public' CHECK (source IN ('public','model','virtual')),
  charger_type_code text REFERENCES code_charger_type,          -- v1.1: charger_type → charger_type_code
  output_kw         numeric(7,2),
  power_type        text,
  charge_method     text,
  maker             text,
  install_year      smallint,
  addr_detail       text,
  location_detail   text,
  floor_no          text,
  floor_type        text,
  is_deleted        boolean NOT NULL DEFAULT false,
  delete_reason     text,
  first_seen_at     timestamptz NOT NULL DEFAULT now(),
  last_seen_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (station_id, charger_no)
);
COMMENT ON TABLE  charger                   IS '충전기. 한 행 = 충전기 1대';
COMMENT ON COLUMN charger.charger_id        IS '충전기 고유 ID. 충전소ID + ''-'' + 충전기번호. 예: PK000434-01, 모형 CH-01, 가상 LT-0001';
COMMENT ON COLUMN charger.station_id        IS '소속 충전소 (API 필드 statId) → station.station_id';
COMMENT ON COLUMN charger.charger_no        IS '충전소 안에서의 충전기 번호 (API 필드 chgerId). 예: 01';
COMMENT ON COLUMN charger.source            IS '데이터 출처. public 공공 API, model 모형 충전기, virtual 가상 충전기';
COMMENT ON COLUMN charger.charger_type_code IS '충전기 타입 코드 (API 필드 chgerType) → code_charger_type.code. v1 의 charger_type';
COMMENT ON COLUMN charger.output_kw         IS '충전 용량 kW (API 필드 output). 숫자가 아니면 비움';
COMMENT ON COLUMN charger.power_type        IS '충전 속도 구분 (API 필드 powerType). 가이드 명세표에는 없고 실제 응답에 있는 필드';
COMMENT ON COLUMN charger.charge_method     IS '충전 방식 (API 필드 method). 단독 또는 동시';
COMMENT ON COLUMN charger.maker             IS '제조사 (API 필드 maker). 가이드에는 없고 실제 응답에 있는 필드';
COMMENT ON COLUMN charger.install_year      IS '설치 연도 (API 필드 year). 4자리 숫자가 아니면 비움';
COMMENT ON COLUMN charger.addr_detail       IS '주소 상세 (API 필드 addrDetail)';
COMMENT ON COLUMN charger.location_detail   IS '상세 위치 (API 필드 location)';
COMMENT ON COLUMN charger.floor_no          IS '층수 (API 필드 floorNum)';
COMMENT ON COLUMN charger.floor_type        IS '지상·지하 구분 (API 필드 floorType). F 지상, B 지하';
COMMENT ON COLUMN charger.is_deleted        IS '철거 등으로 삭제된 충전기면 true (API 필드 delYn). 행은 지우지 않고 표시만 한다';
COMMENT ON COLUMN charger.delete_reason     IS '삭제 사유 (API 필드 delDetail)';
COMMENT ON COLUMN charger.first_seen_at     IS '이 충전기가 처음 들어온 시각';
COMMENT ON COLUMN charger.last_seen_at      IS '기본정보 수집에서 마지막으로 확인된 시각. 오래된 값이면 API에서 사라진 충전기';

-- ───────── 3. 상태: 현재(충전기마다 1행) + 이력(바뀔 때마다 1행, 월 파티션) ─────────
CREATE TABLE charger_status_current (
  charger_id           text PRIMARY KEY REFERENCES charger,
  status_code          text NOT NULL REFERENCES code_status,
  changed_at           timestamptz NOT NULL,
  last_charge_start_at timestamptz,
  last_charge_end_at   timestamptz,
  charging_start_at    timestamptz,
  source               text NOT NULL DEFAULT 'public' CHECK (source IN ('public','model','virtual')),
  received_at          timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE  charger_status_current                      IS '충전기의 현재 상태. 한 행 = 충전기 1대의 가장 최근 상태';
COMMENT ON COLUMN charger_status_current.charger_id           IS '충전기 → charger.charger_id';
COMMENT ON COLUMN charger_status_current.status_code          IS '상태 코드 (API 필드 stat) → code_status.code';
COMMENT ON COLUMN charger_status_current.changed_at           IS '이 상태로 바뀐 시각 (API 필드 statUpdDt)';
COMMENT ON COLUMN charger_status_current.last_charge_start_at IS '마지막 충전 시작 일시 (API 필드 lastTsdt)';
COMMENT ON COLUMN charger_status_current.last_charge_end_at   IS '마지막 충전 종료 일시 (API 필드 lastTedt)';
COMMENT ON COLUMN charger_status_current.charging_start_at    IS '지금 진행 중인 충전의 시작 일시 (API 필드 nowTsdt)';
COMMENT ON COLUMN charger_status_current.source               IS '데이터 출처';
COMMENT ON COLUMN charger_status_current.received_at          IS '이 상태를 받은 시각';

CREATE TABLE status_history (
  id                   bigint GENERATED ALWAYS AS IDENTITY,
  changed_at           timestamptz NOT NULL,
  charger_id           text NOT NULL REFERENCES charger,
  source               text NOT NULL DEFAULT 'public' CHECK (source IN ('public','model','virtual')),
  status_code          text NOT NULL REFERENCES code_status,
  error_code           text,
  last_charge_start_at timestamptz,
  last_charge_end_at   timestamptz,
  charging_start_at    timestamptz,
  received_at          timestamptz NOT NULL DEFAULT now(),
  run_id               bigint,
  PRIMARY KEY (id, changed_at),                    -- 파티션 테이블의 PK 에는 파티션 기준 컬럼이 들어가야 한다
  UNIQUE (charger_id, changed_at, status_code)     -- 같은 변경을 두 번 받아도 한 번만 (멱등 적재)
) PARTITION BY RANGE (changed_at);
COMMENT ON TABLE  status_history                      IS '충전기 상태 이력. 한 행 = 상태가 1번 바뀐 것. 바뀐 시각(한국 시간) 기준 월 파티션';
COMMENT ON COLUMN status_history.id                   IS '이력 번호 (자동 증가)';
COMMENT ON COLUMN status_history.changed_at           IS '상태가 바뀐 시각 (API 필드 statUpdDt). 파티션을 나누는 기준';
COMMENT ON COLUMN status_history.charger_id           IS '충전기 → charger.charger_id';
COMMENT ON COLUMN status_history.source               IS '데이터 출처';
COMMENT ON COLUMN status_history.status_code          IS '바뀐 뒤의 상태 코드 (API 필드 stat) → code_status.code';
COMMENT ON COLUMN status_history.error_code           IS '오류 코드. 공공 API에는 없어 비어 있고, 모형·가상 충전기(OCPP errorCode)가 쓰는 자리';
COMMENT ON COLUMN status_history.last_charge_start_at IS '마지막 충전 시작 일시 (API 필드 lastTsdt)';
COMMENT ON COLUMN status_history.last_charge_end_at   IS '마지막 충전 종료 일시 (API 필드 lastTedt)';
COMMENT ON COLUMN status_history.charging_start_at    IS '진행 중인 충전의 시작 일시 (API 필드 nowTsdt)';
COMMENT ON COLUMN status_history.received_at          IS '이 행을 받아서 넣은 시각. changed_at 과의 차이가 수집 지연';
COMMENT ON COLUMN status_history.run_id               IS '이 행을 넣은 수집 실행 번호 (collect_log.run_id). OCPP 실시간 경로는 비움';

CREATE INDEX status_history_charger_idx ON status_history (charger_id, changed_at DESC);   -- 충전기 한 대의 이력
CREATE INDEX status_history_brin        ON status_history USING brin (changed_at);        -- 시간순으로 쌓이는 데이터에 작고 빠른 인덱스

-- 기본 파티션: 해당 달의 파티션이 없을 때 받는 자리 (기본정보의 "지금 상태"는 몇 년 전 시각일 수 있다)
CREATE TABLE status_history_default PARTITION OF status_history DEFAULT;

-- 그 날짜가 속한 달(한국 시간)의 파티션을 만든다. 이미 있으면 그대로 둔다
CREATE FUNCTION create_status_history_partition(p_month date) RETURNS text
LANGUAGE plpgsql AS $$
DECLARE
  m    date        := date_trunc('month', p_month)::date;
  name text        := format('status_history_%s', to_char(m, 'YYYY_MM'));
  lo   timestamptz := (m::timestamp AT TIME ZONE 'Asia/Seoul');
  hi   timestamptz := ((m + interval '1 month')::timestamp AT TIME ZONE 'Asia/Seoul');
BEGIN
  IF to_regclass(name) IS NOT NULL THEN
    RETURN name || ' (이미 있음)';
  END IF;
  EXECUTE format('CREATE TABLE %I PARTITION OF status_history FOR VALUES FROM (%L) TO (%L)', name, lo, hi);
  RETURN name;
END $$;
COMMENT ON FUNCTION create_status_history_partition(date) IS '그 날짜가 속한 달의 상태 이력 파티션을 만든다. 그 달의 행이 기본 파티션에 이미 있으면 실패한다';

-- 이번 달 · 다음 달 파티션 (실행한 날 기준)
SELECT create_status_history_partition((now() AT TIME ZONE 'Asia/Seoul')::date);
SELECT create_status_history_partition(((now() AT TIME ZONE 'Asia/Seoul') + interval '1 month')::date);

-- ───────── 4. 수집 기록 ─────────
CREATE TABLE collect_log (
  run_id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  operation     text NOT NULL,
  region_code   text,
  period_min    integer,
  started_at    timestamptz NOT NULL DEFAULT now(),
  finished_at   timestamptz,
  status        text NOT NULL DEFAULT 'running' CHECK (status IN ('running','ok','error')),
  api_calls     integer,
  rows_fetched  integer,
  rows_loaded   integer,
  raw_key       text,
  error_message text,
  detail        jsonb
);
COMMENT ON TABLE  collect_log               IS '수집 기록. 한 행 = 수집기를 1번 실행한 것. 실패한 실행도 남는다';
COMMENT ON COLUMN collect_log.run_id        IS '실행 번호 (자동 증가)';
COMMENT ON COLUMN collect_log.operation     IS '부른 API. getChargerInfo(기본정보) 또는 getChargerStatus(상태)';
COMMENT ON COLUMN collect_log.region_code   IS '수집 범위의 시도 코드. 비어 있으면 전국';
COMMENT ON COLUMN collect_log.period_min    IS '상태 수집에서 조회한 범위(분)';
COMMENT ON COLUMN collect_log.started_at    IS '시작 시각';
COMMENT ON COLUMN collect_log.finished_at   IS '끝난 시각';
COMMENT ON COLUMN collect_log.status        IS 'running 실행 중, ok 성공, error 실패';
COMMENT ON COLUMN collect_log.api_calls     IS 'API를 부른 횟수 (하루 호출 한도 계산용)';
COMMENT ON COLUMN collect_log.rows_fetched  IS 'API에서 받은 건수';
COMMENT ON COLUMN collect_log.rows_loaded   IS '실제로 반영된 건수. 기본정보는 충전기 수, 상태는 새로 쌓인 이력 수';
COMMENT ON COLUMN collect_log.raw_key       IS '원본 응답을 보관한 위치 (첫 쪽)';
COMMENT ON COLUMN collect_log.error_message IS '실패했을 때의 오류 내용';
COMMENT ON COLUMN collect_log.detail        IS '적재 함수가 돌려준 세부 결과 (건너뛴 건수, 새로 등록된 코드 등)';
CREATE INDEX collect_log_started_idx ON collect_log (started_at DESC);

-- ───────── 5. 적재 대기 (API 응답 그대로, 모두 text · 키 없음 · UNLOGGED) ─────────
-- UNLOGGED: WAL 을 쓰지 않아 빠르다. 대신 비정상 종료 시 비워지고 Standby 로 복제되지 않는다 (잠깐 거쳐 가는 자리라 괜찮다)
CREATE UNLOGGED TABLE stg_charger_info (
  stat_nm text, stat_id text, chger_id text, chger_type text, addr text, addr_detail text, location text,
  lat text, lng text, use_time text, busi_id text, bnm text, busi_nm text, busi_call text,
  stat text, stat_upd_dt text, last_tsdt text, last_tedt text, now_tsdt text,
  power_type text, output text, method text, zcode text, zscode text, kind text, kind_detail text,
  parking_free text, note text, limit_yn text, limit_detail text, del_yn text, del_detail text,
  traffic_yn text, year text, floor_num text, floor_type text, maker text,
  page_no integer
);
COMMENT ON TABLE stg_charger_info IS '적재 대기 · 기본정보. API 응답 37개 필드를 글자 그대로. 실행마다 비우고 채운다';
COMMENT ON COLUMN stg_charger_info.page_no IS '받은 쪽 번호';

CREATE UNLOGGED TABLE stg_charger_status (
  busi_id text, stat_id text, chger_id text, stat text, stat_upd_dt text,
  last_tsdt text, last_tedt text, now_tsdt text,
  page_no integer
);
COMMENT ON TABLE stg_charger_status IS '적재 대기 · 상태. API 응답 8개 필드를 글자 그대로. 실행마다 비우고 채운다';
COMMENT ON COLUMN stg_charger_status.page_no IS '받은 쪽 번호';

-- 조회용 인덱스 (외래키 쪽: 조인 · 삭제 검사 속도). charger.station_id 는 UNIQUE (station_id, charger_no) 가 같은 역할
CREATE INDEX station_operator_idx ON station (operator_id);
CREATE INDEX station_region_idx   ON station (region_code);

RESET client_min_messages;
