-- ============================================================================
-- V20261014160400__ops_views.sql  (WBS 3.14 — Grafana 관제 화면 · 알림용 조회 뷰)
--   출처: 실습 스키마 17_ops_ui.sql 의 ④ (v_ops_todo ~ 권한)
--   실습판과 다른 점: ③ fn_detect_long_fault 는 뺌(V…160100 에 같은 최신판) · psql 명령 없음
--                     v_ops_notify 에 error_code 칸 (Slack 거절 이유가 120자에서 잘리지 않게, 인수인계 D-3)
--   가동률(V…160100) · 이상치 점수(V…160200) 를 쓰므로 그 뒤에 적용
-- ============================================================================
SET client_min_messages = warning;

-- ④ Grafana 용 뷰 (소유자 postgres 권한으로 읽고, report_ro 에는 뷰만 공개)
--   등급 표기: SEV1 긴급 · SEV2 높음 · SEV3 주의 · SEV4 낮음 — Slack · 메일과 같은 말

-- 조치 필요: 열린 장애(SEV1~3) + 대기 중 승인 요청, 급한 순
CREATE OR REPLACE VIEW v_ops_todo AS
SELECT i.incident_id                                   AS id,
       CASE i.severity WHEN 'SEV1' THEN 1 WHEN 'SEV2' THEN 2 ELSE 3 END AS prio,
       CASE i.severity WHEN 'SEV1' THEN '긴급' WHEN 'SEV2' THEN '높음' ELSE '주의' END AS level,
       coalesce(i.charger_id, array_to_string(i.targets[1:4], ', '))   AS target,
       i.title,
       coalesce(i.detail, '')                          AS detail,
       CASE WHEN i.acked_by IS NULL THEN '담당 없음' ELSE coalesce(u.display_name, i.acked_by) END AS owner,
       extract(epoch FROM now() - i.detected_at)::int  AS age_sec,
       i.detected_at,
       i.slack_permalink
FROM incident i
LEFT JOIN slack_user u ON u.slack_user_id = i.acked_by
WHERE i.status <> 'resolved' AND i.severity <> 'SEV4'
UNION ALL
SELECT a.approval_id, 2, '승인 대기',
       coalesce(aa.charger_id, '-'),
       format('%s %s 승인 대기', coalesce(aa.charger_id, ''),
              CASE aa.step WHEN 'hard_reset' THEN 'Hard Reset' WHEN 'disable' THEN '사용 중지'
                           WHEN 'dr_drill' THEN 'DR 훈련' ELSE aa.step END),
       format('요청 %s · %s 만료', coalesce(u.display_name, a.requested_by), to_char(a.expires_at, 'HH24:MI')),
       '승인자 필요',
       extract(epoch FROM now() - a.created_at)::int,
       a.created_at,
       i.slack_permalink
FROM approval a
JOIN action_audit aa ON aa.action_id = a.action_id
LEFT JOIN incident i ON i.incident_id = aa.incident_id
LEFT JOIN slack_user u ON u.slack_user_id = a.requested_by
WHERE a.decision = 'pending' AND a.approver_id IS NULL AND a.expires_at > now();

-- 조치 · 감사 이력: 누가 · 언제 · 무엇을 · 결과 (사람 이름 · 우리말)
CREATE OR REPLACE VIEW v_ops_actions AS
SELECT a.action_id,
       a.created_at,
       coalesce(u.display_name, a.requested_by) || coalesce(' · ' || a.role, '') AS who,
       coalesce(a.charger_id, '-')                     AS charger_id,
       CASE a.step WHEN 'soft_reset' THEN 'Soft Reset' WHEN 'hard_reset' THEN 'Hard Reset'
                   WHEN 'disable' THEN '사용 중지' WHEN 'enable' THEN '사용 재개' WHEN 'watch' THEN '감시 강화'
                   WHEN 'approve' THEN '승인' WHEN 'false_positive' THEN '오탐 처리'
                   WHEN 'role_grant' THEN '역할 부여' WHEN 'role_revoke' THEN '역할 회수'
                   WHEN 'db_failover' THEN 'DB 장애 전환' WHEN 'db_switchover' THEN 'DB 원복'
                   ELSE a.step END                     AS what,
       CASE a.result WHEN 'executed' THEN '실행' WHEN 'succeeded' THEN '성공' WHEN 'failed' THEN '실패'
                     WHEN 'denied' THEN '거부' WHEN 'rejected' THEN '거절' WHEN 'blocked' THEN '보류'
                     WHEN 'pending_approval' THEN '승인 요청' WHEN 'rate_limited' THEN '횟수 제한'
                     WHEN 'timeout' THEN '응답 없음' WHEN 'expired' THEN '만료' ELSE a.result END AS result,
       coalesce(a2.display_name, a.approved_by)        AS approved_by,
       coalesce(a.reason, '')                          AS reason,
       a.incident_id
FROM action_audit a
LEFT JOIN slack_user u  ON u.slack_user_id  = a.requested_by
LEFT JOIN slack_user a2 ON a2.slack_user_id = a.approved_by;

-- 장애 이력: 해소 시간 · 담당 (오늘 해소 요약 · 추세)
CREATE OR REPLACE VIEW v_ops_incidents AS
SELECT i.incident_id, i.type_code, i.severity, i.title, i.charger_id, i.cause_code, i.status,
       i.detected_at, i.acked_at, i.resolved_at,
       extract(epoch FROM i.resolved_at - i.detected_at)::int AS resolve_sec,
       extract(epoch FROM i.acked_at - i.detected_at)::int    AS ack_sec,
       coalesce(u.display_name, i.acked_by)            AS owner
FROM incident i LEFT JOIN slack_user u ON u.slack_user_id = i.acked_by;

-- 알림 전송 결과 (Slack · 메일)
-- error_code: Slack 이 돌려준 거절 이유(not_authed · channel_not_found …) — detail 은 120자에서 잘려 안 보일 때가 있어 따로 (2026-10-14)
CREATE OR REPLACE VIEW v_ops_notify AS
SELECT id, incident_id, channel, status, sent_at, left(coalesce(detail, ''), 120) AS detail,
       substring(detail from $$'error': '([a-z_]+)'$$) AS error_code
FROM notification_log;

-- 원인 코드 → 우리말 (apps/common/causes.py 의 CAUSES 와 같은 이름)
CREATE OR REPLACE FUNCTION fn_cause_label(p text) RETURNS text LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE p WHEN 'InternalError' THEN '내부 오류' WHEN 'PowerSwitchFailure' THEN '전원 스위치 이상'
                WHEN 'GroundFailure' THEN '누전 감지' WHEN 'OverCurrentFailure' THEN '과전류'
                WHEN 'HighTemperature' THEN '과열' WHEN 'ConnectionLost' THEN '통신 두절'
                WHEN 'OtherError' THEN '원인 미상' ELSE p END $$;

-- 상태 묶음 5가지 (대시보드 색 규칙: 초록 대기 · 파랑 충전 중 · 노랑 통신 이상 · 빨강 고장·중지 · 회색 모름)
CREATE OR REPLACE FUNCTION fn_status_group(p text) RETURNS text LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE WHEN p IN ('2','6') THEN '대기' WHEN p = '3' THEN '충전 중' WHEN p = '1' THEN '통신 이상'
              WHEN p IN ('4','5','F') THEN '고장·중지' ELSE '모름' END $$;

-- 충전기 카드 (모형 · 실물): 상태 · 원인 · 직전 값 · 이상 점수 · 가동률 · 마지막 신호
CREATE OR REPLACE VIEW v_charger_card AS
SELECT c.charger_id,
       fn_status_group(cur.status_code)
         || CASE WHEN cur.error_code IS NOT NULL THEN ' · ' || fn_cause_label(cur.error_code) ELSE '' END AS state,
       m.current_a, m.temp_c, coalesce(c.rated_current_a, 32) AS rated_a,
       a.score AS anomaly,
       u.uptime_pct AS uptime_today,
       extract(epoch FROM now() - cur.last_heartbeat_at)::int AS hb_age_sec
FROM charger c
LEFT JOIN charger_status_current cur USING (charger_id)
LEFT JOIN LATERAL (SELECT current_a, temp_c FROM meter_sample ms
                   WHERE ms.charger_id = c.charger_id AND ms.ts > now() - interval '10 minutes'
                   ORDER BY ts DESC LIMIT 1) m ON true
LEFT JOIN LATERAL (SELECT score FROM v_anomaly_score v WHERE v.charger_id = c.charger_id
                   ORDER BY bucket DESC LIMIT 1) a ON true
LEFT JOIN mv_daily_uptime u ON u.charger_id = c.charger_id AND u.day = current_date
WHERE c.source = 'model' AND NOT c.is_deleted;

-- 역할 목록 (이름 · 역할 · 언제부터 · 누가 줌)
CREATE OR REPLACE VIEW v_ops_roles AS
SELECT coalesce(u.display_name, r.slack_user_id) AS name, r.role, r.valid_from,
       coalesce(g.display_name, r.granted_by) AS granted_by
FROM ops_role r
LEFT JOIN slack_user u ON u.slack_user_id = r.slack_user_id
LEFT JOIN slack_user g ON g.slack_user_id = r.granted_by
WHERE r.valid_to IS NULL;

GRANT SELECT ON v_ops_todo, v_ops_actions, v_ops_incidents, v_ops_notify, v_charger_card, v_ops_roles
  TO report_ro, app_reader;
GRANT EXECUTE ON FUNCTION fn_cause_label(text), fn_status_group(text) TO report_ro, app_reader;
GRANT SELECT ON v_anomaly_score, meter_sample TO report_ro;   -- 이상 점수 · 충전기 카드 (10/13 A-6 을 스키마로)

RESET client_min_messages;
