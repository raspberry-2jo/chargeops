"""Cadence/logging changes must not bypass the exact-candidate gates."""
import pytest
from test_env_cd import cd, B
from test_canary_cd import canary


@pytest.mark.parametrize('value,expected', [(None, 5), ('2', 2), ('7.5', 7.5)])
def test_poll_seconds_default_and_override(monkeypatch, value, expected):
    monkeypatch.delenv('CD_POLL_SECONDS', raising=False)
    if value is not None:
        monkeypatch.setenv('CD_POLL_SECONDS', value)
    assert cd.poll_seconds() == expected


@pytest.mark.parametrize('value', ['0', '-1', 'nan', 'inf', 'bad', ''])
def test_invalid_poll_seconds_cannot_spin_or_wait_forever(monkeypatch, value):
    monkeypatch.setenv('CD_POLL_SECONDS', value)
    with pytest.raises(cd.DeliveryPreflightError, match='CD_POLL_SECONDS'):
        cd.poll_seconds()


def fake_verify(monkeypatch, ready_at=50):
    clock = [0.0]
    candidate = {'image': B, 'development_fingerprint': 'expected', 'verification_mode': 'read-only'}
    monkeypatch.setattr(cd, 'DEV', cd.LEGACY_DEV)
    monkeypatch.setattr(cd, 'load', lambda *args: candidate)
    monkeypatch.setattr(cd, 'candidate_check', lambda *args: None)
    monkeypatch.setattr(cd, 'ensure_clean', lambda *args: None)
    monkeypatch.setattr(cd, 'rendered', lambda *args: {})
    monkeypatch.setattr(cd, 'fingerprint', lambda *args: 'expected')
    monkeypatch.setattr(cd.time, 'monotonic', lambda: clock[0])
    sleeps = []
    def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds
    monkeypatch.setattr(cd.time, 'sleep', sleep)
    monkeypatch.setattr(cd, 'dev_rollout_failed', lambda *args: False)
    monkeypatch.setattr(cd, 'live_candidate', lambda *args: clock[0] >= ready_at)
    monkeypatch.setattr(cd, 'verification_job', lambda *args: {'metadata': {'name': 'check'}})
    monkeypatch.setattr(cd, 'run', lambda *args, **kwargs: '')
    monkeypatch.setattr(cd, 'kubectl', lambda *args: {'metadata': {'uid': 'job'}})
    receipts = []
    monkeypatch.setattr(cd, 'save', lambda *args: receipts.append(args))
    return clock, sleeps, receipts


def test_dev_polls_every_five_seconds_but_logs_changes_or_thirty_seconds(monkeypatch):
    monkeypatch.delenv('CD_POLL_SECONDS', raising=False)
    clock, sleeps, receipts = fake_verify(monkeypatch)
    monkeypatch.setattr(cd, 'dev_progress', lambda: {'sync': 'OutOfSync' if clock[0] < 10 else 'Synced', 'health': 'Progressing'})
    logged = []
    monkeypatch.setattr(cd, 'log_dev_progress', lambda *args: logged.append(clock[0]))
    cd.verify('repo', 'candidate', 'receipt', 600)
    assert sleeps == [5] * 10
    assert logged == [0, 10, 40]
    assert receipts


def test_initial_old_synced_healthy_is_not_accepted_by_logging(monkeypatch):
    monkeypatch.setenv('CD_POLL_SECONDS', '2')
    clock, sleeps, receipts = fake_verify(monkeypatch, ready_at=100)
    monkeypatch.setattr(cd, 'dev_progress', lambda: {'sync': 'Synced', 'health': 'Healthy'})
    with pytest.raises(cd.DeliveryPreflightError, match='exact candidate'):
        cd.verify('repo', 'candidate', 'receipt', 6)
    assert sleeps == [2, 2, 2] and not receipts


def test_canary_heartbeat_log_is_wall_time_based_not_six_polls(monkeypatch, capsys):
    monkeypatch.setenv('CD_POLL_SECONDS', '7')
    clock = [0]
    monkeypatch.setattr(canary.time, 'monotonic', lambda: clock[0])
    sleeps = []
    def sleep(seconds):
        sleeps.append(seconds); clock[0] += seconds
    monkeypatch.setattr(canary.time, 'sleep', sleep)
    monkeypatch.setattr(canary, 'check_application', lambda: {})
    monkeypatch.setattr(canary, 'owns_operation', lambda *args: False)
    monkeypatch.setattr(canary, 'rollout_state', lambda *args: ({'status': {'phase': 'Progressing'}}, False))
    monkeypatch.setattr(canary, 'deployment_image', lambda: ({}, B))
    with pytest.raises(cd.DeliveryPreflightError, match='deadline'):
        canary.wait(B, 'c' * 40, 80, 'marker')
    assert sleeps == [7] * 12
    assert capsys.readouterr().out.count('Canary completion checks:') == 3  # t=0,35,70
