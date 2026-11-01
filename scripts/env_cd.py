"""CSMS development verification and receipt-bound application image promotion.

No kubectl writes to ops here. Optional Canary Sync is handled by canary_cd.py.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import hashlib
import json
import math
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
APP_IMAGES = {name: REGISTRY + '/chargeops/chargeops-' + name for name in
              ('csms', 'ai-gateway', 'chatops-bot', 'notifier', 'vcharger', 'collector')}
IMAGE_ALIASES = {image: {image} for image in APP_IMAGES.values()}
IMAGE_ALIASES[APP_IMAGES['collector']].add(REGISTRY + '/chargeops/collector')
LEGACY_DEV = 'chargeops-chatops/overlays/development'
CANARY_DEV = 'chargeops-chatops/overlays/development-canary'
ROLLING_DEV = 'chargeops-chatops/overlays/development-rolling'
DEV = os.environ.get('DEV_GITOPS_OVERLAY', LEGACY_DEV)
PROD = 'chargeops-chatops/overlays/prod'
CANARY = 'chargeops-chatops/overlays/canary'
APPLICATION = os.environ.get('DEV_ARGO_APPLICATION', 'chargeops-dev')


class DeliveryPreflightError(ValueError):
    """Fixed local validation messages safe for the Jenkins console."""


class RegistryLookupError(RuntimeError):
    """Safe connection diagnostics without response bodies or credentials."""


def poll_seconds():
    """Polling cadence only; convergence and Stage budgets remain unchanged."""
    try:
        value = float(os.environ.get('CD_POLL_SECONDS', '5'))
    except ValueError:
        raise DeliveryPreflightError('CD_POLL_SECONDS must be a finite positive number') from None
    if not math.isfinite(value) or value <= 0:
        raise DeliveryPreflightError('CD_POLL_SECONDS must be a finite positive number')
    return value


def iso_time(value=None):
    return datetime.fromtimestamp(time.time() if value is None else value, timezone.utc).isoformat(timespec='milliseconds')


_active_poll = ContextVar('chargeops_active_poll', default=None)


class PollDiagnostics:
    """Measure polling, never decide delivery state or alter sleep/deadlines."""
    def __init__(self, scope, interval, entry):
        self.scope, self.interval = scope, interval
        self.trace = os.environ.get('CD_POLL_DIAGNOSTICS', '').lower() == 'true'
        self.count, self.window_count = 0, 0
        self.previous_start, self.max_gap = None, None
        self.max_check, self.next_log = 0.0, 0.0
        def code_id(path):
            try:
                return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:12]
            except OSError:
                return 'unavailable'
        source = 'environment' if 'CD_POLL_SECONDS' in os.environ else 'default'
        print(f'CD poll config: at={iso_time()} scope={scope} sleep_seconds={interval:.3f}'
              f' source={source} diagnostics={str(self.trace).lower()} log_heartbeat_seconds=30'
              f' entry_sha256={code_id(entry)} observer_sha256={code_id(__file__)}', flush=True)

    @contextmanager
    def sample(self, started):
        self.count += 1
        gap = None if self.previous_start is None else max(0, started-self.previous_start)
        self.previous_start = started
        record = {'api_calls': 0, 'api_seconds': 0.0, 'terminal': False}
        token = _active_poll.set(record)
        failed = True
        if self.trace:
            print(f'CD poll begin: at={iso_time()} scope={self.scope} poll={self.count}', flush=True)
        try:
            yield record
            failed = False
        finally:
            _active_poll.reset(token)
            ended = time.monotonic()
            check = max(0, ended-started)
            self.window_count += 1
            self.max_check = max(self.max_check, check)
            if gap is not None:
                self.max_gap = gap if self.max_gap is None else max(self.max_gap, gap)
            if self.trace or self.count == 1 or ended >= self.next_log or failed or record['terminal']:
                formatted_gap = 'NA' if gap is None else f'{gap:.3f}'
                max_gap = 'NA' if self.max_gap is None else f'{self.max_gap:.3f}'
                print(f'CD poll sample: at={iso_time()} scope={self.scope} poll={self.count}'
                      f' window_polls={self.window_count} sleep_seconds={self.interval:.3f}'
                      f' check_seconds={check:.3f} start_gap_seconds={formatted_gap}'
                      f' api_calls={record["api_calls"]} api_seconds={record["api_seconds"]:.3f}'
                      f' window_max_check_seconds={self.max_check:.3f} window_max_start_gap_seconds={max_gap}'
                      f' terminal={str(record["terminal"]).lower()} error={str(failed).lower()}', flush=True)
                self.next_log = ended + 30
                self.window_count, self.max_check, self.max_gap = 0, 0.0, None


class DevVerificationTiming:
    """Observations only: never supplies readiness or success to the delivery gate."""
    def __init__(self):
        self.started = time.monotonic()
        self.wall_started = time.time()
        self.events = {}
        self.target = None
        self.initial_revision = None
        self.revision_observed = False

    def event(self, name, detail='', elapsed=None, at=None):
        seconds = time.monotonic() - self.started if elapsed is None else elapsed
        if name not in self.events:
            self.events[name] = seconds
        value = 'NA' if seconds == 'NA' else f'{seconds:.3f}s'
        print(f'Dev timing: at={at or iso_time()} elapsed={value} event={name}'
              + (' ' + detail if detail else ''), flush=True)

    def commit(self, candidate):
        revision = candidate.get('development_revision', '')
        self.target = revision if isinstance(revision, str) and re.fullmatch(r'[a-f0-9]{40}', revision) else None
        if candidate.get('development_unchanged'):
            self.event('development_digest_unchanged', 'git_commit=not_required')
            return
        committed = candidate.get('development_committed_at')
        try:
            value = datetime.fromisoformat(committed)
            if value.tzinfo is None:
                raise ValueError('Timezone required')
            seconds = value.timestamp() - self.wall_started
        except (TypeError, ValueError):
            self.event('git_commit_completed', 'timestamp=unavailable', elapsed='NA')
        else:
            self.event('git_commit_completed', 'source=candidate_metadata', elapsed=seconds, at=value.isoformat(timespec='milliseconds'))

    def observe(self, progress, candidate_ready=False):
        revision = progress.get('revision')
        if self.initial_revision is None and revision:
            self.initial_revision = revision
        changed = bool(revision and self.initial_revision and revision != self.initial_revision)
        exact = bool(self.target and revision == self.target)
        self.revision_observed = self.revision_observed or exact
        if ('argo_detected' not in self.events and
                (progress['sync'] == 'OutOfSync' or changed or exact)):
            self.event('argo_detected', 'signal=' + ('candidate_revision' if exact else 'revision_changed' if changed else 'out_of_sync'))
        # An old Synced/Healthy observation is not a candidate milestone.
        # Exact image readiness remains the existing gate, not this telemetry.
        bound = self.revision_observed or candidate_ready
        if bound and progress['sync'] == 'Synced' and 'synced' not in self.events:
            self.event('synced', 'source=' + ('candidate_revision' if self.revision_observed else 'exact_candidate_ready'))
        if bound and progress['sync'] == 'Synced' and progress['health'] == 'Healthy' and 'healthy' not in self.events:
            self.event('healthy', 'source=' + ('candidate_revision' if self.revision_observed else 'exact_candidate_ready'))

    def summary(self, outcome):
        def duration(end, start=None):
            right = self.events.get(end)
            left = self.events.get(start) if start else 0
            return f'{max(0, right-left):.3f}s' if isinstance(right, (int, float)) and isinstance(left, (int, float)) else 'NA'
        print(f'Dev timing summary: at={iso_time()} detect={duration("argo_detected")}'
              f' sync={duration("synced", "argo_detected")} healthy={duration("healthy", "synced")}'
              f' job={duration("job_completed" if "job_completed" in self.events else "job_wait_ended", "job_created")}'
              f' total={time.monotonic()-self.started:.3f}s'
              f' outcome={outcome}', flush=True)


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
        if args[0] == 'kubectl':
            error = (result.stderr or '').lower()
            if 'forbidden' in error:
                reason = 'Forbidden: Jenkins kubeconfig lacks access. Check the exact Argo Application name and its Role/RoleBinding; run configure-ci-app-access.py on mgmt if needed.'
            elif 'notfound' in error or 'not found' in error:
                reason = 'NotFound: check the configured Application name, namespace and required Dev resources.'
            elif 'unauthorized' in error or 'must be logged in' in error:
                reason = 'Unauthorized: check the Jenkins kubeconfig credential and ServiceAccount token.'
            elif 'x509' in error or 'certificate' in error:
                reason = 'TLS: check the Kubernetes API CA and certificate validity in the kubeconfig.'
            elif any(value in error for value in ['connection refused', 'unable to connect', 'i/o timeout',
                                                   'context deadline exceeded', 'no route to host', 'no such host']):
                reason = 'API connection failed: check the kubeconfig server address and agent connectivity.'
            else:
                reason = 'unclassified error: check the relevant resource from mgmt; child output withheld.'
            raise DeliveryPreflightError('Kubernetes command failed (' + reason + ')')
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
        raise DeliveryPreflightError('Image must be in the ChargeOps registry')
    if '@' in reference:
        repo, tag = reference.split('@', 1)
    else:
        repo, tag = reference.rsplit(':', 1)
    path = repo.removeprefix(REGISTRY + '/')
    if not re.fullmatch(r'[a-z0-9/._-]+', path) or not re.fullmatch(r'[A-Za-z0-9._:-]+', tag):
        raise DeliveryPreflightError('Invalid registry reference')
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
        raise DeliveryPreflightError('Registry digest is missing or does not match the manifest')
    return repo + '@' + digest


def pin_image(repo, overlay, reference):
    if not re.fullmatch(re.escape(IMAGE) + r'@sha256:[a-f0-9]{64}', reference):
        raise DeliveryPreflightError('Only a pinned CSMS candidate may be promoted')
    path = Path(repo) / overlay / 'kustomization.yaml'
    data = yaml.safe_load(path.read_text(encoding='utf-8'))
    image = next((i for i in data.get('images', []) if i['name'] == IMAGE), None)
    if image is None:
        if overlay != CANARY_DEV:
            raise DeliveryPreflightError('Environment CSMS image override is missing')
        image = {'name': IMAGE}
        data.setdefault('images', []).append(image)
    image['newName'], image['digest'] = reference.split('@')
    image.pop('newTag', None)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding='utf-8')


def image_repository(reference):
    repository = reference.split('@', 1)[0]
    prefix, _, name = repository.rpartition('/')
    return prefix + '/' + name.split(':', 1)[0]


def workload_containers(document):
    spec = document.get('spec', {})
    if document['kind'] == 'Deployment':
        return spec.get('template', {}).get('spec', {}).get('containers', [])
    if document['kind'] == 'CronJob':
        return spec.get('jobTemplate', {}).get('spec', {}).get('template', {}).get('spec', {}).get('containers', [])
    return []


def candidate_images(candidate):
    images = candidate.get('images', {IMAGE: candidate['image']})
    built = declared_built_images(candidate)
    csms_required = built is None or 'chargeops-csms' in built
    if (not isinstance(images, dict) or csms_required and images.get(IMAGE) != candidate['image']
            or IMAGE in images and images[IMAGE] != candidate['image']):
        raise DeliveryPreflightError('Release images must include the verified CSMS digest')
    for repository, reference in images.items():
        if repository not in IMAGE_ALIASES or not isinstance(reference, str) or not re.fullmatch(
                re.escape(repository) + r'@sha256:[a-f0-9]{64}', reference):
            raise DeliveryPreflightError('Release contains an unknown or unpinned application image')
    return images


def declared_built_images(candidate):
    if 'built_images' not in candidate:
        return None
    names = candidate['built_images']
    allowed = {repo.rsplit('/', 1)[-1] for repo in APP_IMAGES.values()}
    if (not isinstance(names, list) or any(not isinstance(name, str) for name in names)
            or len(set(names)) != len(names) or set(names) - allowed):
        raise DeliveryPreflightError('Built image list is malformed')
    return names


def selected_candidate_images(candidate):
    images = candidate_images(candidate)
    if 'built_images' not in candidate:
        return images  # Existing CSMS-only candidate/receipt compatibility.
    names = candidate['built_images']
    if not isinstance(names, list) or any(not isinstance(name, str) for name in names):
        raise DeliveryPreflightError('Built image list is malformed')
    selected = {repo: image for repo, image in images.items() if repo.rsplit('/', 1)[-1] in names}
    if set(names) != {repo.rsplit('/', 1)[-1] for repo in selected}:
        raise DeliveryPreflightError('Built images do not match the pinned application selection')
    return selected


def csms_canary_scope(candidate):
    names = declared_built_images(candidate)
    return names is None or 'chargeops-csms' in names


def application_digest_snapshot(repo, overlay):
    """Canonical pinned app identities, including duplicate collector CronJobs."""
    result = {}
    for document in rendered(repo, overlay):
        for container in workload_containers(document):
            reference = container['image']
            repository = image_repository(reference)
            canonical = next((name for name, aliases in IMAGE_ALIASES.items() if repository in aliases), None)
            if canonical is None:
                continue
            if not re.fullmatch(re.escape(repository) + r'@sha256:[a-f0-9]{64}', reference):
                raise DeliveryPreflightError('Application baseline must be digest-pinned: ' + canonical)
            pinned = canonical + '@' + reference.split('@', 1)[1]
            if canonical in result and result[canonical] != pinned:
                raise DeliveryPreflightError('Application baseline contains conflicting digests: ' + canonical)
            result[canonical] = pinned
    return result


def pin_application_images(repo, images):
    """Pin only selected apps in prod, including the legacy collector image alias."""
    path = Path(repo) / PROD / 'kustomization.yaml'
    data = yaml.safe_load(path.read_text(encoding='utf-8'))
    documents = rendered(repo, PROD)
    for repository, reference in images.items():
        names = {image_repository(c['image']) for d in documents for c in workload_containers(d)
                 if image_repository(c['image']) in IMAGE_ALIASES[repository]}
        if not names:
            raise DeliveryPreflightError('Selected application has no production workload: ' + repository)
        for name in sorted(names):
            override = next((i for i in data.get('images', []) if i['name'] == name), None)
            if override is None:
                override = {'name': name}
                data.setdefault('images', []).append(override)
            override.update(newName=repository, digest=reference.split('@', 1)[1])
            override.pop('newTag', None)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding='utf-8')


def application_workloads(repo, images, overlay=CANARY):
    """Derive bounded Sync targets from the exact approved rendered images."""
    found, workloads = set(), []
    for document in rendered(repo, overlay):
        containers = {c['name']: c['image'] for c in workload_containers(document)
                      if c['image'] in images.values()}
        if not containers:
            continue
        found.update(image_repository(image) for image in containers.values())
        if document['metadata']['name'] == 'csms' and document['kind'] == 'Deployment':
            continue
        workloads.append({'group': document['apiVersion'].split('/')[0], 'kind': document['kind'],
                          'name': document['metadata']['name'], 'images': containers})
    if found != set(images):
        raise DeliveryPreflightError('Rendered ops workloads do not contain every approved application digest')
    return workloads


def ensure_clean(repo):
    if run('git', 'status', '--porcelain', cwd=repo):
        raise DeliveryPreflightError('GitOps checkout must be clean')
    if run('git', 'branch', '--show-current', cwd=repo) != 'main':
        raise DeliveryPreflightError('GitOps checkout must be on main')
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


def commit(repo, overlay, message, allow_gate_baseline=False):
    allowed = {overlay + '/kustomization.yaml'}
    changed = run('git', 'diff', 'HEAD', '--name-only', cwd=repo).splitlines()
    gate_file = overlay + '/gate-settings.yaml'
    if allow_gate_baseline and overlay in (CANARY_DEV, PROD) and gate_file in changed:
        before = yaml.safe_load(run('git', 'show', 'HEAD:' + gate_file, cwd=repo))
        after = yaml.safe_load((Path(repo) / gate_file).read_text(encoding='utf-8'))
        try:
            before['data'].pop('GATE_BASELINE_AT')
            timestamp = after['data'].pop('GATE_BASELINE_AT')
        except (KeyError, TypeError, AttributeError):
            raise DeliveryPreflightError('Gate baseline settings are malformed') from None
        if not re.fullmatch(r'[0-9]{10}', str(timestamp)) or before != after:
            raise DeliveryPreflightError('Delivery may update only the gate baseline timestamp, not gate policy')
        allowed.add(gate_file)
    if set(changed) - allowed:
        raise DeliveryPreflightError('Delivery may change only its environment image file')
    if changed:
        run('git', 'add', '--', *sorted(set(changed)), cwd=repo)
        run('git', '-c', 'user.name=ChargeOps CI', '-c', 'user.email=chargeops-ci@example.invalid',
            'commit', '-m', message, cwd=repo)
        run('git', 'push', 'origin', 'HEAD:main', cwd=repo, env=git_environment(repo))
    return run('git', 'rev-parse', 'HEAD', cwd=repo)


def stamp_gate_baseline(repo, overlay):
    path = Path(repo) / overlay / 'gate-settings.yaml'
    if path.exists():
        settings = yaml.safe_load(path.read_text(encoding='utf-8'))
        settings['data']['GATE_BASELINE_AT'] = str(int(time.time()))
        path.write_text(yaml.safe_dump(settings, sort_keys=False), encoding='utf-8')


def candidate_check(candidate):
    if candidate.get('schema') != 1 or not re.fullmatch(
            re.escape(IMAGE) + r'@sha256:[a-f0-9]{64}', candidate.get('image', '')):
        raise DeliveryPreflightError('Invalid CSMS candidate')
    candidate_images(candidate)
    if candidate.get('verification_mode', 'read-only') not in ['read-only', 'ocpp-fixture', 'ocpp-simulation']:
        raise DeliveryPreflightError('Unknown verification mode')
    validate_dev_overlay()
    if candidate.get('development_overlay', LEGACY_DEV) != DEV:
        raise DeliveryPreflightError('Candidate belongs to a different Dev overlay')


def validate_dev_overlay():
    if DEV not in (LEGACY_DEV, CANARY_DEV, ROLLING_DEV):
        raise DeliveryPreflightError('DEV_GITOPS_OVERLAY must select an allowed Dev delivery overlay')


def detect_dev_overlay():
    app = kubectl('-n', 'argocd', 'get', 'application', APPLICATION)
    spec = app['spec']
    path = spec['source'].get('path')
    if (spec['destination']['namespace'] != 'dev' or path not in (LEGACY_DEV, CANARY_DEV, ROLLING_DEV)
            or spec['source'].get('targetRevision') != 'main'
            or spec['source'].get('repoURL') != 'https://github.com/raspberry-2jo/chargeops-gitops.git'):
        raise DeliveryPreflightError('Dev Application target is outside the allowed Dev delivery paths')
    return path


def prepare(repo, tag, source, output, ocpp_fixture_test=False, canary=False, build_images=None):
    validate_dev_overlay()
    if not re.fullmatch(r'build-(?:(?:kim_chanju|dev)-)?[0-9]+-[a-f0-9]{12}', tag):
        raise DeliveryPreflightError('Only a unique build tag may identify a candidate')
    if not re.fullmatch(r'[a-f0-9]{40}', source):
        raise DeliveryPreflightError('Source must be a full Git commit')
    ensure_clean(repo)
    before = production_fingerprint(repo)
    prod_image = csms_image(repo, PROD)
    if '@sha256:' not in prod_image:
        raise DeliveryPreflightError('Production CSMS is not digest-pinned in GitOps. Run freeze-production.py on mgmt, then commit and push overlays/prod/kustomization.yaml before retrying')
    selected = None if build_images is None else set(build_images.split())
    allowed = {image.rsplit('/', 1)[-1]: image for image in APP_IMAGES.values()}
    if selected is not None and selected - allowed.keys():
        raise DeliveryPreflightError('Unknown image selected for application delivery')
    previous_dev = csms_image(repo, DEV)
    csms_selected = selected is None or 'chargeops-csms' in selected
    if not csms_selected:
        canary = False
        print('canary skipped: ' + ('chargeops-csms unchanged' if selected else 'no image changes'), flush=True)
    candidate_image = resolve_digest(IMAGE + ':' + tag) if csms_selected else previous_dev
    if not re.fullmatch(re.escape(IMAGE) + r'@sha256:[a-f0-9]{64}', candidate_image):
        raise DeliveryPreflightError('Current Dev CSMS must be digest-pinned when not rebuilt')
    candidate = {'schema': 1, 'source': source, 'build_tag': tag, 'image': candidate_image,
                 'verification_mode': 'ocpp-simulation' if DEV == ROLLING_DEV else ('ocpp-fixture' if ocpp_fixture_test else 'read-only'),
                 'production_fingerprint': before, 'previous_production_image': prod_image,
                 'development_overlay': DEV}
    if build_images is not None:
        images = {IMAGE: candidate['image']} if csms_selected else {}
        for name in sorted(selected - {'chargeops-csms'}):
            repository = allowed[name]
            images[repository] = resolve_digest(repository + ':' + tag)
        candidate['images'] = images
        candidate['built_images'] = sorted(selected)
        # Bind unchanged application digests too, without rebuilding or repinning them.
        candidate['release_images'] = application_digest_snapshot(repo, PROD)
        candidate['release_images'].update(images)
        candidate['previous_development_images'] = application_digest_snapshot(repo, DEV)
        candidate['development_images'] = dict(candidate['previous_development_images'])
        candidate['development_images'][IMAGE] = candidate['image']
        candidate_images(candidate)
    if DEV in (CANARY_DEV, ROLLING_DEV):
        previous = csms_image(repo, DEV)
        if not re.fullmatch(re.escape(IMAGE) + r'@sha256:[a-f0-9]{64}', previous) or not live_candidate(previous):
            raise DeliveryPreflightError('Dev baseline must first be bootstrapped and Healthy')
        candidate.update(development_mode='canary' if DEV == CANARY_DEV else 'rolling', previous_development_image=previous)
    if canary:
        candidate['canary_fingerprint'] = fingerprint(rendered(repo, CANARY))
    # A fixed pre-delivery timestamp is reused by all Canary analysis steps.
    # Keep the operator's enable flag; never fabricate a baseline during analysis.
    if DEV == CANARY_DEV and csms_selected:
        stamp_gate_baseline(repo, DEV)
    if csms_selected:
        pin_image(repo, DEV, candidate['image'])
    if selected is not None:
        if application_digest_snapshot(repo, DEV) != candidate['development_images']:
            raise DeliveryPreflightError('Development digest combination differs from the prepared candidate')
    if production_fingerprint(repo) != before:
        raise DeliveryPreflightError('Development update changed production')
    candidate['development_fingerprint'] = fingerprint(rendered(repo, DEV))
    candidate['development_unchanged'] = not csms_selected
    if candidate['development_unchanged']:
        candidate['development_revision'] = run('git', 'rev-parse', 'HEAD', cwd=repo)
    else:
        candidate['development_revision'] = commit(repo, DEV, f'build(dev): verify CSMS {tag}',
                                                  allow_gate_baseline=True)
        candidate['development_committed_at'] = iso_time()
    save(output, candidate)
    if candidate['development_unchanged']:
        print('Development digest unchanged; verifying existing deployment without a GitOps commit', flush=True)
    else:
        print('Dev timing: at=' + candidate['development_committed_at']
              + ' elapsed=NA event=git_commit_completed verify=not_started', flush=True)
        print('Development digest committed; production unchanged')


def kubectl(*args):
    sample = _active_poll.get()
    if sample is None:
        return json.loads(run('kubectl', *args, '-o', 'json'))
    started = time.monotonic()
    try:
        return json.loads(run('kubectl', *args, '-o', 'json'))
    finally:
        sample['api_calls'] += 1
        sample['api_seconds'] += max(0, time.monotonic()-started)


def live_candidate(image):
    validate_dev_overlay()
    app = kubectl('-n', 'argocd', 'get', 'application', APPLICATION)
    spec, status = app['spec'], app.get('status', {})
    if spec['destination']['namespace'] != 'dev' or spec['source']['path'] != DEV:
        return False
    if status.get('sync', {}).get('status') != 'Synced' or status.get('health', {}).get('status') != 'Healthy':
        return False
    deployment = kubectl('-n', 'dev', 'get', 'deployment', 'csms')
    if DEV == CANARY_DEV:
        return dev_rollout_ready(deployment, image)
    desired = 2 if DEV == ROLLING_DEV else 1
    if deployment['spec'].get('replicas') != desired:
        return False
    container = next(c for c in deployment['spec']['template']['spec']['containers'] if c['name'] == 'csms')
    state = deployment.get('status', {})
    if container['image'] != image or state.get('observedGeneration', 0) < deployment['metadata']['generation']:
        return False
    if any(state.get(key) != desired for key in ('updatedReplicas', 'availableReplicas', 'readyReplicas', 'replicas')):
        return False
    if DEV == ROLLING_DEV:
        if deployment['spec'].get('strategy', {}).get('type') != 'RollingUpdate':
            return False
        replicasets = kubectl('-n', 'dev', 'get', 'replicasets', '-l', 'app=csms')['items']
        owned = {r['metadata']['uid'] for r in replicasets if any(
            o.get('controller') and o.get('uid') == deployment['metadata']['uid'] for o in r['metadata'].get('ownerReferences', []))}
    else:
        owned = None
    pods = kubectl('-n', 'dev', 'get', 'pods', '-l', 'app=csms')['items']
    active = [p for p in pods if not p['metadata'].get('deletionTimestamp')]
    return len(active) == desired and all(
        next(c['image'] for c in p['spec']['containers'] if c['name'] == 'csms') == image
        and (owned is None or any(o.get('controller') and o.get('uid') in owned for o in p['metadata'].get('ownerReferences', [])))
        and any(c['type'] == 'Ready' and c['status'] == 'True' for c in p.get('status', {}).get('conditions', []))
        for p in active)


def dev_rollout_ready(deployment, image):
    """Bind readiness to the exact desired image and current Rollout-owned RS."""
    rollout = kubectl('-n', 'dev', 'get', 'rollout', 'csms')
    spec, state = rollout['spec'], rollout.get('status', {})
    if (deployment['spec'].get('replicas') != 0 or spec.get('replicas') != 2
            or rollout['metadata'].get('annotations', {}).get('chargeops.io/dev-error-every', '0') != '0'
            or spec.get('workloadRef', {}).get('name') != 'csms'
            or spec.get('workloadRef', {}).get('scaleDown') != 'never'):
        return False
    container = next(c for c in deployment['spec']['template']['spec']['containers'] if c['name'] == 'csms')
    if (container['image'] != image or state.get('phase') != 'Healthy' or state.get('abort')
            or int(state.get('observedGeneration', 0)) < rollout['metadata']['generation']
            or int(state.get('workloadObservedGeneration', 0)) < deployment['metadata']['generation']
            or not state.get('stableRS') or state['stableRS'] != state.get('currentPodHash')
            or any(state.get(key, 0) != 2 for key in ('replicas', 'readyReplicas', 'availableReplicas', 'updatedReplicas'))):
        return False
    rs = kubectl('-n', 'dev', 'get', 'replicaset', 'csms-' + state['stableRS'])
    if not any(o.get('controller') and o.get('uid') == rollout['metadata']['uid']
               for o in rs['metadata'].get('ownerReferences', [])):
        return False
    if next(c['image'] for c in rs['spec']['template']['spec']['containers'] if c['name'] == 'csms') != image:
        return False
    pods = kubectl('-n', 'dev', 'get', 'pods', '-l', 'app=csms')['items']
    active = [p for p in pods if not p['metadata'].get('deletionTimestamp')]
    return len(active) == 2 and all(
        any(o.get('controller') and o.get('uid') == rs['metadata']['uid']
            for o in p['metadata'].get('ownerReferences', []))
        and next(c['image'] for c in p['spec']['containers'] if c['name'] == 'csms') == image
        and any(c['type'] == 'Ready' and c['status'] == 'True' for c in p.get('status', {}).get('conditions', []))
        for p in active)


def dev_rollout_failed(image):
    if DEV == ROLLING_DEV:
        deployment = kubectl('-n', 'dev', 'get', 'deployment', 'csms')
        if next(c['image'] for c in deployment['spec']['template']['spec']['containers'] if c['name'] == 'csms') != image:
            return False
        return any(c.get('type') == 'Progressing' and c.get('status') == 'False'
                   and c.get('reason') == 'ProgressDeadlineExceeded' for c in deployment.get('status', {}).get('conditions', []))
    if DEV != CANARY_DEV:
        return False
    deployment = kubectl('-n', 'dev', 'get', 'deployment', 'csms')
    if next(c['image'] for c in deployment['spec']['template']['spec']['containers'] if c['name'] == 'csms') != image:
        return False
    rollout = kubectl('-n', 'dev', 'get', 'rollout', 'csms')
    state = rollout.get('status', {})
    if not (state.get('abort') or state.get('phase') == 'Degraded') or not state.get('currentPodHash'):
        return False
    rs = kubectl('-n', 'dev', 'get', 'replicaset', 'csms-' + state['currentPodHash'])
    return (any(o.get('controller') and o.get('uid') == rollout['metadata']['uid']
                for o in rs['metadata'].get('ownerReferences', []))
            and next(c['image'] for c in rs['spec']['template']['spec']['containers'] if c['name'] == 'csms') == image)


def verification_job(candidate):
    probe = Path(__file__).with_name('csms_smoke.py').read_text(encoding='utf-8')
    name = 'csms-check-' + fingerprint(candidate)[:16]
    job = {'apiVersion': 'batch/v1', 'kind': 'Job', 'metadata': {'name': name, 'namespace': 'dev'},
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
    if DEV == ROLLING_DEV:
        pod = job['spec']['template']
        pod['metadata'] = {'labels': {'chargeops.io/role': 'dev-release-probe'}}
        job['spec']['activeDeadlineSeconds'] = 240
        container = pod['spec']['containers'][0]
        container['command'] = ['python', '-m', 'csms.release_probe']
        container['env'] = [{'name': 'POD_NAMESPACE', 'valueFrom': {'fieldRef': {'fieldPath': 'metadata.namespace'}}},
                            {'name': 'OCPP_TEST_CHARGER_ID', 'value': 'CI-DEV-CANARY-01'}]
        container['ports'] = [{'name': 'probe-metrics', 'containerPort': 9110}]
    return job


def dev_progress():
    app = kubectl('-n', 'argocd', 'get', 'application', APPLICATION).get('status', {})
    progress = {'sync': app.get('sync', {}).get('status', 'Unknown'),
                'health': app.get('health', {}).get('status', 'Unknown'),
                'revision': app.get('sync', {}).get('revision')}
    if DEV == CANARY_DEV:
        rollout = kubectl('-n', 'dev', 'get', 'rollout', 'csms')
        state = rollout.get('status', {})
        analysis = state.get('currentStepAnalysisRunStatus') or {}
        progress.update(rollout=state.get('phase', 'Unknown'), step=state.get('currentStepIndex', 0),
                        steps=len(rollout.get('spec', {}).get('strategy', {}).get('canary', {}).get('steps', [])),
                        ready=state.get('readyReplicas', 0), desired=rollout.get('spec', {}).get('replicas', 0),
                        analysis=analysis.get('name', 'none'), analysis_phase=analysis.get('phase', 'none'))
    return progress


def log_dev_progress(started, timeout, progress=None):
    progress = dev_progress() if progress is None else progress
    message = (f"Dev verification: elapsed={int(time.monotonic() - started)}s/{timeout}s"
               f", sync={progress['sync']}, health={progress['health']}")
    if DEV == CANARY_DEV:
        message += (f", rollout={progress['rollout']}, step={progress['step']}/{progress['steps']}"
                    f", ready={progress['ready']}/{progress['desired']}"
                    f", analysis={progress['analysis']}, analysis_phase={progress['analysis_phase']}")
    print(message, flush=True)


def verify(repo, candidate_path, receipt_path, timeout):
    timing = DevVerificationTiming()
    outcome = 'failed'
    try:
        _verify(repo, candidate_path, receipt_path, timeout, timing)
        outcome = 'success'
    finally:
        timing.summary(outcome)


def _verify(repo, candidate_path, receipt_path, timeout, timing):
    candidate = load(candidate_path)
    candidate_check(candidate)
    ensure_clean(repo)
    if fingerprint(rendered(repo, DEV)) != candidate['development_fingerprint']:
        raise DeliveryPreflightError('Development configuration changed before verification')
    if not re.fullmatch(r'[a-z0-9][a-z0-9.-]{0,252}', APPLICATION):
        raise DeliveryPreflightError('DEV_ARGO_APPLICATION must be a Kubernetes resource name')
    print('Checking Dev Application=' + APPLICATION + ', namespace=dev, path=' + DEV, flush=True)
    timing.commit(candidate)
    started = time.monotonic()
    interval = poll_seconds()
    deadline = started + timeout
    next_progress = started
    last_progress = None
    diagnostics = PollDiagnostics("dev_verify", interval, __file__)
    while (now := time.monotonic()) < deadline:
        with diagnostics.sample(now) as poll:
            if dev_rollout_failed(candidate['image']):
                raise DeliveryPreflightError('Dev RollingUpdate failed; Dev recovery is required' if DEV == ROLLING_DEV else
                                             'Dev Canary analysis aborted the candidate; Dev recovery is required')
            try:
                progress = dev_progress()
            except (DeliveryPreflightError, RuntimeError):
                # A diagnostic read must not introduce a new delivery verdict.
                progress = {'sync': 'Unknown', 'health': 'Unknown'}
                if DEV == CANARY_DEV:
                    progress.update(rollout='Unknown', step=0, steps=0, ready=0, desired=0,
                                    analysis='none', analysis_phase='none')
            ready = live_candidate(candidate['image'])
            timing.observe(progress, candidate_ready=ready)
            if ready:
                poll["terminal"] = True
                break
            if progress != last_progress or time.monotonic() >= next_progress:
                log_dev_progress(started, timeout, progress)
                last_progress = progress
                next_progress = time.monotonic() + 30
        time.sleep(interval)
    else:
        log_dev_progress(started, timeout)
        raise DeliveryPreflightError('Development did not converge to the exact candidate')
    job = verification_job(candidate)
    name = job['metadata']['name']
    run('kubectl', 'create', '-f', '-', payload=json.dumps(job))
    timing.event('job_created', 'job=' + name)
    try:
        run('kubectl', '-n', 'dev', 'wait', '--for=condition=complete', f'job/{name}', '--timeout=270s')
    except Exception:
        # Preserve the original wait exception and verdict; diagnostics are best effort.
        outcome = 'wait_failed_or_timeout'
        try:
            state = kubectl('-n', 'dev', 'get', 'job', name).get('status', {})
            if any(c.get('type') == 'Failed' and c.get('status') == 'True' for c in state.get('conditions', [])):
                outcome = 'failed'
        except Exception:
            pass
        timing.event('job_completed' if outcome == 'failed' else 'job_wait_ended',
                     'job=' + name + ' outcome=' + outcome)
        raise
    timing.event('job_completed', 'job=' + name + ' outcome=success')
    result = kubectl('-n', 'dev', 'get', 'job', name)
    print(run('kubectl', '-n', 'dev', 'logs', f'job/{name}', '--all-containers=true'))
    if not live_candidate(candidate['image']):
        raise DeliveryPreflightError('Development changed during verification')
    ensure_clean(repo)
    if fingerprint(rendered(repo, DEV)) != candidate['development_fingerprint']:
        raise DeliveryPreflightError('Development configuration changed during verification')
    save(receipt_path, {'candidate': fingerprint(candidate), 'image': candidate['image'],
                        'job': name, 'job_uid': result['metadata']['uid'],
                        'verified_at': int(time.time())})
    print('Exact development digest passed ' + candidate['verification_mode'] + ' integration checks')


def recover_dev(repo, candidate_path, recovery_path, timeout=600):
    """Restore only this failed candidate's Dev image; never use Ops credentials."""
    candidate = load(candidate_path)
    candidate_check(candidate)
    if candidate.get('development_unchanged'):
        print('Dev recovery skipped: verification-only; no Dev image was modified')
        return
    if candidate.get('development_mode') not in ('canary', 'rolling') or DEV not in (CANARY_DEV, ROLLING_DEV):
        print('Dev image recovery skipped: candidate did not use managed Dev delivery')
        return
    previous = candidate.get('previous_development_image', '')
    if not re.fullmatch(re.escape(IMAGE) + r'@sha256:[a-f0-9]{64}', previous):
        raise DeliveryPreflightError('Dev recovery has no verified pinned baseline')
    ensure_clean(repo)
    production_before = production_fingerprint(repo)
    current = csms_image(repo, DEV)
    owns_recovery = (Path(recovery_path).is_file()
        and load(recovery_path).get('candidate') == fingerprint(candidate))
    if current == candidate['image']:
        if fingerprint(rendered(repo, DEV)) != candidate['development_fingerprint']:
            raise DeliveryPreflightError('Dev configuration changed; recovery will not overwrite it')
        pin_image(repo, DEV, previous)
        if production_fingerprint(repo) != production_before:
            raise DeliveryPreflightError('Dev recovery unexpectedly changed production')
        save(recovery_path, {'candidate': fingerprint(candidate), 'phase': 'recovering',
                             'image': previous, 'revision': None})
        revision = commit(repo, DEV, 'revert(dev): restore baseline after failed verification')
        receipt = load(recovery_path)
        receipt['revision'] = revision
        save(recovery_path, receipt)
    elif current != previous or not owns_recovery:
        raise DeliveryPreflightError('Dev has a newer or unrelated image; recovery will not overwrite it')
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if live_candidate(previous):
            receipt = load(recovery_path)
            receipt.update(phase='recovered', verified_at=int(time.time()))
            save(recovery_path, receipt)
            print('Dev recovery complete: previous Git digest restored and Dev workload verified')
            return
        time.sleep(5)
    raise DeliveryPreflightError('Dev recovery Git digest restored; stable Rollout verification timed out')


def validate_receipt(candidate, receipt, now):
    candidate_check(candidate)
    if receipt.get('candidate') != fingerprint(candidate) or receipt.get('image') != candidate['image']:
        raise DeliveryPreflightError('Verification receipt belongs to a different candidate')
    age = now - receipt.get('verified_at', 0)
    if age < 0 or age > 7200:
        raise DeliveryPreflightError('Verification is expired; run a new development test')


def promote(repo, candidate_path, receipt_path, delivery_path='artifacts/production-delivery.json', canary=False,
            require_approved_pr=False):
    candidate, receipt = load(candidate_path), load(receipt_path)
    validate_receipt(candidate, receipt, int(time.time()))
    if candidate.get('built_images') == []:
        print('Ops promote skipped: no image changes; canary skipped: no image changes', flush=True)
        return
    if not csms_canary_scope(candidate):
        canary = False
        print('canary skipped: chargeops-csms unchanged', flush=True)
    approval = None
    if require_approved_pr:
        branch = os.environ.get('CHECKOUT_BRANCH')
        if branch not in ('kim_chanju', 'dev') or os.environ.get('CHANGE_ID'):
            raise DeliveryPreflightError('Only a reviewed dev merge may promote production')
        if branch == 'kim_chanju':
            from github_cd import approved_candidate
            approval = approved_candidate(candidate)
        else:
            from github_cd import approved_merge
            approval = approved_merge(candidate['source'])
    ensure_clean(repo)
    if production_fingerprint(repo) != candidate['production_fingerprint']:
        raise DeliveryPreflightError('Production changed while approval was pending')
    if canary and candidate.get('canary_fingerprint') != fingerprint(rendered(repo, CANARY)):
        raise DeliveryPreflightError('Canary policy changed or was not included in development verification')
    if (fingerprint(rendered(repo, DEV)) != candidate['development_fingerprint']
            or csms_image(repo, DEV) != candidate['image'] or not live_candidate(candidate['image'])):
        raise DeliveryPreflightError('Development no longer runs the verified candidate')
    if 'development_images' in candidate and application_digest_snapshot(repo, DEV) != candidate['development_images']:
        raise DeliveryPreflightError('Development digest combination changed after verification')
    job = kubectl('-n', 'dev', 'get', 'job', receipt['job'])
    if job['metadata']['uid'] != receipt['job_uid'] or job.get('status', {}).get('succeeded') != 1:
        raise DeliveryPreflightError('Successful verification job is missing or replaced')
    delivery = {'schema': 1, 'candidate': fingerprint(candidate), 'image': candidate['image'],
                'previous_image': candidate['previous_production_image'], 'phase': 'prepared', 'canary': canary}
    if 'built_images' in candidate:
        delivery['built_images'] = candidate['built_images']
    if 'images' in candidate:
        delivery['images'] = selected_candidate_images(candidate)
    if 'release_images' in candidate:
        delivery['release_images'] = candidate['release_images']
    if approval:
        delivery['source_approval'] = approval
    save(delivery_path, delivery)
    dev_before = rendered(repo, DEV)
    if canary:
        stamp_gate_baseline(repo, PROD)
    if 'images' in candidate:
        pin_application_images(repo, selected_candidate_images(candidate))
        delivery['workloads'] = application_workloads(repo, selected_candidate_images(candidate), CANARY if canary else PROD)
    else:
        pin_image(repo, PROD, candidate['image'])
    if 'release_images' in candidate and application_digest_snapshot(repo, PROD) != candidate['release_images']:
        raise DeliveryPreflightError('Ops promotion does not match the verified release digest combination')
    if rendered(repo, DEV) != dev_before:
        raise DeliveryPreflightError('Production promotion changed development')
    revision = commit(repo, PROD, f'promote(ops): approved apps, CSMS canary {candidate["source"][:12]}',
                      allow_gate_baseline=canary)
    delivery.update(revision=revision, phase='pending', production_fingerprint=production_fingerprint(repo))
    if canary:
        delivery['canary_fingerprint'] = fingerprint(rendered(repo, CANARY))
    save(delivery_path, delivery)
    print(f'Approved digest committed at {revision}; ' + ('Canary Sync is ready' if canary else 'regular Ops Sync is ready; Canary is not authorized'))


def reset_artifacts(preserve_delivery=False):
    path = Path('artifacts/production-delivery.json')
    if path.exists():
        delivery = load(path)
        if delivery.get('canary') and delivery.get('phase') not in ['succeeded', 'recovered']:
            raise DeliveryPreflightError('Unfinished Canary recovery exists. Run canary_cd.py recover before starting a new build; its receipt was preserved')
    recovery = Path('artifacts/dev-recovery.json')
    if recovery.is_file() and load(recovery).get('phase') != 'recovered':
        raise DeliveryPreflightError('Unfinished Dev recovery exists; run recover-dev before starting a new build')
    for name in ['candidate.json', 'verification.json', 'production-delivery.json', 'pull-request.json', 'source-approval.json', 'dev-recovery.json']:
        if preserve_delivery and name == 'production-delivery.json':
            continue
        (Path('artifacts') / name).unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['prepare', 'verify', 'promote', 'describe', 'reset', 'recover-dev', 'dev-overlay'])
    parser.add_argument('--repo', default='.gitops-delivery')
    parser.add_argument('--candidate', default='artifacts/candidate.json')
    parser.add_argument('--receipt', default='artifacts/verification.json')
    parser.add_argument('--delivery', default='artifacts/production-delivery.json')
    parser.add_argument('--dev-recovery', default='artifacts/dev-recovery.json')
    parser.add_argument('--tag')
    parser.add_argument('--source')
    parser.add_argument('--images', help='Space-separated image names built and pushed by this Jenkins run')
    parser.add_argument('--timeout', type=int, help='Convergence seconds (Dev Canary verify: 900; otherwise: 600)')
    parser.add_argument('--ocpp-fixture-test', action='store_true')
    parser.add_argument('--canary', action='store_true')
    parser.add_argument('--require-approved-pr', action='store_true')
    parser.add_argument('--preserve-delivery', action='store_true', help='Keep prior delivery history for verification-only builds')
    args = parser.parse_args()
    if args.command == 'prepare':
        prepare(args.repo, args.tag or '', args.source or '', args.candidate, args.ocpp_fixture_test, args.canary, args.images)
    elif args.command == 'verify':
        timeout = args.timeout if args.timeout is not None else (900 if DEV == CANARY_DEV else 600)
        verify(args.repo, args.candidate, args.receipt, timeout)
    elif args.command == 'promote':
        promote(args.repo, args.candidate, args.receipt, args.delivery, args.canary, args.require_approved_pr)
    elif args.command == 'reset':
        reset_artifacts(args.preserve_delivery)
    elif args.command == 'recover-dev':
        recover_dev(args.repo, args.candidate, args.dev_recovery, args.timeout if args.timeout is not None else 600)
    elif args.command == 'dev-overlay':
        print(detect_dev_overlay())
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
    except DeliveryPreflightError as error:
        print(f'Delivery stopped: {error}')
        raise SystemExit(1)
    except Exception as error:
        # No secret/configuration payloads in exception output.
        print(f'Delivery stopped: {error.__class__.__name__}; inspect the relevant stage and prerequisites.')
        raise SystemExit(1)
