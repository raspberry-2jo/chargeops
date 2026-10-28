"""Exercise GitHub's real HTTP error boundary without network or credentials."""
import io
import json
from urllib.error import HTTPError

import pytest
from test_github_cd import github, cd, SOURCE, HEAD, pull


def broken_api(monkeypatch, code, document):
    secret = 'private-token-that-must-not-be-logged'
    monkeypatch.setenv('GITHUB_PR_TOKEN', secret)
    body = json.dumps(document).encode() if not isinstance(document, bytes) else document
    class Broken:
        def open(self, request, **kwargs):
            raise HTTPError(request.full_url, code, secret, {}, io.BytesIO(body))
    monkeypatch.setattr(github, 'build_opener', lambda *args: Broken())


@pytest.mark.parametrize('message,reason,text', [
    ('No commits between dev and kim_chanju', 'no_changes', 'no new commits'),
    ('A pull request already exists for raspberry-2jo:kim_chanju.', 'duplicate_pr', 'already exists'),
    ('The head branch is invalid', 'invalid_head', 'head branch'),
])
def test_422_has_a_useful_fixed_reason_without_echoing_response(monkeypatch, message, reason, text):
    broken_api(monkeypatch, 422, {'message':'Validation Failed','errors':[
        {'resource':'PullRequest','field':'head','code':'custom','message':message},
        {'resource':'private-resource','field':'private-field','code':'private-code',
         'message':'private-token-that-must-not-be-logged','value':'private-value'}]})
    with pytest.raises(cd.DeliveryPreflightError) as caught:
        github.api('POST','/repos/'+github.REPOSITORY+'/pulls',{})
    assert caught.value.status_code == 422
    assert caught.value.reason_code == reason
    assert text in str(caught.value)
    assert 'token permissions' not in str(caught.value)
    assert 'private-' not in str(caught.value)


@pytest.mark.parametrize('code,body,expected', [
    (401, b'private-token-that-must-not-be-logged', 'credential'),
    (403, {'message':'API rate limit exceeded for private-address'}, 'rate limit'),
    (422, b'<html>private-body</html>', 'validation'),
    (422, {'message':'private-message','errors':'private-errors'}, 'validation'),
    (500, {'message':'private-server-details'}, 'service'),
])
def test_other_http_errors_remain_bounded_and_sanitized(monkeypatch, code, body, expected):
    broken_api(monkeypatch,code,body)
    with pytest.raises(cd.DeliveryPreflightError) as caught:
        github.api('POST','/repos/'+github.REPOSITORY+'/pulls',{})
    assert expected in str(caught.value)
    assert 'private-' not in str(caught.value)


def source_setup(monkeypatch):
    monkeypatch.setenv('CHECKOUT_BRANCH','kim_chanju')
    monkeypatch.delenv('CHANGE_ID',raising=False)


def test_pr_creation_race_reuses_exact_open_pr_once(tmp_path,monkeypatch):
    source_setup(monkeypatch)
    listing=[[],[{**pull(),'head':{'sha':SOURCE}}]]
    calls=[]
    def api(method,path,data=None):
        calls.append((method,path))
        if '/git/ref/' in path:return {'object':{'sha':SOURCE}}
        if method=='GET':return listing.pop(0)
        if method=='POST':raise github.GitHubRequestError(422,'duplicate_pr')
        assert method=='PATCH'
        return {**pull(),'head':{'sha':SOURCE}}
    monkeypatch.setattr(github,'api',api)
    result=github.source_pr(SOURCE,tmp_path/'pr.json','verified',image='digest')
    assert result['number']==12
    assert sum(method=='POST' for method,_ in calls)==1
    assert sum(method=='PATCH' for method,_ in calls)==1


def test_no_changes_stops_with_actionable_reason_and_no_approval_receipt(tmp_path,monkeypatch):
    source_setup(monkeypatch)
    def api(method,path,data=None):
        if '/git/ref/' in path:return {'object':{'sha':SOURCE}}
        if '/compare/' in path:return {'ahead_by':0,'status':'behind','files':[]}
        if '/git/trees/' in path:return {'tree':[{'path':'apps','type':'tree','sha':'e'*40}]}
        if method=='GET':return []
        raise github.GitHubRequestError(422,'no_changes')
    monkeypatch.setattr(github,'api',api)
    output=tmp_path/'pr.json'
    with pytest.raises(cd.DeliveryPreflightError,match='dev already contains'):
        github.source_pr(SOURCE,output,'verified')
    assert not output.exists()


def merged_retry_api(monkeypatch, failure=None):
    from test_github_cd import review, B
    merge, dev = 'd'*40, 'e'*40
    pr=pull()
    pr['head'].update(sha=SOURCE,ref='kim_chanju')
    pr['merge_commit_sha']=merge
    calls=[]
    def api(method,path,data=None):
        calls.append((method,path))
        if path.endswith('/git/ref/heads/kim_chanju'):return {'object':{'sha':SOURCE}}
        if path.endswith('/git/ref/heads/dev'):return {'object':{'sha':dev}}
        if '/git/trees/' in path:
            different = failure=='apps_drift' and path.endswith(dev)
            return {'tree':[{'path':'apps','type':'tree','sha':('0' if different else 'f')*40}]}
        if '/compare/dev...' in path:return {'status':'behind','ahead_by':0}
        if '/compare/' in path:return {'status':'diverged' if failure=='diverged' else 'ahead',
                                      'behind_by':1 if failure=='diverged' else 0}
        if '/commits/' in path and '/pulls' in path:return [{'number':12}]
        if '/reviews?' in path:
            return [] if failure=='unreviewed' else [review(commit=HEAD if failure=='stale_review' else SOURCE)]
        if '/permission' in path:return {'permission':'write'}
        if path.endswith('/pulls/12'):
            if failure=='wrong_head':return {**pr,'head':{**pr['head'],'sha':HEAD}}
            return pr
        if method=='GET' and '/pulls?' in path:return []
        if method=='POST':raise github.GitHubRequestError(422,'no_changes')
        pytest.fail('Unexpected mutation or query: '+method+' '+path)
    monkeypatch.setattr(github,'api',api)
    return calls,B


def test_reviewed_merged_pr_can_authorize_fresh_verification_retry(tmp_path,monkeypatch):
    source_setup(monkeypatch)
    calls,image=merged_retry_api(monkeypatch)
    output=tmp_path/'pr.json'
    saved=github.source_pr(SOURCE,output,'fresh Dev verification',image)
    assert saved['reused_merged_pr'] is True and saved['image']==image
    approval=github.approved_candidate({'source':SOURCE,'image':image},output)
    assert approval['number']==12 and approval['approvers']==['reviewer']
    assert not any(method=='PATCH' for method,_ in calls)


@pytest.mark.parametrize('failure',['apps_drift','unreviewed','stale_review','wrong_head','diverged'])
def test_merged_retry_cannot_bypass_app_context_or_review(tmp_path,monkeypatch,failure):
    source_setup(monkeypatch)
    merged_retry_api(monkeypatch,failure)
    output=tmp_path/'pr.json'
    with pytest.raises(cd.DeliveryPreflightError):github.source_pr(SOURCE,output,'verified',image='candidate')
    assert not output.exists()


def test_raced_pr_with_different_head_never_receives_current_receipt(tmp_path,monkeypatch):
    source_setup(monkeypatch)
    listing=[[],[{**pull(),'head':{'sha':HEAD}}]]
    def api(method,path,data=None):
        if '/git/ref/' in path:return {'object':{'sha':SOURCE}}
        if method=='GET':return listing.pop(0)
        if method=='POST':raise github.GitHubRequestError(422,'duplicate_pr')
        pytest.fail('A different PR head must not be updated')
    monkeypatch.setattr(github,'api',api)
    output=tmp_path/'pr.json'
    with pytest.raises(cd.DeliveryPreflightError,match='head'):
        github.source_pr(SOURCE,output,'verified')
    assert not output.exists()
