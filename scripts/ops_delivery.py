"""Sync approved regular Ops apps only; never touch CSMS Rollout or Canary recovery."""
import argparse
import json
import signal
import time
import uuid

import env_cd as cd
import canary_cd as canary


def application():
    app = cd.kubectl('-n', 'argocd', 'get', 'application', canary.APPLICATION)
    spec = app['spec']
    source = spec['source']
    auto = spec.get('syncPolicy', {}).get('automated')
    if (spec['destination']['namespace'] != 'ops' or source.get('path') not in (cd.PROD, cd.CANARY)
            or source.get('targetRevision') != 'main'
            or source.get('repoURL') != 'https://github.com/raspberry-2jo/chargeops-gitops.git'
            or auto is not None and auto.get('enabled') is not False):
        raise cd.DeliveryPreflightError('Regular Ops delivery requires the manual approved Ops Application')
    return app


def validate_workloads(workloads):
    if not workloads:
        raise cd.DeliveryPreflightError('No regular application workloads were approved')
    for item in workloads:
        if (item['name'] == 'csms' or (item['group'], item['kind']) not in
                (('apps', 'Deployment'), ('batch', 'CronJob'))):
            raise cd.DeliveryPreflightError('Regular Ops Sync cannot target CSMS or Canary resources')


def preflight(repo, candidate_path):
    candidate = cd.load(candidate_path)
    cd.candidate_check(candidate)
    if cd.csms_canary_scope(candidate) or not cd.selected_candidate_images(candidate):
        raise cd.DeliveryPreflightError('Regular Ops preflight requires changed non-CSMS apps')
    cd.ensure_clean(repo)
    if cd.production_fingerprint(repo) != candidate['production_fingerprint']:
        raise cd.DeliveryPreflightError('Production changed before regular Ops approval')
    app = application()
    if app.get('operation') or app.get('status', {}).get('operationState', {}).get('phase') in ('Running', 'Terminating'):
        raise cd.DeliveryPreflightError('An Ops Sync is already running')
    print('Regular Ops preflight passed; canary skipped: chargeops-csms unchanged', flush=True)


def sync(revision, workloads):
    validate_workloads(workloads)
    if not cd.re.fullmatch(r'[a-f0-9]{40}', revision):
        raise cd.DeliveryPreflightError('A committed Git revision is required for regular Ops Sync')
    app = application()
    if app.get('operation') or app.get('status', {}).get('operationState', {}).get('phase') in ('Running', 'Terminating'):
        raise cd.DeliveryPreflightError('An Ops Sync is already running')
    marker = 'chargeops-regular-' + uuid.uuid4().hex[:12]
    operation = {'operation': {'initiatedBy': {'username': marker},
        'info': [{'name': 'chargeops-operation-id', 'value': marker}],
        'sync': {'revision': revision, 'prune': False,
                 'resources': [{k: item[k] for k in ('group', 'kind', 'name')} for item in workloads]}}}
    cd.run('kubectl', '-n', 'argocd', 'patch', 'application', canary.APPLICATION,
           '--type=merge', '-p', json.dumps(operation))
    return marker


def ready(workloads, operation):
    # Existing generation, exact container digest and rollout-complete checks.
    if not canary.regular_workloads_ready(workloads, operation):
        return False
    for item in workloads:
        if item['kind'] == 'CronJob':
            job = cd.kubectl('-n', 'ops', 'get', 'cronjob', item['name'])
            containers = job['spec']['jobTemplate']['spec']['template']['spec']['containers']
            actual = {c['name']: c['image'] for c in containers}
            if any(actual.get(name) != image for name, image in item['images'].items()):
                return False
    return True


def wait(revision, marker, workloads, timeout):
    interval = cd.poll_seconds()
    deadline = time.monotonic() + timeout
    last = None
    next_log = 0
    diagnostics = cd.PollDiagnostics("ops_regular", interval, __file__)
    while (now := time.monotonic()) < deadline:
        with diagnostics.sample(now) as poll:
            state = application().get('status', {}).get('operationState', {})
            own = canary.owns_operation(state, revision, marker)
            phase = state.get('phase', 'Pending')
            if own and phase in ('Failed', 'Error'):
                raise cd.DeliveryPreflightError('Regular Ops application Sync failed')
            complete = own and phase == 'Succeeded' and ready(workloads, state)
            current = (own, phase, complete)
            if current != last or now >= next_log:
                print('Regular Ops completion: own_sync=' + str(own) + ', phase=' + phase
                      + ', regular_workloads_and_digests_ready=' + str(complete), flush=True)
                last = current; next_log = now + 30
            if complete:
                poll["terminal"] = True
                return
        time.sleep(interval)
    raise cd.DeliveryPreflightError('Regular Ops rollout/digest verification deadline exceeded')


def deliver(repo, delivery_path, timeout):
    delivery = cd.load(delivery_path)
    if delivery.get('built_images') == []:
        print('Ops promote and canary skipped: no image changes', flush=True)
        return
    if delivery.get('canary') or cd.csms_canary_scope(delivery):
        raise cd.DeliveryPreflightError('CSMS changes must use the approved Canary delivery path')
    print('canary skipped: chargeops-csms unchanged', flush=True)
    if delivery.get('phase') != 'pending':
        raise cd.DeliveryPreflightError('A pending approved regular Ops promotion is required')
    cd.ensure_clean(repo)
    if cd.production_fingerprint(repo) != delivery['production_fingerprint']:
        raise cd.DeliveryPreflightError('Production changed after regular Ops approval')
    images = cd.selected_candidate_images(delivery)
    overlay = application()['spec']['source']['path']
    workloads = cd.application_workloads(repo, images, overlay)
    if workloads != delivery.get('workloads'):
        raise cd.DeliveryPreflightError('Approved regular application workloads changed')
    validate_workloads(workloads)
    if 'release_images' in delivery and cd.application_digest_snapshot(repo, cd.PROD) != delivery['release_images']:
        raise cd.DeliveryPreflightError('Regular Ops digest combination changed')
    try:
        marker = sync(delivery['revision'], workloads)
        delivery['operation_id'] = marker
        cd.save(delivery_path, delivery)
        wait(delivery['revision'], marker, workloads, timeout)
    except BaseException:
        delivery['phase'] = 'failed'
        cd.save(delivery_path, delivery)
        raise  # No CSMS abort, rollback, or recovering state in this path.
    delivery['phase'] = 'succeeded'
    cd.save(delivery_path, delivery)
    print('Regular Ops delivery passed: rollout complete and approved digests match', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['run', 'preflight'])
    parser.add_argument('--repo', default='.gitops-delivery')
    parser.add_argument('--delivery', default='artifacts/production-delivery.json')
    parser.add_argument('--candidate', default='artifacts/candidate.json')
    parser.add_argument('--timeout', type=int, default=1200)
    args = parser.parse_args()
    if args.command == 'preflight':
        preflight(args.repo, args.candidate)
    else:
        deliver(args.repo, args.delivery, args.timeout)


if __name__ == '__main__':
    def interrupted(signum, frame):
        raise KeyboardInterrupt('Jenkins interrupted the regular Ops observer')
    signal.signal(signal.SIGTERM, interrupted)
    try:
        main()
    except cd.DeliveryPreflightError as error:
        print('Regular Ops delivery stopped: ' + str(error))
        raise SystemExit(1)
    except (Exception, KeyboardInterrupt) as error:
        print('Regular Ops delivery stopped (' + error.__class__.__name__ + '); inspect approved workload rollout/digest status')
        raise SystemExit(1)
