-- 2026-10-28  latest pgBackRest backup per repo (repo1 = MinIO s3.chargeops.lab, repo2 = AWS S3) for the Grafana backup panel
-- filled every 10 minutes by /usr/local/sbin/chargeops-backup-status.py (systemd timer, primary only, runs as postgres)
CREATE TABLE IF NOT EXISTS backup_repo_status (
  repo        smallint PRIMARY KEY,          -- pgBackRest repo number
  repo_name   text NOT NULL,                 -- repo1 (MinIO) / repo2 (AWS S3)
  last_label  text,                          -- backup label, e.g. 20261026-104738F_20261028-034041D
  last_type   text,                          -- full / diff / incr
  last_start  timestamptz,
  last_stop   timestamptz,                   -- panel shows now() - last_stop
  last_error  boolean,                       -- true = pgBackRest flagged the backup with errors
  size_bytes  bigint,                        -- backup set size in the repo
  repo_status text,                          -- repo status message from pgbackrest info (ok / error ...)
  checked_at  timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE backup_repo_status IS 'latest pgBackRest backup per repo, refreshed every 10 min by chargeops-backup-status.py (systemd timer, primary only)';

-- read access for the Grafana / report accounts that exist on this server
DO $$
DECLARE r text;
BEGIN
  FOREACH r IN ARRAY ARRAY['grafana_ro', 'grafana_aws', 'report_ro'] LOOP
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
      EXECUTE format('GRANT SELECT ON backup_repo_status TO %I', r);
    END IF;
  END LOOP;
END $$;
