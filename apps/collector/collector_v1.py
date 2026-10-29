#!/usr/bin/env python3
"""ChargeOps 공공 API 수집기 v1.1 (데이터팀 collector_v1.py 와 같은 사용법)

  python3 collector_v1.py check  [--zcode 36]   DB 에 넣지 않고 전체 건수와 필요한 호출 수만 확인 (API 2회)
  python3 collector_v1.py info   [--zcode 36]   기본정보 수집 (하루 1회) → 운영기관 · 충전소 · 충전기 · 지금 상태
  python3 collector_v1.py status [--zcode 36]   상태 변경분 수집 (5분마다) → 이력 · 현재 상태

환경변수 (명령에 비밀값을 쓰지 않으려고 전부 환경변수로 받는다. 이름은 저장소 루트 .env.example 기준,
         괄호 안은 실습 환경에서 쓰던 옛 이름 — 팀 이름이 비어 있으면 옛 이름을 읽는다)
  PUBLIC_API_KEY   공공데이터포털 인증키 (EV_SERVICE_KEY). Decoding 키. Encoding 키(%포함)를 넣어도 자동으로 풀어 씀
  DB_HOST DB_PORT DB_NAME DB_USER DB_PASSWORD   DB 접속 = PgBouncer 2대(6432), app_writer 계정
                   (비어 있으면 libpq 표준 PGHOST PGPORT PGDATABASE PGUSER PGPASSWORD · ~/.pgpass)
  PGTARGETSESSIONATTRS=read-write · PGCONNECT_TIMEOUT=3   DB_HOST 에 주소 두 개(쉼표)를 주면 libpq 가 쓰기 되는 쪽을 고른다(장애 전환 대비)
  EV_ZCODE         수집 범위 시도 코드 (--zcode 가 없을 때). 빈 값 = 전국 (공공 API 하루 1,000회 한도에 주의)
  RAW_DIR          원본 JSON 저장 폴더 (기본 ./raw)
  S3_BUCKET_RAW    있으면 원본을 S3(MinIO)에 올림 (S3_BUCKET). S3_ENDPOINT_URL · AWS_REGION · AWS_ACCESS_KEY_ID ·
                   AWS_SECRET_ACCESS_KEY · AWS_CA_BUNDLE 은 boto3 가 읽음
  EV_API_BASE      API 주소 (시험용 가짜 서버를 가리킬 때만)

필요한 DB 객체: collect_log · stg_charger_info · stg_charger_status 테이블, load_charger_info() ·
load_charger_status() · stg_info_conflicts() 함수 (db/migrations)

v1 → v1.1: check 도 collect_log 에 호출 수를 남김(DB 에 닿을 때만) · 쪽마다 바로 적재 대기로 복사(전국 52만 행도
메모리 작게) · 결과 JSON 의 키 이름 고정 · charger_type_code
팀 저장소판: 환경변수 이름을 .env.example 에 맞춤 (옛 이름도 계속 읽어서 실습 환경 · K8s 설정은 그대로 동작)
"""
import argparse
import datetime as dt
import json
import math
import os
import re
import sys
import tempfile
import time
import urllib.parse

import psycopg
import requests

API_BASE = os.environ.get("EV_API_BASE", "https://apis.data.go.kr/B552584/EvCharger").rstrip("/")
OPS = {"info": "getChargerInfo", "status": "getChargerStatus"}
STG = {"info": "stg_charger_info", "status": "stg_charger_status"}
INFO_FIELDS = ["statNm", "statId", "chgerId", "chgerType", "addr", "addrDetail", "location", "lat", "lng",
               "useTime", "busiId", "bnm", "busiNm", "busiCall", "stat", "statUpdDt", "lastTsdt", "lastTedt",
               "nowTsdt", "powerType", "output", "method", "zcode", "zscode", "kind", "kindDetail",
               "parkingFree", "note", "limitYn", "limitDetail", "delYn", "delDetail", "trafficYn", "year",
               "floorNum", "floorType", "maker"]                                   # 37개
STATUS_FIELDS = ["busiId", "statId", "chgerId", "stat", "statUpdDt", "lastTsdt", "lastTedt", "nowTsdt"]  # 8개
FIELDS = {"info": INFO_FIELDS, "status": STATUS_FIELDS}
RETRY_WAITS = [int(x) for x in os.environ.get("COLLECTOR_RETRY_WAITS", "3,10").split(",")]  # 3초, 10초 쉬고 다시 (모두 3번)
MAX_PERIOD = 10                                                                    # 상태 API 가 주는 최대 범위(분)


def snake(name: str) -> str:
    """statUpdDt → stat_upd_dt (적재 대기 테이블의 컬럼 이름 규칙)"""
    return re.sub(r"([A-Z])", r"_\1", name).lower()


def log(msg: str) -> None:
    print(msg, flush=True)


def env(*names: str, default: str = "") -> str:
    """앞의 이름부터 차례로 보고 처음으로 값이 있는 것을 돌려준다 (팀 이름 → 옛 이름)"""
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return default


class ApiError(RuntimeError):
    """인증키 · 활용신청 · 한도 문제처럼 다시 시도해도 소용없는 오류"""


def service_key() -> str:
    key = env("PUBLIC_API_KEY", "EV_SERVICE_KEY")
    if not key:
        raise SystemExit("먼저 인증키를 환경변수로 넣어주세요 (PUBLIC_API_KEY, .env.example 참고)")
    return urllib.parse.unquote(key) if "%" in key else key   # Encoding 키 → Decoding 키 (requests 가 다시 인코딩)


def parse(text: str) -> tuple[int, list[dict]]:
    """응답 → (전체 건수, 행 목록). 평평한 JSON(EvCharger)과 response.header/body 형식을 모두 받는다"""
    t = text.lstrip()
    if not t.startswith("{"):
        m = re.search(r"<(returnAuthMsg|resultMsg|errMsg|returnReasonCode)>([^<]+)<", t)
        raise ApiError("JSON이 아닌 응답이 왔습니다: " + (m.group(2) if m else t[:150].replace("\n", " ")))
    d = json.loads(t)
    hdr, body = (d["response"].get("header", {}), d["response"].get("body", {})) if "response" in d else (d, d)
    code = str(hdr.get("resultCode", "00"))
    if code == "03":                       # NODATA: 범위 안에 변경이 없음 → 0건
        return 0, []
    if code not in ("00", "0"):
        raise ApiError(f"API 오류: {code} {hdr.get('resultMsg', '')}".strip())
    items = body.get("items") or {}
    if isinstance(items, dict):
        items = items.get("item") or []
    if isinstance(items, dict):            # 1건이면 목록이 아니라 객체로 오는 API 가 있다
        items = [items]
    return int(body.get("totalCount") or 0), items


SESSION = requests.Session()
SESSION.trust_env = True                   # HTTPS_PROXY 등 환경변수 프록시는 따른다


def fetch(op: str, params: dict, page: int, rows: int) -> tuple[int, list[dict], str]:
    q = {"serviceKey": service_key(), "pageNo": page, "numOfRows": rows, "dataType": "JSON", **params}
    for attempt in range(len(RETRY_WAITS) + 1):
        try:
            r = SESSION.get(f"{API_BASE}/{op}", params=q, timeout=(10, 120))
            r.raise_for_status()
            total, items = parse(r.text)
            return total, items, r.text
        except ApiError:
            raise                                                   # 다시 해도 같은 결과 → 호출만 낭비
        except (requests.RequestException, ValueError) as e:
            why = e.__class__.__name__
            if isinstance(e, requests.HTTPError) and e.response is not None:
                why += f" {e.response.status_code}"              # 예: HTTPError 503
            if attempt == len(RETRY_WAITS):
                raise RuntimeError(f"API 연결 실패 ({attempt + 1}번 시도): {why}") from e
            wait = RETRY_WAITS[attempt]
            log(f"    ... {page}쪽 받기 실패({why}), {wait}초 뒤 다시 시도")
            time.sleep(wait)
    raise AssertionError("unreachable")


class RawStore:
    """원본 응답을 손대지 않고 쪽마다 보관 → 스키마를 바꿔도 원본에서 다시 넣을 수 있다"""

    def __init__(self, bucket: str | None):
        self.bucket = bucket
        self.root = os.environ.get("RAW_DIR", "raw")
        self.s3 = None
        if bucket:
            try:
                import boto3                                        # S3 를 쓸 때만 필요
            except ImportError as e:
                raise SystemExit("S3 에 올리려면 boto3 가 필요합니다: pip install boto3") from e
            self.s3 = boto3.client("s3", endpoint_url=os.environ.get("S3_ENDPOINT_URL"),
                                   region_name=os.environ.get("AWS_REGION", "us-east-1"))

    def save(self, op: str, stamp: dt.datetime, page: int, text: str) -> str:
        key = f"{op}/{stamp:%Y/%m/%d/%H%M%S}_p{page}.json"          # 폴더 날짜는 UTC
        if self.s3:
            self.s3.put_object(Bucket=self.bucket, Key=key, Body=text.encode("utf-8"),
                               ContentType="application/json")
            return f"s3://{self.bucket}/{key}"
        path = os.path.join(self.root, key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path.replace(os.sep, "/")


DB_ENV = {"host": "DB_HOST", "port": "DB_PORT", "dbname": "DB_NAME", "user": "DB_USER", "password": "DB_PASSWORD"}


def db_connect(**kw) -> psycopg.Connection:
    # 팀 이름(DB_*)에 값이 있는 항목만 넘기고, 나머지는 libpq 가 PG* 환경변수 · ~/.pgpass 에서 채운다
    conn_kw = {arg: os.environ[name].strip() for arg, name in DB_ENV.items() if os.environ.get(name, "").strip()}
    # prepare_threshold=None: PgBouncer(transaction 모드)를 거칠 때 서버 쪽 준비문을 쓰지 않는다
    return psycopg.connect(prepare_threshold=None, application_name="collector_v1", **conn_kw, **kw)


def scope_text(zcode: str) -> str:
    return f"시도 {zcode}" if zcode else "전국"


def decide_period(conn: psycopg.Connection, zcode: str, minimum: int) -> int:
    """직전 성공 이후 지난 시간만큼 조회 범위를 넓힌다(최대 10분) → 실패한 회차의 빈 구간을 메움"""
    row = conn.execute("""SELECT extract(epoch FROM now() - max(started_at)) / 60 FROM collect_log
                          WHERE operation = 'getChargerStatus' AND status = 'ok'
                            AND region_code IS NOT DISTINCT FROM %s""", (zcode or None,)).fetchone()
    if not row or row[0] is None:
        return minimum
    gap = math.ceil(float(row[0]))
    if gap > MAX_PERIOD:
        log(f"    - 직전 성공 이후 {gap}분: 최대 {MAX_PERIOD}분만 조회합니다 (그 사이 변경은 다음 기본정보 수집이 충전기마다 한 건씩 메움)")
    return max(minimum, min(MAX_PERIOD, gap))


def cmd_check(args) -> int:
    zcode = args.zcode
    log(f"[check] 수집 범위: {scope_text(zcode)}")
    base = {"zcode": zcode} if zcode else {}
    totals = {}
    for op, extra in (("getChargerInfo", {}), ("getChargerStatus", {"period": args.period})):
        total, _, _ = fetch(op, {**base, **extra}, 1, 1)
        totals[op] = total
        calls = max(1, math.ceil(total / args.rows))
        log(f"[check] {op}: 전체 {total:,}건 -> {args.rows}건씩 받으면 {calls}회 호출")
    try:                                                            # v1.1: 오늘 쓴 호출 수에 check 도 포함
        with db_connect(autocommit=True, connect_timeout=5) as c:
            c.execute("""INSERT INTO collect_log (operation, region_code, status, finished_at, api_calls, detail)
                         VALUES ('check', %s, 'ok', now(), 2, %s)""", (zcode or None, json.dumps(totals)))
    except psycopg.Error:
        log("[check] (DB 에 닿지 않아 호출 수 기록은 건너뜀 — API 확인은 끝남)")
    return 0


def cmd_load(mode: str, args) -> int:
    op, stg, fields = OPS[mode], STG[mode], FIELDS[mode]
    zcode = args.zcode
    params = {"zcode": zcode} if zcode else {}
    store = RawStore(args.bucket)
    stamp = dt.datetime.now(dt.timezone.utc)
    logc = db_connect(autocommit=True)                              # 수집 기록은 따로 즉시 확정 (실패도 남게)
    period = None
    if mode == "status":
        period = decide_period(logc, zcode, args.period)
        params["period"] = period
    run_id = logc.execute("""INSERT INTO collect_log (operation, region_code, period_min)
                             VALUES (%s, %s, %s) RETURNING run_id""", (op, zcode or None, period)).fetchone()[0]
    calls, fetched, first_key, extra_fields = 0, 0, None, set()
    # v1.2: 공공 API 를 끝까지 받은 뒤에 DB 트랜잭션을 연다. 예전에는 트랜잭션(TRUNCATE 잠금 포함)을 연 채로
    #       API 응답을 기다려서, API 가 느린 회차(10/29 09:30 69초)에 DB 연결 1개를 1분 넘게 붙잡았다.
    #       받은 행은 임시 파일에 두고(전국 수집도 메모리 작게) DB 에는 한 번에 넣는다.
    buf = tempfile.TemporaryFile(mode="w+", encoding="utf-8")
    try:
        page, pages = 1, 1
        while page <= pages:                                        # 1) API → 원본 보관 · 임시 파일 (DB 안 씀)
            total, items, text = fetch(op, params, page, args.rows)
            calls += 1
            key = store.save(op, stamp, page, text)
            first_key = first_key or key
            pages = max(1, math.ceil(total / args.rows))
            for it in items:
                extra_fields.update(set(it) - set(fields))
                row = [None if it.get(f) is None else str(it.get(f)) for f in fields] + [page]
                buf.write(json.dumps(row, ensure_ascii=False) + "\n")
            fetched += len(items)
            if pages > 1 or mode == "info":
                log(f"    ... {page}쪽 받음 ({fetched:,}/{total:,}건)")
            page += 1
        buf.seek(0)
        with db_connect() as conn:                                  # 2) 한 회차 = 한 트랜잭션 (중간 실패 → 전부 되돌림)
            conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"chargeops:{op}",))  # 같은 종류는 차례로
            conn.execute(f"TRUNCATE {stg}")
            cols = ", ".join([snake(f) for f in fields] + ["page_no"])
            with conn.cursor().copy(f"COPY {stg} ({cols}) FROM STDIN") as cp:   # 받은 그대로 복사
                for line in buf:
                    cp.write_row(json.loads(line))
            result = conn.execute(f"SELECT load_charger_{mode}(%s)", (run_id,)).fetchone()[0]
            conflicts = conn.execute("SELECT stg_info_conflicts()").fetchone()[0] if mode == "info" else {}
            if mode == "info":
                result["stations_db"], result["operators_db"] = conn.execute(
                    "SELECT (SELECT count(*) FROM station), (SELECT count(*) FROM operator)").fetchone()
        detail = {**result, "conflicts": conflicts, "unknown_fields": sorted(extra_fields)}
        loaded = result["chargers"] if mode == "info" else result["history_inserted"]
        logc.execute("""UPDATE collect_log SET status = 'ok', finished_at = now(), api_calls = %s, rows_fetched = %s,
                        rows_loaded = %s, raw_key = %s, detail = %s WHERE run_id = %s""",
                     (calls, fetched, loaded, first_key, json.dumps(detail, ensure_ascii=False), run_id))
        report(mode, result, conflicts, sorted(extra_fields), calls, first_key)
        return 0
    except BaseException as e:                                      # Ctrl+C 도 기록 (단, 강제 종료면 running 으로 남음)
        msg = str(e) if not isinstance(e, KeyboardInterrupt) else "사용자가 중단함"
        logc.execute("""UPDATE collect_log SET status = 'error', finished_at = now(), api_calls = %s,
                        rows_fetched = %s, raw_key = %s, error_message = %s WHERE run_id = %s""",
                     (calls, fetched, first_key, msg[:1000], run_id))
        log(f"[{mode}] 실패 ({e.__class__.__name__}): {msg}")
        if isinstance(e, KeyboardInterrupt):
            raise
        return 1
    finally:
        buf.close()
        logc.close()


def report(mode, r, conflicts, extra, calls, key) -> None:
    if mode == "info":
        log(f"[info] 충전기 {r['chargers']:,}대 (충전소 {r['stations']:,}곳, 운영기관 {r['operators']:,}곳) 반영, "
            f"API {calls}회 호출, 원본={key}")
        log(f"    - 새로 들어온 충전기 {r['chargers_new']:,}대, 상태 이력에 새로 쌓인 것 {r['history_inserted']:,}건, "
            f"현재 상태가 바뀐 충전기 {r['current_updated']:,}대")
        if r.get("skipped_invalid"):
            log(f"    - 충전소 ID 나 충전기 번호가 없어 건너뛴 행 {r['skipped_invalid']:,}건")
        if r.get("status_skipped"):
            log(f"    - 상태 코드나 상태 시각이 없거나 형식이 틀려 상태만 건너뛴 충전기 {r['status_skipped']:,}대")
    else:
        log(f"[status] {r['rows']:,}건 중 {r['history_inserted']:,}건 변경, API {calls}회 호출, 원본={key}")
        if r.get("unknown_charger"):
            log(f"    - 기본정보에 없는 충전기라 건너뛴 상태 {r['unknown_charger']:,}건 (같은 --zcode 로 info 를 먼저)")
        if r.get("skipped_invalid"):
            log(f"    - 충전기 · 상태 · 시각 중 하나가 없어 건너뛴 행 {r['skipped_invalid']:,}건")
    new = r.get("new_codes") or {}
    if new:
        n = sum(len(v) for v in new.values())
        log(f"    - 가이드에 없는 코드 {n}개를 '(가이드 미등록)'으로 등록: "
            + ", ".join(f"{t}={','.join(v)}" for t, v in new.items()))
    if conflicts:
        log(f"    - 충전소(운영기관) 하나에 값이 여러 가지인 필드: {json.dumps(conflicts, ensure_ascii=False)}")
    if extra:
        log(f"    - 스키마에 자리가 없는 새 API 필드 (원본 파일에는 남음): {', '.join(extra)}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="ChargeOps 공공 API 수집기 v1.1 (한국환경공단 전기차 충전소 정보)")
    p.add_argument("mode", choices=["check", "info", "status"], help="check 건수 확인 · info 기본정보 · status 상태")
    p.add_argument("--zcode", default=None, help="시도 코드 2자리 (예: 36 세종). 빼면 환경변수 EV_ZCODE, 그것도 없으면 전국")
    p.add_argument("--period", type=int, default=5, help="상태 조회 범위의 최소값(분, 기본 5, 최대 10)")
    p.add_argument("--rows", type=int, default=9999, help="한 번에 받을 건수 (10~9999, 기본 9999)")
    p.add_argument("--bucket", default=env("S3_BUCKET_RAW", "S3_BUCKET") or None,
                   help="원본을 올릴 S3 버킷 (환경변수 S3_BUCKET_RAW)")
    args = p.parse_args(argv)
    if not int(os.environ.get("COLLECTOR_MIN_ROWS", "10")) <= args.rows <= 9999:   # 시험에서만 최소값을 낮춤
        p.error("--rows 는 10~9999")
    if not 1 <= args.period <= MAX_PERIOD:
        p.error("--period 는 1~10")
    args.zcode = (args.zcode if args.zcode is not None else os.environ.get("EV_ZCODE", "")).strip()
    service_key()                                                   # 인증키가 없으면 여기서 바로 멈춤
    try:
        if args.mode == "check":
            return cmd_check(args)
        log(f"[{args.mode}] 수집 범위: {scope_text(args.zcode)}")
        return cmd_load(args.mode, args)
    except ApiError as e:
        log(f"[{args.mode}] 실패 (ApiError): {e}")
        return 1
    except psycopg.OperationalError as e:
        log(f"[{args.mode}] DB 접속 실패: {str(e).strip().splitlines()[0]}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
