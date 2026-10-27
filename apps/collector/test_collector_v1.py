#!/usr/bin/env python3
"""수집기 v1.1 + 적재 함수 시험 — 가짜 API 서버로 돌린다 (인증키 · 인터넷 불필요, API 호출 0회)

실행: 스키마(db/migrations)가 적용된 DB 에 dba 로 닿는 곳에서
      cd apps/collector && PGHOST=<DB 주소> PGPORT=5432 PGUSER=dba python3 test_collector_v1.py
  · 테스트 데이터는 TEST 로 시작하는 ID, 코드는 T 로 시작하는 값만 쓰고 끝나면 모두 지운다 (실데이터는 건드리지 않음)
  · 확인 · 지우기는 dba(5432)로 한다 (app_writer 는 이력을 지울 수 없게 권한이 막혀 있음)
  · 수집기는 위 PG* 로 접속한다 (셸에 팀 .env 의 DB_* 가 있어도 시험에서는 빼고 돌림)
  · 수집기만 운영 경로(PgBouncer 6432 · app_writer)로 돌리려면 APP_ 를 붙인 변수를 더 준다:
      PGPORT=5432 PGUSER=dba APP_PGPORT=6432 APP_PGUSER=app_writer python3 test_collector_v1.py
    (비밀번호는 ~/.pgpass 에서 찾음 — 수집기 프로세스에만 PGPORT · PGUSER 를 바꿔 넘긴다)
확인하는 것 (데이터팀 v1 테스트 5가지 + v1.1 의 check 기록)
  1) 한 행이 운영기관 · 충전소 · 충전기 · 상태로 나뉘어 들어가는지, 이상한 값이 와도 멈추지 않는지
  2) 상태 변경이 이력에 쌓이고 현재 상태가 바뀌는지
  3) 같은 응답을 다시 받으면 0건이 쌓이는지 (변경분만 적재)
  4) 오래된 변경이 뒤늦게 와도 현재 상태가 과거로 돌아가지 않는지
  5) API 연결이 끊기면 실패 기록만 남고 데이터는 그대로인지
마지막 줄이 OK 이면 통과
"""
import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading
import urllib.parse

import psycopg

HERE = os.path.dirname(os.path.abspath(__file__))
FAIL = []
SCENARIO = {"info": [], "status": [], "drop": False}
ZC = "T9"                                            # 시험 전용 시도 코드 (실제 코드와 겹치지 않음)
TEST_CODES = {"code_region": ["T9"], "code_district": ["T9001"], "code_station_kind": ["TZ"],
              "code_station_kind_detail": ["TZ01"], "code_charger_type": ["T1"], "code_status": ["T7"]}


def info_row(stat_id, chger_id, **kw):
    base = {"statNm": "시험 충전소", "statId": stat_id, "chgerId": chger_id, "chgerType": "02", "addr": "세종특별자치시 시험로 1",
            "addrDetail": "지상", "location": "입구", "lat": "36.4800000", "lng": "127.2890000", "useTime": "24시간 이용가능",
            "busiId": "TESTOP", "bnm": "시험기관", "busiNm": "시험운영", "busiCall": "044-000-0000",
            "stat": "2", "statUpdDt": "20261001090000", "lastTsdt": "", "lastTedt": "", "nowTsdt": "",
            "powerType": "완속", "output": "7", "method": "단독", "zcode": ZC, "zscode": "T9001", "kind": "TZ",
            "kindDetail": "TZ01", "parkingFree": "Y", "note": "", "limitYn": "N", "limitDetail": "", "delYn": "N",
            "delDetail": "", "trafficYn": "N", "year": "2021", "floorNum": "1", "floorType": "F", "maker": "시험제조"}
    base.update(kw)
    return base


def st_row(stat_id, chger_id, stat, when):
    return {"busiId": "TESTOP", "statId": stat_id, "chgerId": chger_id, "stat": stat, "statUpdDt": when,
            "lastTsdt": "", "lastTedt": "", "nowTsdt": when if stat == "3" else ""}


class FakeApi(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):                          # 서버 로그는 감춤
        pass

    def do_GET(self):
        if SCENARIO["drop"]:                            # 연결 끊김 흉내: 응답 없이 소켓을 닫는다
            self.close_connection = True
            self.connection.shutdown(2)
            return
        u = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        rows = SCENARIO["info"] if u.path.endswith("getChargerInfo") else SCENARIO["status"]
        n, p = int(q.get("numOfRows", 10)), int(q.get("pageNo", 1))
        body = {"resultMsg": "NORMAL SERVICE.", "totalCount": len(rows), "items": {"item": rows[(p - 1) * n:p * n]},
                "pageNo": p, "resultCode": "00", "numOfRows": n}
        data = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json;charset=UTF-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def check(label, got, want):
    ok = got == want
    print(f"    [{'통과' if ok else '실패'}] {label}: {got}" + ("" if ok else f"  (기대값 {want})"), flush=True)
    if not ok:
        FAIL.append(label)


def run(*args, expect=0):
    r = subprocess.run([sys.executable, os.path.join(HERE, "collector_v1.py"), *args, "--zcode", ZC],
                       env=ENV, capture_output=True, text=True)
    for line in (r.stdout + r.stderr).strip().splitlines():
        print("  " + line)
    check(f"종료 코드 ({' '.join(args)})", r.returncode, expect)


def one(conn, sql, *p):
    return conn.execute(sql, p or None).fetchone()[0]


def cleanup(conn):
    conn.execute("DELETE FROM status_history WHERE charger_id LIKE 'TEST%' OR charger_id LIKE 'UNKNOWN%'")
    conn.execute("DELETE FROM charger_status_current WHERE charger_id LIKE 'TEST%'")
    conn.execute("DELETE FROM charger WHERE station_id LIKE 'TEST%'")
    conn.execute("DELETE FROM station WHERE station_id LIKE 'TEST%'")
    conn.execute("DELETE FROM operator WHERE operator_id = 'TESTOP'")
    for table, codes in TEST_CODES.items():
        conn.execute(f"DELETE FROM {table} WHERE code = ANY(%s) AND NOT in_guide", (codes,))
    conn.execute("DELETE FROM collect_log WHERE region_code = %s", (ZC,))


def main():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeApi)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    global ENV
    base = {k: v for k, v in os.environ.items() if not k.startswith("DB_")}   # 수집기도 PG* 로 접속하게
    ENV = {**base, "EV_API_BASE": f"http://127.0.0.1:{srv.server_port}",
           "PUBLIC_API_KEY": "TEST-KEY", "EV_SERVICE_KEY": "TEST-KEY",           # 진짜 인증키는 쓰지 않음
           "COLLECTOR_RETRY_WAITS": "0,0", "COLLECTOR_MIN_ROWS": "1", "RAW_DIR": tempfile.mkdtemp(prefix="raw-test-"),
           "S3_BUCKET_RAW": "", "S3_BUCKET": "",                                  # 원본은 임시 폴더에만
           "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"}
    app = {k[4:]: v for k, v in os.environ.items() if k.startswith("APP_PG")}   # APP_PGPORT → 수집기의 PGPORT
    ENV.update(app)
    conn = psycopg.connect(autocommit=True)
    print(f"DB: {conn.info.host}:{conn.info.port}/{conn.info.dbname} ({conn.info.user}) · 가짜 API :{srv.server_port}")
    if app:
        print("수집기 접속: " + " ".join(f"{k}={v}" for k, v in sorted(app.items()) if k != "PGPASSWORD"))
    cleanup(conn)                                         # 지난번 시험이 중간에 멈췄을 때의 찌꺼기

    print("== 0) check -> 건수와 호출 수만 (v1.1: 수집 기록에 check 도 남음) ==")
    SCENARIO["info"] = [info_row("TEST0001", "01")] * 3
    run("check", "--rows", "10")
    check("check 기록의 API 호출 수", one(conn, "SELECT api_calls FROM collect_log WHERE region_code=%s AND operation='check'", ZC), 2)

    print("== 1) 기본정보 적재 (2쪽, 4행) ==")
    SCENARIO["info"] = [
        info_row("TEST0001", "01", newField="x"),                                       # 스키마에 없는 새 필드
        info_row("TEST0001", "02", chgerType="T1", output="급속", statUpdDt="20261332000000",
                 useTime="09~18시", year="20xx"),                                        # 숫자 아닌 용량 · 없는 날짜 · 미등록 코드
        info_row("TEST0002", "01", lat="999", statUpdDt="20261001100000"),               # 범위 밖 위도
        info_row("", "01"),                                                              # 충전소 ID 없음
    ]
    run("info", "--rows", "2")
    check("충전기 수", one(conn, "SELECT count(*) FROM charger WHERE station_id LIKE 'TEST%'"), 3)
    check("충전소 수", one(conn, "SELECT count(*) FROM station WHERE station_id LIKE 'TEST%'"), 2)
    check("운영기관", one(conn, "SELECT name FROM operator WHERE operator_id='TESTOP'"), "시험기관")
    check("숫자가 아닌 용량은 비움", one(conn, "SELECT output_kw FROM charger WHERE charger_id='TEST0001-02'"), None)
    check("4자리가 아닌 설치 연도는 비움", one(conn, "SELECT install_year FROM charger WHERE charger_id='TEST0001-02'"), None)
    check("charger_type_code (v1.1 이름)", one(conn, "SELECT charger_type_code FROM charger WHERE charger_id='TEST0001-02'"), "T1")
    check("미등록 코드 자동 등록", one(conn, "SELECT name FROM code_charger_type WHERE code='T1'"), "(가이드 미등록)")
    check("범위 밖 위도는 비움", one(conn, "SELECT lat FROM station WHERE station_id='TEST0002'"), None)
    check("주차료 무료 Y -> true", one(conn, "SELECT parking_free FROM station WHERE station_id='TEST0001'"), True)
    check("충전소 값은 번호가 가장 작은 충전기의 값", one(conn, "SELECT use_time FROM station WHERE station_id='TEST0001'"), "24시간 이용가능")
    check("없는 날짜의 상태는 건너뜀 (현재 상태 수)",
          one(conn, "SELECT count(*) FROM charger_status_current WHERE charger_id LIKE 'TEST%'"), 2)
    d = one(conn, "SELECT detail FROM collect_log WHERE region_code=%s AND operation='getChargerInfo'", ZC)
    check("충전소 단위 필드 충돌 점검", d["conflicts"], {"station.useTime": 1})
    check("새 API 필드 알림", d["unknown_fields"], ["newField"])
    check("건너뛴 행 (충전소 ID 없음)", d["skipped_invalid"], 1)

    print("== 2) 상태 변경 -> 이력이 쌓이고 현재 상태가 바뀜 ==")
    SCENARIO["status"] = [st_row("TEST0001", "01", "3", "20261001091000"), st_row("TEST0001", "01", "2", "20261001093000"),
                          st_row("TEST0002", "01", "5", "20261001101500"), st_row("UNKNOWN9", "01", "2", "20261001093000")]
    run("status")
    check("TEST0001-01의 이력 행 수 (기준 1 + 변경 2)",
          one(conn, "SELECT count(*) FROM status_history WHERE charger_id='TEST0001-01'"), 3)
    check("TEST0001-01의 현재 상태 (가장 늦은 변경)",
          one(conn, "SELECT status_code FROM charger_status_current WHERE charger_id='TEST0001-01'"), "2")
    d = one(conn, "SELECT detail FROM collect_log WHERE region_code=%s AND operation='getChargerStatus' ORDER BY run_id DESC LIMIT 1", ZC)
    check("기본정보에 없는 충전기는 건너뜀", d["unknown_charger"], 1)

    print("== 3) 같은 응답을 다시 받음 -> 0건 ==")
    run("status")
    check("두 번째 상태 수집의 반영 건수", one(conn, "SELECT rows_loaded FROM collect_log WHERE region_code=%s ORDER BY run_id DESC LIMIT 1", ZC), 0)

    print("== 4) 오래된 변경이 뒤늦게 옴 -> 이력에는 남고, 현재 상태는 그대로 ==")
    SCENARIO["status"] = [st_row("TEST0001", "01", "T7", "20261001092000")]
    run("status")
    check("이력에는 쌓임", one(conn, "SELECT count(*) FROM status_history WHERE charger_id='TEST0001-01'"), 4)
    check("현재 상태는 과거로 돌아가지 않음",
          one(conn, "SELECT status_code FROM charger_status_current WHERE charger_id='TEST0001-01'"), "2")

    print("== 5) API 연결이 끊김 -> 실패가 기록되고 데이터는 그대로 ==")
    before = one(conn, "SELECT count(*) FROM status_history WHERE charger_id LIKE 'TEST%'")
    SCENARIO["drop"] = True
    run("status", expect=1)
    SCENARIO["drop"] = False
    check("마지막 실행의 상태", one(conn, "SELECT status FROM collect_log WHERE region_code=%s ORDER BY run_id DESC LIMIT 1", ZC), "error")
    check("이력 행 수 변화 없음", one(conn, "SELECT count(*) FROM status_history WHERE charger_id LIKE 'TEST%'"), before)
    got = conn.execute("""SELECT count(*) FILTER (WHERE status='ok'), count(*) FILTER (WHERE status='error')
                          FROM collect_log WHERE region_code=%s AND operation <> 'check'""", (ZC,)).fetchone()
    check("수집 기록 (성공 4, 실패 1)", tuple(got), (4, 1))

    cleanup(conn)
    check("테스트 데이터를 지운 뒤 남은 가짜 충전기", one(conn, "SELECT count(*) FROM charger WHERE station_id LIKE 'TEST%'"), 0)
    srv.shutdown()
    print("FAIL: " + ", ".join(FAIL) if FAIL else "OK")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
