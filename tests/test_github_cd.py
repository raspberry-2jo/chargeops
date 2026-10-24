"""Exercise review provenance, PR creation and receipt-bound ops promotion."""
import copy
import importlib.util
import json
from pathlib import Path
import sys

import pytest
from test_env_cd import cd, repo, prepared, receipt_for, A, B

sys.modules['env_cd'] = cd
spec = importlib.util.spec_from_file_location('github_cd', Path(__file__).resolve().parents[1] / 'scripts/github_cd.py')
github = importlib.util.module_from_spec(spec)
sys.modules['github_cd'] = github
spec.loader.exec_module(github)
SOURCE, HEAD = 'c' * 40, 'a' * 40


def pull():
    return {'number': 12, 'merged': True, 'merged_at': '2026-10-24T00:00:00Z',
            'merge_commit_sha': SOURCE, 'html_url': 'https://github.com/raspberry-2jo/chargeops/pull/12',
            'user': {'login': 'author'}, 'base': {'ref': 'dev', 'repo': {'full_name': github.REPOSITORY}},
            'head': {'sha': HEAD, 'repo': {'full_name': github.REPOSITORY}}}


def review(state='APPROVED', user='reviewer', commit=HEAD, ident=1, kind='User'):
    return {'id': ident, 'state': state, 'user': {'login': user, 'type': kind}, 'commit_id': commit}


def fixture_api(monkeypatch, pr=None, reviews=None, current=SOURCE, associated=True):
    pr = pull() if pr is None else pr
    reviews = [review()] if reviews is None else reviews
    def api(method, path, data=None):
        assert method == 'GET'
        if '/git/ref/heads/dev' in path: return {'object': {'sha': current}}
        if '/collaborators/' in path: return {'permission': 'write'}
        if '/commits/' in path: return [{'number': 12}] if associated else []
        if '/pulls/12/reviews?' in path: return reviews
        if path.endswith('/pulls/12'): return pr
        raise AssertionError(path)
    monkeypatch.setattr(github, 'api', api)


def test_reviewed_current_dev_merge_is_accepted(monkeypatch):
    fixture_api(monkeypatch, reviews=[review(), review('COMMENTED', ident=2)])
    assert github.approved_merge(SOURCE)['approvers'] == ['reviewer']


def test_read_only_outsider_approval_does_not_authorize_ops(monkeypatch):
    fixture_api(monkeypatch)
    original = github.api
    monkeypatch.setattr(github, 'api', lambda method, path, data=None:
                        {'permission': 'read'} if '/collaborators/' in path else original(method, path, data))
    with pytest.raises(cd.DeliveryPreflightError, match='approved'):
        github.approved_merge(SOURCE)


@pytest.mark.parametrize('case', ['direct', 'old_head', 'self', 'bot', 'stale_review', 'dismissed',
                                  'changes_requested', 'not_merged', 'wrong_base', 'foreign', 'wrong_merge'])
def test_approval_cannot_be_bypassed(monkeypatch, case):
    pr, reviews = pull(), [review()]
    if case == 'self': reviews = [review(user='author')]
    if case == 'bot': reviews = [review(kind='Bot')]
    if case == 'stale_review': reviews = [review(commit='b' * 40)]
    if case == 'dismissed': reviews += [review('DISMISSED', ident=2)]
    if case == 'changes_requested': reviews += [review('CHANGES_REQUESTED', user='other', ident=2)]
    if case == 'not_merged': pr['merged'] = False
    if case == 'wrong_base': pr['base']['ref'] = 'main'
    if case == 'foreign': pr['head']['repo']['full_name'] = 'someone/fork'
    if case == 'wrong_merge': pr['merge_commit_sha'] = 'd' * 40
    fixture_api(monkeypatch, pr, reviews, current='d' * 40 if case == 'old_head' else SOURCE, associated=case != 'direct')
    with pytest.raises(cd.DeliveryPreflightError): github.approved_merge(SOURCE)


@pytest.mark.parametrize('existing', [False, True])
def test_verified_preview_opens_or_reuses_one_pr_without_merging(repo, tmp_path, monkeypatch, existing):
    candidate = prepared(repo, tmp_path, monkeypatch)
    receipt = tmp_path / 'receipt.json'
    cd.save(receipt, receipt_for(candidate))
    monkeypatch.setenv('CHECKOUT_BRANCH', 'kim_chanju')
    monkeypatch.delenv('CHANGE_ID', raising=False)
    monkeypatch.setattr(cd, 'live_candidate', lambda image: image == B)
    pr = pull()
    pr['head']['sha'] = SOURCE
    calls = []
    def api(method, path, data=None):
        calls.append((method, path, data))
        if '/git/ref/heads/kim_chanju' in path: return {'object': {'sha': SOURCE}}
        if method == 'GET': return [pr] if existing else []
        if method == 'PATCH':
            assert existing and path.endswith('/pulls/12')
            assert candidate.read_text() and B in data['body']
            return pr
        assert method == 'POST' and path.endswith('/pulls')
        assert data['head'] == 'kim_chanju' and data['base'] == 'dev'
        return pr
    monkeypatch.setattr(github, 'api', api)
    output = tmp_path / 'pr.json'
    result = github.open_pr(repo, candidate, receipt, output)
    assert cd.load(output) == result
    assert sum(method == 'POST' for method, _, _ in calls) == (0 if existing else 1)
    assert sum(method == 'PATCH' for method, _, _ in calls) == (1 if existing else 0)
    assert cd.csms_image(repo, cd.PROD) == A


def test_preview_never_promotes_even_with_valid_dev_receipt(repo, tmp_path, monkeypatch):
    candidate = prepared(repo, tmp_path, monkeypatch)
    receipt = tmp_path / 'receipt.json'
    cd.save(receipt, receipt_for(candidate))
    monkeypatch.setenv('CHECKOUT_BRANCH', 'kim_chanju')
    with pytest.raises(cd.DeliveryPreflightError, match='reviewed dev merge'):
        cd.promote(repo, candidate, receipt, tmp_path / 'delivery.json', canary=True, require_approved_pr=True)
    assert cd.csms_image(repo, cd.PROD) == A


def test_unapproved_dev_stops_before_writing_ops(repo, tmp_path, monkeypatch):
    candidate = prepared(repo, tmp_path, monkeypatch)
    receipt = tmp_path / 'receipt.json'
    cd.save(receipt, receipt_for(candidate))
    monkeypatch.setenv('CHECKOUT_BRANCH', 'dev')
    monkeypatch.delenv('CHANGE_ID', raising=False)
    fixture_api(monkeypatch, reviews=[])
    with pytest.raises(cd.DeliveryPreflightError, match='approved'):
        cd.promote(repo, candidate, receipt, tmp_path / 'delivery.json', require_approved_pr=True)
    assert cd.csms_image(repo, cd.PROD) == A


def test_reviewed_dev_promotes_the_exact_reverified_digest_and_records_approval(repo, tmp_path, monkeypatch):
    candidate = prepared(repo, tmp_path, monkeypatch, canary=True)
    receipt = tmp_path / 'receipt.json'
    cd.save(receipt, receipt_for(candidate))
    monkeypatch.setenv('CHECKOUT_BRANCH', 'dev')
    monkeypatch.delenv('CHANGE_ID', raising=False)
    fixture_api(monkeypatch)
    monkeypatch.setattr(cd, 'live_candidate', lambda image: image == B)
    monkeypatch.setattr(cd, 'kubectl', lambda *a: {'metadata': {'uid': 'test-job-uid'}, 'status': {'succeeded': 1}})
    path = tmp_path / 'delivery.json'
    cd.promote(repo, candidate, receipt, path, canary=True, require_approved_pr=True)
    delivery = cd.load(path)
    assert delivery['source_approval']['number'] == 12
    assert delivery['phase'] == 'pending' and delivery['image'] == B
    assert delivery['previous_image'] == A
    assert cd.csms_image(repo, cd.PROD) == cd.csms_image(repo, cd.DEV) == B


def test_api_does_not_print_token_or_github_error_body(monkeypatch):
    from urllib.error import HTTPError
    import io
    monkeypatch.setenv('GITHUB_PR_TOKEN', 'private-token-test-only')
    class Broken:
        def open(self, request, **kwargs):
            assert request.get_header('Authorization') == 'Bearer private-token-test-only'
            raise HTTPError(request.full_url, 403, 'private-token-test-only', {}, io.BytesIO(b'private-api-payload'))
    monkeypatch.setattr(github, 'build_opener', lambda *args: Broken())
    with pytest.raises(cd.DeliveryPreflightError) as error:
        github.api('GET', '/repos/' + github.REPOSITORY + '/git/ref/heads/dev')
    assert '403' in str(error.value)
    assert 'private-token' not in str(error.value) and 'private-api' not in str(error.value)


@pytest.mark.parametrize('branch', ['kim_chanju', 'dev'])
def test_environment_delivery_accepts_branch_qualified_build_tags(repo, tmp_path, monkeypatch, branch):
    monkeypatch.setattr(cd, 'resolve_digest', lambda reference: B)
    output = tmp_path / 'branch-candidate.json'
    cd.prepare(repo, 'build-' + branch + '-12-abcdef012345', SOURCE, output)
    assert cd.load(output)['image'] == B


@pytest.mark.parametrize('case', ['untrusted_branch', 'pull_request', 'head_advanced', 'creation_race'])
def test_source_pr_rejects_untrusted_or_unverified_heads(tmp_path, monkeypatch, case):
    monkeypatch.setenv('CHECKOUT_BRANCH', 'dev' if case == 'untrusted_branch' else 'kim_chanju')
    monkeypatch.setenv('CHANGE_ID', '12' if case == 'pull_request' else '')
    calls = []
    def api(method, path, data=None):
        calls.append(method)
        if '/git/ref/' in path:
            return {'object': {'sha': HEAD if case == 'head_advanced' else SOURCE}}
        if method == 'GET': return []
        return {**pull(), 'head': {'sha': HEAD}}
    monkeypatch.setattr(github, 'api', api)
    output = tmp_path / 'pr.json'
    with pytest.raises(cd.DeliveryPreflightError):
        github.source_pr(SOURCE, output, 'CI-only source changes')
    assert not output.exists()
    if case != 'creation_race': assert 'POST' not in calls


def test_ci_only_pr_and_status_do_not_claim_dev_deployment(tmp_path, monkeypatch):
    monkeypatch.setenv('CHECKOUT_BRANCH', 'kim_chanju')
    monkeypatch.delenv('CHANGE_ID', raising=False)
    calls = []
    def api(method, path, data=None):
        calls.append((method, path, data))
        if '/git/ref/' in path: return {'object': {'sha': SOURCE}}
        if method == 'GET': return []
        return {**pull(), 'head': {'sha': SOURCE}}
    monkeypatch.setattr(github, 'api', api)
    result = github.source_pr(SOURCE, tmp_path / 'pr.json', 'CI passed; CSMS unchanged')
    assert 'image' not in result
    github.status(SOURCE, 'success', 'chargeops/ci')
    assert calls[-1][2]['context'] == 'chargeops/ci'
    assert 'Dev' not in calls[-1][2]['description']
