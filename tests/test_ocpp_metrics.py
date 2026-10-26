import asyncio
import importlib.util
import inspect
from pathlib import Path

from prometheus_client import CollectorRegistry
import pytest

spec = importlib.util.spec_from_file_location('ocpp_metrics', Path(__file__).resolve().parents[1] / 'apps/csms/ocpp_metrics.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.mark.parametrize('result', ['success', 'error', 'timeout', 'cancelled'])
def test_results_preserve_response_exception_and_signature(result):
    registry = CollectorRegistry()
    metrics = module.OcppMetrics(registry)
    errors = {'error': ValueError, 'timeout': TimeoutError, 'cancelled': asyncio.CancelledError}
    async def handler(charger_id, value=1):
        if result in errors:
            raise errors[result]('private-payload')
        return {'ok': value}
    wrapped = metrics.observe('Heartbeat')(handler)
    assert inspect.signature(wrapped) == inspect.signature(handler)
    if result == 'success':
        assert asyncio.run(wrapped('private-id', 3)) == {'ok': 3}
    else:
        with pytest.raises(errors[result]):
            asyncio.run(wrapped('private-id'))
    labels = {'action': 'Heartbeat', 'result': result}
    assert registry.get_sample_value('chargeops_ocpp_handler_results_total', labels) == 1
    assert registry.get_sample_value('chargeops_ocpp_request_seconds_count', labels) == 1
    from prometheus_client import generate_latest
    text = generate_latest(registry).decode()
    assert 'private-id' not in text and 'private-payload' not in text
