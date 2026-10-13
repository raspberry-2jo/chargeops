-- ============================================================================
-- V20261013113200__seed_codes.sql  (WBS 2.6 DDL v1)
--   공공 API 활용가이드 공통 코드 (기준 데이터)
--   출처: 실습 스키마 v1.1 의 03_seed_codes.sql (내용 같음, psql 전용 명령만 뺌). 표 정의는 docs/db/table-definitions.md
--   적용 순서 · 방법은 db/migrations/README.md
-- ============================================================================
-- ============================================================================
-- ChargeOps 운영 DB 스키마 v1.1 — 03_seed_codes.sql (공통코드 축약판)
--   한국환경공단 전기자동차 충전소 정보 OpenAPI 활용가이드의 코드표 중 자주 쓰는 것을 옮겼다
--   · 데이터팀의 03_seed_codes.sql(운영기관 196 + 코드 339 = 535건)이 있으면 그 파일을 대신 쓴다
--     (표 구조가 같아 그대로 실행된다. 이 파일과 둘 다 실행해도 ON CONFLICT 로 겹치지 않는다)
--   · 여기에 없는 코드는 수집 때 '(가이드 미등록)'으로 자동 등록되고, '(가이드 미등록)' 코드로 찾을 수 있다
--   · 시군구(zscode)는 세종(36110)만 넣고, 나머지는 첫 수집 뒤 아래 맨 끝의 "주소로 이름 채우기"로 채운다
--   이름은 활용가이드 기준으로 옮겼다. 실제 가이드 문서와 다르면 가이드 쪽이 맞다 (UPDATE 로 고침)
--   다시 실행해도 된다 (있으면 이름만 고친다)
-- ============================================================================

-- 충전기 상태 (stat)
INSERT INTO code_status (code, name) VALUES
  ('1', '통신이상'), ('2', '충전대기'), ('3', '충전중'), ('4', '운영중지'), ('5', '점검중'), ('9', '상태미확인')
ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name, in_guide = true;

-- 충전기 타입 (chgerType)
INSERT INTO code_charger_type (code, name) VALUES
  ('01', 'DC차데모'), ('02', 'AC완속'), ('03', 'DC차데모+AC3상'), ('04', 'DC콤보'),
  ('05', 'DC차데모+DC콤보'), ('06', 'DC차데모+AC3상+DC콤보'), ('07', 'AC3상'), ('08', 'DC콤보(완속)')
ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name, in_guide = true;

-- 시도 (zcode) — 법정동 코드 앞 2자리. 강원 · 전북은 특별자치도 전환 뒤 코드(51 · 52)도 함께 둔다
INSERT INTO code_region (code, name) VALUES
  ('11', '서울특별시'), ('26', '부산광역시'), ('27', '대구광역시'), ('28', '인천광역시'),
  ('29', '광주광역시'), ('30', '대전광역시'), ('31', '울산광역시'), ('36', '세종특별자치시'),
  ('41', '경기도'), ('42', '강원도'), ('43', '충청북도'), ('44', '충청남도'), ('45', '전라북도'),
  ('46', '전라남도'), ('47', '경상북도'), ('48', '경상남도'), ('50', '제주특별자치도'),
  ('51', '강원특별자치도'), ('52', '전북특별자치도')
ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name, in_guide = true;

-- 시군구 (zscode) — 실습 첫 범위인 세종만
INSERT INTO code_district (code, name) VALUES ('36110', '세종특별자치시')
ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name, in_guide = true;

-- 충전소 구분 (kind)
INSERT INTO code_station_kind (code, name) VALUES
  ('A0', '공공시설'), ('B0', '주차시설'), ('C0', '휴게시설'), ('D0', '관광시설'), ('E0', '상업시설'),
  ('F0', '차량정비시설'), ('G0', '기타시설'), ('H0', '공동주택시설'), ('I0', '근린생활시설'), ('J0', '교육문화시설')
ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name, in_guide = true;

-- 충전소 구분 상세 (kindDetail)
INSERT INTO code_station_kind_detail (code, name) VALUES
  ('A001', '관공서'), ('A002', '주민센터'), ('A003', '공공기관'), ('A004', '지자체시설'),
  ('B001', '공영주차장'), ('B002', '공원주차장'), ('B003', '환승주차장'), ('B004', '일반주차장'),
  ('C001', '고속도로 휴게소'), ('C002', '지방도로 휴게소'), ('C003', '쉼터'),
  ('D001', '공원'), ('D002', '전시관'), ('D003', '민속마을'), ('D004', '생태공원'), ('D005', '홍보관'),
  ('D006', '관광안내소'), ('D007', '관광지'), ('D008', '박물관'), ('D009', '유적지'),
  ('E001', '마트(쇼핑몰)'), ('E002', '백화점'), ('E003', '숙박시설'), ('E004', '골프장(CC)'),
  ('E005', '카페'), ('E006', '음식점'), ('E007', '주유소'), ('E008', '영화관'),
  ('F001', '서비스센터'), ('F002', '정비소'),
  ('G001', '군부대'), ('G002', '야영장'), ('G003', '공중전화부스'), ('G004', '기타'), ('G005', '오피스텔'), ('G006', '단독주택'),
  ('H001', '아파트'), ('H002', '빌라'), ('H003', '사업장(사옥)'), ('H004', '기숙사'), ('H005', '연립주택'),
  ('I001', '병원'), ('I002', '종교시설'), ('I003', '보건소'), ('I004', '경찰서'), ('I005', '도서관'),
  ('I006', '복지관'), ('I007', '수련원'), ('I008', '금융기관'),
  ('J001', '학교'), ('J002', '교육원'), ('J003', '학원'), ('J004', '공연장'), ('J005', '관람장'),
  ('J006', '동식물원'), ('J007', '경기장')
ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name, in_guide = true;

SELECT 'code_status' AS code_table, count(*) AS codes FROM code_status
UNION ALL SELECT 'code_charger_type', count(*) FROM code_charger_type
UNION ALL SELECT 'code_region', count(*) FROM code_region
UNION ALL SELECT 'code_district', count(*) FROM code_district
UNION ALL SELECT 'code_station_kind', count(*) FROM code_station_kind
UNION ALL SELECT 'code_station_kind_detail', count(*) FROM code_station_kind_detail;

-- ── (첫 기본정보 수집 뒤에 다시 실행하면 동작) 자동 등록된 시군구 코드에 주소로 이름 채우기 ──
-- 주소의 두 번째 낱말(예: "서울특별시 강남구 ..." → 강남구)을 이름으로 쓴다. in_guide 는 false 그대로 둔다
-- (가이드에서 옮긴 이름이 아니라 데이터에서 추정한 이름이라는 표시)
UPDATE code_district d
   SET name = x.guess || ' (주소 추정)'
  FROM (SELECT DISTINCT ON (district_code) district_code,
               split_part(addr, ' ', 1) || ' ' || split_part(addr, ' ', 2) AS guess
        FROM station WHERE district_code IS NOT NULL AND addr LIKE '% %'
        ORDER BY district_code, station_id) x
 WHERE d.code = x.district_code AND d.name = '(가이드 미등록)';
