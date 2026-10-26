"""Bounded Dev release / Ops candidate OCPP checks; never register chargers."""
import asyncio
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from urllib.request import Request, build_opener, ProxyHandler


class Unverified(ValueError):
    pass


class ProbeFailure(ValueError):
    """Only fixed, locally authored messages may be printed."""


class State:
    def __init__(self, registry):
        from prometheus_client import Gauge, Counter, Histogram
        self.result = Gauge('chargeops_canary_ocpp_result', '-1 failed, 0 running, 1 passed, 3 unverified', registry=registry)
        self.check = Gauge('chargeops_release_check', '-1 failed, 0 waiting, 1 passed, 2 not applicable, 3 unverified', ['check'], registry=registry)
        self.requests = Counter('chargeops_canary_ocpp_requests_total', 'Client observed requests', ['action', 'result'], registry=registry)
        self.latency = Histogram('chargeops_canary_ocpp_roundtrip_seconds', 'Client observed request/reply seconds', ['action', 'result'], registry=registry)
        self.connection = Gauge('chargeops_canary_ocpp_connection_up', 'Test socket open', registry=registry)
        self.started = Gauge('chargeops_release_probe_started_timestamp_seconds', 'Start time of this specific verification Job', registry=registry)
        self.started.set(time.time())
        self.attempts = Counter('chargeops_ws_probe_connection_attempts_total', 'Client connection attempts', ['result'], registry=registry)
        for result in ('success','error'):
            self.attempts.labels(result).inc(0)
        for name in ('health', 'database', 'ocpp', 'session'):
            self.check.labels(name).set(0)
        self.result.set(0)


def configuration(environ):
    namespace = environ.get('POD_NAMESPACE', '')
    charger = environ.get('OCPP_TEST_CHARGER_ID', '')
    if namespace == 'dev':
        if charger != 'CI-DEV-CANARY-01':
            raise Unverified('Dev requires its dedicated in-memory verification ID')
        return namespace, charger, 'csms.dev.svc.cluster.local', ''
    if namespace == 'ops':
        if (not re.fullmatch(r'CI-OPS-CANARY-[A-Za-z0-9_-]{1,40}', charger)
                or environ.get('OCPP_FIXTURE_RESERVED') != 'true'):
            raise Unverified('Ops requires an already registered, exclusively reserved test charger')
        candidate = environ.get('CANDIDATE_POD_HASH', '')
        if not re.fullmatch(r'[a-z0-9]+', candidate):
            raise Unverified('Ops requires the candidate Pod hash')
        return namespace, charger, 'csms-canary.ops.svc.cluster.local', candidate
    raise Unverified('Only namespace dev or ops is allowed')


def db_read(sql, params=()):
    import psycopg
    with psycopg.connect(os.environ['DB_DSN'], connect_timeout=5) as connection:
        connection.execute('SET TRANSACTION READ ONLY')
        connection.execute("SET LOCAL statement_timeout = '5s'")
        return connection.execute(sql, params).fetchone()


def health(host):
    transport = build_opener(ProxyHandler({}))
    with transport.open(Request(f'http://{host}:8000/healthz'), timeout=5) as response:
        if response.status != 200 or json.load(response).get('ok') is not True:
            raise ProbeFailure('Health/DB response failed')


def session_row(charger):
    return db_read('SELECT session_id::text, pod FROM charger_session WHERE charger_id = %s', (charger,))


async def verify(charger, host, candidate, state, connect, read_session=session_row,
                 read_heartbeat=None, sleep=asyncio.sleep, heartbeat_count=7):
    """Two real connections, correlated replies and (Ops) DB session ownership."""
    previous_session = None
    async def call(socket, action, payload):
        identity = uuid.uuid4().hex
        started, outcome = time.monotonic(), 'success'
        try:
            await asyncio.wait_for(socket.send(json.dumps([2, identity, action, payload])), 5)
            reply = json.loads(await asyncio.wait_for(socket.recv(), 5))
            if not isinstance(reply, list) or len(reply) != 3 or reply[:2] != [3, identity] or not isinstance(reply[2], dict):
                raise ProbeFailure('OCPP reply mismatch or CALLERROR')
            if action == 'BootNotification' and reply[2].get('status') != 'Accepted':
                raise ProbeFailure('Boot rejected')
            if action == 'Heartbeat' and not reply[2].get('currentTime'):
                raise ProbeFailure('Heartbeat reply invalid')
        except (asyncio.TimeoutError, TimeoutError):
            outcome = 'timeout'
            raise
        except Exception:
            outcome = 'error'
            raise
        finally:
            state.requests.labels(action, outcome).inc()
            state.latency.labels(action, outcome).observe(time.monotonic() - started)

    for attempt in range(2):
        connection_open = False
        try:
            async with connect(f'ws://{host}:9000/ocpp/{charger}', subprotocols=['ocpp1.6'],
                               additional_headers={'X-Client-CN': charger}, open_timeout=5,
                               close_timeout=3, ping_interval=20, proxy=None) as socket:
                connection_open = True
                state.attempts.labels('success').inc()
                state.connection.set(1)
                started_at = time.time()
                await call(socket, 'BootNotification', {'chargePointVendor': 'ChargeOps-CI', 'chargePointModel': 'ReleaseVerification'})
                row = read_session(charger) if candidate else None
                if candidate and (not row or not row[1].startswith('csms-' + candidate + '-')
                                  or row[0] == previous_session):
                    raise ProbeFailure('Candidate session ownership or reconnect generation mismatch')
                await call(socket, 'StatusNotification', {'connectorId': 1, 'errorCode': 'NoError',
                    'status': 'Available', 'timestamp': datetime.now(timezone.utc).isoformat()})
                count = heartbeat_count if attempt == 0 else 2
                for index in range(count):
                    if index: await sleep(10)
                    await call(socket, 'Heartbeat', {})
                if candidate:
                    if read_session(charger) != row:
                        raise ProbeFailure('Reserved test session replaced unexpectedly')
                    if read_heartbeat is None or read_heartbeat(charger) < started_at:
                        raise ProbeFailure('Heartbeat was not persisted')
                    previous_session = row[0]
        except Exception:
            if not connection_open: state.attempts.labels('error').inc()
            raise
        finally:
            state.connection.set(0)
        if candidate:
            # CSMS finally closes its own session asynchronously after socket close.
            for _ in range(20):
                if read_session(charger) is None: break
                await sleep(0.5)
            else:
                raise ProbeFailure('Reserved test session was not closed')


def heartbeat_timestamp(charger):
    row = db_read('SELECT last_heartbeat_at FROM charger_status_current WHERE charger_id = %s', (charger,))
    return row[0].timestamp() if row and row[0] else 0


def checks(state):
    namespace, charger, host, candidate = configuration(os.environ)
    state.check.labels('session').set(2 if namespace == 'dev' else 0)
    health(host)
    state.check.labels('health').set(1)
    if db_read('SELECT 1') != (1,): raise ProbeFailure('DB read failed')
    if namespace == 'ops':
        row = db_read('SELECT source FROM charger WHERE charger_id = %s AND NOT is_deleted', (charger,))
        if not row or row[0] not in ('model', 'virtual'):
            raise Unverified('Reserved Ops fixture is not registered as model/virtual')
        if session_row(charger) is not None:
            raise Unverified('Reserved Ops fixture already has a session; refusing takeover')
    state.check.labels('database').set(1)
    from websockets.asyncio.client import connect
    asyncio.run(verify(charger, host, candidate, state, connect, read_heartbeat=heartbeat_timestamp))
    state.check.labels('ocpp').set(1)
    if candidate: state.check.labels('session').set(1)
    state.result.set(1)
    print('PASS: release health, DB read, OCPP replies, connection hold and reconnect'
          + ('; candidate session ownership and heartbeat persistence' if candidate else '; Dev memory fixture'), flush=True)


def main():
    from prometheus_client import CollectorRegistry, start_http_server
    registry = CollectorRegistry()
    state = State(registry)
    server, thread = start_http_server(9110, registry=registry)
    code = 0
    try:
        checks(state)
    except Unverified as error:
        state.result.set(3)
        for name in ('health', 'database', 'ocpp', 'session'):
            if state.check.labels(name)._value.get() == 0: state.check.labels(name).set(3)
        print('UNVERIFIED: ' + str(error), flush=True)
        code = 1
    except Exception as error:
        state.result.set(-1)
        # No DB connection strings, message bodies or driver error text in logs.
        for name in ('health', 'database', 'ocpp', 'session'):
            if state.check.labels(name)._value.get() == 0: state.check.labels(name).set(-1)
        print('FAIL: release check (' + (str(error) if isinstance(error, ProbeFailure) else type(error).__name__) + ')', flush=True)
        code = 1
    finally:
        time.sleep(20)  # final result must survive more than one Prometheus scrape
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
