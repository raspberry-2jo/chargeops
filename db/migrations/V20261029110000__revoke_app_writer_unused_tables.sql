-- 2026-10-29  remove app_writer write access on tables no app writes (least privilege)
-- why: default privileges (owner postgres, schema public) give app_writer INSERT/UPDATE on every new table.
--      These tables are written only by postgres, so the app account does not need to write them:
--        backup_repo_status   chargeops-backup-status.py (systemd timer, runs as postgres)
--        stat_operator_daily  pg_cron job stat-daily (username postgres)
--        stat_operator_weekly, stat_operator_monthly, stat_scope, slack_user
--                             no INSERT/UPDATE in app code (git grep, apps/ infra/ monitoring/ outside db/)
--      A wrong write by the app account could make a failed backup look ok, or change stats and Slack user names.
-- kept: ops_role (chatops-bot role command writes it), dq_result (dq-job calls fn_dq_check), all other tables.
--       SELECT stays. The default privileges are not changed here, so new tables still get app_writer write access.
-- rollback: GRANT INSERT, UPDATE ON backup_repo_status, slack_user, stat_operator_daily, stat_operator_weekly,
--           stat_operator_monthly, stat_scope TO app_writer;
DO $$
DECLARE t text;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_writer') THEN
    RETURN;
  END IF;
  FOREACH t IN ARRAY ARRAY['backup_repo_status', 'slack_user', 'stat_operator_daily', 'stat_operator_weekly',
                           'stat_operator_monthly', 'stat_scope'] LOOP
    IF to_regclass(t) IS NOT NULL THEN
      EXECUTE format('REVOKE INSERT, UPDATE ON %I FROM app_writer', t);
    END IF;
  END LOOP;
END $$;
