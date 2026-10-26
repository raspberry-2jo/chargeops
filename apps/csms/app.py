"""ChargeOps CSMS (관제 서버) — 스키마 v1.1
- OCPP 1.6J WebSocket 서버(:OCPP_PORT, 기본 9000 / 시뮬레이션 30900): 충전기 연결 · 상태/계측 수신 · 리셋 명령 송신
  · DB 에 등록된 모형 · 가상 충전기만 받는다 (16_seed_lab.sql). edge 가 넘긴 인증서 CN 과 URL ID 를 한 번 더 비교
  · OCPP 상태 이름 → code_status.code (10_arch_codes.sql 의 ocpp_status) 로 바꿔 공공 데이터와 같은 어휘로 저장
- 복구 REST API(:8000, FastAPI)     : Soft/Hard Reset, 사용 중지, 감시 강화 (안전장치 포함)
- Prometheus 지표(:9100)            : 감지 규칙(C-01~C-04)의 입력
- 여러 파드(2026-10-15, 최종 아키텍처 — CSMS 3파드):
  · 충전기는 한 파드에만 WebSocket 으로 붙는다. 복구 명령이 다른 파드로 오면 headless 서비스(PEERS_DNS)로
    붙어 있는 파드를 찾아 1회 전달한다 (X-CSMS-Forwarded 헤더 → 받은 파드는 다시 넘기지 않음 = 순환 없음)
  · 끝날 때(SIGTERM) 연결을 DRAIN_SECONDS 에 걸쳐 나눠 닫는다 → 배포 · 파드 종료 때 재접속 폭주 완화
  · PEERS_DNS 가 비어 있으면 예전과 똑같이 단일 파드로 동작
"""
import asyncio, logging, os, socket, time, uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import requests
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
from ocpp.routing import on
from ocpp.v16 import ChargePoint as OcppChargePoint, call, call_result
from prometheus_client import Counter, Gauge, Histogram, start_http_server
from psycopg_pool import AsyncConnectionPool
from websockets.asyncio.server import serve
from csms.http_metrics import HTTPMetricsMiddleware
from csms.ocpp_metrics import OcppMetrics
from csms.dev_simulation import simulation_ids

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("csms")
logging.getLogger("ocpp").setLevel(logging.WARNING)   # 메시지 원문 로그는 끔(필요 시 INFO)

# 열 수 있는 파일 수(soft) 를 hard 까지 올림 (2026-10-21)
#   WebSocket 연결 1개 = 파일 1개. 컨테이너 기본 soft 1,024 → 파드당 약 993대에서 새 접속 실패 (부하 시험 4,000대 중단 원인)
#   hard 까지는 권한 없이 올릴 수 있음 (실서버 hard 524,288)
try:
    import resource
    _soft, _hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if _hard == resource.RLIM_INFINITY or _hard > _soft:
        resource.setrlimit(resource.RLIMIT_NOFILE, (_hard, _hard))
    log.info("open files limit %s -> %s", _soft, resource.getrlimit(resource.RLIMIT_NOFILE)[0])
except (ImportError, ValueError, OSError) as e:
    log.warning("open files limit unchanged: %s", e)

DB_DSN = os.environ["DB_DSN"]                     # PgBouncer(쓰기 풀) 접속 문자열 — 주소 두 개 + target_session_attrs=read-write
API_TOKEN = os.environ.get("API_TOKEN", "")       # 봇 → CSMS 내부 호출 인증
REQUIRE_CN = os.environ.get("REQUIRE_CN", "true") == "true"
DEV_SIMULATION_IDS = simulation_ids(os.environ)


def is_simulated(cid):
    return cid in DEV_SIMULATION_IDS
HEARTBEAT = int(os.environ.get("HEARTBEAT_INTERVAL", "30"))
OCPP_PORT = int(os.environ.get("OCPP_PORT", "9000"))           # K8s: Service 가 30900 → 9000, 시뮬레이션: 30900 직접
RESET_LIMIT_PER_HOUR = 3
RESET_TIMEOUT = {"Soft": 90, "Hard": 120}         # 성공 판정: 이 시간 안에 이용 가능 상태로 복귀
# 이용 가능 상태 = Available 또는 (충전 중이던 충전기가 리셋 뒤 다시 충전) Preparing · Charging
#   10/13 까지는 Available 만 성공으로 봐서, 실제로 복구된 충전 중 충전기가 'failed' 로 남았다 (2026-10-14 수정)
RECOVERED = ("Available", "Preparing", "Charging")
POD_IP = os.environ.get("POD_IP", "")             # 내 파드 IP (downward API) — 전달 대상에서 나를 뺀다
POD = os.environ.get("HOSTNAME", "csms")          # 내 파드 이름 — charger_session.pod (18_csms_session.sql, 2026-10-20)
PEERS_DNS = os.environ.get("PEERS_DNS", "")       # headless 서비스 이름 (csms-pods.<ns>.svc.cluster.local). 비우면 단일 파드
DRAIN_SECONDS = float(os.environ.get("DRAIN_SECONDS", "20"))   # 종료 때 연결을 나눠 닫는 시간 (terminationGracePeriod 보다 짧게)

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
ocpp_metrics = OcppMetrics()
# 3파드 시험용 (최종 발표안 7장 1번): 처리 지연 p95 · 접속/끊김 · 파드별 연결 수 · 파드 간 전달
h_ocpp = Histogram("chargeops_ocpp_handle_seconds", "OCPP 메시지 처리 시간 (받기 → 응답, DB 쓰기 포함)", ["action"],
                   buckets=(.005, .01, .025, .05, .1, .25, .5, 1, 2.5, 5, 10))
c_conn = Counter("chargeops_ocpp_connections_total", "WebSocket 접속 결과", ["event"])   # accepted · rejected · closed
for event in ("accepted", "rejected", "closed"):
    c_conn.labels(event).inc(0)
g_pod = Gauge("chargeops_csms_connections", "이 파드에 붙어 있는 충전기 수")
c_fwd = Counter("chargeops_csms_forward_total", "다른 파드로 넘긴 복구 명령", ["result"])  # ok · error · not_found

pool: AsyncConnectionPool | None = None
connected: dict[str, "ChargePoint"] = {}          # 현재 연결된 충전기
state: dict[str, dict] = {}                       # 충전기별 최신 상태(메모리, OCPP 상태 이름)
OCPP_CODE: dict[str, str] = {}                    # OCPP 상태 → code_status.code (시작할 때 DB 에서 읽음)
g_pod.set_function(lambda: len(connected))


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
    @ocpp_metrics.observe("BootNotification")
    async def on_boot(self, charge_point_vendor, charge_point_model, **kw):
        c_msg.labels("BootNotification").inc()
        log.info("boot %s (%s %s)", self.id, charge_point_vendor, charge_point_model)
        return call_result.BootNotification(current_time=now().isoformat(),
                                            interval=HEARTBEAT, status="Accepted")

    @on("Heartbeat")
    @ocpp_metrics.observe("Heartbeat")
    async def on_heartbeat(self, **kw):
        c_msg.labels("Heartbeat").inc()
        with h_ocpp.labels("Heartbeat").time():
            g_hb.labels(**self.labels()).set(time.time())
            if not is_simulated(self.id):
                await db("UPDATE charger_status_current SET last_heartbeat_at = now() WHERE charger_id = %s", (self.id,))
        return call_result.Heartbeat(current_time=now().isoformat())

    @on("StatusNotification")
    @ocpp_metrics.observe("StatusNotification")
    async def on_status(self, connector_id, error_code, status, timestamp=None, **kw):
        c_msg.labels("StatusNotification").inc()
        with h_ocpp.labels("StatusNotification").time():
            await record_status(self.id, status, error_code, timestamp)
        return call_result.StatusNotification()

    @on("MeterValues")
    @ocpp_metrics.observe("MeterValues")
    async def on_meter(self, connector_id, meter_value, **kw):
        c_msg.labels("MeterValues").inc()
        with h_ocpp.labels("MeterValues").time():
            await self.save_meter(meter_value)
        return call_result.MeterValues()

    async def save_meter(self, meter_value):
        for mv in meter_value:
            vals = {s.get("measurand", "Energy.Active.Import.Register"): float(s["value"])
                    for s in mv["sampled_value"]}
            cur, volt, temp = vals.get("Current.Import"), vals.get("Voltage"), vals.get("Temperature")
            energy = vals.get("Energy.Active.Import.Register")
            if cur is not None:
                g_cur.labels(**self.labels()).set(cur)
            if temp is not None:
                g_temp.labels(**self.labels()).set(temp)
            if not is_simulated(self.id):
                await db("""INSERT INTO meter_sample (charger_id, ts, current_a, voltage_v, temp_c, energy_wh)
                            VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING""",
                         (self.id, mv["timestamp"], cur, volt, temp, None if energy is None else int(energy)))


async def record_status(cid: str, status: str, error_code: str | None, ts: str | None):
    """OCPP 상태 → 코드. 바뀌었는지는 DB 현재 상태와 비교 (fn_record_status, 18_csms_session.sql)
    파드 메모리로 비교하면 다른 파드로 재접속했을 때 옛 상태가 남아 기록이 빠지거나 두 번 쌓인다 (2026-10-20)"""
    st = state[cid]
    lab = {"charger_id": cid, "station_id": st["station_id"]}
    err = None if error_code in (None, "NoError") else error_code
    g_faulted.labels(**lab).set(1 if status == "Faulted" else 0)
    g_charging.labels(**lab).set(1 if status == "Charging" else 0)
    st["status"], st["status_at"], st["err"] = status, time.time(), err   # 메모리는 지표 · 조회용으로만
    code = OCPP_CODE.get(status, "9")
    changed = st.get("code") != code if is_simulated(cid) else (await db(
        "SELECT fn_record_status(%s, %s, %s, %s::timestamptz, %s)",
        (cid, code, err, ts or now().isoformat(), st["source"]), "one"))[0]
    if changed:
        st["code"] = code
        if status == "Faulted":
            c_fault.labels(**lab).inc()
        log.info("status %s -> %s [%s] (%s)", cid, status, code, error_code)


async def load_charger(cid: str) -> dict | None:
    """등록된 모형 · 가상 충전기만 (공공 충전기 ID · 모르는 ID 는 None → 접속 거부)"""
    if is_simulated(cid):
        return {"station_id": "CI-DEV-SIMULATION", "rated": 32, "source": "virtual", "code": None}
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
        c_conn.labels("rejected").inc()
        return await ws.close(1008, "bad path")
    if REQUIRE_CN and cn != cid:
        log.warning("reject %s: cert CN=%r", cid, cn)          # 남의 ID 를 사칭하는 접속 차단 (edge 에서 1차, 여기서 2차)
        c_conn.labels("rejected").inc()
        return await ws.close(1008, "certificate CN mismatch")
    info = await load_charger(cid)
    if info is None:
        log.warning("reject %s: 등록되지 않은 충전기", cid)
        c_conn.labels("rejected").inc()
        return await ws.close(1008, "unknown charger")
    c_conn.labels("accepted").inc()
    state[cid] = {**state.get(cid, {}), **info}
    cp = ChargePoint(cid, ws, response_timeout=10)          # 충전기 무응답 시 10초 후 실패 처리
    session = str(uuid.uuid4())
    if not is_simulated(cid):
        await db("SELECT fn_session_open(%s, %s::uuid, %s, %s)", (cid, session, POD, POD_IP))   # 최신 세션 = 나
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
        c_conn.labels("closed").inc()
        if connected.get(cid) is cp:
            connected.pop(cid, None)
            # 0 으로 남겨 둔다 (지우지 않음): 감지 규칙이 충전기별 max by 라 다른 파드에 다시 붙으면 1 이 이기고,
            # 어디에도 안 붙으면 0 이 남아 C-02 가 울린다. 지우면 진짜 두절을 놓친다 (WBS 3.7, 2026-10-15)
            g_connected.labels(**lab).set(0)
        g_faulted.labels(**lab).set(0)         # 이 파드는 더 이상 이 충전기를 갖지 않음 → max by 에 옛 1 이 남지 않게
        g_charging.labels(**lab).set(0)
        # C-02 통신 두절: DB 의 최신 세션이 나일 때만 기록. 다른 파드에 새 세션이 있으면 건너뜀 (가짜 통신이상 방지)
        try:
            closed = connected.get(cid) is None if is_simulated(cid) else (await db(
                "SELECT fn_session_close(%s, %s::uuid, %s)", (cid, session, state[cid]["source"]), "one"))[0]
        except Exception as e:
            closed = False
            log.warning("session close %s 실패: %s", cid, e)
        if closed:
            state[cid]["status"], state[cid]["status_at"] = "Offline", time.time()
            log.info("status %s -> Offline [1] (ConnectionLost)", cid)
        else:
            log.info("disconnect %s: 새 세션이 있어 통신이상 기록 생략", cid)
        if cid.startswith("LT-"):              # 부하 시험 종료 시 1,000대 C-02 경보 폭주 방지: 지표 제거
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


# ───────── 여러 파드: 충전기가 붙어 있는 파드로 1회 전달 ─────────
FWD = "X-CSMS-Forwarded"                          # 전달받은 요청 표시 — 받은 파드는 다시 넘기지 않는다


async def peers() -> list[str]:
    """headless 서비스가 돌려주는 준비된 CSMS 파드 IP 중 나를 뺀 것"""
    if not PEERS_DNS:
        return []
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(PEERS_DNS, 8000, proto=socket.IPPROTO_TCP)
    except OSError:
        return []
    return sorted({i[4][0] for i in infos} - {POD_IP})


def _http(method: str, url: str, body: dict | None = None, token: str | None = None) -> tuple[int, dict]:
    r = requests.request(method, url, json=body, timeout=15,
                         headers={"X-Api-Token": token or "", FWD: POD_IP or "peer"})
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, {"detail": r.text[:200]}


async def owner_of(cid: str) -> tuple[str, dict] | None:
    """다른 파드에 물어 그 충전기가 붙어 있는 파드 (IP, 상태). 없으면 None"""
    ips = await peers()
    if not ips:
        return None
    res = await asyncio.gather(*(asyncio.to_thread(_http, "GET", f"http://{ip}:8000/api/chargers/{cid}") for ip in ips),
                               return_exceptions=True)
    for ip, r in zip(ips, res):
        if not isinstance(r, BaseException) and r[0] == 200 and r[1].get("connected"):
            return ip, r[1]
    return None


async def forward(cid: str, path: str, req: BaseModel, token: str | None, forwarded: str | None) -> dict | None:
    """이 파드에 없는 충전기면 붙어 있는 파드로 넘기고 그 응답을 돌려준다. 넘길 필요가 없으면 None (= 여기서 처리).
    안전장치(멱등 키 · 충전 중 차단 · 1시간 3회)와 감사 기록은 받은 파드가 그대로 한다 → 중복 실행 없음"""
    if forwarded:
        return None
    # 최신 세션을 가진 파드는 DB(charger_session)로 판단 — 옛 세션이 메모리에 남아 있는 파드로 보내지 않게 (2026-10-20)
    row = await db("SELECT pod, pod_ip FROM charger_session WHERE charger_id = %s", (cid,), "one")
    if row and row[0] == POD:
        return None                                   # 내가 최신 세션 → 여기서 처리
    if row and row[1] and row[1] != POD_IP:
        ip = row[1]
    else:                                             # 세션 기록이 없으면 예전 방식(다른 파드에 물어보기)
        if cid in connected:
            return None
        found = await owner_of(cid)
        if found is None:
            c_fwd.labels("not_found").inc()
            return None                               # 아무 데도 없음 → 여기서 guard 가 409 "charger not connected"
        ip = found[0]
    code, data = await asyncio.to_thread(_http, "POST", f"http://{ip}:8000{path}", req.model_dump(), token)
    c_fwd.labels("ok" if code < 400 else "error").inc()
    log.info("forward %s %s -> %s (%d)", path, cid, ip, code)
    if code >= 400:
        raise HTTPException(code, data.get("detail", data))
    return {**data, "via_pod": ip}


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
    row = await db("SELECT status_code FROM charger_status_current WHERE charger_id = %s", (cid,), "one")
    if row and row[0] == "3":                                 # 충전 중(코드 3) — 파드 메모리 대신 DB
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
    # DB 로 판정 (다른 파드로 재접속해도 됨): 리셋 뒤 새 세션이 열렸고, 열린 지 5초 지나(Boot · Status 수신) 상태가 이용 가능
    #   상태 시각(changed_at)은 안 봄 — 옛 세션 끊김이 늦게 감지되면 1 이 안 남아 2 → 2 로 '안 바뀐' 것처럼 보이기 때문
    codes = sorted({OCPP_CODE[s] for s in RECOVERED if s in OCPP_CODE})
    sql = """SELECT cur.status_code = ANY(%s) FROM charger_session s JOIN charger_status_current cur USING (charger_id)
             WHERE s.charger_id = %s AND s.connected_at > to_timestamp(%s) AND s.connected_at < now() - interval '5 seconds'"""
    ok = False
    while time.time() < deadline:
        row = await db(sql, (codes, cid, started), "one")
        if row and row[0]:
            ok = True
            break
        await asyncio.sleep(2)
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
    log.info("OCPP :%d  metrics :%s  상태 코드 %d개  파드 %s  peers=%s", OCPP_PORT, os.environ.get("METRICS_PORT", "9100"),
             len(OCPP_CODE), POD_IP or "-", PEERS_DNS or "(단일 파드)")
    yield
    # 끝날 때: 새 접속은 그만 받고, 붙어 있던 충전기는 DRAIN_SECONDS 에 걸쳐 나눠 닫는다 (1012 = 서버 재시작)
    #   한꺼번에 닫으면 수천 대가 같은 순간 다시 붙어 남은 파드 · edge 가 점검 시간 초과 (10/15 1,400대 실측)
    ws_server.close(close_connections=False)
    cps = list(connected.values())
    if cps:
        gap = DRAIN_SECONDS / len(cps)
        log.info("drain: %d대를 %.0f초에 걸쳐 닫음", len(cps), DRAIN_SECONDS)
        for cp in cps:
            try:
                await cp._connection.close(1012, "csms restarting")
            except Exception:
                pass
            await asyncio.sleep(gap)
    try:
        await asyncio.wait_for(ws_server.wait_closed(), timeout=5)
    except asyncio.TimeoutError:
        pass
    await pool.close()


app = FastAPI(title="ChargeOps CSMS", lifespan=lifespan)
app.add_middleware(HTTPMetricsMiddleware)


@app.get("/healthz")
async def healthz():
    await db("SELECT 1")
    return {"ok": True, "connected": len(connected), "pod": POD_IP or None}


@app.get("/livez")
async def livez():
    """생존 점검용 — DB 를 거치지 않는다 (이벤트 루프가 살아 있으면 응답)"""
    return {"ok": True}


@app.get("/api/connected")
async def connected_total(x_csms_forwarded: str | None = Header(None)):
    """모든 파드의 연결 수 합계 (운영 스크립트용 — 파드 하나만 보던 healthz 대신)"""
    mine = {"pod": POD_IP or "single", "connected": len(connected)}
    if x_csms_forwarded:
        return mine
    ips = await peers()
    res = await asyncio.gather(*(asyncio.to_thread(_http, "GET", f"http://{ip}:8000/api/connected") for ip in ips),
                               return_exceptions=True)
    pods = [mine] + [r[1] for r in res if not isinstance(r, BaseException) and r[0] == 200]
    return {"total": sum(p["connected"] for p in pods), "pods": pods}


@app.get("/api/chargers")
async def list_chargers(x_csms_forwarded: str | None = Header(None)):
    mine = {k: {"charger_id": k, "connected": k in connected, **{x: v.get(x) for x in ("station_id", "status")}}
            for k, v in state.items()}
    if not x_csms_forwarded:                         # 다른 파드 것도 합친다 — 붙어 있는 쪽이 이김
        res = await asyncio.gather(*(asyncio.to_thread(_http, "GET", f"http://{ip}:8000/api/chargers") for ip in await peers()),
                                   return_exceptions=True)
        for r in res:
            if not isinstance(r, BaseException) and r[0] == 200:
                for c in r[1]:
                    if c["connected"] or c["charger_id"] not in mine:
                        mine[c["charger_id"]] = c
    return [mine[k] for k in sorted(mine)]


@app.get("/api/chargers/{cid}")
async def get_charger(cid: str, x_csms_forwarded: str | None = Header(None)):
    if cid not in connected and not x_csms_forwarded:
        found = await owner_of(cid)                  # 다른 파드에 붙어 있으면 그 파드의 상태
        if found:
            return found[1]
    if cid not in state:
        raise HTTPException(404, "unknown charger")
    return {"charger_id": cid, "connected": cid in connected, **state[cid]}


@app.post("/api/chargers/{cid}/reset", status_code=202)
async def reset(cid: str, req: ResetReq, x_api_token: str | None = Header(None),
                x_csms_forwarded: str | None = Header(None)):
    check_token(x_api_token)
    if req.type not in ("Soft", "Hard"):
        raise HTTPException(400, "type must be Soft|Hard")
    if (fw := await forward(cid, f"/api/chargers/{cid}/reset", req, x_api_token, x_csms_forwarded)) is not None:
        return fw
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
async def availability(cid: str, req: AvailReq, x_api_token: str | None = Header(None),
                       x_csms_forwarded: str | None = Header(None)):
    check_token(x_api_token)
    if (fw := await forward(cid, f"/api/chargers/{cid}/availability", req, x_api_token, x_csms_forwarded)) is not None:
        return fw
    cp = await guard(cid, req, "disable" if req.type == "Inoperative" else "enable", limit=False)
    res = await cp.call(call.ChangeAvailability(connector_id=0, type=req.type))
    return {"status": res.status}


@app.post("/api/chargers/{cid}/watch")
async def watch(cid: str, req: WatchReq, x_api_token: str | None = Header(None),
                x_csms_forwarded: str | None = Header(None)):
    """감시 강화: 계측 주기를 interval 초로 줄였다가 seconds 후 원래(10초)로 복귀"""
    check_token(x_api_token)
    if (fw := await forward(cid, f"/api/chargers/{cid}/watch", req, x_api_token, x_csms_forwarded)) is not None:
        return fw
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
