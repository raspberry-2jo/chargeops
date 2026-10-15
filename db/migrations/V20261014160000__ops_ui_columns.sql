-- ============================================================================
-- V20261014160000__ops_ui_columns.sql  (WBS 3.14 — 관제 화면 · 알림용 칸과 Slack 이름표)
--   출처: 실습 스키마 17_ops_ui.sql (2026-10-14 판) 의 ① ②
--   incident 에 담당 · 원인 코드 · 한 줄 상황 · Slack 링크 칸, slack_user(Slack 멤버 ID → 표시 이름) 테이블
--   + 실습 12_stats.sql(강영수 10/14 판) 맨 위의 인덱스 2개 — v_stat_charger 두 갈래(V…160100)와 짝
--   실습판과 다른 점: psql 전용 명령(\set) 없음
-- ============================================================================
SET client_min_messages = warning;

-- ① 장애 표시용 칸
ALTER TABLE incident ADD COLUMN IF NOT EXISTS acked_by        text;   -- 처음 조치한 사람(Slack 멤버 ID) = 담당
ALTER TABLE incident ADD COLUMN IF NOT EXISTS cause_code      text;   -- OCPP errorCode 등 (InternalError …)
ALTER TABLE incident ADD COLUMN IF NOT EXISTS detail          text;   -- 감지 시점 한 줄 상황 (전류 · 온도 · 영향)
ALTER TABLE incident ADD COLUMN IF NOT EXISTS slack_permalink text;   -- 대시보드에서 누르면 그 Slack 스레드로
COMMENT ON COLUMN incident.acked_by IS '처음 조치(버튼)한 사람 Slack ID — 대시보드 "담당"';

-- ② Slack 이름표
CREATE TABLE IF NOT EXISTS slack_user (
  slack_user_id text PRIMARY KEY,
  display_name  text NOT NULL,
  updated_at    timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE slack_user IS 'Slack 멤버 ID → 표시 이름. chatops-bot 이 시작할 때 · 버튼을 누를 때 갱신';
GRANT SELECT, INSERT, UPDATE ON slack_user TO app_writer;
GRANT SELECT ON slack_user TO app_reader;

-- 통계 대상 충전기를 찾는 인덱스 2개 (강영수 10/12 튜닝 — v_stat_charger 의 두 갈래와 짝, V…160100)
CREATE INDEX IF NOT EXISTS charger_station_idx   ON charger (station_id);                           -- 범위 시도 충전소 → 그 충전기
CREATE INDEX IF NOT EXISTS charger_nonpublic_idx ON charger (charger_id) WHERE source <> 'public';  -- 모형 · 가상 1,006대만
RESET client_min_messages;
