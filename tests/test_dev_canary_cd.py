"""Dev Rollout ownership, stale readiness and image-only recovery guards."""
import copy
import importlib.util
import json
from pathlib import Path
import shutil

import pytest

from test_env_cd import repo, A, B

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('dev_canary_cd', ROOT / 'scripts/env_cd.py')
cd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cd)


def runtime():
    deployment = {'metadata': {'generation': 9}, 'spec': {'replicas': 0, 'template': {'spec': {
        'containers': [{'name': 'csms', 'image': B}]}}}}
    rollout = {'metadata': {'uid': 'rollout', 'generation': 4},
        'spec': {'replicas': 2, 'workloadRef': {'name': 'csms', 'scaleDown': 'never'}},
        'status': {'phase': 'Healthy', 'stableRS': 'hash', 'currentPodHash': 'hash',
                   'observedGeneration': '4', 'workloadObservedGeneration': '9',
                   'replicas': 2, 'readyReplicas': 2, 'updatedReplicas': 2, 'availableReplicas': 2}}
    rs = {'metadata': {'uid': 'rs', 'ownerReferences': [{'controller': True, 'uid': 'rollout'}]},
          'spec': {'template': deployment['spec']['template']}}
    pods = [{'metadata': {'uid': str(i), 'ownerReferences': [{'controller': True, 'uid': 'rs'}]},
             'spec': {'containers': [{'name': 'sidecar', 'image': 'other'}, {'name': 'csms', 'image': B}]},
             'status': {'conditions': [{'type': 'Ready', 'status': 'True'}]}} for i in range(2)]
    return deployment, rollout, rs, pods


@pytest.mark.parametrize('fault', ['none', 'stale_rollout', 'stale_workload', 'aborted', 'old_stable',
    'wrong_image', 'wrong_rs_owner', 'wrong_pod_owner', 'unready', 'extra_pod', 'deployment_running', 'fault_enabled'])
def test_exact_rollout_readiness(monkeypatch, fault):
    d, r, rs, pods = copy.deepcopy(runtime())
    if fault == 'stale_rollout': r['status']['observedGeneration'] = '3'
    if fault == 'stale_workload': r['status']['workloadObservedGeneration'] = '8'
    if fault == 'aborted': r['status']['abort'] = True
    if fault == 'old_stable': r['status']['stableRS'] = 'old'
    if fault == 'wrong_image': rs['spec']['template']['spec']['containers'][0]['image'] = A
    if fault == 'wrong_rs_owner': rs['metadata']['ownerReferences'][0]['uid'] = 'deployment'
    if fault == 'wrong_pod_owner': pods[0]['metadata']['ownerReferences'][0]['uid'] = 'old-rs'
    if fault == 'unready': pods[0]['status']['conditions'][0]['status'] = 'False'
    if fault == 'extra_pod': pods.append(copy.deepcopy(pods[0]))
    if fault == 'deployment_running': d['spec']['replicas'] = 1
    if fault == 'fault_enabled': r['metadata']['annotations'] = {'chargeops.io/dev-error-every': '20'}
    def kube(*args):
        assert args[:2] == ('-n', 'dev')
        return {'rollout': r, 'replicaset': rs, 'pods': {'items': pods}}[args[3]]
    monkeypatch.setattr(cd, 'kubectl', kube)
    assert bool(cd.dev_rollout_ready(d, B)) == (fault == 'none')


def make_candidate(repo, tmp_path, monkeypatch):
    shutil.copytree(repo / cd.LEGACY_DEV, repo / cd.CANARY_DEV)
    cd.run('git', 'add', '.', cwd=repo)
    cd.run('git', '-c', 'core.hooksPath=/dev/null', 'commit', '-m', 'add opt-in Dev overlay', cwd=repo)
    cd.run('git', 'push', 'origin', 'main', cwd=repo)
    monkeypatch.setattr(cd, 'DEV', cd.CANARY_DEV)
    monkeypatch.setattr(cd, 'live_candidate', lambda image: image == A)
    monkeypatch.setattr(cd, 'resolve_digest', lambda reference: B)
    path = tmp_path / 'candidate.json'
    cd.prepare(repo, 'build-1-abcdef012345', 'c' * 40, path)
    return path


def test_real_git_recovery_changes_only_dev_image_and_resumes_idempotently(repo, tmp_path, monkeypatch):
    before = cd.production_fingerprint(repo)
    candidate = make_candidate(repo, tmp_path, monkeypatch)
    receipt = tmp_path / 'dev-recovery.json'
    cd.recover_dev(repo, candidate, receipt, timeout=1)
    assert cd.load(receipt)['phase'] == 'recovered'
    assert cd.csms_image(repo, cd.CANARY_DEV) == A
    assert cd.production_fingerprint(repo) == before
    assert cd.run('git', 'diff', '--name-only', 'HEAD~1', 'HEAD', cwd=repo) == cd.CANARY_DEV + '/kustomization.yaml'
    head = cd.run('git', 'rev-parse', 'HEAD', cwd=repo)
    cd.recover_dev(repo, candidate, receipt, timeout=1)
    assert cd.run('git', 'rev-parse', 'HEAD', cwd=repo) == head


def test_newer_dev_image_is_not_overwritten(repo, tmp_path, monkeypatch):
    candidate = make_candidate(repo, tmp_path, monkeypatch)
    newer = cd.IMAGE + '@sha256:' + 'd' * 64
    cd.pin_image(repo, cd.CANARY_DEV, newer)
    cd.commit(repo, cd.CANARY_DEV, 'newer independent Dev delivery')
    with pytest.raises(cd.DeliveryPreflightError, match='newer or unrelated'):
        cd.recover_dev(repo, candidate, tmp_path / 'dev-recovery.json')
    assert cd.csms_image(repo, cd.CANARY_DEV) == newer


def test_overlay_injection_is_rejected_before_git_or_cluster(monkeypatch):
    monkeypatch.setattr(cd, 'DEV', cd.PROD)
    monkeypatch.setattr(cd, 'ensure_clean', lambda *args: pytest.fail('must reject before Git'))
    with pytest.raises(cd.DeliveryPreflightError, match='DEV_GITOPS_OVERLAY'):
        cd.prepare('unused', '', '', 'unused')


def test_pipeline_opt_in_and_dev_only_recovery_credentials():
    pipeline = (ROOT / 'Jenkinsfile').read_text()
    assert "choices: ['auto', 'deployment', 'canary']" in pipeline
    assert 'scripts/env_cd.py dev-overlay' in pipeline
    block = pipeline[pipeline.index("if (env.DEV_CHECK_STATE == 'pending' && env.DEV_GITOPS_OVERLAY"):]
    block = block[:block.index("if (env.DEV_CHECK_STATE == 'pending' && fileExists")]
    assert 'DEV_KUBECONFIG_CREDENTIAL_ID' in block
    assert 'CANARY_KUBECONFIG_CREDENTIAL_ID' not in block
    assert 'scripts/env_cd.py recover-dev' in block


@pytest.mark.parametrize('path,namespace,revision,allowed', [
    ('chargeops-chatops/overlays/development', 'dev', 'main', True),
    ('chargeops-chatops/overlays/development-canary', 'dev', 'main', True),
    ('chargeops-chatops/overlays/canary', 'ops', 'main', False),
    ('chargeops-chatops/overlays/development-canary', 'dev', 'other', False),
])
def test_auto_detection_is_limited_to_main_dev_paths(monkeypatch, path, namespace, revision, allowed):
    a = {'spec': {'destination': {'namespace': namespace}, 'source': {'path': path, 'targetRevision': revision,
        'repoURL': 'https://github.com/raspberry-2jo/chargeops-gitops.git'}}}
    monkeypatch.setattr(cd, 'kubectl', lambda *args: a)
    if allowed:
        assert cd.detect_dev_overlay() == path
    else:
        with pytest.raises(cd.DeliveryPreflightError):
            cd.detect_dev_overlay()


def test_first_canary_pin_creates_override_without_modifying_parent(repo, monkeypatch):
    child = repo / cd.CANARY_DEV
    child.mkdir(parents=True)
    (child / 'kustomization.yaml').write_text('apiVersion: kustomize.config.k8s.io/v1beta1\nkind: Kustomization\nresources: [../development]\n')
    cd.pin_image(repo, cd.CANARY_DEV, B)
    assert cd.csms_image(repo, cd.CANARY_DEV) == B
    assert cd.csms_image(repo, cd.LEGACY_DEV) == A
