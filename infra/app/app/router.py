"""Incident를 mock CSMS에 등록하고 Slack #ops-alerts에 버튼 알림 발행."""
import hmac
import os
import uuid

import httpx
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

app = FastAPI(title="ChargeOps Alert Router", version="1.0.0")


class AlertIn(BaseModel):
    device: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=100)
    detail: str = Field(min_length=1, max_length=500)
    severity: str = Field(pattern="^(info|warning|critical)$")


def alert_blocks(incident_id: str, alert: AlertIn):
    return [
        {"type": "header", "text": {"type": "plain_text", "text": "🚨 ChargeOps 장비 알림"}},
        {"type": "section", "fields": [
            {"type": "plain_text", "text": f"등급: {alert.severity}"},
            {"type": "plain_text", "text": f"장비: {alert.device}"},
        ]},
        {"type": "section", "text": {"type": "plain_text", "text": f"{alert.title}\n{alert.detail}"}},
        {"type": "context", "elements": [
            {"type": "plain_text", "text": f"Incident ID: {incident_id}"},
        ]},
        {"type": "actions", "elements": [
            {"type": "button", "action_id": "incident_view", "value": incident_id,
             "text": {"type": "plain_text", "text": "🔎 조회"}},
            {"type": "button", "action_id": "incident_approve", "value": incident_id,
             "style": "primary", "text": {"type": "plain_text", "text": "✅ 승인"},
             "confirm": {
                "title": {"type": "plain_text", "text": "승인 확인"},
                "text": {"type": "plain_text", "text": "CSMS Mock으로 승인 요청을 보냅니다. 계속할까요?"},
                "confirm": {"type": "plain_text", "text": "승인"},
                "deny": {"type": "plain_text", "text": "취소"},
             }},
        ]},
    ]


def check_trigger(provided: str):
    expected = os.environ["TRIGGER_KEY"]
    if not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="Invalid trigger key")


@app.get("/healthz")
def health():
    return {"status": "ok"}


@app.post("/alerts", status_code=201)
def issue_alert(alert: AlertIn, x_trigger_key: str = Header(default="")):
    check_trigger(x_trigger_key)
    incident_id = str(uuid.uuid4())
    csms_url = os.environ.get("CSMS_URL", "http://mock-csms:8000")
    try:
        # 실제 CSMS 연동 때는 이미 생성된 Incident ID를 받아도 됩니다.
        with httpx.Client(timeout=5.0) as client:
            resp = client.post(
                f"{csms_url}/api/incidents",
                json={"incident_id": incident_id, **alert.model_dump()},
                headers={"X-Internal-Token": os.environ["INTERNAL_TOKEN"]},
            )
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="CSMS registration failed") from exc

    slack = WebClient(token=os.environ["SLACK_BOT_TOKEN"], timeout=8)
    try:
        result = slack.chat_postMessage(
            channel=os.environ["SLACK_CHANNEL_ID"],
            text=f"[{alert.severity}] {alert.title} / {incident_id}",
            blocks=alert_blocks(incident_id, alert),
        )
    except SlackApiError as exc:
        # Mock CSMS에 PENDING 상태가 남을 수 있으므로 실제 운영에서 보상/재시도 설계 필요
        raise HTTPException(status_code=502, detail=f"Slack post failed: {exc.response.get('error')}") from exc
    return {"incident_id": incident_id, "status": "PENDING", "slack_channel": result["channel"], "slack_ts": result["ts"]}
