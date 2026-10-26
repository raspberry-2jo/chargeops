"""Exercise digest promotion using real Kustomize and temporary Git repositories."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('environment_cd', ROOT / 'scripts/env_cd.py')
cd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cd)
A = cd.IMAGE + '@sha256:' + 'a' * 64
B = cd.IMAGE + '@sha256:' + 'b' * 64


def test_build_digest_lookup_uses_https(monkeypatch):
    import hashlib
    body = b'{"schemaVersion":2}'
    digest = 'sha256:' + hashlib.sha256(body).hexdigest()
    class Response:
        headers = {'Docker-Content-Digest': digest}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return body
    def open_manifest(request, **kwargs):
        assert request.full_url.startswith('https://registry.chargeops.lab:5000/v2/')
        return Response()
    monkeypatch.setattr(cd, 'urlopen', open_manifest)
    assert cd.resolve_digest(cd.IMAGE + ':build-1-abcdef012345') == cd.IMAGE + '@' + digest


@pytest.fixture
def repo(tmp_path):
    def git(*args, cwd=tmp_path):
        return subprocess.check_output(['git', *args], cwd=cwd, text=True).strip()
    remote = tmp_path / 'remote.git'
    subprocess.run(['git', 'init', '--bare', '-q', str(remote)], check=True)
    work = tmp_path / 'work'
    subprocess.run(['git', 'clone', '-q', str(remote), str(work)], check=True)
    git('checkout', '-qb', 'main', cwd=work)
    git('config', 'user.name', 'Test', cwd=work)
    git('config', 'user.email', 'test@example.invalid', cwd=work)
    for env, namespace in [(cd.DEV, 'dev'), (cd.PROD, 'ops')]:
        directory = work / env
        directory.mkdir(parents=True)
        deployment = {'apiVersion': 'apps/v1', 'kind': 'Deployment', 'metadata': {'name': 'csms'},
                      'spec': {'replicas': 1, 'selector': {'matchLabels': {'app': 'csms'}},
                               'template': {'metadata': {'labels': {'app': 'csms'}},
                                            'spec': {'containers': [{'name': 'csms', 'image': cd.IMAGE + ':dev'}]}}}}
        (directory / 'deployment.yaml').write_text(yaml.safe_dump(deployment))
        (directory / 'kustomization.yaml').write_text(yaml.safe_dump({
            'apiVersion': 'kustomize.config.k8s.io/v1beta1', 'kind': 'Kustomization',
            'namespace': namespace, 'resources': ['deployment.yaml'],
            'images': [{'name': cd.IMAGE, 'newName': cd.IMAGE, 'digest': 'sha256:' + 'a' * 64}]}))
    canary = work / cd.CANARY
    canary.mkdir(parents=True)
    (canary / 'kustomization.yaml').write_text(yaml.safe_dump({
        'apiVersion': 'kustomize.config.k8s.io/v1beta1', 'kind': 'Kustomization', 'resources': ['../prod']}))
    git('add', '.', cwd=work)
    git('-c', 'core.hooksPath=/dev/null', 'commit', '-qm', 'initial', cwd=work)
    git('push', '-qu', 'origin', 'main', cwd=work)
    return work


def prepared(repo, tmp_path, monkeypatch, canary=False):
    monkeypatch.setattr(cd, 'resolve_digest', lambda reference: B)
    candidate = tmp_path / 'candidate.json'
    cd.prepare(repo, 'build-15-abcdef012345', 'c' * 40, candidate, canary=canary)
    return candidate


def receipt_for(candidate):
    data = cd.load(candidate)
    return {'candidate': cd.fingerprint(data), 'image': data['image'], 'verified_at': int(time.time()),
            'job': 'csms-check-test', 'job_uid': 'test-job-uid'}


def test_development_commit_never_changes_production(repo, tmp_path, monkeypatch):
    before = cd.production_fingerprint(repo)
    prepared(repo, tmp_path, monkeypatch)
    assert cd.csms_image(repo, cd.DEV) == B
    assert cd.csms_image(repo, cd.PROD) == A
    assert cd.production_fingerprint(repo) == before
    files = cd.run('git', 'diff', '--name-only', 'HEAD~1', 'HEAD', cwd=repo).splitlines()
    assert files == [cd.DEV + '/kustomization.yaml']


def test_uncommitted_bootstrap_stops_before_registry_or_dev_change(repo, tmp_path, monkeypatch):
    path = repo / cd.PROD / 'kustomization.yaml'
    config = yaml.safe_load(path.read_text())
    config['images'][0].pop('digest')
    config['images'][0]['newTag'] = 'dev'
    path.write_text(yaml.safe_dump(config))
    cd.run('git', 'add', '.', cwd=repo)
    cd.run('git', '-c', 'core.hooksPath=/dev/null', 'commit', '-m', 'unpinned bootstrap fixture', cwd=repo)
    cd.run('git', 'push', 'origin', 'main', cwd=repo)
    monkeypatch.setattr(cd, 'resolve_digest', lambda reference: pytest.fail('Unpinned production must stop before Registry access'))
    with pytest.raises(cd.DeliveryPreflightError, match='commit and push'):
        cd.prepare(repo, 'build-10-d0a911b838e8', 'c' * 40, tmp_path / 'candidate.json')
    assert cd.csms_image(repo, cd.DEV) == A
    assert not (tmp_path / 'candidate.json').exists()


def test_cli_explains_fixed_validation_failure():
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/env_cd.py'), 'prepare', '--tag', 'invalid', '--source', 'c' * 40],
                            cwd=ROOT, text=True, capture_output=True)
    assert result.returncode == 1
    assert 'Only a unique build tag may identify a candidate' in result.stdout
    assert 'Traceback' not in result.stderr


def test_promotion_uses_verified_digest_without_registry_lookup(repo, tmp_path, monkeypatch):
    candidate = prepared(repo, tmp_path, monkeypatch)
    receipt = tmp_path / 'receipt.json'
    cd.save(receipt, receipt_for(candidate))
    monkeypatch.setattr(cd, 'resolve_digest', lambda reference: pytest.fail('Promotion must not resolve a mutable tag'))
    monkeypatch.setattr(cd, 'live_candidate', lambda image: image == B)
    monkeypatch.setattr(cd, 'kubectl', lambda *args: {'metadata': {'uid': 'test-job-uid'}, 'status': {'succeeded': 1}})
    cd.promote(repo, candidate, receipt, tmp_path / 'production-delivery.json')
    assert cd.csms_image(repo, cd.PROD) == B
    assert cd.csms_image(repo, cd.DEV) == B
    assert cd.run('git', 'diff', '--name-only', 'HEAD~1', 'HEAD', cwd=repo) == cd.PROD + '/kustomization.yaml'


def test_ops_promotion_commits_only_image_and_baseline_with_real_git(repo,tmp_path,monkeypatch):
    directory=repo/cd.PROD
    settings=directory/'gate-settings.yaml'
    settings.write_text(yaml.safe_dump({'apiVersion':'v1','kind':'ConfigMap',
        'metadata':{'name':'csms-ops-service-gate-settings'},
        'data':{'GATE_ENABLED':'true','GATE_BASELINE_AT':'0','GATE_THROUGHPUT_DROP_PERCENT':'50'}}))
    config=yaml.safe_load((directory/'kustomization.yaml').read_text())
    config['resources'].append('gate-settings.yaml')
    (directory/'kustomization.yaml').write_text(yaml.safe_dump(config))
    cd.run('git','add','.',cwd=repo)
    cd.run('git','-c','core.hooksPath=/dev/null','commit','-m','Ops gate policy',cwd=repo)
    cd.run('git','push','origin','main',cwd=repo)
    candidate=prepared(repo,tmp_path,monkeypatch,canary=True)
    dev_before=cd.rendered(repo,cd.DEV)
    receipt=tmp_path/'receipt.json'
    cd.save(receipt,receipt_for(candidate))
    monkeypatch.setattr(cd,'live_candidate',lambda image:image==B)
    monkeypatch.setattr(cd,'kubectl',lambda *a:{'metadata':{'uid':'test-job-uid'},'status':{'succeeded':1}})
    cd.promote(repo,candidate,receipt,tmp_path/'delivery.json',canary=True)
    data=yaml.safe_load(settings.read_text())['data']
    assert data['GATE_ENABLED']=='true' and data['GATE_THROUGHPUT_DROP_PERCENT']=='50'
    assert int(data['GATE_BASELINE_AT'])>0
    assert cd.rendered(repo,cd.DEV)==dev_before
    assert cd.csms_image(repo,cd.PROD)==B
    assert set(cd.run('git','diff','--name-only','HEAD~1','HEAD',cwd=repo).splitlines())=={
        cd.PROD+'/gate-settings.yaml',cd.PROD+'/kustomization.yaml'}
    assert not cd.run('git','status','--porcelain',cwd=repo)


@pytest.mark.parametrize('failure', ['missing_receipt', 'wrong_receipt', 'expired', 'future', 'dev_drift', 'job_replaced', 'prod_changed'])
def test_failed_or_stale_verification_never_updates_production(repo, tmp_path, monkeypatch, failure):
    candidate = prepared(repo, tmp_path, monkeypatch)
    data = receipt_for(candidate)
    receipt = tmp_path / 'receipt.json'
    if failure == 'wrong_receipt': data['candidate'] = 'wrong'
    if failure == 'expired': data['verified_at'] -= 7201
    if failure == 'future': data['verified_at'] += 60
    if failure != 'missing_receipt': cd.save(receipt, data)
    monkeypatch.setattr(cd, 'live_candidate', lambda image: failure != 'dev_drift')
    monkeypatch.setattr(cd, 'kubectl', lambda *args: {'metadata': {'uid': 'different' if failure == 'job_replaced' else 'test-job-uid'}, 'status': {'succeeded': 1}})
    if failure == 'prod_changed':
        original = cd.production_fingerprint
        monkeypatch.setattr(cd, 'production_fingerprint', lambda repo: 'changed')
    with pytest.raises((ValueError, FileNotFoundError)):
        cd.promote(repo, candidate, receipt)
    assert cd.csms_image(repo, cd.PROD) == A


def test_verification_job_is_confined_to_dev_and_has_no_literal_credentials():
    candidate = {'schema': 1, 'source': 'c' * 40, 'image': B}
    job = cd.verification_job(candidate)
    assert job['metadata']['namespace'] == 'dev'
    pod = job['spec']['template']['spec']
    assert pod['automountServiceAccountToken'] is False
    assert pod['containers'][0]['image'] == B
    assert {'secretRef': {'name': 'chargeops-dev-secrets'}} in pod['containers'][0]['envFrom']
    assert 'chargeops-secrets' not in json.dumps(job)
    assert job['spec']['backoffLimit'] == 0


def test_single_job_waits_for_reviewed_merge_after_verification_before_production():
    pipeline = (ROOT / 'Jenkinsfile').read_text(encoding='utf-8')
    assert pipeline.index('scripts/env_cd.py verify') < pipeline.index('scripts/github_cd.py open-pr') < pipeline.index('scripts/github_cd.py wait-merge') < pipeline.index('scripts/env_cd.py promote --require-approved-pr')
    assert "stage('Production approval')" not in pipeline
    assert '([env.PUBLISH_BRANCH, env.PRODUCTION_BRANCH].contains' not in pipeline
    assert "name: 'ENABLE_ENVIRONMENT_CD', defaultValue: true" in pipeline
    assert "name: 'ENABLE_CANARY', defaultValue: true" in pipeline
    assert "error('PR-based Ops delivery requires ENABLE_CANARY=true')" in pipeline


def test_unpinned_or_foreign_images_cannot_be_promoted(tmp_path):
    for image in [cd.IMAGE + ':dev', 'example.invalid/csms@sha256:' + 'b' * 64]:
        with pytest.raises(ValueError):
            cd.pin_image(tmp_path, cd.PROD, image)


def test_default_shared_db_probe_uses_only_reads(monkeypatch):
    import types
    statements = []
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, sql, *args):
            statements.append(sql)
            return self
        def fetchone(self): return (1,)
    class Response:
        def raise_for_status(self): pass
        def json(self): return {'ok': True}
    monkeypatch.setitem(sys.modules, 'psycopg', types.SimpleNamespace(connect=lambda *a, **k: Connection()))
    monkeypatch.setitem(sys.modules, 'requests', types.SimpleNamespace(get=lambda *a, **k: Response()))
    client = types.ModuleType('websockets.asyncio.client')
    client.connect = lambda *a, **k: pytest.fail('Read-only verification must not connect a charger')
    monkeypatch.setitem(sys.modules, 'websockets', types.ModuleType('websockets'))
    monkeypatch.setitem(sys.modules, 'websockets.asyncio', types.ModuleType('websockets.asyncio'))
    monkeypatch.setitem(sys.modules, 'websockets.asyncio.client', client)
    monkeypatch.setenv('ENVIRONMENT', 'dev')
    monkeypatch.setenv('DB_DSN', 'test-only-placeholder')
    monkeypatch.setenv('VERIFY_OCPP', 'false')
    probe_spec = importlib.util.spec_from_file_location('shared_db_probe', ROOT / 'scripts/csms_smoke.py')
    probe = importlib.util.module_from_spec(probe_spec)
    probe_spec.loader.exec_module(probe)
    probe.main()
    assert statements[0] == 'SET TRANSACTION READ ONLY'
    assert all(s.startswith('SELECT ') for s in statements[1:])
