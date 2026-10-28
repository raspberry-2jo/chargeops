"""Exercise real notifier handlers without starting its background thread or integrations."""
import ast
import asyncio
import copy
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(params=['apps/notifier/app.py', 'infra/app/apps/notifier/app.py'])
def handler(request):
    path = ROOT / request.param
    tree = ast.parse(path.read_text(encoding='utf-8'))
    names = {'virtual_charger_ids', 'service_alerts', 'severity_for', 'alertmanager', 'poll_anomaly'}
    definitions = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and n.name in names]
    for node in definitions:
        node.decorator_list = []
    def query(sql, args=()):
        if 'SELECT charger_id FROM charger' in sql:
            return [{'charger_id': cid} for cid in args[0] if cid == 'VIRTUAL-01']
        return []
    def query_one(sql, args=()):
        if 'count(*) AS n' in sql:
            return {'n': 10}
        if 'INSERT INTO incident' in sql:
            if args[1] == 'CI-DEV-CANARY-01':
                raise RuntimeError('incident_charger_id_fkey: CI-DEV-CANARY-01')
            return {'incident_id': 123}
        return None
    ns = dict(Request=object, q=Mock(side_effect=query), q1=Mock(side_effect=query_one),
              dispatch=Mock(), log=logging.getLogger('test.notifier'), csms_watch=Mock())
    exec(compile(ast.Module(body=definitions, type_ignores=[]), str(path), 'exec'), ns)
    return ns


def alert(cid=None, status='firing'):
    labels = {'code': 'C-01', 'station_id': 'ST-01', 'alertname': 'ChargerFault'}
    if cid:
        labels['charger_id'] = cid
    return {'status': status, 'labels': labels, 'annotations': {'summary': 'charger fault'}}


def deliver(ns, alerts, status='firing', common=None):
    body = {'commonLabels': common or {'code': 'C-01', 'station_id': 'ST-01'},
            'status': status, 'alerts': alerts}
    before = copy.deepcopy(body)
    result = asyncio.run(ns['alertmanager'](SimpleNamespace(json=AsyncMock(return_value=body))))
    assert body == before
    return result


@pytest.mark.parametrize('cid', ['CI-DEV-CANARY-01', 'CI-DEV-SIM-01', 'CI-OPS-CANARY-001', 'LT-001', 'VIRTUAL-01'])
@pytest.mark.parametrize('status', ['firing', 'resolved'])
def test_virtual_only_webhook_never_reads_or_writes_incidents(handler, cid, status):
    result = deliver(handler, [alert(cid, status)], status)
    assert result == {'skipped': 'virtual charger alerts'}
    handler['q1'].assert_not_called()
    handler['dispatch'].assert_not_called()
    assert all('SELECT charger_id FROM charger' in c.args[0] for c in handler['q'].call_args_list)


def test_mixed_group_keeps_real_charger_and_excludes_virtual_from_severity(handler):
    result = deliver(handler, [alert('CH-01'), alert('VIRTUAL-01'), alert('CI-DEV-CANARY-01'), alert('LT-001')])
    assert result == {'created': 123}
    insert = next(c for c in handler['q1'].call_args_list if 'INSERT INTO incident' in c.args[0])
    params = insert.args[1]
    assert params[1] == 'CH-01'
    assert params[3] == ['CH-01']
    assert params[5] == 'SEV3'
    handler['dispatch'].assert_called_once()


def test_virtual_firing_does_not_close_group_with_real_firing(handler):
    deliver(handler, [alert('CH-01'), alert('VIRTUAL-01')])
    assert not any("SET status='resolved'" in c.args[0] for c in handler['q'].call_args_list)


def test_model_and_infrastructure_alerts_still_create_incidents(handler):
    assert deliver(handler, [alert('CH-01')]) == {'created': 123}
    assert deliver(handler, [alert()]) == {'created': 123}
    assert handler['dispatch'].call_count == 2


def test_common_charger_id_is_filtered_when_not_repeated_on_alert(handler):
    assert deliver(handler, [alert()], common={'code': 'C-01', 'charger_id': 'CI-DEV-CANARY-01'}) == {
        'skipped': 'virtual charger alerts'}
    handler['q1'].assert_not_called()


def test_virtual_anomaly_is_marked_notified_without_incident_or_watch(handler):
    query = handler['q'].side_effect
    handler['q'].side_effect = lambda sql, args=(): (
        [{'charger_id': 'CI-DEV-CANARY-01', 'level': 'CRITICAL', 'anomaly_id': 42}]
        if 'SELECT * FROM anomaly_event' in sql else query(sql, args))
    handler['poll_anomaly']()
    handler['q1'].assert_not_called()
    handler['csms_watch'].assert_not_called()
    assert any('UPDATE anomaly_event SET notified_at' in c.args[0] for c in handler['q'].call_args_list)
