"""PR decisions, retry checkpoints and non-deployment notification semantics."""
import copy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import sys

import pytest

import test_github_cd
import test_notify_ci
from test_ci import bash

sys.modules['notify_ci'] = test_notify_ci.notify
spec = importlib.util.spec_from_file_location('pr_notifications', Path(__file__).resolve().parents[1] / 'scripts/pr_notifications.py')
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)
NOW = datetime(2026, 10, 24, 9, 0, tzinfo=timezone.utc)


def pull():
    return {'number': 42, 'state': 'open', 'closed_at': None, 'merged': False,
            'updated_at': NOW.isoformat(), 'body': 'private-body-never-notify',
            'base': {'ref': 'dev', 'repo': {'full_name': monitor.github.REPOSITORY}},
            'head': {'ref': 'kim_chanju', 'sha': 'a' * 40, 'repo': {'full_name': monitor.github.REPOSITORY}}}


def review(ident=1, state='APPROVED', offset=0):
    return {'id': ident, 'state': state, 'commit_id': 'a' * 40,
            'user': {'login': 'reviewer'}, 'submitted_at': (NOW + timedelta(seconds=offset)).isoformat(),
            'body': 'private-review-never-notify'}


def transport(monkeypatch, pr, reviews):
    calls, messages = [], []
    def pages(path):
        calls.append(('GET', path))
        if '/reviews' in path:
            return copy.deepcopy(reviews)
        if 'state=open' in path:
            return [copy.deepcopy(pr)] if pr['state'] == 'open' else []
        if 'state=closed' in path:
            return [copy.deepcopy(pr)] if pr['state'] == 'closed' else []
        raise AssertionError(path)
    def api(method, path, data=None):
        assert method == 'GET' and path.endswith('/pulls/42') and data is None
        calls.append((method, path))
        return copy.deepcopy(pr)
    monkeypatch.setattr(monitor.github, 'pages', pages)
    monkeypatch.setattr(monitor.github, 'api', api)
    monkeypatch.setattr(monitor.notify_ci, 'send_text', lambda text: messages.append(text) or True)
    return calls, messages


def test_approval_and_request_changes_notify_once_without_deploying(tmp_path, monkeypatch):
    pr = pull()
    calls, messages = transport(monkeypatch, pr, [review(), review(2, 'CHANGES_REQUESTED', 1)])
    path = tmp_path / 'state.json'
    assert monitor.check(path, NOW) == 2
    assert monitor.check(path, NOW + timedelta(minutes=1)) == 0
    assert 'PR 리뷰 승인' in messages[0] and 'PR 수정 요청' in messages[1]
    assert all('private-' not in text for text in messages)
    assert all(method == 'GET' for method, _ in calls)
    assert len(json.loads(path.read_text())['delivered']) == 2


@pytest.mark.parametrize('merged', [False, True])
def test_closed_and_merged_are_distinct_and_include_prior_review(tmp_path, monkeypatch, merged):
    pr = pull()
    pr.update(state='closed', closed_at=(NOW + timedelta(seconds=2)).isoformat(), merged=merged,
              merged_by={'login': 'operator'})
    _, messages = transport(monkeypatch, pr, [review()])
    assert monitor.check(tmp_path / 'state.json', NOW) == 2
    closure = messages[-1]
    assert ('PR Merge 완료' in closure) == merged
    assert ('PR 거부·종료' in closure) != merged
    if merged:
        assert 'operator' in closure and 'Ops 배포 완료 알림은 별도' in closure
    else:
        assert 'Dev를 롤백하지 않습니다' in closure


def test_failed_slack_delivery_is_retried_without_reposting_successful_events(tmp_path, monkeypatch):
    _, messages = transport(monkeypatch, pull(), [review(), review(2, 'CHANGES_REQUESTED', 1)])
    path = tmp_path / 'state.json'
    def send(text):
        messages.append(text)
        return len(messages) != 2
    monkeypatch.setattr(monitor.notify_ci, 'send_text', send)
    with pytest.raises(RuntimeError, match='cursor preserved'):
        monitor.check(path, NOW)
    assert len(json.loads(path.read_text())['delivered']) == 1
    assert monitor.check(path, NOW + timedelta(minutes=1)) == 1
    assert sum('PR 리뷰 승인' in text for text in messages) == 1
    assert sum('PR 수정 요청' in text for text in messages) == 2


def test_old_open_pr_reviews_are_detected_even_without_updated_at_change(tmp_path, monkeypatch):
    pr, reviews = pull(), []
    pr['updated_at'] = (NOW - timedelta(days=7)).isoformat()
    _, messages = transport(monkeypatch, pr, reviews)
    path = tmp_path / 'state.json'
    monitor.check(path, NOW)
    reviews.append(review(offset=300))
    assert monitor.check(path, NOW + timedelta(minutes=5)) == 1
    assert 'PR 리뷰 승인' in messages[0]


def test_closed_event_is_not_lost_after_observer_downtime(tmp_path, monkeypatch):
    pr = pull()
    _, messages = transport(monkeypatch, pr, [])
    path = tmp_path / 'state.json'
    monitor.check(path, NOW)
    pr.update(state='closed', closed_at=(NOW + timedelta(minutes=5)).isoformat(),
              updated_at=(NOW + timedelta(minutes=5)).isoformat())
    assert monitor.check(path, NOW + timedelta(hours=3)) == 1
    assert 'PR 거부·종료' in messages[0]


@pytest.mark.parametrize('case', ['foreign', 'other_source', 'other_base', 'comment', 'old_review'])
def test_unrelated_prs_and_non_decisions_do_not_notify(tmp_path, monkeypatch, case):
    pr, reviews = pull(), [review()]
    if case == 'foreign': pr['head']['repo']['full_name'] = 'someone/fork'
    if case == 'other_source': pr['head']['ref'] = 'someone-else'
    if case == 'other_base': pr['base']['ref'] = 'main'
    if case == 'comment': reviews[0]['state'] = 'COMMENTED'
    if case == 'old_review': reviews[0]['submitted_at'] = (NOW - timedelta(days=1)).isoformat()
    _, messages = transport(monkeypatch, pr, reviews)
    assert monitor.check(tmp_path / 'state.json', NOW) == 0
    assert messages == []


def test_old_review_is_not_presented_as_current_head_approval(monkeypatch):
    pr, reviews = pull(), [review()]
    reviews[0]['commit_id'] = 'b' * 40
    transport(monkeypatch, pr, reviews)
    events = monitor.decisions(pr, NOW - timedelta(minutes=1))
    assert '이전 커밋에 대한 리뷰' in events[0][2]


def test_corrupt_state_stops_without_resending_history(tmp_path, monkeypatch):
    _, messages = transport(monkeypatch, pull(), [review()])
    path = tmp_path / 'state.json'
    path.write_text('{"schema":99}')
    with pytest.raises(ValueError, match='restore'):
        monitor.check(path, NOW)
    assert messages == []


def test_observer_shell_syntax_and_no_deployment_tools(bash):
    import re
    import subprocess
    pipeline = (Path(__file__).resolve().parents[1] / 'Jenkinsfile.pr-notifications').read_text()
    for block in re.findall(r"sh\s*'''(.*?)'''", pipeline, re.DOTALL):
        result = subprocess.run([bash, '-n'], input=block, text=True, capture_output=True)
        assert result.returncode == 0, result.stderr
    assert "cron('* * * * *')" in pipeline
    assert 'disableConcurrentBuilds()' in pipeline
    assert 'kubectl' not in pipeline and 'docker' not in pipeline
