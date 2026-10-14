"""로컬 config/.env 이용: 토큰/채널 연결 확인 및 테스트 메시지 발송."""
from pathlib import Path
from slack_sdk import WebClient

values = {}
for line in Path("config/.env").read_text(encoding="utf-8-sig").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        values[k.strip()] = v.strip()
client = WebClient(token=values["SLACK_BOT_TOKEN"])
auth = client.auth_test()
print("Slack 인증 성공 / Team ID:", auth["team_id"], "/ Bot User ID:", auth["user_id"])
print(".env SLACK_TEAM_ID 와 동일한지 확인:", values.get("SLACK_TEAM_ID") == auth["team_id"])
message = client.chat_postMessage(channel=values["SLACK_CHANNEL_ID"], text="✅ ChargeOps ChatOps 연결 테스트 (버튼 없음)")
print("#ops-alerts 테스트 메시지 발송 성공 / ts:", message["ts"])
