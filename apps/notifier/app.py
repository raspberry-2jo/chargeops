"""Notifier (알림 라우터)
- POST /alertmanager : Alertmanager 웹훅 → incident 생성/갱신 → 등급별 채널 발송
- 이상 징후 폴링      : anomaly_event(WARN/CRITICAL) → Slack 버튼 메시지, WATCH → 감시 강화
- 에스컬레이션        : 무응답 시 상위 역할 재호출
- POST /watchdog     : Alertmanager Watchdog 수신 → 5분 끊기면 P-05(알림 경로 장애)
- POST /report/daily : 일일 리포트 (SEV4 · WATCH · 가동률 · 품질 점수)
- 장애 정리(reconcile): 해소 알림을 놓친 장애를 1분마다 Alertmanager 현재 경보와 맞춰 보고 자동으로 닫음
"""
import json, logging, os, smtplib, threading, time, uuid
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

import requests
from fastapi import FastAPI, Request
from slack_sdk import WebClient

from common.db import q, q1
from common.slackfmt import LEVEL_ICON, incident_blocks

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("notifier")

slack = WebClient(token=os.environ["SLACK_BOT_TOKEN"])
CH_ALERTS = os.environ["SLACK_ALERT_CHANNEL"]          # #ops-alerts 채널 ID
DISCORD = os.environ.get("DISCORD_WEBHOOK_URL", "")
SMTP_HOST, SMTP_PORT = os.environ.get("SMTP_HOST", "10.10.40.10"), int(os.environ.get("SMTP_PORT", "1025"))
MAIL_TO = os.environ.get("MAIL_TO", "ops@chargeops.lab")
CSMS, CSMS_TOKEN = os.environ.get("CSMS_URL", "http://csms:8000"), os.environ.get("CSMS_API_TOKEN", "")
AI = os.environ.get("AI_URL", "http://ai-gateway:8000")
AM = os.environ.get("ALERTMANAGER_URL", "http://10.10.20.203:9093")     # 장애 정리용 (현재 경보 조회)
RECONCILE_GRACE = int(os.environ.get("RECONCILE_GRACE_SEC", "300"))     # 이만큼 계속 안 울려야 닫음
RECONCILE_EVERY = int(os.environ.get("RECONCILE_EVERY_SEC", "60"))      # 정리 주기
KST = timezone(timedelta(hours=9))                                     # 한국은 서머타임이 없어 고정 +09:00 (tzdata 없는 이미지 대비)

# 등급별 채널 (문서 2.4 알림 채널 표)
ROUTE = {"SEV1": {"slack", "discord", "mail"}, "SEV2": {"slack", "mail"},
         "SEV3": {"slack"}, "SEV4": set()}
ESCALATE_MIN = {"SEV1": 5, "SEV2": 15, "SEV3": 15}
state = {"slack_fail": 0, "last_watchdog": time.time(), "p05_sent": False, "last_reconcile": 0.0}
_missing: dict[int, float] = {}   # incident_id -> 경보가 처음 안 울린 것을 본 시각 (장애 정리용)
app = FastAPI(title="ChargeOps notifier")


# ───────── 채널별 발송 (결과는 모두 notification_log 에 기록) ─────────
def log_send(inc_id, channel, target, status, detail=""):
    q("INSERT INTO notification_log (incident_id, channel, target, status, detail) VALUES (%s,%s,%s,%s,%s)",
      (inc_id, channel, target, status, detail[:500]))


def send_slack(inc_id, text, blocks=None, thread_ts=None) -> str | None:
    try:
        r = slack.chat_postMessage(channel=CH_ALERTS, text=text, blocks=blocks, thread_ts=thread_ts)
        state["slack_fail"] = 0
        log_send(inc_id, "slack", CH_ALERTS, "ok")
        return r["ts"]
    except Exception as e:
        state["slack_fail"] += 1
        log_send(inc_id, "slack", CH_ALERTS, "error", str(e))
        log.error("slack send failed (%d): %s", state["slack_fail"], e)
        if state["slack_fail"] >= 3:                  # P-05: Slack API 3회 연속 오류
            alert_path_down(f"Slack API {state['slack_fail']}회 연속 오류: {e}")
        return None


def send_discord(inc_id, text):
    if not DISCORD:
        return
    try:
        requests.post(DISCORD, json={"content": text[:1900]}, timeout=5).raise_for_status()
        log_send(inc_id, "discord", "webhook", "ok")
    except Exception as e:
        log_send(inc_id, "discord", "webhook", "error", str(e))


def send_mail(inc_id, subject, body):
    try:
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = "chargeops@chargeops.lab", MAIL_TO, subject
        msg.set_content(body)
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=5) as s:
            s.send_message(msg)
        log_send(inc_id, "mail", MAIL_TO, "ok")
    except Exception as e:
        log_send(inc_id, "mail", MAIL_TO, "error", str(e))


def alert_path_down(reason: str):
    """Slack 을 우회해 Discord + 메일로 즉시 발송 (같은 장애는 1회)"""
    if state["p05_sent"]:
        return
    state["p05_sent"] = True
    text = f"🚨 [SEV2] P-05 알림 경로 장애 — {reason}"
    send_discord(None, text)
    send_mail(None, "[ChargeOps][SEV2] P-05 알림 경로 장애", text)


def mentions_for(sev: str) -> str:
    roles = {"SEV1": ("Lead", "Admin"), "SEV2": ("Operator", "Lead"), "SEV3": ("Operator",)}.get(sev, ())
    if not roles:
        return ""
    rows = q("""SELECT DISTINCT slack_user_id FROM ops_role
                WHERE role = ANY(%s) AND (valid_to IS NULL OR valid_to > now())""", (list(roles),))
    return " ".join(f"<@{r['slack_user_id']}>" for r in rows)


def dispatch(inc: dict, extra: str = ""):
    """새 장애 1건을 등급별 채널로 발송하고, 스레드에 AI 요약을 붙인다"""
    sev, chans = inc["severity"], ROUTE[inc["severity"]]
    text = f"[{sev}] {inc['title']} (#{inc['incident_id']})"
    ts = None
    if "slack" in chans or inc["type_code"] == "A-01":
        ts = send_slack(inc["incident_id"], text, incident_blocks(inc, mentions_for(sev)))
        if ts:
            q("UPDATE incident SET slack_channel=%s, slack_thread_ts=%s WHERE incident_id=%s",
              (CH_ALERTS, ts, inc["incident_id"]))
    if "discord" in chans or ("slack" in chans and ts is None):   # Slack 실패 시 Discord 대체
        send_discord(inc["incident_id"], text + extra)
    if "mail" in chans:
        send_mail(inc["incident_id"], f"[ChargeOps]{text}", text + extra)
    if ts:
        threading.Thread(target=attach_ai, args=(inc["incident_id"], ts), daemon=True).start()


def attach_ai(inc_id: int, ts: str):
    """AI 게이트웨이 요약 + 유사 사례 Top3 를 스레드에 첨부 (실패해도 흐름은 계속)"""
    try:
        r = requests.post(f"{AI}/summarize", json={"incident_id": inc_id}, timeout=60).json()
        sim = "\n".join(f"• #{s['incident_id']} {s['title']} (유사도 {s['similarity']:.2f}, 조치: {s.get('root_cause') or '-'})"
                        for s in r.get("similar", [])) or "• 유사 사례 없음"
        send_slack(inc_id, "AI 요약", thread_ts=ts, blocks=[
            {"type": "section", "text": {"type": "mrkdwn",
             "text": f"*🤖 AI 요약* ({r.get('provider')}, {r.get('latency_ms')}ms)\n{r.get('summary')}"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*📚 유사 사례*\n{sim}"}},
            {"type": "context", "elements": [{"type": "mrkdwn", "text": "AI 는 판단을 돕고, 실행은 사람이 승인합니다."}]}])
    except Exception as e:
        log.warning("AI summary failed: %s", e)


# ───────── Alertmanager 웹훅 ─────────
def virtual_charger_ids(charger_ids):
    """Exclude registered virtual chargers and reserved simulation/load-test IDs."""
    virtual = {cid for cid in charger_ids if cid.startswith(("CI-DEV-", "CI-OPS-CANARY-", "LT-"))}
    remaining = sorted(set(charger_ids) - virtual)
    if remaining:
        virtual.update(r["charger_id"] for r in q(
            "SELECT charger_id FROM charger WHERE charger_id = ANY(%s) AND source = 'virtual'",
            (remaining,)))
    return virtual


def service_alerts(alerts, common=None):
    common = common or {}
    ids = {a.get("labels", {}).get("charger_id", common.get("charger_id")) for a in alerts}
    virtual = virtual_charger_ids(ids - {None, ""})
    return [a for a in alerts
            if a.get("labels", {}).get("charger_id", common.get("charger_id")) not in virtual]


def severity_for(code: str, base: str, n: int) -> str:
    """같은 충전소 동시 장애 수로 등급 상향: 3대↑ SEV2, 5대↑ & 전체 30%↑ SEV1"""
    if code.startswith("C-") and code not in ("C-03", "C-05"):
        total = q1("""SELECT count(*) AS n FROM charger_status_current cur JOIN charger c USING (charger_id)
                       WHERE c.source <> 'public'""")["n"] or 1      # 우리가 관제하는 충전기(모형 · 가상) 기준
        if n >= 5 and n / total >= 0.3:
            return "SEV1"
        if n >= 3:
            return "SEV2"
    return base


@app.post("/alertmanager")
async def alertmanager(req: Request):
    body = await req.json()
    common = body.get("commonLabels", {})
    code = common.get("code")
    if not code:
        return {"skipped": "no code label"}
    alerts = service_alerts(body["alerts"], common)
    if not alerts:
        log.info("alertmanager: skipped virtual charger alerts")
        return {"skipped": "virtual charger alerts"}
    firing = [a for a in alerts if a["status"] == "firing"]
    station = common.get("station_id")
    fp = f"{code}:{station or common.get('charger_id') or common.get('alertname')}"
    open_inc = q1("SELECT * FROM incident WHERE fingerprint=%s AND status <> 'resolved'", (fp,))

    if body["status"] == "resolved" or not firing:                     # ① 전체 복구
        if open_inc:
            q("UPDATE incident SET status='resolved', resolved_at=now() WHERE incident_id=%s",
              (open_inc["incident_id"],))
            mttr = q1("SELECT round(extract(epoch FROM resolved_at-detected_at)/60,1) AS m FROM incident WHERE incident_id=%s",
                      (open_inc["incident_id"],))["m"]
            if open_inc["slack_thread_ts"]:
                send_slack(open_inc["incident_id"], f"✅ 복구 확인 — 소요 {mttr}분", thread_ts=open_inc["slack_thread_ts"])
            threading.Thread(target=lambda: requests.post(f"{AI}/embed", json={"incident_id": open_inc["incident_id"]},
                                                          timeout=60), daemon=True).start()
        return {"resolved": fp}

    targets = sorted({a["labels"].get("charger_id") for a in firing if a["labels"].get("charger_id")})
    sev = severity_for(code, common.get("severity", "SEV3").upper(), len(targets))
    title = firing[0]["annotations"].get("summary", code)
    if len(targets) > 1:
        title = f"{station} 충전기 {len(targets)}대 {firing[0]['labels'].get('alertname')}"

    if open_inc:                                                        # ② 진행 중 장애 갱신(새 스레드 X)
        if set(targets) != set(open_inc["targets"] or []):
            q("UPDATE incident SET targets=%s, severity=%s, title=%s WHERE incident_id=%s",
              (targets, sev, title, open_inc["incident_id"]))
            if open_inc["slack_thread_ts"]:
                send_slack(open_inc["incident_id"], f"🔁 대상 갱신: {len(targets)}대 ({sev}) {', '.join(targets[:20])}",
                           thread_ts=open_inc["slack_thread_ts"])
        return {"updated": open_inc["incident_id"]}

    inc = q1("""INSERT INTO incident (fingerprint, charger_id, station_id, targets, type_code, severity, title, root_cause)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (fingerprint) WHERE status <> 'resolved' DO NOTHING RETURNING *""",
             (fp, targets[0] if len(targets) == 1 else None, station, targets, code, sev, title,
              firing[0]["annotations"].get("description")))
    if inc:                                                             # ③ 신규 장애
        dispatch(inc, "\n" + (firing[0]["annotations"].get("description") or ""))
    return {"created": inc and inc["incident_id"]}


@app.post("/watchdog")
async def watchdog():
    state["last_watchdog"], state["p05_sent"] = time.time(), False
    return {"ok": True}


# ───────── 이상 징후 폴링 · 에스컬레이션 (백그라운드 스레드) ─────────
def csms_watch(cid: str):
    requests.post(f"{CSMS}/api/chargers/{cid}/watch", headers={"X-Api-Token": CSMS_TOKEN}, timeout=10,
                  json={"idempotency_key": f"auto-watch-{cid}-{uuid.uuid4().hex[:8]}",
                        "requested_by": "system", "role": "system", "interval": 5, "seconds": 3600})


def poll_anomaly():
    for ev in q("""SELECT * FROM anomaly_event WHERE notified_at IS NULL AND verdict = 'open'
                   ORDER BY detected_at"""):
        cid, lvl = ev["charger_id"], ev["level"]
        try:
            if cid in virtual_charger_ids({cid}):
                q("UPDATE anomaly_event SET notified_at = now() WHERE anomaly_id=%s", (ev["anomaly_id"],))
                continue
            if lvl == "WATCH":                    # WATCH: Slack 없이 수집 주기만 5초로 강화
                csms_watch(cid)
            else:
                last = ev["feature"][-1] if ev["feature"] else {}
                inc = q1("""INSERT INTO incident (fingerprint, charger_id, station_id, targets, type_code, severity, title, root_cause)
                            SELECT %s, c.charger_id, c.station_id, ARRAY[c.charger_id], 'A-01', %s, %s, %s
                            FROM charger c WHERE c.charger_id = %s
                            ON CONFLICT (fingerprint) WHERE status <> 'resolved'
                            DO UPDATE SET severity = EXCLUDED.severity RETURNING *""",
                         (f"A-01:{cid}", "SEV2" if lvl == "CRITICAL" else "SEV3",
                          f"{LEVEL_ICON[lvl]} 이상 징후 {lvl}: {cid} 온도 {last.get('temp')}℃ (점수 {ev['score']})",
                          json.dumps(last, ensure_ascii=False), cid))
                inc["anomaly_id"] = ev["anomaly_id"]
                q("UPDATE anomaly_event SET incident_id=%s WHERE anomaly_id=%s", (inc["incident_id"], ev["anomaly_id"]))
                if inc["slack_thread_ts"]:        # WARN → CRITICAL 상향은 같은 스레드에
                    send_slack(inc["incident_id"], f"⬆️ {lvl} 로 상향 (점수 {ev['score']})", thread_ts=inc["slack_thread_ts"])
                else:
                    dispatch(inc)
            q("UPDATE anomaly_event SET notified_at = now() WHERE anomaly_id=%s", (ev["anomaly_id"],))
        except Exception as e:
            log.error("anomaly notify failed %s: %s", ev["anomaly_id"], e)


def escalate():
    for inc in q("""SELECT * FROM incident i WHERE status = 'open' AND acked_at IS NULL AND slack_thread_ts IS NOT NULL
                    AND NOT EXISTS (SELECT 1 FROM notification_log n WHERE n.incident_id = i.incident_id
                                    AND n.channel = 'escalation')"""):
        limit = ESCALATE_MIN.get(inc["severity"])
        if limit and (time.time() - inc["detected_at"].timestamp()) > limit * 60:
            who = mentions_for("SEV1")            # Lead · Admin 호출
            send_slack(inc["incident_id"], f"⏰ {limit}분 무응답 — 에스컬레이션 {who}", thread_ts=inc["slack_thread_ts"])
            log_send(inc["incident_id"], "escalation", who, "ok")
            if inc["severity"] == "SEV1":
                send_discord(inc["incident_id"], f"⏰ SEV1 무응답 재발송: {inc['title']}")


def reconcile():
    """해소 알림을 놓친 장애 정리 (재시작 · 알림 유실 · fingerprint 불일치 대비)
    같은 code(+충전소)로 울리는 경보가 RECONCILE_GRACE 동안 없으면 닫는다.
    A-01(이상 탐지) · C-05(장기 방치)는 DB 가 판단하는 장애라 제외. Alertmanager 조회 실패 시 아무것도 안 닫음"""
    try:
        alerts = requests.get(f"{AM}/api/v2/alerts", params={"active": "true"}, timeout=5).json()
    except Exception as e:
        log.warning("reconcile skip (alertmanager): %s", e)
        return
    firing = {(a["labels"].get("code"), a["labels"].get("station_id")) for a in service_alerts(alerts)}
    codes = {c for c, _ in firing}
    rows = q("""SELECT incident_id, type_code, station_id, detected_at, slack_thread_ts FROM incident
                WHERE status <> 'resolved' AND type_code NOT IN ('A-01', 'C-05')""")
    open_ids = {r["incident_id"] for r in rows}
    for iid in list(_missing):                    # 그사이 다른 경로로 닫힌 장애는 기록에서 뺌
        if iid not in open_ids:
            _missing.pop(iid, None)
    now = time.time()
    for r in rows:
        iid = r["incident_id"]
        still = (r["type_code"], r["station_id"]) in firing if r["station_id"] else r["type_code"] in codes
        if still:
            _missing.pop(iid, None)
            continue
        first = _missing.setdefault(iid, now)
        if now - first < RECONCILE_GRACE:
            continue
        first = max(first, r["detected_at"].timestamp())
        done = q("""UPDATE incident SET status = 'resolved', resolved_at = to_timestamp(%s),
                        root_cause = coalesce(root_cause, '') || ' / auto-resolved: alert cleared, resolve notice missed'
                    WHERE incident_id = %s AND status <> 'resolved' RETURNING incident_id""", (first, iid))
        _missing.pop(iid, None)
        if not done:                              # 그사이 해소 알림 · 다른 파드가 먼저 닫음 → 메시지 중복 방지
            continue
        log.info("reconcile: incident %s auto-resolved", iid)
        if r["slack_thread_ts"]:
            mins = round((first - r["detected_at"].timestamp()) / 60, 1)
            since = datetime.fromtimestamp(first, KST).strftime("%H:%M")
            send_slack(iid,
                       f"✅ 복구 확인 (자동 정리) — 소요 {mins}분\n"
                       f"{since}부터 이 장애의 경보가 {RECONCILE_GRACE // 60}분 넘게 꺼져 있어 장애를 닫았습니다.\n"
                       "재시작 등으로 해소 알림이 오지 않아, notifier가 현재 경보 상태를 직접 확인해 정리한 것입니다. "
                       "같은 문제가 다시 생기면 새 스레드로 알립니다.",
                       thread_ts=r["slack_thread_ts"])


def loop():
    while True:
        for fn in (poll_anomaly, escalate):
            try:
                fn()
            except Exception as e:
                log.error("%s: %s", fn.__name__, e)
        if time.time() - state["last_reconcile"] >= RECONCILE_EVERY:   # 장애 정리는 1분마다
            state["last_reconcile"] = time.time()
            try:
                reconcile()
            except Exception as e:
                log.error("reconcile: %s", e)
        if time.time() - state["last_watchdog"] > 300:   # Watchdog 5분 미수신 → 알림 경로 이상
            alert_path_down("Alertmanager Watchdog 5분 미수신")
        time.sleep(10)


threading.Thread(target=loop, daemon=True).start()


# ───────── 일일 리포트 (CronJob 이 매일 09:00 호출) ─────────
@app.post("/report/daily")
def daily_report():
    d = "current_date - 1"
    up = q(f"SELECT operator_id, uptime_pct, mttr_min, fault_cnt, long_fault_cnt FROM stat_operator_daily WHERE day = {d}")
    inc = q(f"SELECT severity, count(*) AS n FROM incident WHERE detected_at >= {d} AND detected_at < current_date GROUP BY 1 ORDER BY 1")
    watch = q1(f"SELECT count(*) AS n FROM anomaly_event WHERE level='WATCH' AND detected_at >= {d} AND detected_at < current_date")
    dq = q1(f"SELECT score FROM dq_result WHERE check_date = {d} AND check_name = 'overall'")
    sev4 = q("SELECT incident_id, title FROM incident WHERE severity='SEV4' AND status <> 'resolved' ORDER BY incident_id LIMIT 20")
    lines = ["*📊 ChargeOps 일일 리포트 (어제)*",
             *[f"• {r['operator_id']}: 가동률 {r['uptime_pct']}% · MTTR {r['mttr_min'] or '-'}분 · 고장 {r['fault_cnt']} · 장기방치 {r['long_fault_cnt']}" for r in up],
             "• 장애: " + (", ".join(f"{r['severity']} {r['n']}건" for r in inc) or "없음"),
             f"• 이상 징후 WATCH: {watch['n']}건",
             f"• 데이터 품질 점수: {dq['score'] if dq else '미집계'}",
             "*SEV4 (장기 방치 등)*", *[f"• #{r['incident_id']} {r['title']}" for r in sev4]]
    text = "\n".join(lines)
    send_slack(None, "일일 리포트", blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": text[:2900]}}])
    send_mail(None, "[ChargeOps] 일일 리포트", text.replace("*", ""))
    return {"ok": True}


@app.get("/healthz")
def healthz():
    q("SELECT 1")
    return {"ok": True, "slack_fail": state["slack_fail"], "watchdog_age": int(time.time() - state["last_watchdog"])}
