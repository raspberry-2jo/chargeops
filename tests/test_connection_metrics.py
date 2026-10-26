import importlib.util
from pathlib import Path
from prometheus_client import CollectorRegistry

spec = importlib.util.spec_from_file_location('connection_metrics', Path(__file__).resolve().parents[1] / 'apps/csms/connection_metrics.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def test_reconnect_counts_only_seen_ids_and_does_not_label_charger_ids():
    registry = CollectorRegistry()
    now = [0]
    metrics = m.ConnectionMetrics(registry, lambda: now[0], capacity=2)
    metrics.opened('private-id')
    assert registry.get_sample_value('chargeops_csms_reconnect_total', {'kind':'service','scope':'pod_local'}) == 0
    metrics.opened('private-id')
    assert registry.get_sample_value('chargeops_csms_reconnect_total', {'kind':'service','scope':'pod_local'}) == 1
    now[0] = 3601
    metrics.opened('private-id')
    assert registry.get_sample_value('chargeops_csms_reconnect_total', {'kind':'service','scope':'pod_local'}) == 1
    metrics.opened('CI-DEV-CANARY-01', fixture=True)
    metrics.opened('CI-DEV-CANARY-01', fixture=True)
    assert registry.get_sample_value('chargeops_csms_reconnect_total', {'kind':'fixture','scope':'pod_local'}) == 1
    metrics.opened('third')
    assert len(metrics.seen) == 2


def test_intentional_fixture_close_and_server_drain_are_not_abnormal():
    registry = CollectorRegistry()
    metrics = m.ConnectionMetrics(registry)
    assert metrics.closed(1000, fixture=True) == 'normal'
    assert metrics.closed(1012, draining=True) == 'server_shutdown'
    assert metrics.closed(1006) == 'abnormal'
    assert metrics.closed(None, handler_error=True) == 'handler_error'
    assert registry.get_sample_value('chargeops_csms_disconnect_total', {'kind':'service','reason':'abnormal'}) == 1
