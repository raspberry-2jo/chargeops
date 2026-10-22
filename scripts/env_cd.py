"""CSMS-only development delivery and receipt-bound production promotion.

No kubectl writes to ops. Production remains manually synced in Argo CD.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import ssl
import subprocess
import time
from urllib.request import Request, build_opener, ProxyHandler, HTTPSHandler
from urllib.error import HTTPError, URLError

import yaml

REGISTRY = 'registry.chargeops.lab:5000'
IMAGE = REGISTRY + '/chargeops/chargeops-csms'
DEV = 'chargeops-chatops/overlays/development'
PROD = 'chargeops-chatops/overlays/prod'
APPLICATION = 'chargeops-dev'


class RegistryLookupError(RuntimeError):
    """Safe connection diagnostics without response bodies or credentials."""


def urlopen(request, timeout=30):
    if not request.full_url.startswith(f'https://{REGISTRY}/v2/'):
        raise RegistryLookupError('Registry requests must use the configured HTTPS endpoint.')
    context = ssl.create_default_context()
    ca = os.environ.get('CHARGEOPS_REGISTRY_CA')
    docker_ca = Path('/etc/docker/certs.d') / REGISTRY / 'ca.crt'
    if ca:
        context.load_verify_locations(cafile=ca)
    elif docker_ca.is_file():
        context.load_verify_locations(cafile=str(docker_ca))
    return build_opener(ProxyHandler({}), HTTPSHandler(context=context)).open(request, timeout=timeout)


def run(*args, cwd=None, payload=None, env=None):
    result = subprocess.run(args, cwd=cwd, input=payload, text=True,
                            capture_output=True, env=env)
    if result.returncode:
        # Child output could contain configuration or credentials. Do not echo it.
        raise RuntimeError(f'{args[0]} {args[1] if len(args) > 1 else ""} failed')
    return result.stdout.strip()


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def save(path, value):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, sort_keys=True, indent=2) + '\n', encoding='utf-8')


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def rendered(repo, overlay):
    return [d for d in yaml.safe_load_all(run('kubectl', 'kustomize', overlay, cwd=repo)) if d]


def production_fingerprint(repo):
    docs = rendered(repo, PROD)
    docs.sort(key=lambda d: (d['kind'], d['metadata']['name']))
    return fingerprint(docs)


def csms_image(repo, overlay):
    d = next(d for d in rendered(repo, overlay)
             if d['kind'] == 'Deployment' and d['metadata']['name'] == 'csms')
    return next(c['image'] for c in d['spec']['template']['spec']['containers'] if c['name'] == 'csms')


def resolve_digest(reference):
    """Read a registry manifest and validate the server digest against its bytes."""
    if not reference.startswith(REGISTRY + '/chargeops/'):
        raise ValueError('Image must be in the ChargeOps registry')
    if '@' in reference:
        repo, tag = reference.split('@', 1)
    else:
        repo, tag = reference.rsplit(':', 1)
    path = repo.removeprefix(REGISTRY + '/')
    if not re.fullmatch(r'[a-z0-9/._-]+', path) or not re.fullmatch(r'[A-Za-z0-9._:-]+', tag):
        raise ValueError('Invalid registry reference')
    request = Request(f'https://{REGISTRY}/v2/{path}/manifests/{tag}', headers={
        'Accept': ', '.join(['application/vnd.oci.image.index.v1+json',
                            'application/vnd.docker.distribution.manifest.list.v2+json',
                            'application/vnd.oci.image.manifest.v1+json',
                            'application/vnd.docker.distribution.manifest.v2+json'])})
    try:
        with urlopen(request, timeout=30) as response:
            body = response.read()
            digest = response.headers.get('Docker-Content-Digest', '')
    except HTTPError as error:
        raise RegistryLookupError(f'HTTP {error.code} while reading {reference}. Check image existence and Registry permissions.') from None
    except URLError as error:
        if isinstance(error.reason, ssl.SSLCertVerificationError):
            raise RegistryLookupError('Registry TLS certificate verification failed. Configure CHARGEOPS_REGISTRY_CA or the Docker registry CA file on mgmt-agent.') from None
        raise RegistryLookupError('Cannot reach the HTTPS Registry. Check DNS and port 5000 on mgmt-agent.') from None
    if digest != 'sha256:' + hashlib.sha256(body).hexdigest():
        raise ValueError('Registry digest is missing or does not match the manifest')
    return repo + '@' + digest


def pin_image(repo, overlay, reference):
    if not re.fullmatch(re.escape(IMAGE) + r'@sha256:[a-f0-9]{64}', reference):
        raise ValueError('Only a pinned CSMS candidate may be promoted')
    path = Path(repo) / overlay / 'kustomization.yaml'
    data = yaml.safe_load(path.read_text(encoding='utf-8'))
    image = next(i for i in data['images'] if i['name'] == IMAGE)
    image['newName'], image['digest'] = reference.split('@')
    image.pop('newTag', None)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding='utf-8')


def ensure_clean(repo):
    if run('git', 'status', '--porcelain', cwd=repo):
        raise ValueError('GitOps checkout must be clean')
    if run('git', 'branch', '--show-current', cwd=repo) != 'main':
        raise ValueError('GitOps checkout must be on main')
    run('git', 'pull', '--ff-only', 'origin', 'main', cwd=repo, env=git_environment(repo))


def git_environment(repo):
    """Provide Jenkins credentials through askpass, never a URL or a tracked file."""
    env = os.environ.copy()
    env['GIT_TERMINAL_PROMPT'] = '0'
    if os.environ.get('GIT_USERNAME') and os.environ.get('GIT_PASSWORD'):
        git_dir = Path(run('git', 'rev-parse', '--absolute-git-dir', cwd=repo))
        helper = git_dir / 'chargeops-askpass.sh'
        helper.write_text('#!/bin/sh\ncase "$1" in\n*Username*) printf "%s\\n" "$GIT_USERNAME";;\n*Password*) printf "%s\\n" "$GIT_PASSWORD";;\nesac\n', encoding='utf-8')
        helper.chmod(0o700)
        env['GIT_ASKPASS'] = str(helper)
    return env


def commit(repo, overlay, message):
    allowed = overlay + '/kustomization.yaml'
    changed = run('git', 'diff', '--name-only', cwd=repo).splitlines()
    if set(changed) - {allowed}:
        raise ValueError('Delivery may change only its environment image file')
    if changed:
        run('git', 'add', '--', allowed, cwd=repo)
        run('git', '-c', 'user.name=ChargeOps CI', '-c', 'user.email=chargeops-ci@example.invalid',
            'commit', '-m', message, cwd=repo)
        run('git', 'push', 'origin', 'HEAD:main', cwd=repo, env=git_environment(repo))
    return run('git', 'rev-parse', 'HEAD', cwd=repo)


def candidate_check(candidate):
    if candidate.get('schema') != 1 or not re.fullmatch(
            re.escape(IMAGE) + r'@sha256:[a-f0-9]{64}', candidate.get('image', '')):
        raise ValueError('Invalid CSMS candidate')
    if candidate.get('verification_mode', 'read-only') not in ['read-only', 'ocpp-fixture']:
        raise ValueError('Unknown verification mode')


def prepare(repo, tag, source, output, ocpp_fixture_test=False):
    if not re.fullmatch(r'build-[0-9]+-[a-f0-9]{12}', tag):
        raise ValueError('Only a unique build tag may identify a candidate')
    if not re.fullmatch(r'[a-f0-9]{40}', source):
        raise ValueError('Source must be a full Git commit')
    ensure_clean(repo)
    before = production_fingerprint(repo)
    prod_image = csms_image(repo, PROD)
    if '@sha256:' not in prod_image:
        raise ValueError('Freeze the production CSMS digest with the bootstrap procedure first')
    candidate = {'schema': 1, 'source': source, 'image': resolve_digest(IMAGE + ':' + tag),
                 'verification_mode': 'ocpp-fixture' if ocpp_fixture_test else 'read-only',
                 'production_fingerprint': before, 'previous_production_image': prod_image}
    pin_image(repo, DEV, candidate['image'])
    if production_fingerprint(repo) != before:
        raise ValueError('Development update changed production')
    candidate['development_fingerprint'] = fingerprint(rendered(repo, DEV))
    candidate['development_revision'] = commit(repo, DEV, f'build(dev): verify CSMS {tag}')
    save(output, candidate)
    print('Development digest committed; production unchanged')


def kubectl(*args):
    return json.loads(run('kubectl', *args, '-o', 'json'))


def live_candidate(image):
    app = kubectl('-n', 'argocd', 'get', 'application', APPLICATION)
    spec, status = app['spec'], app.get('status', {})
    if spec['destination']['namespace'] != 'dev' or spec['source']['path'] != DEV:
        return False
    if status.get('sync', {}).get('status') != 'Synced' or status.get('health', {}).get('status') != 'Healthy':
        return False
    deployment = kubectl('-n', 'dev', 'get', 'deployment', 'csms')
    if deployment['spec'].get('replicas') != 1:
        return False
    container = next(c for c in deployment['spec']['template']['spec']['containers'] if c['name'] == 'csms')
    state = deployment.get('status', {})
    if container['image'] != image or state.get('observedGeneration', 0) < deployment['metadata']['generation']:
        return False
    if state.get('updatedReplicas') != 1 or state.get('availableReplicas') != 1 or state.get('replicas') != 1:
        return False
    pods = kubectl('-n', 'dev', 'get', 'pods', '-l', 'app=csms')['items']
    active = [p for p in pods if not p['metadata'].get('deletionTimestamp')]
    return len(active) == 1 and all(
        p['spec']['containers'][0]['image'] == image
        and any(c['type'] == 'Ready' and c['status'] == 'True' for c in p.get('status', {}).get('conditions', []))
        for p in active)


def verification_job(candidate):
    probe = Path(__file__).with_name('csms_smoke.py').read_text(encoding='utf-8')
    name = 'csms-check-' + fingerprint(candidate)[:16]
    return {'apiVersion': 'batch/v1', 'kind': 'Job', 'metadata': {'name': name, 'namespace': 'dev'},
            'spec': {'backoffLimit': 0, 'activeDeadlineSeconds': 180, 'ttlSecondsAfterFinished': 3600,
                     'template': {'spec': {'restartPolicy': 'Never', 'automountServiceAccountToken': False,
                         'securityContext': {'runAsNonRoot': True, 'runAsUser': 10001, 'runAsGroup': 10001},
                         'containers': [{'name': 'verify', 'image': candidate['image'],
                             'command': ['python', '-c', probe],
                             'env': [{'name': 'VERIFY_OCPP', 'value': str(candidate.get('verification_mode') == 'ocpp-fixture').lower()}],
                             'envFrom': [{'configMapRef': {'name': 'chargeops-dev-config'}},
                                         {'secretRef': {'name': 'chargeops-dev-secrets'}}],
                             'resources': {'requests': {'cpu': '100m', 'memory': '128Mi'},
                                           'limits': {'cpu': '500m', 'memory': '256Mi'}}}]}}}}


def verify(repo, candidate_path, receipt_path, timeout):
    candidate = load(candidate_path)
    candidate_check(candidate)
    ensure_clean(repo)
    if fingerprint(rendered(repo, DEV)) != candidate['development_fingerprint']:
        raise ValueError('Development configuration changed before verification')
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if live_candidate(candidate['image']):
            break
        time.sleep(5)
    else:
        raise ValueError('Development did not converge to the exact candidate')
    job = verification_job(candidate)
    name = job['metadata']['name']
    run('kubectl', 'create', '-f', '-', payload=json.dumps(job))
    run('kubectl', '-n', 'dev', 'wait', '--for=condition=complete', f'job/{name}', '--timeout=210s')
    result = kubectl('-n', 'dev', 'get', 'job', name)
    print(run('kubectl', '-n', 'dev', 'logs', f'job/{name}', '--all-containers=true'))
    if not live_candidate(candidate['image']):
        raise ValueError('Development changed during verification')
    ensure_clean(repo)
    if fingerprint(rendered(repo, DEV)) != candidate['development_fingerprint']:
        raise ValueError('Development configuration changed during verification')
    save(receipt_path, {'candidate': fingerprint(candidate), 'image': candidate['image'],
                        'job': name, 'job_uid': result['metadata']['uid'],
                        'verified_at': int(time.time())})
    print('Exact development digest passed ' + candidate['verification_mode'] + ' integration checks')


def validate_receipt(candidate, receipt, now):
    candidate_check(candidate)
    if receipt.get('candidate') != fingerprint(candidate) or receipt.get('image') != candidate['image']:
        raise ValueError('Verification receipt belongs to a different candidate')
    age = now - receipt.get('verified_at', 0)
    if age < 0 or age > 7200:
        raise ValueError('Verification is expired; run a new development test')


def promote(repo, candidate_path, receipt_path):
    candidate, receipt = load(candidate_path), load(receipt_path)
    validate_receipt(candidate, receipt, int(time.time()))
    ensure_clean(repo)
    if production_fingerprint(repo) != candidate['production_fingerprint']:
        raise ValueError('Production changed while approval was pending')
    if (fingerprint(rendered(repo, DEV)) != candidate['development_fingerprint']
            or csms_image(repo, DEV) != candidate['image'] or not live_candidate(candidate['image'])):
        raise ValueError('Development no longer runs the verified candidate')
    job = kubectl('-n', 'dev', 'get', 'job', receipt['job'])
    if job['metadata']['uid'] != receipt['job_uid'] or job.get('status', {}).get('succeeded') != 1:
        raise ValueError('Successful verification job is missing or replaced')
    dev_before = rendered(repo, DEV)
    pin_image(repo, PROD, candidate['image'])
    if rendered(repo, DEV) != dev_before:
        raise ValueError('Production promotion changed development')
    revision = commit(repo, PROD, f'promote(ops): verified CSMS {candidate["source"][:12]}')
    print(f'Approved digest committed at {revision}; manual Argo CD ops Sync is required')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['prepare', 'verify', 'promote', 'describe'])
    parser.add_argument('--repo', default='.gitops-delivery')
    parser.add_argument('--candidate', default='artifacts/candidate.json')
    parser.add_argument('--receipt', default='artifacts/verification.json')
    parser.add_argument('--tag')
    parser.add_argument('--source')
    parser.add_argument('--timeout', type=int, default=600)
    parser.add_argument('--ocpp-fixture-test', action='store_true')
    args = parser.parse_args()
    if args.command == 'prepare':
        prepare(args.repo, args.tag or '', args.source or '', args.candidate, args.ocpp_fixture_test)
    elif args.command == 'verify':
        verify(args.repo, args.candidate, args.receipt, args.timeout)
    elif args.command == 'promote':
        promote(args.repo, args.candidate, args.receipt)
    else:
        candidate = load(args.candidate)
        candidate_check(candidate)
        print(candidate['image'] + ' [' + candidate.get('verification_mode', 'read-only') + ']')


if __name__ == '__main__':
    try:
        main()
    except RegistryLookupError as error:
        print(f'Delivery stopped: {error}')
        raise SystemExit(1)
    except Exception as error:
        # No secret/configuration payloads in exception output.
        print(f'Delivery stopped: {error.__class__.__name__}; inspect the relevant stage and prerequisites.')
        raise SystemExit(1)
