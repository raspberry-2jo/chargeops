"""Exercise the actual connection and handlers with a DB that rejects every call."""
import ast
import asyncio
import importlib.util
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import time
import uuid
from datetime import datetime, timezone

import pytest

ROOT = Path(__file__).resolve().parents[1] / 'apps/csms'
spec = importlib.util.spec_from_file_location('dev_simulation', ROOT / 'dev_simulation.py')
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


def test_simulation_disabled_by_default_and_fails_closed_outside_dev():
    assert policy.simulation_ids({}) == frozenset()
    env = {'DEV_SIMULATION_ENABLED': 'true', 'ENVIRONMENT': 'dev', 'POD_NAMESPACE': 'dev',
           'DEV_SIMULATION_IDS': 'CI-DEV-SIM-01,CI-DEV-CANARY-01'}
    assert policy.simulation_ids(env) == {'CI-DEV-SIM-01', 'CI-DEV-CANARY-01'}
    for update in ({'ENVIRONMENT': 'ops'}, {'POD_NAMESPACE': 'ops'}, {'POD_NAMESPACE': ''},
                   {'DEV_SIMULATION_IDS': 'CH-01'}, {'DEV_SIMULATION_IDS': ''}):
        with pytest.raises(ValueError): policy.simulation_ids(dict(env, **update))


def app_handlers():
    # Import the actual handler definitions without starting servers or requiring a lab DB.
    tree = ast.parse((ROOT / 'app.py').read_text(encoding='utf-8'))
    selected = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and node.name in {'is_simulated', 'ChargePoint', 'record_status', 'load_charger', 'on_connect'}]
    for node in selected:
        if isinstance(node, ast.ClassDef):
            for method in node.body:
                if isinstance(method, ast.AsyncFunctionDef): method.decorator_list = []
    class CP:
        def __init__(self, cid, ws, **kwargs): self.id = cid
        async def start(self):
            await self.on_boot('Dev', 'Simulator')
            await self.on_heartbeat()
            await self.on_status(1, 'NoError', 'Available')
            await self.on_meter(1, [{'timestamp': '2026-10-26T00:00:00Z',
                'sampled_value': [{'measurand': 'Current.Import', 'value': '0'},
                                  {'measurand': 'Voltage', 'value': '221.5'},
                                  {'measurand': 'Energy.Active.Import.Register', 'value': '123.4'}]}])
    ns = dict(DEV_SIMULATION_IDS={'CI-DEV-SIM-01'}, OcppChargePoint=CP,
              call_result=SimpleNamespace(**{name: lambda **kw: kw for name in
                  ['BootNotification', 'Heartbeat', 'StatusNotification', 'MeterValues']}),
              db=AsyncMock(side_effect=AssertionError('unexpected DB access')), state={}, connected={},
              connection_metrics=MagicMock(), draining=False,
              OCPP_CODE={'Available': '2'}, REQUIRE_CN=False, HEARTBEAT=30, POD='dev-pod', POD_IP='127.0.0.1',
              time=time, uuid=uuid, log=logging.getLogger('test'), now=lambda: datetime.now(timezone.utc))
    for name in ['c_msg', 'h_ocpp', 'g_hb', 'g_cur', 'g_temp', 'g_volt', 'g_energy', 'g_faulted', 'g_charging',
                 'c_fault', 'c_conn', 'g_connected', 'g_rated']:
        ns[name] = MagicMock()
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(ROOT / 'app.py'), 'exec'), ns)
    return ns


def test_allowlisted_charger_full_lifecycle_never_accesses_db():
    ns = app_handlers()
    ws = SimpleNamespace(request=SimpleNamespace(path='/ocpp/CI-DEV-SIM-01', headers={}), close=AsyncMock())
    asyncio.run(ns['on_connect'](ws))
    ns['db'].assert_not_awaited()
    ws.close.assert_not_awaited()
    assert ns['connected'] == {}
    assert ns['state']['CI-DEV-SIM-01']['status'] == 'Offline'
    ns['g_connected'].labels.return_value.set.assert_any_call(1)
    ns['g_connected'].labels.return_value.set.assert_any_call(0)
    assert ns['c_msg'].labels.call_count == 4
    ns['g_volt'].labels.return_value.set.assert_called_once_with(221.5)
    ns['g_energy'].labels.return_value.set.assert_called_once_with(123.4)


def test_other_chargers_still_require_registration_and_persist_data():
    ns = app_handlers()
    ns['db'].side_effect = None
    ns['db'].return_value = None
    ws = SimpleNamespace(request=SimpleNamespace(path='/ocpp/CI-DEV-UNKNOWN', headers={}), close=AsyncMock())
    asyncio.run(ns['on_connect'](ws))
    ws.close.assert_awaited_once_with(1008, 'unknown charger')
    ns['db'].reset_mock()
    ns['db'].side_effect = [('station', 32, 'virtual', '2'), None, None, (True,), None, (True,)]
    ws.request.path = '/ocpp/CH-01'
    asyncio.run(ns['on_connect'](ws))
    sql = [call.args[0] for call in ns['db'].await_args_list]
    assert len(sql) == 6
    assert any('fn_session_open' in query for query in sql)
    assert any('UPDATE charger_status_current' in query for query in sql)
    assert any('fn_record_status' in query for query in sql)
    assert any('INSERT INTO meter_sample' in query for query in sql)
    assert any('fn_session_close' in query for query in sql)
