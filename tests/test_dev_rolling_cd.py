import copy,shutil
import pytest,yaml
from test_env_cd import cd,repo,A,B,receipt_for


@pytest.mark.parametrize('fault',['none','old_image','rollout_owned','not_ready','extra_old_pod','stale_generation','wrong_strategy'])
def test_rolling_readiness_requires_exact_deployment_owned_pods(monkeypatch,fault):
    monkeypatch.setattr(cd,'DEV',cd.ROLLING_DEV)
    deployment={'metadata':{'generation':4,'uid':'deployment'},'spec':{'replicas':2,'strategy':{'type':'RollingUpdate'},
        'template':{'spec':{'containers':[{'name':'csms','image':B}]}}},
        'status':{'observedGeneration':4,'replicas':2,'updatedReplicas':2,'readyReplicas':2,'availableReplicas':2}}
    rs={'metadata':{'uid':'rs','ownerReferences':[{'controller':True,'uid':'deployment'}]}}
    pods=[{'metadata':{'ownerReferences':[{'controller':True,'uid':'rs'}]},
        'spec':{'containers':[{'name':'sidecar','image':'other'},{'name':'csms','image':B}]},
        'status':{'conditions':[{'type':'Ready','status':'True'}]}} for _ in range(2)]
    if fault=='old_image':pods[0]['spec']['containers'][1]['image']=A
    if fault=='rollout_owned':rs['metadata']['ownerReferences'][0]['uid']='rollout'
    if fault=='not_ready':pods[0]['status']['conditions'][0]['status']='False'
    if fault=='extra_old_pod':pods.append(copy.deepcopy(pods[0]))
    if fault=='stale_generation':deployment['status']['observedGeneration']=3
    if fault=='wrong_strategy':deployment['spec']['strategy']['type']='Recreate'
    app={'spec':{'destination':{'namespace':'dev'},'source':{'path':cd.ROLLING_DEV}},'status':{'sync':{'status':'Synced'},'health':{'status':'Healthy'}}}
    def kube(*args):
        kind=args[3]
        return {'application':app,'deployment':deployment,'replicasets':{'items':[rs]},'pods':{'items':pods}}[kind]
    monkeypatch.setattr(cd,'kubectl',kube)
    assert cd.live_candidate(B)==(fault=='none')


def test_rolling_progress_failure_only_applies_to_this_candidate(monkeypatch):
    monkeypatch.setattr(cd,'DEV',cd.ROLLING_DEV)
    deployment={'spec':{'template':{'spec':{'containers':[{'name':'csms','image':B}]}}},
        'status':{'conditions':[{'type':'Progressing','status':'False','reason':'ProgressDeadlineExceeded'}]}}
    monkeypatch.setattr(cd,'kubectl',lambda *a:deployment)
    assert cd.dev_rollout_failed(B) and not cd.dev_rollout_failed(A)


def test_rolling_failure_restores_only_previous_dev_digest_with_real_git(repo,tmp_path,monkeypatch):
    shutil.copytree(repo/cd.LEGACY_DEV,repo/cd.ROLLING_DEV)
    cd.run('git','add','.',cwd=repo)
    cd.run('git','-c','core.hooksPath=/dev/null','commit','-m','rolling fixture',cwd=repo)
    cd.run('git','push','origin','main',cwd=repo)
    monkeypatch.setattr(cd,'DEV',cd.ROLLING_DEV)
    monkeypatch.setattr(cd,'live_candidate',lambda image:image==A)
    monkeypatch.setattr(cd,'resolve_digest',lambda tag:B)
    candidate=tmp_path/'candidate.json'
    cd.prepare(repo,'build-1-abcdef012345','c'*40,candidate)
    document=cd.load(candidate)
    assert document['development_mode']=='rolling' and document['verification_mode']=='ocpp-simulation'
    job=cd.verification_job(document)
    container=job['spec']['template']['spec']['containers'][0]
    assert container['command']==['python','-m','csms.release_probe']
    assert job['spec']['template']['metadata']['labels']['chargeops.io/role']=='dev-release-probe'
    production=cd.production_fingerprint(repo)
    cd.recover_dev(repo,candidate,tmp_path/'recovery.json',timeout=1)
    assert cd.csms_image(repo,cd.ROLLING_DEV)==A and cd.production_fingerprint(repo)==production
