"""Run inside an isolated DEV Job; uses a pre-existing reserved charger fixture."""
import asyncio
from datetime import datetime, timezone
import json
import os
import sys

import psycopg
import requests
from websockets.asyncio.client import connect


async def ocpp(charger, started):
    url = f'ws://csms.dev.svc.cluster.local:9000/ocpp/{charger}'
    for attempt in range(2):
        async with connect(url, subprotocols=['ocpp1.6'], open_timeout=10) as socket:
            for action, data in [
                ('BootNotification', {'chargePointVendor': 'ChargeOps-CI', 'chargePointModel': 'DevVerification'}),
                ('Heartbeat', {}),
                ('StatusNotification', {'connectorId': 1, 'errorCode': 'NoError', 'status': 'Available',
                                        'timestamp': datetime.now(timezone.utc).isoformat()}),
            ]:
                request_id = f'ci-{attempt}-{action}'
                await socket.send(json.dumps([2, request_id, action, data]))
                reply = json.loads(await asyncio.wait_for(socket.recv(), timeout=15))
                if reply[0] != 3 or reply[1] != request_id:
                    raise RuntimeError('OCPP call failed')
                if action == 'BootNotification' and reply[2].get('status') != 'Accepted':
                    raise RuntimeError('Boot notification rejected')
        await asyncio.sleep(1)
    with psycopg.connect(os.environ['DB_DSN'], connect_timeout=10) as conn:
        row = conn.execute('SELECT last_heartbeat_at FROM charger_status_current WHERE charger_id = %s',
                           (charger,)).fetchone()
        if not row or not row[0] or row[0] < started:
            raise RuntimeError('OCPP heartbeat was not persisted')


def main():
    if os.environ.get('ENVIRONMENT') != 'dev':
        raise RuntimeError('Only dev is supported')
    response = requests.get('http://csms.dev.svc.cluster.local:8000/healthz', timeout=10)
    response.raise_for_status()
    if response.json().get('ok') is not True:
        raise RuntimeError('CSMS DB health check failed')
    print('PASS: API health and application DB connectivity', flush=True)
    with psycopg.connect(os.environ['DB_DSN'], connect_timeout=10) as conn:
        conn.execute('SET TRANSACTION READ ONLY')
        if conn.execute('SELECT 1').fetchone() != (1,):
            raise RuntimeError('DB read check failed')
        if not conn.execute('SELECT code FROM code_status WHERE ocpp_status IS NOT NULL LIMIT 1').fetchone():
            raise RuntimeError('CSMS reference data is missing')
    print('PASS: direct DB read-only transaction and CSMS reference data', flush=True)
    if os.environ.get('VERIFY_OCPP') != 'true':
        print('SKIP: OCPP writes disabled because DEV shares the existing database', flush=True)
        return
    charger = os.environ['TEST_CHARGER_ID']
    if not charger.startswith('CI-DEV-'):
        raise RuntimeError('A reserved CI-DEV charger fixture is required')
    with psycopg.connect(os.environ['DB_DSN'], connect_timeout=10) as conn:
        row = conn.execute("SELECT source FROM charger WHERE charger_id = %s AND NOT is_deleted", (charger,)).fetchone()
        if not row or row[0] not in ('virtual', 'model'):
            raise RuntimeError('Reserved development fixture is missing')
    asyncio.run(ocpp(charger, datetime.now(timezone.utc)))
    print('PASS: OCPP boot, heartbeat, status, reconnection and DB persistence', flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('FAIL: development integration check; inspect fixture, connectivity and CSMS logs', flush=True)
        sys.exit(1)
