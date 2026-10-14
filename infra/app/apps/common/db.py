"""DB 접속 공통 모듈 — psycopg3 연결 풀(동기)"""
import atexit, os
from psycopg_pool import ConnectionPool
from psycopg.rows import dict_row

_pools: dict[str, ConnectionPool] = {}


def pool(env: str = "DB_DSN") -> ConnectionPool:
    """환경변수 이름별로 풀 1개. prepare_threshold=None → PgBouncer 트랜잭션 풀링 호환
    check → 빌려주기 전에 연결 확인 (장애 전환 뒤 옛 Primary 쪽의 죽은 연결을 버림 — csms/app.py 와 같은 이유)"""
    if env not in _pools:
        _pools[env] = ConnectionPool(os.environ[env], min_size=1, max_size=5, open=True,
                                     kwargs={"autocommit": True, "prepare_threshold": None,
                                             "row_factory": dict_row},
                                     check=ConnectionPool.check_connection)
    return _pools[env]


@atexit.register
def _close():
    """프로세스 종료 시 풀 정리 (CronJob 이 대기 없이 바로 끝나도록)"""
    for p in _pools.values():
        p.close(timeout=1)


def q(sql: str, args: tuple | dict = (), env: str = "DB_DSN") -> list[dict]:
    """조회: 결과 행을 dict 리스트로 반환"""
    with pool(env).connection() as c:
        cur = c.execute(sql, args)
        return cur.fetchall() if cur.description else []


def q1(sql: str, args: tuple | dict = (), env: str = "DB_DSN") -> dict | None:
    rows = q(sql, args, env)
    return rows[0] if rows else None
