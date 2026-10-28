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


def test_recovery_success_timeout_and_session_replacement():
    r = CollectorRegistry()
    now = [0.]
    g = m.ConnectionMetrics(r, lambda: now[0], recovery_timeout=60)
    labels = {'kind':'service','scope':'pod_local'}
    g.opened('one', session='old')
    g.closed(1006, cid='one', session='old')
    now[0] = 8
    g.opened('one', session='new')
    assert r.get_sample_value('chargeops_csms_recovery_outcomes_total',dict(labels,result='success')) == 1
    assert r.get_sample_value('chargeops_csms_recovery_seconds_sum',labels) == 8
    # A delayed old close must not begin a phantom recovery for the live new session.
    g.closed(1006,cid='one',session='old')
    assert not g.pending
    g.closed(1006,cid='one',session='new')
    now[0] = 69
    g.expire()
    g.opened('one',session='later')
    assert r.get_sample_value('chargeops_csms_recovery_outcomes_total',dict(labels,result='timeout')) == 1
    assert r.get_sample_value('chargeops_csms_recovery_outcomes_total',dict(labels,result='success')) == 1


def test_no_traffic_fixture_drain_eviction_and_cross_pod_are_not_fake_success():
    r = CollectorRegistry()
    g = m.ConnectionMetrics(r, lambda: 0, capacity=1)
    for cid in ('one','two'):
        g.opened(cid)
        g.closed(1006,cid=cid)
    assert r.get_sample_value('chargeops_csms_recovery_discarded_total') == 1
    assert r.get_sample_value('chargeops_csms_recovery_pending') == 1
    g.opened('fixture',fixture=True)
    g.closed(1006,cid='fixture',fixture=True)
    g.opened('drain')
    g.closed(1012,cid='drain',draining=True)
    assert len(g.pending) == 1
    other = m.ConnectionMetrics(CollectorRegistry(), lambda: 0)
    other.opened('one')
    assert not other.pending
    assert 'private-id' not in str(list(r.collect()))


def test_recovery_deadline_is_inclusive_and_histogram_has_no_idle_samples():
    r = CollectorRegistry()
    now = [0]
    g = m.ConnectionMetrics(r,lambda:now[0])
    labels={'kind':'service','scope':'pod_local'}
    assert r.get_sample_value('chargeops_csms_recovery_seconds_count',labels) == 0
    g.opened('one');g.closed(1000,cid='one')
    now[0]=60
    g.opened('one')
    assert r.get_sample_value('chargeops_csms_recovery_seconds_sum',labels) == 60
