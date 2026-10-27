-- ============================================================================
-- ChargeOps 스키마 · CSMS 다중 파드 대응 — V20261020103000__csms_session.sql (실습 스키마 18_csms_session.sql)
--   문제: CSMS 파드마다 충전기 세션 · 상태를 메모리에 따로 보관 → 재접속 시 상태 미기록 · 가짜 통신이상 · 이력 중복
--   원칙: 파드끼리 공유돼야 하는 상태는 DB 를 기준으로 판단
--     1) charger_session  : 충전기마다 "지금 살아 있는 세션 1개"(어느 파드 · 언제)
--     2) fn_session_open  : 새 접속 시 세션 등록 (옛 세션을 덮어씀)
--     3) fn_record_status : 현재 상태와 다를 때만 이력 1행 + 현재 상태 갱신 (한 트랜잭션, 충전기 행 잠금)
--     4) fn_session_close : 내가 최신 세션일 때만 통신이상(1) 기록
--   V…113400(arch_tables) · V…113500(roles_and_grants) 다음. postgres(소유자)로 실행. 다시 실행해도 됨
-- ============================================================================
SET client_min_messages = warning;

CREATE TABLE IF NOT EXISTS charger_session (
  charger_id   text PRIMARY KEY REFERENCES charger,
  session_id   uuid        NOT NULL,
  pod          text        NOT NULL,
  pod_ip       text,
  connected_at timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE  charger_session            IS '충전기별 현재 OCPP 세션 1개. CSMS 파드가 여러 개일 때 누가 최신 세션인지의 기준';
COMMENT ON COLUMN charger_session.session_id IS '접속마다 새로 만드는 세션 번호 (CSMS 가 uuid4 로 생성)';
COMMENT ON COLUMN charger_session.pod        IS '세션을 가진 CSMS 파드 이름 (HOSTNAME)';
COMMENT ON COLUMN charger_session.pod_ip     IS '그 파드 IP — 원격 명령을 받은 다른 파드가 이 파드로 넘길 때 사용';

-- 새 접속: 옛 세션을 덮어씀 → 옛 세션이 나중에 끊겨도 통신이상을 못 씀
CREATE OR REPLACE FUNCTION fn_session_open(p_cid text, p_session uuid, p_pod text, p_pod_ip text)
RETURNS void LANGUAGE sql AS $$
  INSERT INTO charger_session (charger_id, session_id, pod, pod_ip, connected_at)
  VALUES (p_cid, p_session, p_pod, p_pod_ip, now())
  ON CONFLICT (charger_id) DO UPDATE
     SET session_id = EXCLUDED.session_id, pod = EXCLUDED.pod, pod_ip = EXCLUDED.pod_ip, connected_at = EXCLUDED.connected_at;
$$;

-- OCPP 상태 기록: 현재 상태(DB)와 다를 때만 이력 1행 + 현재 상태 갱신. 기록했으면 true
--   충전기 행을 FOR UPDATE 로 잠가 같은 충전기의 동시 기록을 차례로 처리
--   시각 비교(옛 보고 무시)는 하지 않음: 한 세션 안의 메시지는 순서대로 오고, 세션 사이 순서는 charger_session 이 정함
--   (충전기 시각은 초 단위 · 서버 시각은 밀리초라 시각 비교를 하면 재접속 직후 상태가 버려질 수 있음)
CREATE OR REPLACE FUNCTION fn_record_status(p_cid text, p_code text, p_err text, p_ts timestamptz, p_src text)
RETURNS boolean LANGUAGE plpgsql AS $$
DECLARE
  cur_code text;
  cur_err  text;
BEGIN
  SELECT status_code, error_code INTO cur_code, cur_err
  FROM charger_status_current WHERE charger_id = p_cid FOR UPDATE;
  IF FOUND AND cur_code = p_code AND cur_err IS NOT DISTINCT FROM p_err THEN
    RETURN false;                                        -- 바뀐 게 없음 → 이력 안 남김
  END IF;

  INSERT INTO status_history (changed_at, charger_id, source, status_code, error_code)
  VALUES (p_ts, p_cid, p_src, p_code, p_err)
  ON CONFLICT (charger_id, changed_at, status_code) DO NOTHING;

  INSERT INTO charger_status_current AS cur
         (charger_id, status_code, changed_at, source, error_code, charging_start_at, last_heartbeat_at)
  VALUES (p_cid, p_code, p_ts, p_src, p_err, CASE WHEN p_code = '3' THEN p_ts END, now())
  ON CONFLICT (charger_id) DO UPDATE SET
    status_code = EXCLUDED.status_code, changed_at = EXCLUDED.changed_at, source = EXCLUDED.source,
    error_code = EXCLUDED.error_code, received_at = now(),
    charging_start_at    = CASE WHEN EXCLUDED.status_code = '3' THEN coalesce(cur.charging_start_at, EXCLUDED.changed_at) END,
    last_charge_start_at = CASE WHEN EXCLUDED.status_code <> '3' AND cur.charging_start_at IS NOT NULL
                                THEN cur.charging_start_at ELSE cur.last_charge_start_at END,
    last_charge_end_at   = CASE WHEN EXCLUDED.status_code <> '3' AND cur.status_code = '3'
                                THEN EXCLUDED.changed_at ELSE cur.last_charge_end_at END;
  RETURN true;
END $$;

-- 세션 종료: 내가 최신 세션일 때만 세션을 지우고 통신이상(1) 기록. 더 새 세션이 있으면 false (아무것도 안 함)
CREATE OR REPLACE FUNCTION fn_session_close(p_cid text, p_session uuid, p_src text)
RETURNS boolean LANGUAGE plpgsql AS $$
BEGIN
  DELETE FROM charger_session WHERE charger_id = p_cid AND session_id = p_session;
  IF NOT FOUND THEN
    RETURN false;
  END IF;
  PERFORM fn_record_status(p_cid, '1', 'ConnectionLost', now(), p_src);
  RETURN true;
END $$;

-- 권한: CSMS(app_writer)만 쓰기 · 실행, 나머지는 읽기
GRANT SELECT, INSERT, UPDATE, DELETE ON charger_session TO app_writer;
GRANT SELECT ON charger_session TO app_reader, report_ro;
GRANT EXECUTE ON FUNCTION fn_session_open(text, uuid, text, text), fn_record_status(text, text, text, timestamptz, text),
                          fn_session_close(text, uuid, text) TO app_writer;
