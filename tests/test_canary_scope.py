import copy
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import pytest

from test_application_delivery import cd, repo, apps, A, B, N
from test_selective_delivery import run_existing_verification
from test_canary_cd import canary

ROOT = Path(__file__).resolve().parents[1]
sys.modules['env_cd'] = cd
sys.modules['canary_cd'] = canary
spec = importlib.util.spec_from_file_location('ops_delivery', ROOT / 'scripts/ops_delivery.py')
regular = importlib.util.module_from_spec(spec); spec.loader.exec_module(regular)


def ops_app():
    return {'spec': {'destination': {'namespace': 'ops'}, 'source': {
        'path': cd.CANARY, 'targetRevision': 'main',
        'repoURL': 'https://github.com/raspberry-2jo/chargeops-gitops.git'}}}


@pytest.mark.parametrize('selection,expected', [('chargeops-csms', True), ('chargeops-notifier', False)])
def test_requested_canary_only_authorizes_selected_csms(apps, tmp_path, monkeypatch, selection, expected):
    monkeypatch.setattr(cd, 'resolve_digest', lambda ref: B if ref.startswith(cd.IMAGE + ':') else N)
    path, receipt, delivery = (tmp_path / name for name in ('candidate.json', 'receipt.json', 'delivery.json'))
    cd.prepare(apps, 'build-86-abcdef012345', 'c' * 40, path, canary=True, build_images=selection)
    assert ('canary_fingerprint' in cd.load(path)) is expected
    run_existing_verification(apps, path, receipt, monkeypatch)
    cd.promote(apps, path, receipt, delivery, canary=True)
    value = cd.load(delivery)
    assert value['canary'] is expected and value['phase'] == 'pending'
    if expected:
        monkeypatch.setattr(canary, 'sync', lambda *a: 'marker')
        calls = []
        monkeypatch.setattr(canary, 'wait', lambda *a: calls.append(a))
        canary.deliver(apps, delivery, 1200)
        assert calls and cd.load(delivery)['phase'] == 'succeeded'
    else:
        assert all(item['name'] != 'csms' for item in value['workloads'])
        saved = delivery.read_bytes()
        monkeypatch.setattr(canary, 'sync', lambda *a: pytest.fail('Canary must not Sync'))
        canary.deliver(apps, delivery, 1200)
        canary.recover(apps, delivery)
        assert delivery.read_bytes() == saved


def test_notifier_delivery_does_not_promote_dev_csms_anchor(apps, tmp_path, monkeypatch):
    cd.pin_image(apps, cd.PROD, B)
    cd.commit(apps, cd.PROD, 'distinct Ops CSMS baseline')
    monkeypatch.setattr(cd, 'resolve_digest', lambda ref: N)
    path, receipt, delivery = (tmp_path / name for name in ('candidate.json', 'receipt.json', 'delivery.json'))
    cd.prepare(apps, 'build-86-abcdef012345', 'c' * 40, path, canary=True, build_images='chargeops-notifier')
    run_existing_verification(apps, path, receipt, monkeypatch)
    cd.promote(apps, path, receipt, delivery, canary=True)
    assert cd.csms_image(apps, cd.DEV) == A and cd.csms_image(apps, cd.PROD) == B
    assert cd.IMAGE not in cd.load(delivery)['images']


def test_zero_images_preserve_delivery_bytes_through_reset_promote_and_canary(apps, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    delivery = tmp_path / 'artifacts/production-delivery.json'
    cd.save(delivery, {'canary': True, 'phase': 'succeeded', 'history': 'prior build'})
    saved = delivery.read_bytes()
    cd.reset_artifacts(preserve_delivery=True)
    assert delivery.read_bytes() == saved
    path, receipt = tmp_path / 'candidate.json', tmp_path / 'receipt.json'
    cd.prepare(apps, 'build-86-abcdef012345', 'c' * 40, path, canary=True, build_images='')
    run_existing_verification(apps, path, receipt, monkeypatch)
    cd.promote(apps, path, receipt, delivery, canary=True)
    assert delivery.read_bytes() == saved
    # A zero-image current-run receipt also cannot enter the Canary/recovery runner.
    empty = tmp_path / 'zero.json'
    cd.save(empty, {'built_images': [], 'canary': False, 'phase': 'pending'})
    before = empty.read_bytes()
    canary.deliver(apps, empty, 1200); canary.recover(apps, empty); regular.deliver(apps, empty, 1200)
    assert empty.read_bytes() == before


def test_regular_sync_targets_no_rollout_analysis_or_csms(monkeypatch):
    monkeypatch.setattr(regular, 'application', ops_app)
    calls = []
    monkeypatch.setattr(cd, 'run', lambda *args, **kwargs: calls.append(args))
    regular.sync('c' * 40, [{'group': 'apps', 'kind': 'Deployment', 'name': 'notifier', 'images': {'notifier': N}}])
    resources = json.loads(calls[0][-1])['operation']['sync']['resources']
    assert resources == [{'group': 'apps', 'kind': 'Deployment', 'name': 'notifier'}]
    with pytest.raises(cd.DeliveryPreflightError):
        regular.sync('c' * 40, [{'group': 'argoproj.io', 'kind': 'Rollout', 'name': 'csms'}])


@pytest.mark.parametrize('fault', [None, 'digest', 'generation', 'replicas', 'old_operation', 'sync_failed'])
def test_regular_wait_requires_owned_sync_rollout_and_digest_without_csms(monkeypatch, fault):
    clock = [0]
    monkeypatch.setattr(regular.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(regular.time, 'sleep', lambda n: clock.__setitem__(0, clock[0] + n))
    state = {'phase': 'Failed' if fault == 'sync_failed' else 'Succeeded', 'operation': {
        'sync': {'revision': 'c' * 40}, 'info': [{'name': 'chargeops-operation-id', 'value': 'old' if fault == 'old_operation' else 'marker'}]},
        'syncResult': {'resources': [{'namespace': 'ops', 'kind': 'Deployment', 'name': 'notifier', 'status': 'Synced'}]}}
    app = ops_app(); app['status'] = {'operationState': state}
    monkeypatch.setattr(regular, 'application', lambda: app)
    deployment = {'metadata': {'generation': 2}, 'spec': {'replicas': 1, 'template': {'spec': {'containers': [
        {'name': 'notifier', 'image': A if fault == 'digest' else N}]}}}, 'status': {
        'observedGeneration': 1 if fault == 'generation' else 2,
        'replicas': 1, 'updatedReplicas': 1, 'readyReplicas': 0 if fault == 'replicas' else 1, 'availableReplicas': 1}}
    def get(*args):
        assert args == ('-n', 'ops', 'get', 'deployment', 'notifier')
        return deployment
    monkeypatch.setattr(cd, 'kubectl', get)
    if fault:
        with pytest.raises(cd.DeliveryPreflightError): regular.wait('c' * 40, 'marker', [
            {'group': 'apps', 'kind': 'Deployment', 'name': 'notifier', 'images': {'notifier': N}}], 6)
    else:
        regular.wait('c' * 40, 'marker', [
            {'group': 'apps', 'kind': 'Deployment', 'name': 'notifier', 'images': {'notifier': N}}], 6)
        assert clock[0] == 0


def test_regular_delivery_runs_regular_wait_and_never_recovery(apps, tmp_path, monkeypatch):
    monkeypatch.setattr(cd, 'resolve_digest', lambda *args: N)
    path, receipt, delivery = (tmp_path / name for name in ('candidate.json', 'receipt.json', 'delivery.json'))
    cd.prepare(apps, 'build-86-abcdef012345', 'c' * 40, path, canary=True, build_images='chargeops-notifier')
    run_existing_verification(apps, path, receipt, monkeypatch)
    cd.promote(apps, path, receipt, delivery, canary=True)
    monkeypatch.setattr(regular, 'application', ops_app)
    monkeypatch.setattr(regular, 'sync', lambda *args: 'marker')
    calls = []
    monkeypatch.setattr(regular, 'wait', lambda *args: calls.append(args))
    monkeypatch.setattr(canary, 'recover', lambda *args: pytest.fail('No Canary recovery'))
    regular.deliver(apps, delivery, 1200)
    assert calls and cd.load(delivery)['phase'] == 'succeeded'
    data = cd.load(delivery); data['phase'] = 'pending'; cd.save(delivery, data)
    def failed(*args): raise cd.DeliveryPreflightError('regular rollout failed')
    monkeypatch.setattr(regular, 'wait', failed)
    with pytest.raises(cd.DeliveryPreflightError): regular.deliver(apps, delivery, 1200)
    assert cd.load(delivery)['phase'] == 'failed' and cd.load(delivery)['canary'] is False


@pytest.mark.parametrize('selection,flag', [('chargeops-csms', 'true'), ('chargeops-notifier', 'false'), ('', 'false')])
def test_pipeline_shell_only_passes_canary_for_selected_csms(tmp_path, selection, flag):
    pipeline = (ROOT / 'Jenkinsfile').read_text(encoding='utf-8')
    assert "env.RUN_CSMS_CANARY = (params.ENABLE_CANARY && selection.selected.contains('chargeops-csms')).toString()" in pipeline
    blocks = re.findall(r"sh(?:\(script:)?\s*'''(.*?)'''", pipeline, re.S)
    script = next(b for b in blocks if 'scripts/env_cd.py prepare' in b)
    executable = Path('C:/Program Files/Git/bin/bash.exe')
    bash = str(executable) if executable.exists() else shutil.which('bash')
    assert bash
    stub = tmp_path / '.ci-venv/bin/python'; stub.parent.mkdir(parents=True)
    stub.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALL_LOG"\n', encoding='utf-8'); stub.chmod(0o755)
    log = tmp_path / 'commands.log'
    env = dict(os.environ, RUN_CSMS_CANARY=flag, ENABLE_CANARY='true', BUILD_IMAGES=selection,
               IMAGE_TAG='build-86-abcdef012345', RUN_OCPP_FIXTURE_TEST='false', CALL_LOG=log.as_posix())
    if not selection:
        env.pop('BUILD_IMAGES', None)  # Jenkins can omit an empty env value entirely.
    prefix = "kubectl() { :; }\ngit() { printf '%s' " + 'c' * 40 + "; }\n"
    result = subprocess.run([bash], input=prefix + script, cwd=tmp_path, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    commands = log.read_text().splitlines()
    assert ('--canary' in commands[0]) is (flag == 'true')
    assert ('--verify-current' in commands[0]) is (not selection)
    assert commands[1] == 'scripts/env_cd.py verify'


def test_verify_current_does_not_resolve_pin_or_commit_and_runs_integration(apps, tmp_path, monkeypatch):
    before = cd.run('git', 'rev-parse', 'HEAD', cwd=apps)
    images = cd.application_digest_snapshot(apps, cd.DEV)
    for name in ('resolve_digest', 'pin_image', 'commit'):
        monkeypatch.setattr(cd, name, lambda *a, **kw: pytest.fail('Verification-only must not mutate GitOps'))
    path, receipt = tmp_path / 'candidate.json', tmp_path / 'receipt.json'
    cd.prepare(apps, 'build-90-abcdef012345', 'c' * 40, path, verify_current=True)
    value = cd.load(path)
    assert value['built_images'] == [] and value['development_unchanged'] is True
    assert value['development_images'] == images
    run_existing_verification(apps, path, receipt, monkeypatch)
    assert receipt.exists()
    assert cd.run('git', 'rev-parse', 'HEAD', cwd=apps) == before
    assert cd.application_digest_snapshot(apps, cd.DEV) == images


def test_cronjob_digest_check_does_not_create_an_execution_job(monkeypatch):
    workload = {'group': 'batch', 'kind': 'CronJob', 'name': 'collector-status', 'images': {'collector': N}}
    state = {'syncResult': {'resources': [{'namespace': 'ops', 'kind': 'CronJob', 'name': 'collector-status', 'status': 'Synced'}]}}
    template = {'spec': {'jobTemplate': {'spec': {'template': {'spec': {'containers': [{'name': 'collector', 'image': N}]}}}}}}
    def get(*args):
        assert args == ('-n', 'ops', 'get', 'cronjob', 'collector-status')
        return template
    monkeypatch.setattr(cd, 'kubectl', get)
    assert regular.ready([workload], state)
    template['spec']['jobTemplate']['spec']['template']['spec']['containers'][0]['image'] = A
    assert not regular.ready([workload], state)


@pytest.mark.parametrize('busy', [False, True])
def test_regular_preflight_is_scoped_to_ops_and_does_not_read_csms(monkeypatch, busy):
    candidate = {'schema': 1, 'image': A, 'images': {cd.APP_IMAGES['notifier']: N},
                 'built_images': ['chargeops-notifier'], 'production_fingerprint': 'same'}
    monkeypatch.setattr(cd, 'load', lambda *a: candidate)
    monkeypatch.setattr(cd, 'ensure_clean', lambda *a: None)
    monkeypatch.setattr(cd, 'production_fingerprint', lambda *a: 'same')
    app = ops_app()
    if busy: app['status'] = {'operationState': {'phase': 'Running'}}
    def get(*args):
        assert args == ('-n', 'argocd', 'get', 'application', canary.APPLICATION)
        return app
    monkeypatch.setattr(cd, 'kubectl', get)
    if busy:
        with pytest.raises(cd.DeliveryPreflightError, match='already running'): regular.preflight('repo', 'candidate')
    else:
        regular.preflight('repo', 'candidate')


def test_existing_canary_wait_can_pass_same_healthy_digest_without_new_rs(monkeypatch):
    app = ops_app()
    app['status'] = {'operationState': {'phase': 'Succeeded', 'operation': {
        'sync': {'revision': 'c' * 40},
        'info': [{'name': 'chargeops-operation-id', 'value': 'marker'}]}}}
    monkeypatch.setattr(canary, 'check_application', lambda: app)
    monkeypatch.setattr(canary, 'rollout_state', lambda image: ({'status': {
        'phase': 'Healthy', 'stableRS': 'samehash', 'currentPodHash': 'samehash'}}, True))
    monkeypatch.setattr(canary, 'deployment_image', lambda: ({}, A))
    monkeypatch.setattr(canary.time, 'sleep', lambda *args: pytest.fail('Same healthy digest should not require a new RS'))
    canary.wait(A, 'c' * 40, 10, 'marker')
