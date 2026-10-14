"""ChargeOps CSMS (관제 서버) — 스키마 v1.1
- OCPP 1.6J WebSocket 서버(:OCPP_PORT, 기본 9000 / 시뮬레이션 30900): 충전기 연결 · 상태/계측 수신 · 리셋 명령 송신
  · DB 에 등록된 모형 · 가상 충전기만 받는다 (16_seed_lab.sql). edge 가 넘긴 인증서 CN 과 URL ID 를 한 번 더 비교
  · OCPP 상태 이름 → code_status.code (10_arch_codes.sql 의 ocpp_status) 로 바꿔 공공 데이터와 같은 어휘로 저장
- 복구 REST API(:8000, FastAPI)     : Soft/Hard Reset, 사용 중지, 감시 강화 (안전장치 포함)
- Prometheus 지표(:9100)            : 감지 규칙(C-01~C-04)의 입력
"""
import asyncio, logging, os, time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
from ocpp.routing import on
from ocpp.v16 import ChargePoint as OcppChargePoint, call, call_result
from prometheus_client import Counter, Gauge, start_http_server
from psycopg_pool import AsyncConnectionPool
from websockets.asyncio.server import serve

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("csms")
logging.getLogger("ocpp").setLevel(logging.WARNING)   # 메시지 원문 로그는 끔(필요 시 INFO)

DB_DSN = os.environ["DB_DSN"]                     # PgBouncer(쓰기 풀) 접속 문자열 — 주소 두 개 + target_session_attrs=read-write
API_TOKEN = os.environ.get("API_TOKEN", "")       # 봇 → CSMS 내부 호출 인증
REQUIRE_CN = os.environ.get("REQUIRE_CN", "true") == "true"
HEARTBEAT = int(os.environ.get("HEARTBEAT_INTERVAL", "30"))
OCPP_PORT = int(os.environ.get("OCPP_PORT", "9000"))           # K8s: Service 가 30900 → 9000, 시뮬레이션: 30900 직접
RESET_LIMIT_PER_HOUR = 3
RESET_TIMEOUT = {"Soft": 90, "Hard": 120}         # 성공 판정: 이 시간 안에 Available 복귀

# ───────── Prometheus 지표 ─────────
L = ["charger_id", "station_id"]
g_connected = Gauge("chargeops_charger_connected", "1=WebSocket 연결됨", L)
g_faulted = Gauge("chargeops_charger_faulted", "1=Faulted 상태", L)
g_charging = Gauge("chargeops_charger_charging", "1=충전 중", L)
g_hb = Gauge("chargeops_charger_last_heartbeat_timestamp_seconds", "마지막 Heartbeat 시각", L)
g_temp = Gauge("chargeops_charger_temp_celsius", "온도", L)
g_cur = Gauge("chargeops_charger_current_amperes", "전류", L)
g_rated = Gauge("chargeops_charger_rated_current_amperes", "정격 전류", L)
c_fault = Counter("chargeops_charger_fault_total", "Faulted 진입 횟수", L)
c_reset = Counter("chargeops_reset_total", "리셋 요청 결과", ["type", "result"])
c_msg = Counter("chargeops_ocpp_messages_total", "수신 OCPP 메시지", ["action"])

pool: AsyncConnectionPool | None = None
connected: dict[str, "ChargePoint"] = {}          # 현재 연결된 충전기
state: dict[str, dict] = {}                       # 충전기별 최신 상태(메모리, OCPP 상태 이름)
OCPP_CODE: dict[str, str] = {}                    # OCPP 상태 → code_status.code (시작할 때 DB 에서 읽음)


def now() -> datetime:
    return datetime.now(timezone.utc)


async def db(sql: str, args: tuple = (), fetch: str | None = None):
    """짧은 트랜잭션 1회 실행. fetch='one'|'all'"""
    async with pool.connection() as conn:
        cur = await conn.execute(sql, args)
        if fetch == "one":
            return await cur.fetchone()
        if fetch == "all":
            return await cur.fetchall()


class ChargePoint(OcppChargePoint):
    """충전기 1대와의 OCPP 세션. @on 데코레이터 = 충전기가 보낸 메시지 처리기"""

    def labels(self):
        return {"charger_id": self.id, "station_id": state[self.id]["station_id"]}

    @on("BootNotification")
    async def on_boot(self, charge_point_vendor, charge_point_model, **kw):
        c_msg.labels("BootNotification").inc()
        log.info("boot %s (%s %s)", self.id, charge_point_vendor, charge_point_model)
        return call_result.BootNotification(current_time=now().isoformat(),
                                            interval=HEARTBEAT, status="Accepted")

    @on("Heartbeat")
    async def on_heartbeat(self, **kw):
        c_msg.labels("Heartbeat").inc()
        g_hb.labels(**self.labels()).set(time.time())
        await db("UPDATE charger_status_current SET last_heartbeat_at = now() WHERE charger_id = %s", (self.id,))
        return call_result.Heartbeat(current_time=now().isoformat())

    @on("StatusNotification")
    async def on_status(self, connector_id, error_code, status, timestamp=None, **kw):
        c_msg.labels("StatusNotification").inc()
        await record_status(self.id, status, error_code, timestamp)
        return call_result.StatusNotification()

    @on("MeterValues")
    async def on_meter(self, connector_id, meter_value, **kw):
        c_msg.labels("MeterValues").inc()
        for mv in meter_value:
            vals = {s.get("measurand", "Energy.Active.Import.Register"): float(s["value"])
                    for s in mv["sampled_value"]}
            cur, volt, temp = vals.get("Current.Import"), vals.get("Voltage"), vals.get("Temperature")
            energy = vals.get("Energy.Active.Import.Register")
            if cur is not None:
                g_cur.labels(**self.labels()).set(cur)
            if temp is not None:
                g_temp.labels(**self.labels()).set(temp)
            await db("""INSERT INTO meter_sample (charger_id, ts, current_a, voltage_v, temp_c, energy_wh)
                        VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING""",
                     (self.id, mv["timestamp"], cur, volt, temp, None if energy is None else int(energy)))
        return call_result.MeterValues()


async def record_status(cid: str, status: str, error_code: str | None, ts: str | None):
    """OCPP 상태 → 코드. 코드가 '바뀔 때만' 이력 1행 (Preparing → Charging → Finishing 은 모두 3 이라 한 번)"""
    st = state[cid]
    lab = {"charger_id": cid, "station_id": st["station_id"]}
    if st.get("status") == status:
        return
    if status == "Faulted":
        c_fault.labels(**lab).inc()
    g_faulted.labels(**lab).set(1 if status == "Faulted" else 0)
    g_charging.labels(**lab).set(1 if status == "Charging" else 0)
    st["status"], st["status_at"] = status, time.time()
    code = OCPP_CODE.get(status, "9")
    err = None if error_code in (None, "NoError") else error_code
    log.info("status %s -> %s [%s] (%s)", cid, status, code, error_code)
    if st.get("code") == code and st.get("err") == err:
        return
    st["code"], st["err"] = code, err
    changed = ts or now().isoformat()
    args = {"cid": cid, "code": code, "ts": changed, "src": st["source"], "err": err}
    await db("""INSERT INTO status_history (changed_at, charger_id, source, status_code, error_code)
                VALUES (%(ts)s, %(cid)s, %(src)s, %(code)s, %(err)s)
                ON CONFLICT (charger_id, changed_at, status_code) DO NOTHING""", args)
    # 현재 상태 + 충전 시각 (공공 API 의 nowTsdt · lastTsdt · lastTedt 와 같은 뜻으로 채움)
    await db("""INSERT INTO charger_status_current AS cur
                       (charger_id, status_code, changed_at, source, error_code, charging_start_at, last_heartbeat_at)
                VALUES (%(cid)s, %(code)s, %(ts)s, %(src)s, %(err)s,
                        CASE WHEN %(code)s = '3' THEN %(ts)s::timestamptz END, now())
                ON CONFLICT (charger_id) DO UPDATE SET
                  status_code = EXCLUDED.status_code, changed_at = EXCLUDED.changed_at, source = EXCLUDED.source,
                  error_code = EXCLUDED.error_code, received_at = now(),
                  charging_start_at = CASE WHEN EXCLUDED.status_code = '3'
                                           THEN coalesce(cur.charging_start_at, EXCLUDED.changed_at) END,
                  last_charge_start_at = CASE WHEN EXCLUDED.status_code <> '3' AND cur.charging_start_at IS NOT NULL
                                              THEN cur.charging_start_at ELSE cur.last_charge_start_at END,
                  last_charge_end_at = CASE WHEN EXCLUDED.status_code <> '3' AND cur.status_code = '3'
                                            THEN EXCLUDED.changed_at ELSE cur.last_charge_end_at END
                WHERE EXCLUDED.changed_at >= cur.changed_at""", args)


async def load_charger(cid: str) -> dict | None:
    """등록된 모형 · 가상 충전기만 (공공 충전기 ID · 모르는 ID 는 None → 접속 거부)"""
    row = await db("""SELECT c.station_id, coalesce(c.rated_current_a, 32), c.source, cur.status_code
                      FROM charger c LEFT JOIN charger_status_current cur USING (charger_id)
                      WHERE c.charger_id = %s AND c.source IN ('model', 'virtual') AND NOT c.is_deleted""",
                   (cid,), "one")
    if row is None:
        return None
    return {"station_id": row[0], "rated": row[1], "source": row[2], "code": row[3]}


async def on_connect(ws):
    """WebSocket 접속 처리: 경로 /ocpp/<충전기ID>, 인증서 CN 확인 후 세션 시작"""
    path = ws.request.path.rstrip("/")
    cid = path.split("/")[-1]
    cn = ws.request.headers.get("X-Client-CN", "")
    if not path.startswith("/ocpp/") or not cid:
        return await ws.close(1008, "bad path")
    if REQUIRE_CN and cn != cid:
        log.warning("reject %s: cert CN=%r", cid, cn)          # 남의 ID 를 사칭하는 접속 차단 (edge 에서 1차, 여기서 2차)
        return await ws.close(1008, "certificate CN mismatch")
    info = await load_charger(cid)
    if info is None:
        log.warning("reject %s: 등록되지 않은 충전기", cid)
        return await ws.close(1008, "unknown charger")
    state[cid] = {**state.get(cid, {}), **info}
    cp = ChargePoint(cid, ws, response_timeout=10)          # 충전기 무응답 시 10초 후 실패 처리
    connected[cid] = cp
    lab = cp.labels()
    g_connected.labels(**lab).set(1)
    g_rated.labels(**lab).set(state[cid]["rated"])
    g_hb.labels(**lab).set(time.time())
    try:
        await cp.start()                                      # 연결이 끊길 때까지 메시지 수신 루프
    except Exception as e:
        log.info("disconnect %s: %s", cid, type(e).__name__)
    finally:
        if connected.get(cid) is cp:
            connected.pop(cid, None)
            g_connected.labels(**lab).set(0)
            await record_status(cid, "Offline", "ConnectionLost", None)   # C-02 통신 두절
            if cid.startswith("LT-"):          # 부하 시험 종료 시 1,000대 C-02 경보 폭주 방지: 지표 제거
                for g in (g_connected, g_faulted, g_charging, g_hb, g_temp, g_cur, g_rated, c_fault):
                    try:
                        g.remove(*lab.values())
                    except KeyError:
                        pass


# ───────── 복구 REST API ─────────
class ActionReq(BaseModel):
    idempotency_key: str            # 같은 요청 재전송 시 중복 실행 방지
    requested_by: str
    approved_by: str | None = None
    role: str | None = None
    incident_id: int | None = None
    reason: str | None = None


class ResetReq(ActionReq):
    type: str = "Soft"              # Soft | Hard


class AvailReq(ActionReq):
    type: str = "Inoperative"       # Inoperative(사용 중지) | Operative(재개)


class WatchReq(ActionReq):
    interval: int = 5
    seconds: int = 3600


def check_token(token: str | None):
    if API_TOKEN and token != API_TOKEN:
        raise HTTPException(401, "invalid token")


async def audit(req: ActionReq, cid: str, step: str, result: str, key_suffix: str = "") -> bool:
    """감사 기록 INSERT. 같은 idempotency_key 가 이미 있으면 False(=중복 요청)"""
    row = await db("""INSERT INTO action_audit (incident_id, charger_id, idempotency_key, step,
                        requested_by, approved_by, role, result, reason, executed_at)
                      VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s, now())
                      ON CONFLICT (idempotency_key) DO NOTHING RETURNING action_id""",
                   (req.incident_id, cid, req.idempotency_key + key_suffix, step, req.requested_by,
                    req.approved_by, req.role, result, req.reason), "one")
    return row is not None


async def guard(cid: str, req: ActionReq, step: str, limit: bool = True) -> ChargePoint:
    """실행 전 안전장치: 멱등 키 · 연결 여부 · 충전 중 차단 · 1시간 3회 제한"""
    if await db("SELECT 1 FROM action_audit WHERE idempotency_key = %s", (req.idempotency_key,), "one"):
        raise HTTPException(409, "duplicate request (idempotency_key already used)")
    cp = connected.get(cid)
    if cp is None:
        raise HTTPException(409, "charger not connected")
    if state[cid].get("status") == "Charging":
        await audit(req, cid, step, "blocked", ":blocked")
        raise HTTPException(409, "charging in progress - action deferred")
    if limit:
        n = (await db("""SELECT count(*) FROM action_audit WHERE charger_id = %s
                           AND step IN ('soft_reset','hard_reset') AND result = 'executed'
                           AND created_at > now() - interval '1 hour'""", (cid,), "one"))[0]
        if n >= RESET_LIMIT_PER_HOUR:
            await audit(req, cid, step, "rate_limited", ":limited")
            raise HTTPException(429, f"reset limit {RESET_LIMIT_PER_HOUR}/hour exceeded")
    if not await audit(req, cid, step, "executed"):
        raise HTTPException(409, "duplicate request (idempotency_key already used)")
    return cp


async def verify_reset(cid: str, req: ResetReq, started: float):
    """리셋 후 제한 시간 안에 Available 로 돌아오는지 확인해 결과를 추가 기록"""
    deadline = started + RESET_TIMEOUT[req.type]
    await asyncio.sleep(3)
    while time.time() < deadline:
        st = state.get(cid, {})
        if cid in connected and st.get("status") == "Available" and st.get("status_at", 0) > started:
            break
        await asyncio.sleep(2)
    ok = cid in connected and state[cid].get("status") == "Available"
    result = "succeeded" if ok else "failed"
    c_reset.labels(req.type, result).inc()
    await audit(req, cid, f"{req.type.lower()}_reset", result, ":result")
    log.info("reset %s %s -> %s (%.0fs)", cid, req.type, result, time.time() - started)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global pool
    # prepare_threshold=None: PgBouncer 트랜잭션 풀링에서 서버측 prepared statement 충돌 방지
    # check: 빌려주기 전에 연결이 살아 있는지 확인 → 장애 전환 뒤 죽은 연결(옛 Primary 쪽)을 버리고 새로 붙는다
    #   (실습 실측: check 없이 DSN 만 바꾸면 끊긴 연결을 받은 요청이 실패, 있으면 실패 0 — 가이드 9.4)
    pool = AsyncConnectionPool(DB_DSN, min_size=2, max_size=int(os.environ.get("DB_POOL", "10")),
                               kwargs={"autocommit": True, "prepare_threshold": None}, open=False,
                               check=AsyncConnectionPool.check_connection)
    await pool.open()
    rows = await db("SELECT code, ocpp_status FROM code_status WHERE ocpp_status IS NOT NULL", fetch="all")
    OCPP_CODE.update({o: code for code, arr in rows for o in arr})
    if not OCPP_CODE:
        raise RuntimeError("code_status.ocpp_status 가 비어 있음 — 10_arch_codes.sql 을 먼저 적용")
    start_http_server(int(os.environ.get("METRICS_PORT", "9100")))
    ws_server = await serve(on_connect, "0.0.0.0", OCPP_PORT, subprotocols=["ocpp1.6"],
                            ping_interval=60, ping_timeout=60)
    log.info("OCPP :%d  metrics :%s  상태 코드 %d개", OCPP_PORT, os.environ.get("METRICS_PORT", "9100"), len(OCPP_CODE))
    yield
    ws_server.close()
    await pool.close()


app = FastAPI(title="ChargeOps CSMS", lifespan=lifespan)


@app.get("/healthz")
async def healthz():
    await db("SELECT 1")
    return {"ok": True, "connected": len(connected)}


@app.get("/api/chargers")
async def list_chargers():
    return [{"charger_id": k, "connected": k in connected, **{x: v.get(x) for x in ("station_id", "status")}}
            for k, v in sorted(state.items())]


@app.get("/api/chargers/{cid}")
async def get_charger(cid: str):
    if cid not in state:
        raise HTTPException(404, "unknown charger")
    return {"charger_id": cid, "connected": cid in connected, **state[cid]}


@app.post("/api/chargers/{cid}/reset", status_code=202)
async def reset(cid: str, req: ResetReq, x_api_token: str | None = Header(None)):
    check_token(x_api_token)
    if req.type not in ("Soft", "Hard"):
        raise HTTPException(400, "type must be Soft|Hard")
    cp = await guard(cid, req, f"{req.type.lower()}_reset")
    started = time.time()
    try:
        res = await cp.call(call.Reset(type=req.type))        # OCPP Reset 명령 송신
    except asyncio.TimeoutError:
        await audit(req, cid, f"{req.type.lower()}_reset", "timeout", ":result")
        raise HTTPException(504, "charger did not respond to Reset")
    if res.status != "Accepted":
        c_reset.labels(req.type, "rejected").inc()
        await audit(req, cid, f"{req.type.lower()}_reset", "rejected", ":result")
        raise HTTPException(502, f"charger rejected reset: {res.status}")
    asyncio.create_task(verify_reset(cid, req, started))     # 결과 확인은 백그라운드
    return {"accepted": True, "charger_id": cid, "type": req.type, "timeout_sec": RESET_TIMEOUT[req.type]}


@app.post("/api/chargers/{cid}/availability")
async def availability(cid: str, req: AvailReq, x_api_token: str | None = Header(None)):
    check_token(x_api_token)
    cp = await guard(cid, req, "disable" if req.type == "Inoperative" else "enable", limit=False)
    res = await cp.call(call.ChangeAvailability(connector_id=0, type=req.type))
    return {"status": res.status}


@app.post("/api/chargers/{cid}/watch")
async def watch(cid: str, req: WatchReq, x_api_token: str | None = Header(None)):
    """감시 강화: 계측 주기를 interval 초로 줄였다가 seconds 후 원래(10초)로 복귀"""
    check_token(x_api_token)
    cp = connected.get(cid)
    if cp is None:
        raise HTTPException(409, "charger not connected")
    if not await audit(req, cid, "watch", "executed"):
        raise HTTPException(409, "duplicate request")
    await cp.call(call.ChangeConfiguration(key="MeterValueSampleInterval", value=str(req.interval)))
    await cp.call(call.TriggerMessage(requested_message="MeterValues", connector_id=1))

    async def revert():
        await asyncio.sleep(req.seconds)
        if cid in connected:
            await connected[cid].call(call.ChangeConfiguration(key="MeterValueSampleInterval", value="10"))
    asyncio.create_task(revert())
    return {"interval": req.interval, "until": time.time() + req.seconds}
