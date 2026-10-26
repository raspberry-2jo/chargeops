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


def test_pr_api_contract_retains_merge_commit_provenance(monkeypatch):
    # Reproduce the live schema difference without credentials or network calls.
    class Response:
        def __init__(self, payload): self.payload = payload
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return json.dumps(self.payload).encode()
    class Opener:
        def open(self, request, **kwargs):
            document = pull()
            if request.get_header('X-github-api-version') != '2022-11-28':
                document.pop('merge_commit_sha')
            return Response(document)
    monkeypatch.setenv('GITHUB_PR_TOKEN','test-token')
    monkeypatch.setattr(github,'build_opener',lambda *args:Opener())
    assert github.api('GET','/repos/'+github.REPOSITORY+'/pulls/12')['merge_commit_sha'] == SOURCE


def preview_receipts(tmp_path):
    candidate = {'schema':1,'source':HEAD,'image':B,'verification_mode':'read-only'}
    path = tmp_path/'pr.json'
    cd.save(path,{'number':12,'source':HEAD,'image':B})
    return candidate,path


@pytest.mark.parametrize('change', ['none','merged','closed','head','branch','repo','new_build','receipt','api_error'])
def test_timeout_closes_only_the_owned_unchanged_open_pr(tmp_path,monkeypatch,change):
    from types import SimpleNamespace
    now=[0];calls=[];build='https://jenkins.invalid/job/chargeops-ci/60/'
    monkeypatch.setenv('BUILD_URL',build)
    monkeypatch.setenv('CHECKOUT_BRANCH','kim_chanju')
    monkeypatch.delenv('CHANGE_ID',raising=False)
    monkeypatch.setattr(github,'time',SimpleNamespace(time=lambda:1000,monotonic=lambda:now[0],
        sleep=lambda seconds:now.__setitem__(0,now[0]+seconds)))
    candidate,pr_path=preview_receipts(tmp_path)
    saved=cd.load(pr_path);saved['build_url']=build if change!='receipt' else build+'other'
    cd.save(pr_path,saved)
    cp,rp=tmp_path/'candidate.json',tmp_path/'verification.json'
    cd.save(cp,candidate)
    cd.save(rp,{'candidate':cd.fingerprint(candidate),'image':B,'verified_at':1000})
    pr={'state':'open','merged':False,'body':'<!-- chargeops-preview-build: '+build+' -->',
        'head':{'sha':HEAD,'ref':'kim_chanju','repo':{'full_name':github.REPOSITORY}},
        'base':{'ref':'dev','repo':{'full_name':github.REPOSITORY}}}
    def api(method,path,data=None):
        calls.append((method,path,data))
        # Change only on the final GET, after the deadline is reached.
        if method=='GET' and now[0]>=2:
            if change=='merged':pr['merged']=True
            if change=='closed':pr['state']='closed'
            if change=='head':pr['head']['sha']='f'*40
            if change=='branch':pr['base']['ref']='main'
            if change=='repo':pr['head']['repo']['full_name']='other/repo'
            if change=='new_build':pr['body']='new owner'
            if change=='api_error':raise RuntimeError('not printed')
        if method=='PATCH':
            assert data=={'state':'closed'}
            return {'state':'closed','merged':False}
        return pr
    monkeypatch.setattr(github,'api',api)
    with pytest.raises(cd.DeliveryPreflightError,match='wait timed out'):
        github.wait_for_merge(cp,rp,pr_path,tmp_path/'approval.json',timeout=2,interval=1)
    assert sum(method=='PATCH' for method,_,_ in calls)==(1 if change=='none' else 0)
    assert not (tmp_path/'approval.json').exists()


@pytest.mark.parametrize('same_apps',[True,False])
def test_single_build_binds_reviewed_merge_to_exact_verified_apps_tree(tmp_path,monkeypatch,same_apps):
    fixture_api(monkeypatch)
    original = github.api
    def api(method,path,data=None):
        if '/git/trees/' in path:
            value = 'tree-a' if same_apps or path.endswith(HEAD) else 'tree-b'
            return {'tree':[{'path':'apps','type':'tree','sha':value}]}
        return original(method,path,data)
    monkeypatch.setattr(github,'api',api)
    candidate,path = preview_receipts(tmp_path)
    if same_apps:
        approval = github.approved_candidate(candidate,path)
        assert approval['source'] == SOURCE and approval['candidate_source'] == HEAD
        assert approval['image'] == B
    else:
        with pytest.raises(cd.DeliveryPreflightError,match='build context'):
            github.approved_candidate(candidate,path)


@pytest.mark.parametrize('state',['open','closed','head_changed'])
def test_single_build_does_not_promote_approval_without_merge_or_changed_head(tmp_path,monkeypatch,state):
    from types import SimpleNamespace
    now=[0]
    monkeypatch.setattr(github,'time',SimpleNamespace(time=lambda:1000,monotonic=lambda:now[0],
        sleep=lambda seconds:now.__setitem__(0,now[0]+seconds)))
    candidate,pr_path=preview_receipts(tmp_path)
    candidate_path,receipt_path=tmp_path/'candidate.json',tmp_path/'verification.json'
    cd.save(candidate_path,candidate)
    cd.save(receipt_path,{'candidate':cd.fingerprint(candidate),'image':B,'verified_at':1000})
    monkeypatch.setenv('CHECKOUT_BRANCH','kim_chanju')
    monkeypatch.delenv('CHANGE_ID',raising=False)
    response={'state':'closed' if state=='closed' else 'open','merged':False,
              'head':{'sha':'b'*40 if state=='head_changed' else HEAD}}
    monkeypatch.setattr(github,'api',lambda *a,**k:response)
    with pytest.raises(cd.DeliveryPreflightError):
        github.wait_for_merge(candidate_path,receipt_path,pr_path,tmp_path/'approval.json',timeout=2,interval=1)
    assert not (tmp_path/'approval.json').exists()


def test_single_preview_promotes_same_dev_digest_after_live_pr_revalidation(repo,tmp_path,monkeypatch):
    candidate_path=prepared(repo,tmp_path,monkeypatch,canary=True)
    candidate=cd.load(candidate_path)
    receipt=tmp_path/'receipt.json'
    cd.save(receipt,receipt_for(candidate_path))
    monkeypatch.setenv('CHECKOUT_BRANCH','kim_chanju')
    monkeypatch.delenv('CHANGE_ID',raising=False)
    monkeypatch.setattr(cd,'live_candidate',lambda image:image==B)
    monkeypatch.setattr(cd,'kubectl',lambda *a:{'metadata':{'uid':'test-job-uid'},'status':{'succeeded':1}})
    revalidated=[]
    def approval(value):
        revalidated.append(value)
        return {'number':12,'head':value['source'],'source':SOURCE,'image':value['image'],'approvers':['reviewer']}
    monkeypatch.setattr(github,'approved_candidate',approval)
    delivery=tmp_path/'delivery.json'
    cd.promote(repo,candidate_path,receipt,delivery,canary=True,require_approved_pr=True)
    assert revalidated == [candidate]
    assert cd.csms_image(repo,cd.DEV) == cd.csms_image(repo,cd.PROD) == B
    assert cd.load(delivery)['source_approval']['image'] == B


def test_wait_resumes_only_after_merged_candidate_is_revalidated(tmp_path,monkeypatch):
    candidate,pr_path=preview_receipts(tmp_path)
    candidate_path,receipt_path=tmp_path/'candidate.json',tmp_path/'verification.json'
    cd.save(candidate_path,candidate)
    cd.save(receipt_path,{'candidate':cd.fingerprint(candidate),'image':B,'verified_at':int(github.time.time())})
    monkeypatch.setenv('CHECKOUT_BRANCH','kim_chanju')
    monkeypatch.delenv('CHANGE_ID',raising=False)
    monkeypatch.setattr(github,'api',lambda *a,**k:{'merged':True,'head':{'sha':HEAD}})
    approval={'number':12,'image':B,'approvers':['reviewer']}
    calls=[]
    def revalidate(value,path):
        calls.append((value,path))
        return approval
    monkeypatch.setattr(github,'approved_candidate',revalidate)
    output=tmp_path/'approval.json'
    assert github.wait_for_merge(candidate_path,receipt_path,pr_path,output)==approval
    assert calls==[(candidate,pr_path)] and cd.load(output)==approval


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
    build='https://jenkins.invalid/job/chargeops-ci/61/'
    monkeypatch.setenv('BUILD_URL',build)
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
    assert result['build_url']==build
    assert any('<!-- chargeops-preview-build: '+build+' -->' in (data or {}).get('body','')
               for method,_,data in calls if method in ('PATCH','POST'))
    assert sum(method == 'POST' for method, _, _ in calls) == (0 if existing else 1)
    assert sum(method == 'PATCH' for method, _, _ in calls) == (1 if existing else 0)
    assert cd.csms_image(repo, cd.PROD) == A


def test_preview_cannot_promote_without_reviewed_source_pr(repo, tmp_path, monkeypatch):
    candidate = prepared(repo, tmp_path, monkeypatch)
    receipt = tmp_path / 'receipt.json'
    cd.save(receipt, receipt_for(candidate))
    monkeypatch.setenv('CHECKOUT_BRANCH', 'kim_chanju')
    with pytest.raises(cd.DeliveryPreflightError, match='reviewed'):
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
