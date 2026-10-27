"""가상 충전기 (ESP32 모형 충전기 대체) — OCPP 1.6J 클라이언트 N대 + 장애 주입 제어 API

모형 3대  : python vcharger.py --ids CH-01,CH-02,CH-03 --ca certs/ca.crt --cert-dir certs/chargers
부하 시험 : python vcharger.py --count 1000 --prefix LT- --ca certs/ca.crt --cert-dir certs/chargers \
                                --meter-interval 60 --ramp 50 --control-port 8082
  · 충전기마다 자기 인증서(CN = 충전기 ID)로 접속 — pki/chargerpki.py 가 일괄 발급 (v1 의 공용 LOADGEN 인증서는 없앰)
  · 시뮬레이션 단계에서는 tools/sim/units/chargeops-vcharger-*.service 가 이 명령으로 띄운다
제어 API(:8081): curl -X POST loadgen:8081/CH-01/fault?kind=soft   (아래 control 섹션 참고)
"""
import argparse, asyncio, json, logging, random, ssl, time
from datetime import datetime, timezone

import uvicorn
from fastapi import FastAPI, HTTPException
from ocpp.routing import after, on
from ocpp.v16 import ChargePoint as OcppCP, call, call_result
from websockets.asyncio.client import connect

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("vcharger")
logging.getLogger("ocpp").setLevel(logging.WARNING)
iso = lambda: datetime.now(timezone.utc).isoformat()


class Sim:
    """충전기 1대의 '물리 상태' (OCPP 세션과 별개로 유지됨 → 재접속해도 고장은 남는다)"""

    def __init__(self, cid: str, meter_interval: int):
        self.id, self.meter_interval = cid, meter_interval
        self.fault: str | None = None      # soft(Soft Reset 로 해결) | hard(Hard 필요) | field(현장 점검 필요)
        self.available = True              # ChangeAvailability 결과
        self.charging = False
        self.temp, self.heat_rate = 35.0 + random.uniform(-1, 1), 0.0   # ℃, ℃/분
        self.overcurrent = False
        self.offline_until = 0.0           # 통신 두절 주입
        self.cp: "ClientCP | None" = None
        self.connected = False
        self.stats = {"attempts": 0, "connects": 0, "failures": 0}

    def status(self) -> str:
        if self.fault:
            return "Faulted"
        if not self.available:
            return "Unavailable"
        return "Charging" if self.charging else "Available"

    def sample(self, dt: float) -> dict:
        self.temp += self.heat_rate * dt / 60 if self.heat_rate else (35.0 - self.temp) * 0.02
        self.temp = min(self.temp, 95.0)
        rated = 32.0
        cur = rated * 1.3 if self.overcurrent else (16.0 + random.gauss(0, 0.4) if self.charging else 0.0)
        return {"Temperature": round(self.temp + random.gauss(0, 0.15), 2),
                "Current.Import": round(max(cur, 0), 2),
                "Voltage": round(220 + random.gauss(0, 1.5), 1)}

    async def send_status(self):
        if self.cp:
            await self.cp.call(call.StatusNotification(
                connector_id=1, status=self.status(), timestamp=iso(),
                error_code="OtherError" if self.fault else "NoError"))

    async def send_meter(self):
        if self.cp:
            vals = self.sample(self.meter_interval)
            await self.cp.call(call.MeterValues(connector_id=1, meter_value=[{
                "timestamp": iso(),
                "sampledValue": [{"value": str(v), "measurand": k} for k, v in vals.items()]}]))


class ClientCP(OcppCP):
    """CSMS → 충전기 명령 처리기"""
    sim: Sim

    @on("Reset")
    async def on_reset(self, type, **kw):
        return call_result.Reset(status="Accepted")

    @after("Reset")                         # 응답을 보낸 '뒤' 재부팅 동작
    async def do_reset(self, type, **kw):
        s = self.sim
        log.info("%s %s reset (fault=%s)", s.id, type, s.fault)
        if s.fault == "soft" or (s.fault == "hard" and type == "Hard"):
            s.fault = None                  # 소프트웨어 계열 고장은 리셋으로 해결
        await asyncio.sleep(1)              # 응답(Accepted)이 확실히 전달된 뒤 재부팅
        s.offline_until = time.time() + (5 if type == "Soft" else 15)
        await self._connection.close()      # 재부팅 = 연결 끊고 잠시 후 재접속

    @on("ChangeAvailability")
    async def on_avail(self, connector_id, type, **kw):
        self.sim.available = (type == "Operative")
        asyncio.create_task(self.sim.send_status())
        return call_result.ChangeAvailability(status="Accepted")

    @on("ChangeConfiguration")
    async def on_conf(self, key, value, **kw):
        if key == "MeterValueSampleInterval":
            self.sim.meter_interval = int(value)
            return call_result.ChangeConfiguration(status="Accepted")
        return call_result.ChangeConfiguration(status="NotSupported")

    @on("TriggerMessage")
    async def on_trigger(self, requested_message, **kw):
        if requested_message == "MeterValues":
            asyncio.create_task(self.sim.send_meter())
        return call_result.TriggerMessage(status="Accepted")


async def run_charger(sim: Sim, url: str, ctx: ssl.SSLContext | None, hb_interval: int):
    """접속 → Boot → 상태 보고 → Heartbeat/MeterValues 반복. 끊기면 지수 백오프로 재접속"""
    backoff = 1
    while True:
        wait = sim.offline_until - time.time()
        if wait > 0:
            await asyncio.sleep(wait)
        sim.stats["attempts"] += 1
        try:
            async with connect(f"{url}/{sim.id}", subprotocols=["ocpp1.6"], ssl=ctx,
                               open_timeout=20, ping_interval=60, proxy=None) as ws:   # 시스템 프록시 무시(직접 접속)
                cp = ClientCP(sim.id, ws)
                cp.sim, sim.cp = sim, cp
                runner = asyncio.create_task(cp.start())
                boot = await cp.call(call.BootNotification(charge_point_model="VC-1",
                                                           charge_point_vendor="ChargeOpsLab"))
                hb_interval = boot.interval or hb_interval
                sim.connected, backoff = True, 1
                sim.stats["connects"] += 1
                await sim.send_status()
                last_hb = last_meter = time.time()
                while not runner.done() and time.time() >= sim.offline_until:
                    t = time.time()
                    if t - last_hb >= hb_interval:
                        await cp.call(call.Heartbeat()); last_hb = t
                    if t - last_meter >= sim.meter_interval:
                        await sim.send_meter(); last_meter = t
                    await asyncio.sleep(1)
                runner.cancel()
        except Exception as e:
            sim.stats["failures"] += 1
            log.debug("%s connection error: %s", sim.id, e)
        sim.connected, sim.cp = False, None
        await asyncio.sleep(min(backoff, 30) + random.random())
        backoff *= 2


# ───────── 장애 주입 제어 API ─────────
control = FastAPI(title="vcharger control")
SIMS: dict[str, Sim] = {}


def get(cid: str) -> Sim:
    if cid not in SIMS:
        raise HTTPException(404, cid)
    return SIMS[cid]


@control.get("/status")
def status():
    conn = sum(s.connected for s in SIMS.values())
    agg = {k: sum(s.stats[k] for s in SIMS.values()) for k in ("attempts", "connects", "failures")}
    detail = {s.id: {"status": s.status(), "connected": s.connected, "temp": round(s.temp, 1),
                     "interval": s.meter_interval} for s in list(SIMS.values())[:10]}
    return {"total": len(SIMS), "connected": conn, **agg, "sample": detail}


# 고정 경로(/bulk/...)는 변수 경로(/{cid}/...)보다 먼저 선언해야 매칭됨
@control.post("/bulk/fault")
async def bulk_fault(count: int = 100, kind: str = "soft"):   # 동시 다발 장애(알림 묶음 시험)
    targets = list(SIMS.values())[:count]
    for s in targets:
        s.fault = kind
    await asyncio.gather(*(s.send_status() for s in targets), return_exceptions=True)
    return {"faulted": len(targets)}


@control.post("/bulk/recover")
async def bulk_recover():
    for s in SIMS.values():
        s.fault = None
    await asyncio.gather(*(s.send_status() for s in SIMS.values()), return_exceptions=True)
    return {"ok": True}


@control.post("/{cid}/fault")
async def fault(cid: str, kind: str = "soft"):          # C-01 충전기 고장
    s = get(cid); s.fault = kind; await s.send_status(); return {"fault": kind}


@control.post("/{cid}/recover")
async def recover(cid: str):
    s = get(cid); s.fault, s.heat_rate, s.overcurrent = None, 0.0, False
    await s.send_status(); return {"ok": True}


@control.post("/{cid}/heat")
async def heat(cid: str, rate: float = 0.5):             # A-01 이상 징후 → C-03 과열
    get(cid).heat_rate = rate; return {"heat_rate_per_min": rate}


@control.post("/{cid}/overcurrent")
async def overcurrent(cid: str):                          # C-03 과전류 (정격 130%)
    get(cid).overcurrent = True; return {"ok": True}


@control.post("/{cid}/charge/{action}")
async def charge(cid: str, action: str):                  # 충전 중 리셋 차단 시험
    s = get(cid); s.charging = (action == "start"); await s.send_status(); return {"charging": s.charging}


@control.post("/{cid}/disconnect")
async def disconnect(cid: str, seconds: int = 120):       # C-02 통신 두절
    s = get(cid); s.offline_until = time.time() + seconds
    if s.cp: await s.cp._connection.close()
    return {"offline_sec": seconds}


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="wss://edge.chargeops.lab/ocpp")
    p.add_argument("--ids", help="CH-01,CH-02,CH-03")
    p.add_argument("--count", type=int, default=0)
    p.add_argument("--prefix", default="LT-")
    p.add_argument("--ca", default="pki/ca.crt")
    p.add_argument("--cert-dir", help="충전기별 인증서 폴더(<id>.crt/.key)")
    p.add_argument("--cert"); p.add_argument("--key")
    p.add_argument("--meter-interval", type=int, default=10)
    p.add_argument("--heartbeat", type=int, default=30)
    p.add_argument("--ramp", type=int, default=50, help="초당 신규 접속 수")
    p.add_argument("--control-port", type=int, default=8081)
    a = p.parse_args()

    ids = a.ids.split(",") if a.ids else [f"{a.prefix}{i:04d}" for i in range(1, a.count + 1)]

    def ctx_for(cid):
        if not a.url.startswith("wss"):
            return None
        ctx = ssl.create_default_context(cafile=a.ca)          # 서버(edge) 인증서 검증
        cert = f"{a.cert_dir}/{cid}.crt" if a.cert_dir else a.cert
        key = f"{a.cert_dir}/{cid}.key" if a.cert_dir else a.key
        ctx.load_cert_chain(cert, key)                        # 내 클라이언트 인증서 제시(mTLS)
        return ctx

    shared = None if a.cert_dir else ctx_for(ids[0])
    tasks = []
    for i, cid in enumerate(ids):
        SIMS[cid] = Sim(cid, a.meter_interval)
        tasks.append(asyncio.create_task(
            run_charger(SIMS[cid], a.url, ctx_for(cid) if a.cert_dir else shared, a.heartbeat)))
        if (i + 1) % a.ramp == 0:
            await asyncio.sleep(1)                            # 접속 폭주 방지(램프업)
    server = uvicorn.Server(uvicorn.Config(control, host="0.0.0.0", port=a.control_port, log_level="warning"))
    log.info("%d chargers started, control API :%d", len(ids), a.control_port)
    await server.serve()


if __name__ == "__main__":
    asyncio.run(main())
