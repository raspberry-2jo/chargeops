"""로컬 config/.env를 Secret/ConfigMap으로 보냄. 비밀값을 셸 인자에 출력하지 않음."""
import json
import subprocess
from pathlib import Path

NS = "chargeops"
path = Path("config/.env")
if not path.exists():
    raise SystemExit("config/.env 없음. config/.env.example 복사 후 설정하세요.")
settings = {}
for line in path.read_text(encoding="utf-8-sig").splitlines():
    s = line.strip()
    if not s or s.startswith("#"):
        continue
    if "=" not in s:
        raise SystemExit(f"잘못된 .env 줄: {s[:30]}")
    k, v = s.split("=", 1)
    settings[k.strip()] = v.strip()
required = (
    "SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "SLACK_CHANNEL_ID", "SLACK_TEAM_ID",
    "ALLOWED_APPROVER_IDS", "INTERNAL_TOKEN", "TRIGGER_KEY",
)
for k in required:
    v = settings.get(k, "")
    if not v or "REPLACE" in v:
        raise SystemExit(f"{k} 항목을 config/.env에서 실제 값으로 수정하세요.")

objects = [
    ("Secret", "slack-credentials", {"SLACK_BOT_TOKEN": settings["SLACK_BOT_TOKEN"], "SLACK_APP_TOKEN": settings["SLACK_APP_TOKEN"]}),
    ("Secret", "router-credentials", {"SLACK_BOT_TOKEN": settings["SLACK_BOT_TOKEN"], "INTERNAL_TOKEN": settings["INTERNAL_TOKEN"], "TRIGGER_KEY": settings["TRIGGER_KEY"]}),
    ("Secret", "bot-backend-credentials", {"INTERNAL_TOKEN": settings["INTERNAL_TOKEN"], "TRIGGER_KEY": settings["TRIGGER_KEY"], "ALLOWED_APPROVER_IDS": settings["ALLOWED_APPROVER_IDS"]}),
    ("Secret", "csms-credentials", {"INTERNAL_TOKEN": settings["INTERNAL_TOKEN"], "ALLOWED_APPROVER_IDS": settings["ALLOWED_APPROVER_IDS"]}),
    ("ConfigMap", "chatops-config", {"SLACK_CHANNEL_ID": settings["SLACK_CHANNEL_ID"], "SLACK_TEAM_ID": settings["SLACK_TEAM_ID"], "CSMS_URL": "http://mock-csms:8000", "ROUTER_URL": "http://alert-router:8080", "CSMS_DB_PATH": "/data/csms.sqlite3"}),
]
for kind, name, values in objects:
    manifest = {"apiVersion": "v1", "kind": kind, "metadata": {"name": name, "namespace": NS},
                ("stringData" if kind == "Secret" else "data"): values}
    subprocess.run(["kubectl", "apply", "-f", "-"], input=json.dumps(manifest),
                   text=True, check=True)
    print(f"{kind}/{name} applied (values hidden)")
