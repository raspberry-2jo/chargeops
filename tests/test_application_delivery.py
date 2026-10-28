"""Promote real Kustomize app bundles while keeping only CSMS under Canary recovery."""
import copy
import json

import pytest
import yaml

from test_env_cd import cd, repo, receipt_for, A, B
from test_canary_cd import canary, application


N = cd.APP_IMAGES['notifier'] + '@sha256:' + 'd' * 64
C = cd.APP_IMAGES['collector'] + '@sha256:' + 'e' * 64
OLD_N = cd.APP_IMAGES['notifier'] + '@sha256:' + 'a' * 64


@pytest.fixture
def apps(repo):
    directory = repo / cd.PROD
    config = yaml.safe_load((directory / 'kustomization.yaml').read_text())
    documents = []
    for name in ('notifier', 'ai-gateway', 'vcharger'):
        documents.append({'apiVersion': 'apps/v1', 'kind': 'Deployment', 'metadata': {'name': name},
                          'spec': {'replicas': 0 if name == 'vcharger' else 1,
                                   'selector': {'matchLabels': {'app': name}},
                                   'template': {'metadata': {'labels': {'app': name}}, 'spec': {'containers': [
                                       {'name': name, 'image': cd.APP_IMAGES[name] + '@sha256:' + 'a' * 64}]}}}})
    for name in ('collector-status', 'collector-info', 'collector-check'):
        documents.append({'apiVersion': 'batch/v1', 'kind': 'CronJob', 'metadata': {'name': name},
                          'spec': {'schedule': '*/5 * * * *', 'jobTemplate': {'spec': {'template': {'spec': {
                              'restartPolicy': 'Never', 'containers': [{'name': 'collector', 'image':
                                  cd.REGISTRY + '/chargeops/collector:0.2'}]}}}}}})
    (directory / 'apps.yaml').write_text(yaml.safe_dump_all(documents))
    config['resources'].append('apps.yaml')
    config['images'].append({'name': cd.REGISTRY + '/chargeops/collector',
                             'newName': cd.REGISTRY + '/chargeops/collector', 'digest': 'sha256:' + 'a' * 64})
    (directory / 'kustomization.yaml').write_text(yaml.safe_dump(config))
    cd.run('git', 'add', '.', cwd=repo)
    cd.run('git', '-c', 'core.hooksPath=/dev/null', 'commit', '-m', 'application workloads', cwd=repo)
    cd.run('git', 'push', 'origin', 'main', cwd=repo)
    return repo


def bundle(apps, tmp_path, monkeypatch):
    references = {cd.IMAGE: B, cd.APP_IMAGES['notifier']: N, cd.APP_IMAGES['collector']: C}
    monkeypatch.setattr(cd, 'resolve_digest', lambda ref: references[ref.rsplit(':', 1)[0]])
    candidate = tmp_path / 'candidate.json'
    cd.prepare(apps, 'build-65-653542ecbaed', 'c' * 40, candidate, canary=True,
               build_images='chargeops-csms chargeops-notifier chargeops-collector')
    return candidate


def promote(apps, tmp_path, monkeypatch):
    candidate = bundle(apps, tmp_path, monkeypatch)
    receipt = tmp_path / 'receipt.json'
    cd.save(receipt, receipt_for(candidate))
    monkeypatch.setattr(cd, 'live_candidate', lambda image: image == B)
    monkeypatch.setattr(cd, 'kubectl', lambda *a: {'metadata': {'uid': 'test-job-uid'}, 'status': {'succeeded': 1}})
    path = tmp_path / 'delivery.json'
    cd.promote(apps, candidate, receipt, path, canary=True)
    return candidate, path


def images(repo):
    return {d['metadata']['name']: [c['image'] for c in cd.workload_containers(d)]
            for d in cd.rendered(repo, cd.PROD) if cd.workload_containers(d)}


def test_prepare_binds_every_built_digest_without_changing_ops(apps, tmp_path, monkeypatch):
    before = cd.production_fingerprint(apps)
    candidate = bundle(apps, tmp_path, monkeypatch)
    assert cd.load(candidate)['images'] == {cd.IMAGE: B, cd.APP_IMAGES['notifier']: N, cd.APP_IMAGES['collector']: C}
    assert cd.production_fingerprint(apps) == before
    assert images(apps)['notifier'] == [OLD_N]


def test_promotion_pins_selected_apps_and_legacy_collector_alias(apps, tmp_path, monkeypatch):
    candidate, delivery = promote(apps, tmp_path, monkeypatch)
    actual = images(apps)
    assert actual['csms'] == [B] and actual['notifier'] == [N]
    assert all(actual[name] == [C] for name in ('collector-status', 'collector-info', 'collector-check'))
    assert actual['ai-gateway'] == [cd.APP_IMAGES['ai-gateway'] + '@sha256:' + 'a' * 64]
    assert cd.csms_image(apps, cd.DEV) == B
    targets = cd.load(delivery)['workloads']
    assert {t['name'] for t in targets} == {'notifier', 'collector-status', 'collector-info', 'collector-check'}
    assert {t['kind'] for t in targets} == {'Deployment', 'CronJob'}
    assert cd.load(delivery)['candidate'] == cd.fingerprint(cd.load(candidate))


def test_changed_bundle_invalidates_verification_receipt(apps, tmp_path, monkeypatch):
    candidate = bundle(apps, tmp_path, monkeypatch)
    value = cd.load(candidate)
    receipt = receipt_for(candidate)
    value['images'][cd.APP_IMAGES['notifier']] = OLD_N
    with pytest.raises(ValueError, match='different candidate'):
        cd.validate_receipt(value, receipt, receipt['verified_at'])


def test_application_sync_adds_only_approved_workloads(apps, tmp_path, monkeypatch):
    _, delivery = promote(apps, tmp_path, monkeypatch)
    data = cd.load(delivery)
    monkeypatch.setattr(canary, 'check_application', application)
    calls = []
    monkeypatch.setattr(cd, 'run', lambda *a, **kw: calls.append(a))
    canary.sync(data['revision'], data['workloads'])
    operation = json.loads(calls[0][-1])['operation']['sync']
    resources = operation['resources']
    assert operation['prune'] is False
    assert {'group': 'apps', 'kind': 'Deployment', 'name': 'notifier'} in resources
    assert {'group': 'batch', 'kind': 'CronJob', 'name': 'collector-status'} in resources
    assert not any(r['kind'] == 'PersistentVolumeClaim' or r['name'] == 'ai-gateway' for r in resources)
    assert [r for r in resources if r['kind'] == 'Rollout'] == [
        {'group': 'argoproj.io', 'kind': 'Rollout', 'name': 'csms'}]


@pytest.mark.parametrize('fault', ['none', 'stale_image', 'not_ready', 'old_generation', 'unapplied_cronjob'])
def test_regular_workload_readiness_and_cronjob_template_application(monkeypatch, fault):
    workloads = [{'group': 'apps', 'kind': 'Deployment', 'name': 'notifier', 'images': {'notifier': N}},
                 {'group': 'batch', 'kind': 'CronJob', 'name': 'collector-status', 'images': {'collector': C}}]
    operation = {'syncResult': {'resources': [{'namespace': 'ops', 'kind': w['kind'], 'name': w['name'],
                                              'status': 'Synced'} for w in workloads]}}
    deployment = {'metadata': {'generation': 2}, 'spec': {'replicas': 1, 'template': {'spec': {
        'containers': [{'name': 'notifier', 'image': N}]}}}, 'status': {'observedGeneration': 2,
        'replicas': 1, 'updatedReplicas': 1, 'readyReplicas': 1, 'availableReplicas': 1}}
    if fault == 'stale_image': deployment['spec']['template']['spec']['containers'][0]['image'] = OLD_N
    if fault == 'not_ready': deployment['status']['readyReplicas'] = 0
    if fault == 'old_generation': deployment['status']['observedGeneration'] = 1
    if fault == 'unapplied_cronjob': operation['syncResult']['resources'].pop()
    monkeypatch.setattr(cd, 'kubectl', lambda *a: deployment)
    assert canary.regular_workloads_ready(workloads, operation) == (fault == 'none')


def test_zero_replica_virtual_charger_stays_scaled_down(monkeypatch):
    image = cd.APP_IMAGES['vcharger'] + '@sha256:' + 'f' * 64
    workload = {'group': 'apps', 'kind': 'Deployment', 'name': 'vcharger', 'images': {'vcharger': image}}
    deployment = {'metadata': {'generation': 2}, 'spec': {'replicas': 0, 'template': {'spec': {
        'containers': [{'name': 'vcharger', 'image': image}]}}}, 'status': {'observedGeneration': 2}}
    monkeypatch.setattr(cd, 'kubectl', lambda *a: deployment)
    assert canary.regular_workloads_ready([workload], {'syncResult': {'resources': [
        {'namespace': 'ops', 'kind': 'Deployment', 'name': 'vcharger', 'status': 'Synced'}]}})


def test_canary_failure_restores_only_csms_and_keeps_regular_app_images(apps, tmp_path, monkeypatch):
    _, delivery = promote(apps, tmp_path, monkeypatch)
    monkeypatch.setattr(canary, 'check_application', application)
    monkeypatch.setattr(canary, 'abort_if_current', lambda image: None)
    syncs = []
    monkeypatch.setattr(canary, 'sync', lambda revision, workloads=None: syncs.append(workloads) or 'marker')
    def wait(image, *args):
        if image == B:
            raise cd.DeliveryPreflightError('synthetic CSMS analysis failure')
    monkeypatch.setattr(canary, 'wait', wait)
    with pytest.raises(ValueError, match='synthetic CSMS'):
        canary.deliver(apps, delivery, 30)
    assert images(apps)['csms'] == [A]
    assert images(apps)['notifier'] == [N]
    assert images(apps)['collector-status'] == [C]
    assert syncs[0] and syncs[1] is None
    assert cd.load(delivery)['phase'] == 'recovered'


@pytest.mark.parametrize('repository,reference', [
    ('unknown/image', 'unknown/image@sha256:' + 'd' * 64),
    (cd.APP_IMAGES['notifier'], cd.APP_IMAGES['notifier'] + ':dev')])
def test_bundle_rejects_foreign_or_mutable_images(repository, reference):
    with pytest.raises(ValueError, match='unknown or unpinned'):
        cd.candidate_images({'image': B, 'images': {cd.IMAGE: B, repository: reference}})
