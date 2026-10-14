# CI/CD Image Updater E2E test2
# CI/CD Image Updater E2E test3
"""Socket Mode가 Slack 버튼 이벤트를 수신하고 CSMS로 조회/승인 전달."""
import logging
import os
import httpx
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from slack_sdk.errors import SlackApiError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)
app = App(token=os.environ["SLACK_BOT_TOKEN"])


def valid_origin(body):
    team = body.get("team") or {}
    channel = body.get("channel") or {}
    return team.get("id") == os.environ["SLACK_TEAM_ID"] and channel.get("id") == os.environ["SLACK_CHANNEL_ID"]


def csms_call(method, path, **kwargs):
    url = os.environ.get("CSMS_URL", "http://mock-csms:8000")
    with httpx.Client(timeout=7.0) as client:
        response = client.request(
            method, f"{url}{path}",
            headers={"X-Internal-Token": os.environ["INTERNAL_TOKEN"]}, **kwargs,
        )
        response.raise_for_status()
        return response.json()


def reply_private(respond, message):
    respond(text=message, response_type="ephemeral")


@app.action("incident_view")
def view_alert(ack, body, action, respond):
    ack()  # Slack 타임아웃 방지: 반드시 먼저 ACK
    if not valid_origin(body):
        reply_private(respond, "허용되지 않은 워크스페이스/채널입니다.")
        return
    incident_id = action["value"]
    try:
        incident = csms_call("GET", f"/api/incidents/{incident_id}")
        reply_private(respond, f"🔎 {incident_id}\n장비: {incident['device']}\n상태: {incident['status']}\n승인자: {incident.get('approved_by') or '-'}")
        log.info("view incident_id=%s user_id=%s", incident_id, body["user"]["id"])
    except (httpx.HTTPError, KeyError) as exc:
        log.warning("query failed incident_id=%s type=%s", incident_id, type(exc).__name__)
        reply_private(respond, "CSMS 조회 실패. 서버 로그를 확인하세요.")


@app.action("incident_approve")
def approve_alert(ack, body, action, respond, client):
    ack()
    if not valid_origin(body):
        reply_private(respond, "허용되지 않은 워크스페이스/채널입니다.")
        return
    user_id = body["user"]["id"]
    allowed = {s.strip() for s in os.environ.get("ALLOWED_APPROVER_IDS", "").split(",") if s.strip()}
    if user_id not in allowed:  # Demo 기본 정책: 승인 권한이 명시적으로 등록된 사람만 허용
        reply_private(respond, "⛔ 승인 권한이 없습니다. Slack 사용자 ID 허용 목록을 확인하세요.")
        log.warning("denied approval user_id=%s", user_id)
        return
    incident_id = action["value"]
    try:
        result = csms_call("POST", f"/api/incidents/{incident_id}/approve", json={
            "slack_user_id": user_id,
            "team_id": body["team"]["id"],
        })
    except httpx.HTTPError as exc:
        log.warning("approval failed incident_id=%s type=%s", incident_id, type(exc).__name__)
        reply_private(respond, "❌ CSMS 승인 처리 실패. 실제 상태를 조회하여 확인하세요.")
        return

    reply_private(respond, f"✅ CSMS 상태: {result['status']} (Incident: {incident_id})")
    # Slack 화면 실패 여부와 관계없이 CSMS 승인이 원본 상태(source of truth)
    if result["status"] == "APPROVED":
        try:
            client.chat_update(
                channel=body["channel"]["id"], ts=body["message"]["ts"],
                text=f"✅ 승인됨 / {incident_id} / 승인자 <@{result['approved_by']}>",
                blocks=[
                    {"type": "header", "text": {"type": "plain_text", "text": "✅ ChargeOps 알림 승인 완료"}},
                    {"type": "section", "text": {"type": "mrkdwn", "text": f"Incident `{incident_id}`\n승인자: <@{result['approved_by']}>\n상태: *APPROVED*"}},
                ],
            )
        except (SlackApiError, KeyError) as exc:
            log.warning("Slack message update failed incident_id=%s type=%s", incident_id, type(exc).__name__)
    log.info("approved incident_id=%s user_id=%s idempotent=%s", incident_id, user_id, result.get("idempotent"))


if __name__ == "__main__":
    log.info("Starting Slack Socket Mode (no Ingress or Service required)")
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()
