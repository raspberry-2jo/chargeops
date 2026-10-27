import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location('notify_ci', Path(__file__).resolve().parents[1] / 'scripts/notify_ci.py')
notify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(notify)


def test_only_allowlisted_receipt_fields_are_notified(tmp_path):
    (tmp_path / 'candidate.json').write_text(json.dumps({'image': 'registry.chargeops.lab:5000/chargeops/chargeops-csms@sha256:' + 'a' * 64,
                                                      'secret': 'must-not-appear'}))
    (tmp_path / 'production-delivery.json').write_text(json.dumps({'phase': 'recovered', 'password': 'must-not-appear'}))
    text = notify.message({'NOTIFY_EVENT': 'Recovery completed', 'CHECKOUT_BRANCH': 'dev',
                           'SLACK_CICD_TOKEN': 'must-not-appear'}, tmp_path)
    assert 'Recovery completed' in text and 'recovered' in text and 'sha256:' in text
    assert 'must-not-appear' not in text
    assert 'sha256:' not in notify.message({'NOTIFY_EVENT': 'CI started'}, tmp_path)


def test_slack_payload_uses_configured_channel_and_no_token_in_body(monkeypatch, capsys):
    calls = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return b'{"ok":true}'
    class Transport:
        def open(self, request, **kwargs):
            calls.append(request)
            return Response()
    monkeypatch.setattr(notify, 'build_opener', lambda *args: Transport())
    assert notify.send({'SLACK_CICD_TOKEN': 'token-test-only', 'SLACK_CICD_CHANNEL': 'C0CBS4DFPGW',
                        'NOTIFY_EVENT': 'Dev verification passed'})
    payload = json.loads(calls[0].data)
    assert payload['channel'] == 'C0CBS4DFPGW' and payload['mrkdwn'] is False
    assert 'token-test-only' not in calls[0].data.decode()
    assert 'token-test-only' not in capsys.readouterr().out


def test_slack_api_failure_does_not_claim_success(monkeypatch):
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return b'{"ok":false,"error":"channel_not_found"}'
    class Transport:
        def open(self, *a, **k): return Response()
    monkeypatch.setattr(notify, 'build_opener', lambda *args: Transport())
    assert notify.send({'SLACK_CICD_TOKEN': 'test-token', 'SLACK_CICD_CHANNEL': 'C0CBS4DFPGW'}) is False
