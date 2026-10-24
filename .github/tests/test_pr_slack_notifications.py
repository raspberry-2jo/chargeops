"""Execute the workflow's exact inline Python with synthetic events and HTTP."""
import copy
import io
import json
from pathlib import Path
import textwrap
import types
import unittest
from contextlib import redirect_stdout
from urllib.error import HTTPError, URLError

WORKFLOW = Path(__file__).resolve().parents[1] / 'workflows/pr-slack-notifications.yml'
TEXT = WORKFLOW.read_text(encoding='utf-8')
CODE = textwrap.dedent(TEXT.split("python3 - <<'PY'\n", 1)[1].rsplit('\n          PY', 1)[0])
app = types.ModuleType('workflow_notification')
exec(compile(CODE, str(WORKFLOW), 'exec'), app.__dict__)


def event(action='submitted', state='approved'):
    return {'repository': {'full_name': app.REPOSITORY}, 'action': action,
            'sender': {'login': 'operator'}, 'pull_request': {
                'number': 38, 'merged': False, 'title': 'private-title-do-not-notify',
                'html_url': 'https://untrusted.invalid/private-value',
                'base': {'ref': 'dev', 'repo': {'full_name': app.REPOSITORY}},
                'head': {'ref': 'any-team-branch', 'sha': 'a' * 40, 'repo': {'full_name': app.REPOSITORY}}},
            'review': {'state': state, 'user': {'login': 'reviewer'}, 'commit_id': 'a' * 40,
                       'body': 'private-review-do-not-notify'}}


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class Transport:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def open(self, request, **kwargs):
        self.calls.append(request)
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return Response(json.dumps(value).encode())


class Notifications(unittest.TestCase):
    def test_approvals_and_change_requests_are_distinct_and_do_not_claim_deployment(self):
        for state, label in [('approved', 'PR 리뷰 승인'), ('changes_requested', 'PR 수정 요청')]:
            with self.subTest(state=state):
                text = app.message('pull_request_review', event(state=state))
                self.assertIn(label, text)
                self.assertIn('reviewer', text)
                self.assertIn('https://github.com/raspberry-2jo/chargeops/pull/38', text)
                self.assertIn('Merge 후 기존 Jenkins 검증', text)
                self.assertNotIn('private-', text)
                self.assertNotIn('untrusted.invalid', text)

    def test_close_is_not_merge(self):
        payload = event(action='closed')
        text = app.message('pull_request_target', payload)
        self.assertIn('PR 거부·종료', text)
        self.assertIn('자동 롤백을 실행하지 않습니다', text)
        payload['pull_request']['merged'] = True
        text = app.message('pull_request_target', payload)
        self.assertIn('PR Merge 완료', text)
        self.assertNotIn('PR 거부', text)

    def test_all_internal_source_branches_targeting_dev_are_supported(self):
        payload = event()
        payload['pull_request']['head']['ref'] = 'someone-else'
        self.assertIsNotNone(app.message('pull_request_review', payload))

    def test_other_bases_forks_comments_and_unrelated_events_are_ignored(self):
        variants = []
        for field in ['base', 'head']:
            payload = event()
            payload['pull_request'][field]['repo']['full_name'] = 'someone/fork'
            variants.append(('pull_request_review', payload))
        payload = event()
        payload['pull_request']['base']['ref'] = 'main'
        variants.extend([('pull_request_review', payload), ('pull_request_review', event(state='commented')),
                         ('pull_request_target', event(action='opened')), ('push', event())])
        for kind, payload in variants:
            with self.subTest(kind=kind, action=payload['action']):
                self.assertIsNone(app.message(kind, payload))

    def test_old_commit_review_is_labelled_as_old(self):
        payload = event()
        payload['review']['commit_id'] = 'b' * 40
        self.assertIn('이전 커밋에 대한 리뷰', app.message('pull_request_review', payload))

    def test_manual_connection_test_does_not_require_a_pr(self):
        text = app.message('workflow_dispatch', {'repository': {'full_name': app.REPOSITORY}})
        self.assertIn('연결 테스트', text)
        self.assertIn('배포는 실행하지 않습니다', text)

    def test_transport_uses_header_and_configured_channel_without_logging_token(self):
        transport = Transport([{'ok': True}])
        output = io.StringIO()
        with redirect_stdout(output):
            app.send('PR test', 'test-token-private', 'C0CBS4DFPGW', opener=transport)
        request = transport.calls[0]
        self.assertEqual(request.full_url, 'https://slack.com/api/chat.postMessage')
        self.assertEqual(request.get_header('Authorization'), 'Bearer test-token-private')
        self.assertEqual(json.loads(request.data)['channel'], 'C0CBS4DFPGW')
        self.assertFalse(json.loads(request.data)['mrkdwn'])
        self.assertNotIn('test-token-private', request.data.decode() + output.getvalue())

    def test_missing_config_and_app_token_stop_without_posting(self):
        for token, channel in [('', 'C0CBS4DFPGW'), ('xapp-test-only', 'C0CBS4DFPGW'), ('test-token', '')]:
            transport = Transport([])
            with self.subTest(channel=channel), self.assertRaises(app.NotificationError):
                app.send('test', token, channel, opener=transport)
            self.assertEqual(transport.calls, [])

    def test_rate_limit_and_connection_failures_are_bounded_and_retried(self):
        for error in [HTTPError('https://slack.com', 429, 'private', {}, io.BytesIO(b'private')),
                      URLError('private')]:
            transport, delays = Transport([error, {'ok': True}]), []
            app.send('test', 'test-token', 'C0CBS4DFPGW', opener=transport, sleep=delays.append)
            self.assertEqual(len(transport.calls), 2)
            self.assertEqual(delays, [2])

    def test_http_and_slack_failures_do_not_leak_response_bodies(self):
        cases = [HTTPError('https://slack.com', 403, 'private-error', {}, io.BytesIO(b'private-body')),
                 {'ok': False, 'error': 'private-upstream-message'},
                 {'ok': False, 'error': 'not_in_channel'}]
        for failure in cases:
            with self.subTest(failure=type(failure).__name__), self.assertRaises(app.NotificationError) as caught:
                app.send('test', 'test-token-private', 'C0CBS4DFPGW', opener=Transport([failure]))
            self.assertNotIn('private', str(caught.exception))

    def test_no_redirect_is_allowed(self):
        self.assertIsNone(app.NoRedirect().redirect_request(None, None, None, None, None, None))


if __name__ == '__main__':
    unittest.main()
