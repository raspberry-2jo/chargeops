-- ============================================================================
-- V20261029100000__mttr_exclude_test_incidents.sql  (WBS 3.8 - exclude test incidents from daily MTTR)
--   why: incident 22088 (C-01, CH-01, detected 2026-10-28 16:58, resolved 2026-10-29 09:38) was an
--        intentional fault raised to test Slack Viewer permissions and was left on overnight.
--        Counted as a real incident it would add about 1,000 min to the 10/29 MTTR.
--   changed: 1) mark that incident with cause_code = 'TEST'
--               (cause_code holds OCPP error codes such as ConnectionLost; 'TEST' is not an OCPP code)
--            2) MTTR (mt) skips incidents with cause_code = 'TEST'. NULL cause_code is still counted.
--   unchanged: uptime, fault count, long fault, return value, upsert. Function body is the same as
--              V20261027150000__mttr_count_grouped_incidents.sql except the one marked line.
--   note: uptime and fault count come from status history, so the test still shows there (10/28, 10/29).
--   rollback: re-apply V20261027150000__mttr_count_grouped_incidents.sql, then
--             UPDATE incident SET cause_code = NULL WHERE incident_id = 22088 AND cause_code = 'TEST';
-- ============================================================================
SET client_min_messages = warning;
BEGIN;

UPDATE incident SET cause_code = 'TEST'
 WHERE incident_id = 22088 AND type_code = 'C-01' AND charger_id = 'CH-01' AND cause_code IS NULL
   AND detected_at >= '2026-10-28 16:58:00+09' AND detected_at < '2026-10-28 16:59:00+09';

CREATE OR REPLACE FUNCTION fn_refresh_stat_operator_daily(p_day date DEFAULT (now() AT TIME ZONE 'Asia/Seoul')::date - 1)
RETURNS int LANGUAGE plpgsql AS $$
DECLARE n int;
  d0 timestamptz := p_day::timestamp AT TIME ZONE 'Asia/Seoul';
  d1 timestamptz := (p_day + 1)::timestamp AT TIME ZONE 'Asia/Seoul';
BEGIN
  INSERT INTO stat_operator_daily AS t (operator_id, day, chargers, uptime_pct, mttr_min, fault_cnt, long_fault_cnt)
  WITH sc AS (                 -- target chargers (reused 4 times)
    SELECT charger_id, operator_id FROM v_stat_charger WHERE operator_id IS NOT NULL
  ), up AS (                   -- uptime: that day's rows in the matview
    SELECT sc.operator_id, count(DISTINCT u.charger_id) AS chargers, round(avg(u.uptime_pct), 3) AS uptime_pct
    FROM mv_daily_uptime u JOIN sc USING (charger_id)
    WHERE u.day = p_day
    GROUP BY sc.operator_id
  ), mt AS (                   -- MTTR: incidents resolved that day, incl. grouped (targets), counted once per operator, test incidents excluded (2026-10-29)
    SELECT x.operator_id, round(avg(x.sec) / 60, 1) AS mttr_min
    FROM (
      SELECT DISTINCT sc.operator_id, i.incident_id,
             extract(epoch FROM i.resolved_at - i.detected_at)::numeric AS sec
      FROM incident i
      CROSS JOIN LATERAL unnest(CASE WHEN cardinality(i.targets) > 0 THEN i.targets
                                     ELSE ARRAY[i.charger_id] END) AS tg(charger_id)
      JOIN sc ON sc.charger_id = tg.charger_id
      WHERE i.resolved_at >= d0 AND i.resolved_at < d1
        AND i.cause_code IS DISTINCT FROM 'TEST'   -- added 2026-10-29
    ) x
    GROUP BY x.operator_id
  ), fc AS (                   -- fault count: changes into a down status that day
    SELECT sc.operator_id, count(*) AS fault_cnt
    FROM status_history h JOIN sc USING (charger_id) JOIN code_status cs ON cs.code = h.status_code
    WHERE h.changed_at >= d0 AND h.changed_at < d1 AND cs.is_down
    GROUP BY sc.operator_id
  ), lf AS (                   -- long fault: down for 24h+ at end of day (current status table)
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
COMMENT ON FUNCTION fn_refresh_stat_operator_daily(date) IS 'daily stats per operator (default: yesterday). matview is a 7-day window. MTTR includes grouped incidents (targets, 2026-10-27) and skips cause_code TEST (2026-10-29)';

COMMIT;
RESET client_min_messages;
