#!/usr/bin/env python3
"""ChargeOps - save the latest pgBackRest backup of each repo into backup_repo_status (Grafana backup panel).
Installed as /usr/local/sbin/chargeops-backup-status.py on both DB servers (db-01, db-02).
Called every 10 minutes by chargeops-backup-status.timer as user postgres.
Does nothing on a standby, so a failover needs no change (same idea as chargeops-backup.sh).
repo1 = MinIO (s3.chargeops.lab), repo2 = AWS S3."""
import json
import subprocess
import sys

STANZA = "chargeops"
DB = "chargeops"
NAMES = {1: "repo1 (MinIO)", 2: "repo2 (AWS S3)"}


def psql(sql):
    r = subprocess.run(["psql", "-X", "-q", "-At", "-v", "ON_ERROR_STOP=1", "-d", DB],
                       input=sql, text=True, capture_output=True, timeout=60)
    if r.returncode != 0:
        sys.exit("psql failed: " + r.stderr.strip())
    return r.stdout.strip()


def lit(v):
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


def ts(b, key):
    return "to_timestamp(%d)" % b["timestamp"][key] if b else "NULL"


if psql("SELECT pg_is_in_recovery()") == "t":
    sys.exit(0)

r = subprocess.run(["pgbackrest", "--stanza=" + STANZA, "info", "--output=json"],
                   capture_output=True, text=True, timeout=120)
if r.returncode != 0:
    msg = ((r.stderr or r.stdout).strip() or "pgbackrest info failed")[:200]
    psql("UPDATE backup_repo_status SET repo_status = %s, checked_at = now();" % lit("error: " + msg))
    sys.exit("pgbackrest info failed: " + msg)

stanza = json.loads(r.stdout)[0]
status = {x["key"]: x.get("status", {}).get("message", "") for x in stanza.get("repo", [])}
latest = {}
for b in stanza.get("backup", []):
    key = b.get("database", {}).get("repo-key", 1)
    if key not in latest or b["timestamp"]["stop"] >= latest[key]["timestamp"]["stop"]:
        latest[key] = b

rows = []
for key in sorted(set(status) | set(latest)):
    b = latest.get(key)
    info = b.get("info", {}) if b else {}
    size = info.get("repository", {}).get("size", info.get("size"))
    rows.append("(%d, %s, %s, %s, %s, %s, %s, %s, %s, now())" % (
        key, lit(NAMES.get(key, "repo%d" % key)), lit(b["label"] if b else None),
        lit(b["type"] if b else None), ts(b, "start"), ts(b, "stop"),
        lit(b.get("error", False) if b else None), lit(size),
        lit(status.get(key) or stanza.get("status", {}).get("message"))))
    print("repo%d %s %s" % (key, b["label"] if b else "-", status.get(key, "")))

if rows:
    psql("""INSERT INTO backup_repo_status
  (repo, repo_name, last_label, last_type, last_start, last_stop, last_error, size_bytes, repo_status, checked_at)
VALUES %s
ON CONFLICT (repo) DO UPDATE SET
  repo_name = EXCLUDED.repo_name, last_label = EXCLUDED.last_label, last_type = EXCLUDED.last_type,
  last_start = EXCLUDED.last_start, last_stop = EXCLUDED.last_stop, last_error = EXCLUDED.last_error,
  size_bytes = EXCLUDED.size_bytes, repo_status = EXCLUDED.repo_status, checked_at = EXCLUDED.checked_at;""" % ",\n".join(rows))
