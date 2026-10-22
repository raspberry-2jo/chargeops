"""AI 게이트웨이 — 장애 요약 · 유사 사례 검색 · /ask
원칙: AI 는 판정·실행하지 않는다. 근거 데이터를 함께 보여 주고, 실패하면 템플릿으로 폴백한다.
모델: Ollama(로컬 LLM, Windows 호스트 10.10.0.1:11434)  ※ 원 설계의 Bedrock 을 대체
"""
import logging, os, re, time

import requests
from fastapi import FastAPI
from pydantic import BaseModel

from common.db import q, q1

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ai")

OLLAMA = os.environ.get("OLLAMA_URL", "http://10.10.0.1:11434")
GEN_MODEL = os.environ.get("GEN_MODEL", "qwen2.5:1.5b")
EMB_MODEL = os.environ.get("EMB_MODEL", "nomic-embed-text")
TIMEOUT = int(os.environ.get("LLM_TIMEOUT", "45"))     # CPU 추론은 10~40초 걸릴 수 있음
DAILY_LIMIT = int(os.environ.get("DAILY_LIMIT", "300"))
app = FastAPI(title="ChargeOps AI gateway")

# 프롬프트에 들어가기 전 민감정보 마스킹 (토큰·이메일·전화번호·IP)
MASKS = [(re.compile(r"xox[abpe]-[\w-]+"), "[TOKEN]"), (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "[EMAIL]"),
         (re.compile(r"01[016789]-?\d{3,4}-?\d{4}"), "[PHONE]"), (re.compile(r"\b\d{1,3}(\.\d{1,3}){3}\b"), "[IP]")]


def mask(text: str) -> str:
    for pat, rep in MASKS:
        text = pat.sub(rep, text)
    return text


def log_call(inc_id, feature, provider, model, t_in, t_out, ms, fallback):
    q("""INSERT INTO llm_call_log (incident_id, feature, provider, model_id, tokens_in, tokens_out, latency_ms, fallback)
         VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""", (inc_id, feature, provider, model, t_in, t_out, ms, fallback))


def over_budget() -> bool:
    n = q1("SELECT count(*) AS n FROM llm_call_log WHERE provider='ollama' AND created_at >= current_date")["n"]
    return n >= DAILY_LIMIT


def generate(prompt: str, inc_id, feature) -> tuple[str | None, str, int]:
    """Ollama 호출. 실패·시간초과·예산 초과 시 None 반환 → 호출자가 템플릿 사용"""
    t0 = time.time()
    if over_budget():
        return None, "budget", 0
    try:
        r = requests.post(f"{OLLAMA}/api/generate", timeout=TIMEOUT, json={
            "model": GEN_MODEL, "prompt": mask(prompt), "stream": False,
            "options": {"num_predict": 512, "temperature": 0.2}})
        r.raise_for_status()
        j = r.json()
        ms = int((time.time() - t0) * 1000)
        log_call(inc_id, feature, "ollama", GEN_MODEL, j.get("prompt_eval_count"), j.get("eval_count"), ms, False)
        return j["response"].strip(), "ollama", ms
    except Exception as e:
        ms = int((time.time() - t0) * 1000)
        log.warning("LLM fallback: %s", e)
        log_call(inc_id, feature, "template", None, None, None, ms, True)
        return None, "template", ms


def embed(text: str) -> list[float] | None:
    try:
        r = requests.post(f"{OLLAMA}/api/embed", json={"model": EMB_MODEL, "input": mask(text)}, timeout=30)
        r.raise_for_status()
        return r.json()["embeddings"][0]
    except Exception as e:
        log.warning("embed failed: %s", e)
        return None


def context_of(inc_id: int) -> tuple[dict, str]:
    inc = q1("SELECT * FROM incident WHERE incident_id=%s", (inc_id,))
    cid = inc["charger_id"] or (inc["targets"] or [None])[0]
    hist = q("""SELECT cs.name AS status, h.error_code, h.changed_at
                FROM status_history h JOIN code_status cs ON cs.code = h.status_code
                WHERE h.charger_id=%s ORDER BY h.changed_at DESC LIMIT 8""", (cid,)) if cid else []
    meter = q("""SELECT ts, temp_c, current_a FROM meter_sample WHERE charger_id=%s
                 ORDER BY ts DESC LIMIT 6""", (cid,)) if cid else []
    acts = q("""SELECT step, result, created_at FROM action_audit WHERE charger_id=%s
                AND created_at > now() - interval '1 day' ORDER BY created_at DESC LIMIT 5""", (cid,)) if cid else []
    ctx = (f"장애: {inc['type_code']} {inc['severity']} {inc['title']}\n대상: {cid} (총 {len(inc['targets'] or [])}대)\n"
           f"상태 이력(최신순): " + "; ".join(f"{h['changed_at']:%H:%M:%S} {h['status']} {h['error_code'] or ''}" for h in hist) +
           "\n계측(최신순): " + "; ".join(f"{m['ts']:%H:%M:%S} {m['temp_c']}℃ {m['current_a']}A" for m in meter) +
           "\n최근 조치: " + ("; ".join(f"{a['step']}={a['result']}" for a in acts) or "없음") +
           f"\n판단 근거: {inc['root_cause'] or '-'}")
    return inc, ctx


def similar(vec: list[float] | None, exclude: int) -> list[dict]:
    if vec is None:
        return []
    # <=> = 코사인 거리(0에 가까울수록 유사). HNSW 인덱스가 이 연산을 가속
    return q("""SELECT e.incident_id, i.title, i.root_cause,
                       round((1 - (e.embedding <=> %s::vector))::numeric, 3)::float AS similarity
                FROM incident_embedding e JOIN incident i USING (incident_id)
                WHERE e.incident_id <> %s ORDER BY e.embedding <=> %s::vector LIMIT 3""",
             (str(vec), exclude, str(vec)))


class Inc(BaseModel):
    incident_id: int


@app.post("/summarize")
def summarize(req: Inc):
    inc, ctx = context_of(req.incident_id)
    prompt = ("너는 전기차 충전기 관제 운영 보조자다. 아래 데이터만 근거로, 한국어로 4줄 이내로 "
              "①현상 ②추정 원인 ③권장 조치 순서(Soft Reset → Hard Reset → 현장 점검)를 요약하라. "
              "데이터에 없는 사실은 추측이라고 표시하라.\n\n" + ctx)
    text, provider, ms = generate(prompt, req.incident_id, "summary")
    if text is None:                                  # 템플릿 폴백: AI 없이도 최소 정보 제공
        text = f"(템플릿) {ctx.splitlines()[0]}\n권장: 상태 재확인 → Soft Reset → 실패 시 Hard Reset(Lead 승인) → 현장 점검"
    q("UPDATE incident SET summary=%s WHERE incident_id=%s", (text, req.incident_id))
    return {"summary": text, "provider": provider, "latency_ms": ms,
            "similar": similar(embed(ctx), req.incident_id), "evidence": ctx}


@app.post("/embed")
def embed_incident(req: Inc):
    """복구 완료된 장애를 '사례'로 저장 → 다음 장애 때 유사 사례로 검색됨"""
    inc, ctx = context_of(req.incident_id)
    content = f"{ctx}\n요약: {inc['summary'] or ''}"
    vec = embed(content)
    if vec is None:
        return {"stored": False}
    q("""INSERT INTO incident_embedding (incident_id, content, embedding) VALUES (%s,%s,%s::vector)
         ON CONFLICT (incident_id) DO UPDATE SET content=EXCLUDED.content, embedding=EXCLUDED.embedding""",
      (req.incident_id, content, str(vec)))
    log_call(req.incident_id, "embed", "ollama", EMB_MODEL, None, None, None, False)
    return {"stored": True, "dim": len(vec)}


class Ask(BaseModel):
    question: str


@app.post("/ask")
def ask(req: Ask):
    """질문 → 운영 지표를 근거로 붙여 답변 (근거를 항상 함께 반환)"""
    open_inc = q("SELECT incident_id, severity, title FROM incident WHERE status <> 'resolved' ORDER BY severity LIMIT 10")
    worst = q("""SELECT charger_id, round(avg(uptime_pct),2) AS uptime FROM mv_daily_uptime
                 WHERE day > current_date - 7 GROUP BY 1 ORDER BY 2 LIMIT 5""")
    stat = q("SELECT * FROM stat_operator_daily ORDER BY day DESC LIMIT 5")
    evidence = (f"열린 장애: {[(r['incident_id'], r['severity'], r['title']) for r in open_inc]}\n"
                f"7일 가동률 하위: {[(r['charger_id'], float(r['uptime'])) for r in worst]}\n"
                f"운영사 일 통계: {[(r['operator_id'], str(r['day']), float(r['uptime_pct'] or 0)) for r in stat]}")
    text, provider, _ = generate(f"다음 운영 데이터만 근거로 질문에 한국어로 짧게 답하라.\n{evidence}\n질문: {req.question}",
                                 None, "ask")
    return {"answer": text or "(AI 응답 불가 — 근거 데이터를 확인하세요)", "evidence": evidence, "provider": provider}


@app.get("/healthz")
def healthz():
    return {"ok": True}
