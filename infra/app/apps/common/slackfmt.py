"""Slack 메시지(Block Kit) 구성 — 장애 메시지와 버튼"""
import json, os

GRAFANA = os.environ.get("GRAFANA_URL", "http://10.10.20.201")
SEV_ICON = {"SEV1": "🔴", "SEV2": "🟠", "SEV3": "🟡", "SEV4": "⚪"}
LEVEL_ICON = {"WATCH": "👀", "WARN": "🟠", "CRITICAL": "🔴"}


def btn(text: str, action: str, inc: dict, style: str | None = None, **extra) -> dict:
    """버튼 value 에 장애 ID·대상 충전기를 JSON 으로 담아 봇이 그대로 꺼내 쓴다"""
    value = {"incident_id": inc["incident_id"], "charger_id": inc.get("charger_id"),
             "targets": inc.get("targets") or [], "severity": inc["severity"], **extra}
    b = {"type": "button", "text": {"type": "plain_text", "text": text},
         "action_id": action, "value": json.dumps(value)}
    if style:
        b["style"] = style
    return b


def incident_blocks(inc: dict, mentions: str = "") -> list[dict]:
    cid = inc.get("charger_id") or ""
    targets = inc.get("targets") or []
    detail_url = f"{GRAFANA}/d/chargeops-main?var-charger={cid or 'All'}"
    blocks = [
        {"type": "header", "text": {"type": "plain_text",
                                    "text": f"{SEV_ICON.get(inc['severity'], '')} [{inc['severity']}] {inc['title']}"[:150]}},
        {"type": "section", "fields": [
            {"type": "mrkdwn", "text": f"*유형*\n{inc['type_code']}"},
            {"type": "mrkdwn", "text": f"*장애 번호*\n#{inc['incident_id']}"},
            {"type": "mrkdwn", "text": f"*대상*\n{cid or ', '.join(targets[:10]) or inc.get('station_id') or '-'}"},
            {"type": "mrkdwn", "text": f"*감지 시각*\n{inc['detected_at']:%m-%d %H:%M:%S}"}]},
    ]
    if mentions:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"호출: {mentions}"}]})

    detail = {"type": "button", "text": {"type": "plain_text", "text": "상세 보기"},
              "url": detail_url, "action_id": "detail"}
    code = inc["type_code"]
    if code == "A-01":           # 이상 징후: 버튼 5종
        actions = [detail, btn("감시 강화 1시간", "watch", inc),
                   btn("예방 점검(Soft Reset)", "soft_reset", inc, "primary"),
                   btn("사용 중지·현장 점검", "disable", inc, "danger"),
                   btn("오탐 처리", "false_positive", inc, anomaly_id=inc.get("anomaly_id"))]
    elif code == "C-03":         # 안전 이상: 리셋 금지, 사용 중지만
        actions = [detail, btn("사용 중지·현장 점검", "disable", inc, "danger")]
    elif code.startswith("C-") and len(targets) > 1:
        actions = [detail, btn(f"일괄 Soft Reset ({len(targets)}대)", "soft_reset", inc, "primary")]
    elif code.startswith("C-") and cid:
        actions = [detail, btn("Soft Reset", "soft_reset", inc, "primary"),
                   btn("Hard Reset", "hard_reset", inc),
                   btn("사용 중지·현장 점검", "disable", inc, "danger")]
    else:                        # 플랫폼 장애(P-*)는 런북 대응 — 조회만
        actions = [detail]
    blocks.append({"type": "actions", "block_id": f"inc-{inc['incident_id']}", "elements": actions})
    return blocks
