import re
import pytest
from test_env_cd import cd, B
from test_cd_polling import fake_verify


def scenario(monkeypatch):
    monkeypatch.delenv('CD_POLL_SECONDS', raising=False)
    clock, sleeps, receipts = fake_verify(monkeypatch, ready_at=20)
    candidate = {'image': B, 'development_fingerprint': 'expected', 'verification_mode': 'read-only',
                 'development_revision': 'b' * 40, 'development_committed_at': '2026-11-01T00:00:00+00:00'}
    monkeypatch.setattr(cd, 'load', lambda *args: candidate)
    def progress():
        t = clock[0]
        return {'sync': 'OutOfSync' if 5 <= t < 10 else 'Synced',
                'health': 'Healthy' if t < 5 or t >= 20 else 'Progressing',
                'revision': ('a' if t < 5 else 'b') * 40}
    monkeypatch.setattr(cd, 'dev_progress', progress)
    return clock, receipts


def test_milestones_split_rollout_wait_and_integration_job(monkeypatch, capsys):
    clock, receipts = scenario(monkeypatch)
    def run(*args, **kwargs):
        if 'wait' in args: clock[0] += 7
        return 'PASS' if 'logs' in args else ''
    monkeypatch.setattr(cd, 'run', run)
    cd.verify('repo', 'candidate', 'receipt', 600)
    out = capsys.readouterr().out
    events = [line for line in out.splitlines() if 'Dev timing:' in line]
    assert any('elapsed=5.000s event=argo_detected' in line for line in events)
    assert any('elapsed=10.000s event=synced' in line for line in events)
    assert any('elapsed=20.000s event=healthy' in line for line in events)
    assert any('elapsed=20.000s event=job_created' in line for line in events)
    assert any('elapsed=27.000s event=job_completed job=check outcome=success' in line for line in events)
    assert all(re.search(r'at=\d{4}-\d{2}-\d{2}T.*[+]00:00', line) for line in events)
    assert 'detect=5.000s sync=5.000s healthy=10.000s job=7.000s total=27.000s outcome=success' in out
    assert receipts


@pytest.mark.parametrize('terminal', [True, False])
def test_failed_job_logs_without_changing_original_exception(monkeypatch, capsys, terminal):
    clock, receipts = scenario(monkeypatch)
    original = RuntimeError('original wait failure')
    def run(*args, **kwargs):
        if 'wait' in args:
            clock[0] += 7
            raise original
        return ''
    monkeypatch.setattr(cd, 'run', run)
    monkeypatch.setattr(cd, 'kubectl', lambda *args: {'status': {'conditions': [{'type': 'Failed', 'status': 'True'}] if terminal else []}})
    with pytest.raises(RuntimeError) as caught:
        cd.verify('repo', 'candidate', 'receipt', 600)
    assert caught.value is original and not receipts
    out = capsys.readouterr().out
    expected = 'event=job_completed job=check outcome=failed' if terminal else 'event=job_wait_ended job=check outcome=wait_failed_or_timeout'
    assert expected in out and 'job=7.000s total=27.000s outcome=failed' in out


def test_old_healthy_never_becomes_a_candidate_milestone(monkeypatch, capsys):
    fake_verify(monkeypatch, ready_at=100)
    monkeypatch.setattr(cd, 'dev_progress', lambda: {'sync': 'Synced', 'health': 'Healthy', 'revision': 'a' * 40})
    with pytest.raises(cd.DeliveryPreflightError, match='exact candidate'):
        cd.verify('repo', 'candidate', 'receipt', 10)
    out = capsys.readouterr().out
    assert 'event=synced' not in out and 'event=healthy' not in out and 'event=job_created' not in out
    assert 'detect=NA sync=NA healthy=NA job=NA' in out


def test_commit_timestamp_is_relative_to_verify_start_and_old_candidates_work(monkeypatch, capsys):
    monkeypatch.setattr(cd.time, 'monotonic', lambda: 10)
    monkeypatch.setattr(cd.time, 'time', lambda: 100)
    t = cd.DevVerificationTiming()
    t.commit({'development_revision': 'b' * 40, 'development_committed_at': cd.iso_time(95)})
    assert t.events['git_commit_completed'] == -5
    t.commit({'development_revision': None})  # Optional telemetry never invalidates an old artifact.
    out = capsys.readouterr().out
    assert 'elapsed=-5.000s event=git_commit_completed' in out
    assert 'elapsed=NA event=git_commit_completed timestamp=unavailable' in out
