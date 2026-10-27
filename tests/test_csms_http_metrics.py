"""Drive the real ASGI middleware, including handled and unhandled errors."""
import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace

from prometheus_client import CollectorRegistry, generate_latest
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('csms_http_metrics', ROOT / 'apps/csms/http_metrics.py')
metrics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(metrics)


def run_request(status=200, error=False, path='/healthz', route='/healthz', method='GET'):
    registry = CollectorRegistry()
    sent = []
    async def app(scope, receive, send):
        scope['route'] = SimpleNamespace(path=route) if route else None
        if error:
            raise RuntimeError('private database exception')
        await send({'type': 'http.response.start', 'status': status, 'headers': []})
        await send({'type': 'http.response.body', 'body': b'ok'})
    wrapped = metrics.HTTPMetricsMiddleware(app, registry=registry, hostname='csms-75ccccccc9-ab123')
    async def receive():
        return {'type': 'http.request', 'body': b'', 'more_body': False}
    async def send(message):
        sent.append(message)
    coroutine = wrapped({'type': 'http', 'method': method, 'path': path}, receive, send)
    if error:
        with pytest.raises(RuntimeError, match='private database'):
            asyncio.run(coroutine)
    else:
        asyncio.run(coroutine)
    return registry, sent


@pytest.mark.parametrize('status,group', [(200, '2xx'), (503, '5xx'), (401, '4xx')])
def test_records_actual_response_and_seeds_zero_error_series(status, group):
    registry, sent = run_request(status)
    labels = {'route': '/healthz', 'method': 'GET', 'revision': '75ccccccc9', 'status_class': group}
    assert registry.get_sample_value('chargeops_csms_http_requests_total', labels) == 1
    assert registry.get_sample_value('chargeops_csms_http_requests_total', dict(labels, status_class='5xx')) == (1 if group == '5xx' else 0)
    assert sent[0]['status'] == status


def test_unhandled_exception_is_counted_but_not_swallowed_or_exposed():
    registry, _ = run_request(error=True)
    assert registry.get_sample_value('chargeops_csms_http_requests_total', {
        'route': '/healthz', 'method': 'GET', 'revision': '75ccccccc9', 'status_class': '5xx'}) == 1
    assert b'private database' not in generate_latest(registry)


def test_normalizes_routes_and_never_exposes_charger_ids_or_unknown_urls():
    registry, _ = run_request(path='/api/chargers/private-charger', route='/api/chargers/{cid}')
    assert b'private-charger' not in generate_latest(registry)
    assert b'/api/chargers/{cid}' in generate_latest(registry)
    registry, _ = run_request(status=404, path='/secret-path', route=None, method='UNTRUSTED')
    text = generate_latest(registry)
    assert b'secret-path' not in text and b'UNTRUSTED' not in text
    assert b'__unmatched__' in text and b'OTHER' in text


def test_websocket_and_lifespan_scopes_are_forwarded_without_http_metrics():
    registry = CollectorRegistry()
    calls = []
    async def app(scope, receive, send):
        calls.append(scope['type'])
    wrapped = metrics.HTTPMetricsMiddleware(app, registry=registry, hostname='local')
    for kind in ['websocket', 'lifespan']:
        asyncio.run(wrapped({'type': kind}, None, None))
    assert calls == ['websocket', 'lifespan']
    assert b'__unmatched__' not in generate_latest(registry)


@pytest.mark.parametrize('hostname,expected', [('csms-bbcf7ddc8-4vn67', 'bbcf7ddc8'), ('local', 'unknown'), ('csms-bad\n-private', 'unknown')])
def test_revision_is_bounded_and_matches_rollouts_latest_hash(hostname, expected):
    assert metrics.revision_from_hostname(hostname) == expected


def test_app_installs_the_middleware():
    import ast
    source = ast.parse((ROOT / 'apps/csms/app.py').read_text(encoding='utf-8'))
    calls = [n for n in ast.walk(source) if isinstance(n, ast.Call)]
    assert any(isinstance(n.func, ast.Attribute) and n.func.attr == 'add_middleware'
               and n.args and isinstance(n.args[0], ast.Name)
               and n.args[0].id == 'HTTPMetricsMiddleware' for n in calls)
