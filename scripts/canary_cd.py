"""Run an approved CSMS rollout; abort and restore its Git image on failure.

Only Argo CD writes the workload spec. The sole direct workload mutation is
Rollout/status abort, so an unhealthy candidate stops before Git recovery.
"""
import argparse
import json
from pathlib import Path
import signal
import time
import uuid

import env_cd as cd

OVERLAY = 'chargeops-chatops/overlays/canary'
APPLICATION = 'chargeops-chatops-dev'


def deployment_image():
    deployment = cd.kubectl('-n', 'ops', 'get', 'deployment', 'csms')
    image = next(c['image'] for c in deployment['spec']['template']['spec']['containers'] if c['name'] == 'csms')
    return deployment, image


def rollout_state(image):
    rollout = cd.kubectl('-n', 'ops', 'get', 'rollout', 'csms')
    status = rollout.get('status', {})
    deployment, actual = deployment_image()
    if actual != image or deployment['spec'].get('replicas') != 0:
        return rollout, False
    if (status.get('phase') != 'Healthy' or status.get('abort')
            or str(status.get('observedGeneration', '')) != str(rollout['metadata']['generation'])
            or not status.get('stableRS') or status['stableRS'] != status.get('currentPodHash')
            or status.get('availableReplicas', 0) < 4):
        return rollout, False
    replicasets = cd.kubectl('-n', 'ops', 'get', 'replicasets', '-l', 'app=csms')['items']
    stable = [r for r in replicasets if r['metadata'].get('labels', {}).get('rollouts-pod-template-hash') == status['stableRS']
              and any(o.get('uid') == rollout['metadata']['uid'] and o.get('controller') for o in r['metadata'].get('ownerReferences', []))]
    ready = len(stable) == 1 and stable[0].get('status', {}).get('availableReplicas', 0) >= 4 and any(
        c['name'] == 'csms' and c['image'] == image for c in stable[0]['spec']['template']['spec']['containers'])
    return rollout, ready


def check_application():
    app = cd.kubectl('-n', 'argocd', 'get', 'application', APPLICATION)
    spec = app['spec']
    if (spec['destination']['namespace'] != 'ops' or spec['source']['path'] != OVERLAY
            or spec.get('syncPolicy', {}).get('automated')):
        raise cd.DeliveryPreflightError('Bootstrap the manual ops Canary Application first')
    return app


def preflight(repo, candidate_path):
    candidate = cd.load(candidate_path)
    cd.candidate_check(candidate)
    cd.ensure_clean(repo)
    if cd.production_fingerprint(repo) != candidate['production_fingerprint']:
        raise cd.DeliveryPreflightError('Production changed before Canary approval')
    if candidate.get('canary_fingerprint') != cd.fingerprint(cd.rendered(repo, cd.CANARY)):
        raise cd.DeliveryPreflightError('Canary policy changed or was not part of the verified candidate')
    app = check_application()
    if app.get('operation') or app.get('status', {}).get('operationState', {}).get('phase') == 'Running':
        raise cd.DeliveryPreflightError('An ops sync operation is already running')
    _, healthy = rollout_state(candidate['previous_production_image'])
    if not healthy:
        raise cd.DeliveryPreflightError('Canary bootstrap or stable production readiness is incomplete')
    live, _ = deployment_image()
    desired = next(d for d in cd.rendered(repo, cd.PROD) if d['kind'] == 'Deployment' and d['metadata']['name'] == 'csms')
    if (live['spec']['template']['spec'] != desired['spec']['template']['spec']
            or live['spec']['template']['metadata'].get('labels') != desired['spec']['template']['metadata'].get('labels')):
        raise cd.DeliveryPreflightError('Live CSMS runtime differs from the preserved Git baseline; reconcile it before Canary')
    print('Canary preflight passed; no production changes')


def sync(revision):
    if not cd.re.fullmatch(r'[a-f0-9]{40}', revision):
        raise cd.DeliveryPreflightError('A committed Git revision is required for Sync')
    app = check_application()
    if app.get('operation') or app.get('status', {}).get('operationState', {}).get('phase') in ['Running', 'Terminating']:
        raise cd.DeliveryPreflightError('An ops Sync is already running; refusing a concurrent operation')
    # Selective Sync does not reapply unrelated workloads, CronJobs or PVCs.
    marker = 'chargeops-jenkins-' + uuid.uuid4().hex[:12]
    operation = {'operation': {'initiatedBy': {'username': marker},
                 'sync': {'revision': revision, 'prune': False,
                          'resources': [{'group': 'apps', 'kind': 'Deployment', 'name': 'csms'}]}}}
    cd.run('kubectl', '-n', 'argocd', 'patch', 'application', APPLICATION, '--type=merge', '-p', json.dumps(operation))
    return marker


def abort_if_current(image):
    _, actual = deployment_image()
    if actual != image:
        return  # A newer deployment must never be aborted by an old build.
    cd.run('kubectl', '-n', 'ops', 'patch', 'rollout', 'csms', '--subresource=status',
           '--type=merge', '-p', '{"status":{"abort":true}}')


def recover(repo, delivery_path, timeout=600):
    path = Path(delivery_path)
    if not path.exists():
        return
    delivery = cd.load(path)
    if not delivery.get('canary'):
        return  # Manual-only promotions never authorize automatic recovery.
    if delivery.get('phase') in ['succeeded', 'recovered']:
        return
    image, previous = delivery['image'], delivery['previous_image']
    for reference in [image, previous]:
        if not cd.re.fullmatch(cd.re.escape(cd.IMAGE) + r'@sha256:[a-f0-9]{64}', reference):
            raise cd.DeliveryPreflightError('Recovery receipt has an invalid pinned image')
    # Abort first even if Git is temporarily unreachable. Analysis failures also
    # abort in the controller independently of Jenkins availability.
    try:
        if image != previous:
            abort_if_current(image)
    except Exception:
        print('Abort request unavailable; restoring the Git baseline before retrying Sync', flush=True)
    cd.ensure_clean(repo)
    current = cd.csms_image(repo, cd.PROD)
    if current not in [image, previous]:
        raise cd.DeliveryPreflightError('A newer production image exists; recovery will not overwrite it')
    if current == image:
        cd.pin_image(repo, cd.PROD, previous)
        revision = cd.commit(repo, cd.PROD, 'revert(ops): abort failed CSMS canary')
    else:
        revision = cd.run('git', 'rev-parse', 'HEAD', cwd=repo)
    delivery.update(phase='recovering', recovery_revision=revision)
    cd.save(path, delivery)
    # A failed/running old Sync must stop before the recovery operation is set.
    app = check_application()
    state = app.get('status', {}).get('operationState', {})
    if state.get('phase') in ['Running', 'Terminating']:
        cd.run('kubectl', '-n', 'argocd', 'patch', 'application', APPLICATION,
               '--type=merge', '-p', '{"status":{"operationState":{"phase":"Terminating"}}}')
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            app = check_application()
            if app.get('status', {}).get('operationState', {}).get('phase') not in ['Running', 'Terminating']:
                break
            time.sleep(2)
        else:
            raise cd.DeliveryPreflightError('Old Sync did not terminate; Git restored, retry recover to Sync it')
    marker = sync(revision)
    wait(previous, revision, timeout, marker)
    delivery['phase'] = 'recovered'
    cd.save(path, delivery)
    print('Recovery complete: previous Git digest restored and stable Rollout verified')


def wait(image, revision, timeout, marker):
    deadline = time.monotonic() + timeout
    last_phase = None
    while time.monotonic() < deadline:
        app = check_application()
        operation = app.get('status', {}).get('operationState', {})
        requested = operation.get('operation', {}).get('sync', {}).get('revision')
        own = requested == revision and operation.get('operation', {}).get('initiatedBy', {}).get('username') == marker
        if own and operation.get('phase') in ['Failed', 'Error']:
            raise cd.DeliveryPreflightError('Argo CD selective CSMS Sync failed')
        rollout, healthy = rollout_state(image)
        phase = rollout.get('status', {}).get('phase', 'Pending')
        if phase != last_phase:
            print('CSMS Rollout phase: ' + phase, flush=True)
            last_phase = phase
        deployment, actual = deployment_image()
        if actual == image and (rollout.get('status', {}).get('abort') or phase == 'Degraded'):
            # During recovery the old aborted generation may still be observed.
            # Wait until the new workload template is reflected in current RS.
            if image_is_current_rs(rollout, image):
                raise cd.DeliveryPreflightError('Canary analysis or progress deadline aborted the update')
        if own and operation.get('phase') == 'Succeeded' and healthy:
            return
        time.sleep(5)
    raise cd.DeliveryPreflightError('Canary rollout did not become healthy within the deadline')


def image_is_current_rs(rollout, image):
    current_hash = rollout.get('status', {}).get('currentPodHash')
    replicasets = cd.kubectl('-n', 'ops', 'get', 'replicasets', '-l', 'app=csms')['items']
    return any(r['metadata'].get('labels', {}).get('rollouts-pod-template-hash') == current_hash
               and any(o.get('uid') == rollout['metadata']['uid'] for o in r['metadata'].get('ownerReferences', []))
               and any(c['name'] == 'csms' and c['image'] == image for c in r['spec']['template']['spec']['containers'])
               for r in replicasets)


def deliver(repo, delivery_path, timeout):
    delivery = cd.load(delivery_path)
    if delivery.get('phase') != 'pending':
        raise cd.DeliveryPreflightError('An approved pending production promotion is required')
    if not delivery.get('canary'):
        raise cd.DeliveryPreflightError('This promotion was approved for manual Sync, not Canary')
    cd.ensure_clean(repo)
    if cd.production_fingerprint(repo) != delivery['production_fingerprint']:
        raise cd.DeliveryPreflightError('Production changed after promotion; refusing stale Canary Sync')
    if cd.fingerprint(cd.rendered(repo, cd.CANARY)) != delivery.get('canary_fingerprint'):
        raise cd.DeliveryPreflightError('Canary policy changed after approval; refusing stale Sync')
    try:
        marker = sync(delivery['revision'])
        wait(delivery['image'], delivery['revision'], timeout, marker)
        delivery['phase'] = 'succeeded'
        cd.save(delivery_path, delivery)
        print('Canary passed: exact verified digest is stable at four replicas')
    except BaseException:
        recover(repo, delivery_path)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['preflight', 'run', 'recover'])
    parser.add_argument('--repo', default='.gitops-delivery')
    parser.add_argument('--candidate', default='artifacts/candidate.json')
    parser.add_argument('--delivery', default='artifacts/production-delivery.json')
    parser.add_argument('--timeout', type=int, default=1200)
    args = parser.parse_args()
    if args.command == 'preflight':
        preflight(args.repo, args.candidate)
    elif args.command == 'run':
        deliver(args.repo, args.delivery, args.timeout)
    else:
        recover(args.repo, args.delivery, args.timeout)


def interrupted(signum, frame):
    raise KeyboardInterrupt('Jenkins interrupted the Canary observer')


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, interrupted)
    try:
        main()
    except cd.DeliveryPreflightError as error:
        print('Canary stopped: ' + str(error))
        raise SystemExit(1)
    except (Exception, KeyboardInterrupt) as error:
        print('Canary stopped (' + error.__class__.__name__ + '); inspect receipt and retry recover if needed')
        raise SystemExit(1)
