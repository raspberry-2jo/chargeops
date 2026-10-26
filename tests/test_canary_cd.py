"""Real Git recovery plus simulated controller/Argo state transitions."""
import copy
import importlib.util
import json
from pathlib import Path
import sys

import pytest

from test_env_cd import cd, repo, prepared, receipt_for, A, B

sys.modules['env_cd'] = cd
spec = importlib.util.spec_from_file_location('canary_cd', Path(__file__).resolve().parents[1] / 'scripts/canary_cd.py')
canary = importlib.util.module_from_spec(spec)
spec.loader.exec_module(canary)


@pytest.fixture
def promoted(repo, tmp_path, monkeypatch):
    candidate = prepared(repo, tmp_path, monkeypatch, canary=True)
    receipt = tmp_path / 'receipt.json'
    cd.save(receipt, receipt_for(candidate))
    monkeypatch.setattr(cd, 'live_candidate', lambda image: image == B)
    monkeypatch.setattr(cd, 'kubectl', lambda *args: {'metadata': {'uid': 'test-job-uid'}, 'status': {'succeeded': 1}})
    delivery = tmp_path / 'delivery.json'
    cd.promote(repo, candidate, receipt, delivery, canary=True)
    return delivery


def application():
    return {'spec': {'destination': {'namespace': 'ops'}, 'source': {'path': canary.OVERLAY}}}


def test_sync_is_bound_to_revision_and_only_csms(monkeypatch):
    commands = []
    monkeypatch.setattr(canary, 'check_application', application)
    monkeypatch.setattr(cd, 'run', lambda *a, **kw: commands.append(a))
    marker = canary.sync('c' * 40)
    operation = json.loads(commands[0][-1])['operation']
    assert operation['initiatedBy']['username'] == marker
    assert operation['info'] == [{'name': 'chargeops-operation-id', 'value': marker}]
    assert operation['sync'] == {'revision': 'c' * 40, 'prune': False,
                                 'resources': canary.SYNC_RESOURCES}
    workloads=[r for r in canary.SYNC_RESOURCES if r['kind'] in ('Deployment','Rollout','StatefulSet','CronJob')]
    assert workloads == [{'group':'apps','kind':'Deployment','name':'csms'}]
    assert any(r['kind']=='ConfigMap' and r['name']=='csms-ops-service-gate-settings' for r in canary.SYNC_RESOURCES)


@pytest.mark.parametrize('drift', [False, True])
def test_preflight_checks_preserved_live_runtime(repo, tmp_path, monkeypatch, drift):
    candidate = prepared(repo, tmp_path, monkeypatch, canary=True)
    desired = next(d for d in cd.rendered(repo, cd.PROD) if d['kind'] == 'Deployment')
    actual = copy.deepcopy(desired)
    actual['spec']['replicas'] = 0
    if drift: actual['spec']['template']['spec']['containers'][0]['command'] = ['different-runtime']
    monkeypatch.setattr(canary, 'check_application', application)
    monkeypatch.setattr(canary, 'rollout_state', lambda image: ({}, True))
    monkeypatch.setattr(canary, 'deployment_image', lambda: (actual, A))
    if drift:
        with pytest.raises(ValueError, match='Live CSMS runtime'):
            canary.preflight(repo, candidate)
    else:
        canary.preflight(repo, candidate)


def test_changed_canary_policy_cannot_be_approved(repo, tmp_path, monkeypatch):
    candidate = prepared(repo, tmp_path, monkeypatch, canary=True)
    document = cd.load(candidate)
    document['canary_fingerprint'] = 'different-policy'
    cd.save(candidate, document)
    receipt = tmp_path / 'receipt.json'
    cd.save(receipt, receipt_for(candidate))
    with pytest.raises(ValueError, match='Canary policy changed'):
        cd.promote(repo, candidate, receipt, tmp_path / 'delivery.json', canary=True)
    assert cd.csms_image(repo, cd.PROD) == A


def test_concurrent_sync_is_not_overwritten(monkeypatch):
    app = application()
    app['operation'] = {'sync': {}}
    monkeypatch.setattr(canary, 'check_application', lambda: app)
    monkeypatch.setattr(cd, 'run', lambda *a, **kw: pytest.fail('Must not overwrite another sync'))
    with pytest.raises(ValueError, match='concurrent'):
        canary.sync('c' * 40)


def test_failure_aborts_then_restores_git_and_selectively_syncs(repo, promoted, monkeypatch):
    events = []
    monkeypatch.setattr(canary, 'abort_if_current', lambda image: events.append(('abort', image)))
    monkeypatch.setattr(canary, 'check_application', application)
    def sync(revision):
        events.append(('sync', cd.csms_image(repo, cd.PROD)))
        return 'operation-marker'
    monkeypatch.setattr(canary, 'sync', sync)
    attempts = []
    def wait(image, revision, timeout, marker):
        attempts.append(image)
        if image == B:
            raise cd.DeliveryPreflightError('Failed synthetic analysis')
    monkeypatch.setattr(canary, 'wait', wait)
    with pytest.raises(ValueError, match='synthetic analysis'):
        canary.deliver(repo, promoted, 300)
    assert events == [('sync', B), ('abort', B), ('sync', A)]
    assert attempts == [B, A]
    assert cd.csms_image(repo, cd.PROD) == A
    assert cd.csms_image(repo, cd.DEV) == B
    assert cd.load(promoted)['phase'] == 'recovered'
    assert cd.run('git', 'diff', '--name-only', 'HEAD~1', 'HEAD', cwd=repo) == cd.PROD + '/kustomization.yaml'
    # Repeating post-condition recovery does nothing.
    canary.recover(repo, promoted)
    assert len(events) == 3


def test_success_never_rolls_back_in_post_actions(repo, promoted, monkeypatch):
    monkeypatch.setattr(canary, 'sync', lambda revision: 'operation-marker')
    monkeypatch.setattr(canary, 'wait', lambda *a: None)
    monkeypatch.setattr(canary, 'abort_if_current', lambda image: pytest.fail('Successful rollout must not abort'))
    canary.deliver(repo, promoted, 300)
    assert cd.load(promoted)['phase'] == 'succeeded'
    canary.recover(repo, promoted)
    assert cd.csms_image(repo, cd.PROD) == B


def test_interrupted_observer_restores_git(repo, promoted, monkeypatch):
    monkeypatch.setattr(canary, 'sync', lambda revision: 'operation-marker')
    monkeypatch.setattr(canary, 'check_application', application)
    monkeypatch.setattr(canary, 'abort_if_current', lambda image: None)
    def wait(image, *args):
        if image == B: raise KeyboardInterrupt()
    monkeypatch.setattr(canary, 'wait', wait)
    with pytest.raises(KeyboardInterrupt):
        canary.deliver(repo, promoted, 300)
    assert cd.csms_image(repo, cd.PROD) == A
    assert cd.load(promoted)['phase'] == 'recovered'


def test_newer_production_image_is_never_overwritten(repo, promoted, monkeypatch):
    newer = cd.IMAGE + '@sha256:' + 'd' * 64
    cd.pin_image(repo, cd.PROD, newer)
    cd.commit(repo, cd.PROD, 'promote newer verified candidate')
    monkeypatch.setattr(canary, 'abort_if_current', lambda image: None)
    monkeypatch.setattr(canary, 'sync', lambda revision: pytest.fail('Must not Sync stale recovery'))
    with pytest.raises(ValueError, match='newer production image'):
        canary.recover(repo, promoted)
    assert cd.csms_image(repo, cd.PROD) == newer


def test_recovery_preserves_unrelated_changes(repo, promoted, monkeypatch):
    (repo / 'other.md').write_text('concurrent unrelated edit')
    cd.run('git', 'add', 'other.md', cwd=repo)
    cd.run('git', '-c', 'core.hooksPath=/dev/null', 'commit', '-m', 'unrelated edit', cwd=repo)
    cd.run('git', 'push', 'origin', 'main', cwd=repo)
    monkeypatch.setattr(canary, 'abort_if_current', lambda image: None)
    monkeypatch.setattr(canary, 'check_application', application)
    monkeypatch.setattr(canary, 'sync', lambda revision: 'operation-marker')
    monkeypatch.setattr(canary, 'wait', lambda *a: None)
    canary.recover(repo, promoted)
    assert (repo / 'other.md').read_text() == 'concurrent unrelated edit'
    assert cd.csms_image(repo, cd.PROD) == A


def test_abort_does_not_touch_newer_live_candidate(monkeypatch):
    monkeypatch.setattr(canary, 'deployment_image', lambda: ({}, A))
    monkeypatch.setattr(cd, 'run', lambda *a, **kw: pytest.fail('Do not abort a different image'))
    canary.abort_if_current(B)


def test_unreachable_abort_does_not_prevent_git_recovery(repo, promoted, monkeypatch):
    def unavailable(*args): raise RuntimeError('API unavailable')
    monkeypatch.setattr(canary, 'abort_if_current', unavailable)
    monkeypatch.setattr(canary, 'check_application', application)
    monkeypatch.setattr(canary, 'sync', unavailable)
    with pytest.raises(RuntimeError):
        canary.recover(repo, promoted)
    assert cd.csms_image(repo, cd.PROD) == A
    assert cd.load(promoted)['phase'] == 'recovering'


def test_next_build_cannot_erase_an_unfinished_canary_receipt(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cd.save('artifacts/production-delivery.json', {'canary': True, 'phase': 'recovering'})
    with pytest.raises(ValueError, match='preserved'):
        cd.reset_artifacts()
    assert Path('artifacts/production-delivery.json').exists()


def test_old_manual_promotion_is_never_automatically_rolled_back(tmp_path, monkeypatch):
    path = tmp_path / 'delivery.json'
    cd.save(path, {'canary': False, 'phase': 'pending', 'image': B, 'previous_image': A})
    monkeypatch.setattr(canary, 'abort_if_current', lambda image: pytest.fail('Manual promotion is outside Canary recovery'))
    canary.recover(tmp_path, path)
    assert cd.load(path)['phase'] == 'pending'


def test_same_image_recovery_does_not_abort_the_healthy_baseline(repo, promoted, monkeypatch):
    data = cd.load(promoted)
    data['previous_image'] = B
    cd.save(promoted, data)
    monkeypatch.setattr(canary, 'abort_if_current', lambda image: pytest.fail('Identical baseline must not be aborted'))
    monkeypatch.setattr(canary, 'check_application', application)
    monkeypatch.setattr(canary, 'sync', lambda revision: 'operation-marker')
    monkeypatch.setattr(canary, 'wait', lambda *a: None)
    canary.recover(repo, promoted)
    assert cd.csms_image(repo, cd.PROD) == B
    assert cd.load(promoted)['phase'] == 'recovered'


@pytest.mark.parametrize('receipt', [{'canary': False, 'phase': 'pending'}, {'canary': True, 'phase': 'succeeded'}, {'canary': True, 'phase': 'recovered'}])
def test_reset_allows_manual_or_completed_delivery(tmp_path, monkeypatch, receipt):
    monkeypatch.chdir(tmp_path)
    cd.save('artifacts/production-delivery.json', receipt)
    cd.reset_artifacts()
    assert not Path('artifacts/production-delivery.json').exists()


def test_old_success_for_same_revision_cannot_complete_a_new_operation(monkeypatch):
    app = application()
    app['status'] = {'operationState': {'phase': 'Succeeded', 'operation': {
        'sync': {'revision': 'c' * 40}, 'initiatedBy': {'username': 'old-marker'}}}}
    monkeypatch.setattr(canary, 'check_application', lambda: app)
    monkeypatch.setattr(canary, 'rollout_state', lambda image: ({'status': {'phase': 'Healthy'}}, True))
    monkeypatch.setattr(canary, 'deployment_image', lambda: ({}, B))
    ticks = iter([0, 0, 3])
    monkeypatch.setattr(canary.time, 'monotonic', lambda: next(ticks))
    monkeypatch.setattr(canary.time, 'sleep', lambda *a: None)
    with pytest.raises(ValueError, match='deadline'):
        canary.wait(B, 'c' * 40, 2, 'new-marker')


@pytest.mark.parametrize('case', ['same', 'stale', 'wrong_revision', 'conflicting'])
def test_sync_identity_uses_explicit_operation_info_with_rewritten_username(monkeypatch, case):
    app = application()
    info = [{'name': 'chargeops-operation-id', 'value': 'new-marker' if case != 'stale' else 'old-marker'}]
    if case == 'conflicting':
        info.append({'name': 'chargeops-operation-id', 'value': 'other-marker'})
    app['status'] = {'operationState': {'phase': 'Succeeded', 'operation': {
        'sync': {'revision': ('d' if case == 'wrong_revision' else 'c') * 40},
        'initiatedBy': {'username': 'admin'}, 'info': info}}}
    monkeypatch.setattr(canary, 'check_application', lambda: app)
    monkeypatch.setattr(canary, 'rollout_state', lambda image: ({'status': {'phase': 'Healthy'}}, True))
    monkeypatch.setattr(canary, 'deployment_image', lambda: ({}, B))
    ticks = iter([0, 0, 3])
    monkeypatch.setattr(canary.time, 'monotonic', lambda: next(ticks))
    monkeypatch.setattr(canary.time, 'sleep', lambda *a: None)
    if case == 'same':
        canary.wait(B, 'c' * 40, 2, 'new-marker')
    else:
        with pytest.raises(ValueError, match='deadline'):
            canary.wait(B, 'c' * 40, 2, 'new-marker')


@pytest.mark.parametrize('failure', ['old_generation', 'wrong_image', 'old_stable_rs', 'available_short', 'deployment_active', 'wrong_owner'])
def test_rollout_cannot_report_false_success(monkeypatch, failure):
    rollout = {'metadata': {'uid': 'rollout-uid', 'generation': 2}, 'status': {
        'phase': 'Healthy', 'observedGeneration': '2', 'stableRS': 'hash', 'currentPodHash': 'hash', 'availableReplicas': 4}}
    deployment = {'spec': {'replicas': 0}}
    image = B
    rs = {'metadata': {'labels': {'rollouts-pod-template-hash': 'hash'},
                       'ownerReferences': [{'uid': 'rollout-uid', 'controller': True}]},
          'status': {'availableReplicas': 4}, 'spec': {'template': {'spec': {'containers': [{'name': 'csms', 'image': B}]}}}}
    if failure == 'old_generation': rollout['status']['observedGeneration'] = '1'
    if failure == 'wrong_image': rs['spec']['template']['spec']['containers'][0]['image'] = A
    if failure == 'old_stable_rs': rollout['status']['stableRS'] = 'old-hash'
    if failure == 'available_short': rollout['status']['availableReplicas'] = 3
    if failure == 'deployment_active': deployment['spec']['replicas'] = 4
    if failure == 'wrong_owner': rs['metadata']['ownerReferences'][0]['uid'] = 'different-rollout'
    monkeypatch.setattr(canary, 'deployment_image', lambda: (deployment, image))
    monkeypatch.setattr(cd, 'kubectl', lambda *a: rollout if 'rollout' in a else {'items': [rs]})
    assert canary.rollout_state(B)[1] is False
@pytest.mark.parametrize('automated,allowed', [(None, True), ({'enabled': False}, True), ({}, False), ({'enabled': True}, False)])
def test_ops_application_requires_explicit_manual_sync(monkeypatch, automated, allowed):
    app = {'spec': {'destination': {'namespace': 'ops'}, 'source': {'path': canary.OVERLAY},
                    'syncPolicy': {'automated': automated}}}
    monkeypatch.setattr(canary.cd, 'kubectl', lambda *args: app)
    if allowed:
        assert canary.check_application() == app
    else:
        with pytest.raises(canary.cd.DeliveryPreflightError):
            canary.check_application()
