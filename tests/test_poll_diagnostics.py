import json
import re
import pytest
from test_env_cd import cd
from test_cd_polling import fake_verify


def test_config_reports_effective_default_or_agent_override(monkeypatch, capsys):
    monkeypatch.delenv('CD_POLL_SECONDS', raising=False)
    monkeypatch.delenv('CD_POLL_DIAGNOSTICS', raising=False)
    cd.PollDiagnostics('test', cd.poll_seconds(), __file__)
    monkeypatch.setenv('CD_POLL_SECONDS', '30')
    cd.PollDiagnostics('test', cd.poll_seconds(), __file__)
    out = capsys.readouterr().out
    assert 'sleep_seconds=5.000 source=default' in out
    assert 'sleep_seconds=30.000 source=environment' in out
    assert re.search(r'entry_sha256=[a-f0-9]{12} observer_sha256=[a-f0-9]{12}', out)


def test_five_second_sleep_plus_twenty_five_second_check_reports_thirty_second_cadence(monkeypatch, capsys):
    clock = [0.0]
    monkeypatch.setattr(cd.time, 'monotonic', lambda: clock[0])
    monkeypatch.setenv('CD_POLL_DIAGNOSTICS', 'true')
    def run(*args, **kwargs):
        clock[0] += {'first': 8, 'second': 8, 'third': 9}[args[1]]
        return json.dumps({})
    monkeypatch.setattr(cd, 'run', run)
    diagnostics = cd.PollDiagnostics('test', 5, __file__)
    for _ in range(2):
        with diagnostics.sample(clock[0]):
            for name in ('first', 'second', 'third'):
                cd.kubectl(name)
        clock[0] += 5  # The existing caller's sleep; not part of check/API duration.
    out = capsys.readouterr().out
    assert 'check_seconds=25.000 start_gap_seconds=30.000 api_calls=3 api_seconds=25.000' in out
    assert out.count('CD poll begin:') == out.count('CD poll sample:') == 2
    assert cd._active_poll.get() is None


def test_default_throttling_reports_intervening_polls_and_terminal_sample(monkeypatch, capsys):
    monkeypatch.delenv('CD_POLL_DIAGNOSTICS', raising=False)
    clock = [0.0]
    monkeypatch.setattr(cd.time, 'monotonic', lambda: clock[0])
    diagnostics = cd.PollDiagnostics('test', 5, __file__)
    for index in range(8):
        with diagnostics.sample(clock[0]) as sample:
            clock[0] += .1
            sample['terminal'] = index == 7
        clock[0] += 5
    out = capsys.readouterr().out
    assert out.count('CD poll sample:') == 3  # first, >=30s, terminal
    assert 'poll=7 window_polls=6' in out
    assert 'start_gap_seconds=5.100' in out
    assert 'poll=8 window_polls=1' in out and 'terminal=true' in out
    assert 'CD poll begin:' not in out


def test_api_failure_is_measured_and_original_error_preserved(monkeypatch, capsys):
    clock = [0.0]
    monkeypatch.setattr(cd.time, 'monotonic', lambda: clock[0])
    original = cd.DeliveryPreflightError('safe test failure')
    def run(*args, **kwargs):
        clock[0] += 25
        raise original
    monkeypatch.setattr(cd, 'run', run)
    diagnostics = cd.PollDiagnostics('test', 5, __file__)
    with pytest.raises(cd.DeliveryPreflightError) as caught:
        with diagnostics.sample(0):
            cd.kubectl('safe-label')
    assert caught.value is original and cd._active_poll.get() is None
    out = capsys.readouterr().out
    assert 'api_calls=1 api_seconds=25.000' in out and 'error=true' in out


def test_verify_loop_records_observed_cadence_without_changing_sleep(monkeypatch, capsys):
    monkeypatch.delenv('CD_POLL_SECONDS', raising=False)
    clock, sleeps, receipts = fake_verify(monkeypatch, ready_at=50)
    def failure_check(*args): clock[0] += 8; return False
    def progress(): clock[0] += 8; return {'sync': 'Synced', 'health': 'Healthy'}
    def ready(*args): clock[0] += 9; return clock[0] >= 50
    monkeypatch.setattr(cd, 'dev_rollout_failed', failure_check)
    monkeypatch.setattr(cd, 'dev_progress', progress)
    monkeypatch.setattr(cd, 'live_candidate', ready)
    cd.verify('repo', 'candidate', 'receipt', 600)
    out = capsys.readouterr().out
    assert 'scope=dev_verify sleep_seconds=5.000' in out
    assert 'check_seconds=25.000 start_gap_seconds=30.000' in out
    assert sleeps == [5] and receipts
