"""ChatOps 봇 (Slack Bolt · Socket Mode)
- 버튼 클릭 → 역할 조회(ops_role) → 정책 판정(common.policy) → 승인/실행 → 감사 기록
- 슬래시 명령: /ask  /incident  /role  /deploy  /dr-drill
Socket Mode = 봇이 Slack 으로 '나가는' WebSocket 연결 → 외부 공개 포트가 필요 없다
"""
import json, logging, os, threading, time, uuid

import requests
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from common.db import q, q1
from common.policy import REASON_REQUIRED, can_approve, decide

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bot")

app = App(token=os.environ["SLACK_BOT_TOKEN"])
CSMS, TOKEN = os.environ.get("CSMS_URL", "http://csms:8000"), os.environ.get("CSMS_API_TOKEN", "")
AI = os.environ.get("AI_URL", "http://ai-gateway:8000")
CH_AUDIT = os.environ.get("SLACK_AUDIT_CHANNEL", "")
JENKINS = os.environ.get("JENKINS_URL", "http://10.10.40.10:8080")
JENKINS_AUTH = (os.environ.get("JENKINS_USER", "admin"), os.environ.get("JENKINS_TOKEN", ""))
BUTTON_TTL = 600                                      # 버튼 10분 후 만료
STEP = {"soft_reset": "Soft", "hard_reset": "Hard"}


def role_of(user: str) -> str:
    """실행 직전에 매번 DB 에서 역할 조회 (캐시하지 않음 → 회수 즉시 반영)"""
    r = q1("""SELECT role FROM ops_role WHERE slack_user_id = %s AND valid_from <= now()
              AND (valid_to IS NULL OR valid_to > now()) ORDER BY valid_from DESC LIMIT 1""", (user,))
    return r["role"] if r else "Viewer"


def audit(user, role, step, result, inc_id=None, cid=None, reason=None, approved_by=None) -> int:
    """감사 기록 1행 추가 (수정 · 삭제 불가 테이블). 승인 요청이 이 행을 가리키도록 action_id 를 돌려준다"""
    return q1("""INSERT INTO action_audit (incident_id, charger_id, idempotency_key, step, requested_by,
                                          approved_by, role, result, reason, executed_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s, now()) RETURNING action_id""",
              (inc_id, cid, f"bot-{uuid.uuid4().hex}", step, user, approved_by, role, result, reason))["action_id"]


def thread_say(client, body, text):
    ch = body["channel"]["id"]
    ts = body["message"].get("thread_ts") or body["message"]["ts"]
    client.chat_postMessage(channel=ch, thread_ts=ts, text=text)


def deny(client, body, user, role, action, reason, v):
    """권한 없는 클릭: 본인에게만 보이는 메시지(ephemeral) + 감사 기록"""
    client.chat_postEphemeral(channel=body["channel"]["id"], user=user, text=f"⛔ {reason}")
    audit(user, role, action, "denied", v.get("incident_id"), v.get("charger_id"), reason)


def csms_call(path: str, payload: dict) -> requests.Response:
    return requests.post(f"{CSMS}{path}", json=payload, headers={"X-Api-Token": TOKEN}, timeout=15)


def charger_status(cid: str) -> str | None:
    r = requests.get(f"{CSMS}/api/chargers/{cid}", timeout=5)
    return r.json().get("status") if r.ok else None


def execute(client, body, action, v, user, role, approved_by=None, reason=None):
    """실제 실행: 직전 상태 재확인 → CSMS 호출 → 결과 추적"""
    inc_id = v["incident_id"]
    targets = v.get("targets") or [v["charger_id"]]
    q("UPDATE incident SET status='acked', acked_at=coalesce(acked_at, now()) WHERE incident_id=%s AND status='open'", (inc_id,))
    for cid in targets:
        st = charger_status(cid)
        if action in STEP and st == "Available":
            thread_say(client, body, f"ℹ️ {cid} 는 이미 Available — 실행 보류"); continue
        if st == "Charging" and action != "watch":
            thread_say(client, body, f"⏸ {cid} 충전 중 — 종료 후 다시 시도하세요"); continue
        key = f"inc{inc_id}-{action}-{cid}-{int(time.time() // 10)}"     # 10초 내 중복 클릭 = 같은 키
        base = {"idempotency_key": key, "requested_by": user, "approved_by": approved_by,
                "role": role, "incident_id": inc_id, "reason": reason}
        if action in STEP:
            r = csms_call(f"/api/chargers/{cid}/reset", {**base, "type": STEP[action]})
        elif action == "disable":
            r = csms_call(f"/api/chargers/{cid}/availability", {**base, "type": "Inoperative"})
        elif action == "watch":
            r = csms_call(f"/api/chargers/{cid}/watch", {**base, "interval": 5, "seconds": 3600})
        else:
            continue
        if not r.ok:
            thread_say(client, body, f"❌ {cid} {action} 거부: {r.json().get('detail')}"); continue
        thread_say(client, body, f"▶️ <@{user}>({role}) {cid} {action} 실행" + (f" · 승인 <@{approved_by}>" if approved_by else ""))
        if action in STEP:
            threading.Thread(target=follow_reset, args=(client, body, cid, action, inc_id), daemon=True).start()
        if action == "disable":
            q("UPDATE incident SET type_code = CASE WHEN type_code='A-01' THEN 'C-01' ELSE type_code END, "
              "severity = CASE WHEN severity='SEV4' THEN 'SEV3' ELSE severity END, root_cause=%s WHERE incident_id=%s",
              (f"현장 점검 이관: {reason}", inc_id))
            thread_say(client, body, f"🛠 {cid} 사용 중지 · 현장 점검 요청 등록 (사유: {reason})")


def follow_reset(client, body, cid, action, inc_id):
    """리셋 후 Available 복귀 여부를 지켜보다가 스레드에 결과 보고"""
    limit = 90 if action == "soft_reset" else 120
    t0 = time.time()
    time.sleep(5)
    while time.time() - t0 < limit:
        if charger_status(cid) == "Available":
            thread_say(client, body, f"✅ {cid} {action} 성공 — {time.time() - t0:.0f}초 만에 Available")
            q("UPDATE anomaly_event SET verdict='confirmed' WHERE incident_id=%s AND verdict='open'", (inc_id,))
            q("""UPDATE incident SET status='resolved', resolved_at=now(), root_cause=coalesce(root_cause,'')||%s
                 WHERE incident_id=%s AND type_code='A-01'""", (f" / 예방 점검 {action} 성공", inc_id))
            return
        time.sleep(3)
    nxt = "Hard Reset(Lead 승인)" if action == "soft_reset" else "사용 중지 · 현장 점검"
    thread_say(client, body, f"⚠️ {cid} {action} 후 {limit}초 내 복귀 실패 → 다음 단계: {nxt}")


# ───────── 버튼 처리 ─────────
@app.action("detail")
def on_detail(ack):
    ack()                                   # URL 버튼도 이벤트가 오므로 응답만


@app.action("soft_reset")
@app.action("hard_reset")
@app.action("disable")
@app.action("watch")
@app.action("false_positive")
def on_action(ack, body, client):
    ack()                                   # 3초 안에 ack 해야 Slack 이 '실패'로 표시하지 않음
    action = body["actions"][0]["action_id"]
    v = json.loads(body["actions"][0]["value"])
    user = body["user"]["id"]
    role = role_of(user)

    if time.time() - float(body["message"]["ts"]) > BUTTON_TTL and action != "false_positive":
        return deny(client, body, user, role, action, "버튼이 만료되었습니다(10분). 새 상태로 다시 판단하세요", v)
    d = decide(action, role, v.get("severity"), len(v.get("targets") or [1]))
    if not d.allowed:
        return deny(client, body, user, role, action, d.reason, v)

    if action in REASON_REQUIRED:           # 사유 입력 모달
        return client.views_open(trigger_id=body["trigger_id"], view={
            "type": "modal", "callback_id": "reason_modal",
            "private_metadata": json.dumps({"action": action, "v": v, "approvals": d.approvals_needed,
                                            "channel": body["channel"]["id"],
                                            "ts": body["message"].get("thread_ts") or body["message"]["ts"]}),
            "title": {"type": "plain_text", "text": "사유 입력"},
            "submit": {"type": "plain_text", "text": "확인"},
            "blocks": [{"type": "input", "block_id": "r", "label": {"type": "plain_text", "text": f"{action} 사유"},
                        "element": {"type": "plain_text_input", "action_id": "reason", "multiline": True}}]})

    if d.approvals_needed:                  # 승인 요청 생성
        return request_approval(client, body["channel"]["id"],
                                body["message"].get("thread_ts") or body["message"]["ts"],
                                action, v, user, role, d.approvals_needed)
    execute(client, body, action, v, user, role)


def request_approval(client, channel, ts, action, v, user, role, need, reason=None):
    payload = {"action": action, "v": v, "need": need, "reason": reason, "role": role}
    # 스키마 v1.1: 승인은 감사 기록(pending_approval 행)을 action_id 로 가리킨다 (ERD 의 approval → action_audit)
    action_id = audit(user, role, action, "pending_approval", v["incident_id"], v.get("charger_id"), reason)
    row = q1("INSERT INTO approval (action_id, requested_by, payload) VALUES (%s,%s,%s) RETURNING approval_id",
             (action_id, user, json.dumps(payload)))
    leads = " ".join(f"<@{r['slack_user_id']}>" for r in q(
        "SELECT DISTINCT slack_user_id FROM ops_role WHERE role IN ('Lead','Admin') AND valid_to IS NULL AND slack_user_id <> %s", (user,)))
    client.chat_postMessage(channel=channel, thread_ts=ts, text="승인 요청", blocks=[
        {"type": "section", "text": {"type": "mrkdwn",
         "text": f"🔐 *승인 요청* <@{user}> → `{action}` {v.get('charger_id') or v.get('targets')}\n필요 승인: Lead {need}인 · 10분 내 · {leads}"}},
        {"type": "actions", "elements": [
            {"type": "button", "text": {"type": "plain_text", "text": "승인"}, "style": "primary",
             "action_id": "approve", "value": str(row["approval_id"])},
            {"type": "button", "text": {"type": "plain_text", "text": "거절"}, "style": "danger",
             "action_id": "reject", "value": str(row["approval_id"])}]}])


@app.view("reason_modal")
def on_reason(ack, body, client, view):
    ack()
    meta = json.loads(view["private_metadata"])
    reason = view["state"]["values"]["r"]["reason"]["value"]
    user, action, v = body["user"]["id"], meta["action"], meta["v"]
    role = role_of(user)
    fake_body = {"channel": {"id": meta["channel"]}, "message": {"ts": meta["ts"], "thread_ts": meta["ts"]}}
    if action == "false_positive":          # 오탐 처리: 이벤트 종료 + 판정 저장 → 주간 임계값 조정 근거
        q("UPDATE anomaly_event SET verdict='false_positive' WHERE incident_id=%s AND verdict='open'", (v["incident_id"],))
        q("UPDATE incident SET status='resolved', resolved_at=now(), root_cause=%s WHERE incident_id=%s",
          (f"오탐: {reason}", v["incident_id"]))
        audit(user, role, "false_positive", "executed", v["incident_id"], v.get("charger_id"), reason)
        return thread_say(client, fake_body, f"🙆 <@{user}> 오탐 처리 — {reason}")
    if meta["approvals"]:
        return request_approval(client, meta["channel"], meta["ts"], action, v, user, role, meta["approvals"], reason)
    execute(client, fake_body, action, v, user, role, reason=reason)


@app.action("approve")
@app.action("reject")
def on_approve(ack, body, client):
    ack()
    user, role = body["user"]["id"], role_of(body["user"]["id"])
    apr = q1("SELECT * FROM approval WHERE approval_id=%s", (int(body["actions"][0]["value"]),))
    p = apr["payload"]
    if apr["decision"] != "pending" or apr["expires_at"].timestamp() < time.time():
        if apr["decision"] == "pending":
            q("UPDATE approval SET decision='expired' WHERE approval_id=%s", (apr["approval_id"],))
        return deny(client, body, user, role, "approve", "이미 처리되었거나 만료된 요청입니다", p["v"])
    done = [r["approver_id"] for r in q("""SELECT approver_id FROM approval WHERE action_id=%s
                                           AND decision='approved' AND approver_id IS NOT NULL""",
                                        (apr["action_id"],))]
    d = can_approve(p["action"], role, apr["requested_by"], user, done)
    if not d.allowed:
        return deny(client, body, user, role, "approve", d.reason, p["v"])

    if body["actions"][0]["action_id"] == "reject":
        q("""INSERT INTO approval (action_id, requested_by, approver_id, role, decision, decided_at)
             VALUES (%s,%s,%s,%s,'rejected', now())""", (apr["action_id"], apr["requested_by"], user, role))
        q("UPDATE approval SET decision='rejected', decided_at=now() WHERE approval_id=%s", (apr["approval_id"],))
        audit(user, role, p["action"], "rejected", p["v"]["incident_id"], p["v"].get("charger_id"), approved_by=user)
        return thread_say(client, body, f"🚫 <@{user}> 거절")

    # 승인 1표 기록 (다인 승인: 같은 action_id 의 approved 표가 need 개 모이면 실행. 한 사람 한 표는 DB 가 보장)
    q("""INSERT INTO approval (action_id, requested_by, approver_id, role, decision, decided_at)
         VALUES (%s,%s,%s,%s,'approved', now())""", (apr["action_id"], apr["requested_by"], user, role))
    if len(done) + 1 < p["need"]:
        return thread_say(client, body, f"👍 <@{user}> 승인 ({len(done) + 1}/{p['need']})")
    q("UPDATE approval SET decision='approved', decided_at=now() WHERE approval_id=%s", (apr["approval_id"],))
    if p["action"] == "dr_drill":
        return run_jenkins(client, body, "chargeops-dr-drill", {}, apr["requested_by"], user)
    execute(client, body, p["action"], p["v"], apr["requested_by"], p["role"], approved_by=user, reason=p.get("reason"))


# ───────── 슬래시 명령 ─────────
@app.command("/ask")
def cmd_ask(ack, command, respond):
    ack()
    r = requests.post(f"{AI}/ask", json={"question": command["text"]}, timeout=90).json()
    respond(f"*Q.* {command['text']}\n{r.get('answer')}\n_근거: {r.get('evidence')} · {r.get('provider')}_")


@app.command("/incident")
def cmd_incident(ack, respond):
    ack()
    rows = q("SELECT incident_id, severity, status, title, detected_at FROM incident WHERE status <> 'resolved' ORDER BY severity, detected_at")
    respond("\n".join(f"• #{r['incident_id']} [{r['severity']}] {r['status']} {r['title']} ({r['detected_at']:%m-%d %H:%M})"
                      for r in rows) or "열린 장애가 없습니다 ✅")


@app.command("/role")
def cmd_role(ack, command, respond, client):
    """/role list | /role grant @user Operator | /role revoke @user"""
    ack()
    user, args = command["user_id"], command["text"].split()
    if not args or args[0] == "list":
        rows = q("SELECT slack_user_id, role, valid_from FROM ops_role WHERE valid_to IS NULL ORDER BY role")
        return respond("\n".join(f"• <@{r['slack_user_id']}> {r['role']} (since {r['valid_from']:%m-%d})" for r in rows))
    role = role_of(user)
    if not decide("role_grant", role).allowed:
        audit(user, role, "role_grant", "denied", reason=command["text"])
        return respond("⛔ Admin 만 역할을 부여·회수할 수 있습니다")
    target = args[1].split("|")[0].strip("<@>")
    q("UPDATE ops_role SET valid_to = now() WHERE slack_user_id=%s AND valid_to IS NULL", (target,))   # 이전 역할 종료
    if args[0] == "grant":
        q("INSERT INTO ops_role (slack_user_id, role, granted_by) VALUES (%s,%s,%s)", (target, args[2], user))
    audit(user, role, f"role_{args[0]}", "executed", reason=command["text"])
    msg = f"🔑 <@{user}> {args[0]} <@{target}> {args[2] if args[0] == 'grant' else ''}"
    respond(msg)
    if CH_AUDIT:
        client.chat_postMessage(channel=CH_AUDIT, text=msg)         # #ops-audit 자동 게시


def run_jenkins(client, body_or_cmd, job, params, requester, approver=None):
    r = requests.post(f"{JENKINS}/job/{job}/buildWithParameters", params=params, auth=JENKINS_AUTH, timeout=10)
    text = f"🚀 Jenkins `{job}` 실행 요청 ({r.status_code}) 요청 <@{requester}>" + (f" · 승인 <@{approver}>" if approver else "")
    audit(requester, "Admin", job, "executed" if r.ok else "failed", approved_by=approver)
    if CH_AUDIT:
        client.chat_postMessage(channel=CH_AUDIT, text=text)
    return text


@app.command("/deploy")
def cmd_deploy(ack, command, respond, client):
    ack()
    user, role = command["user_id"], role_of(command["user_id"])
    if not decide("deploy", role).allowed:
        audit(user, role, "deploy", "denied")
        return respond("⛔ 배포는 Admin 만 가능합니다")
    respond(run_jenkins(client, command, "chargeops", {"BRANCH": "main"}, user))


@app.command("/dr-drill")
def cmd_dr(ack, command, respond, client):
    ack()
    user, role = command["user_id"], role_of(command["user_id"])
    d = decide("dr_drill", role)
    if not d.allowed:
        audit(user, role, "dr_drill", "denied")
        return respond("⛔ DR 훈련 요청은 Admin 만 가능합니다")
    request_approval(client, command["channel_id"], None, "dr_drill", {"incident_id": None}, user, role, 1)
    respond("DR 훈련 승인 요청을 올렸습니다 (Lead 승인 필요)")


if __name__ == "__main__":
    log.info("ChatOps bot starting (Socket Mode)")
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()
