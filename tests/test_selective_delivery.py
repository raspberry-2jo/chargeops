import json
import pytest
from test_application_delivery import cd, repo, apps, A, B, N, OLD_N, images


@pytest.mark.parametrize('selected', ['', 'chargeops-notifier', 'chargeops-csms'])
def test_prepare_only_resolves_selected_images_and_preserves_others(apps, tmp_path, monkeypatch, selected):
    before_head = cd.run('git', 'rev-parse', 'HEAD', cwd=apps)
    before_dev = cd.rendered(apps, cd.DEV)
    before_ops = cd.application_digest_snapshot(apps, cd.PROD)
    lookups = []
    def resolve(ref):
        lookups.append(ref)
        name = ref.rsplit(':', 1)[0]
        assert name in {cd.IMAGE if 'chargeops-csms' in selected else None,
                        cd.APP_IMAGES['notifier'] if 'chargeops-notifier' in selected else None}
        return B if name == cd.IMAGE else N
    monkeypatch.setattr(cd, 'resolve_digest', resolve)
    path = tmp_path / 'candidate.json'
    cd.prepare(apps, 'build-84-abcdef012345', 'c' * 40, path, canary=True, build_images=selected)
    candidate = cd.load(path)
    assert len(lookups) == len(selected.split())
    assert candidate['image'] == (B if 'chargeops-csms' in selected else A)
    assert cd.csms_image(apps, cd.DEV) == candidate['image']
    assert cd.application_digest_snapshot(apps, cd.PROD) == before_ops
    for name, ref in before_ops.items():
        if name.rsplit('/', 1)[-1] not in selected.split() and name != cd.IMAGE:
            assert candidate['release_images'][name] == ref
    if 'chargeops-csms' not in selected:
        assert cd.rendered(apps, cd.DEV) == before_dev
        assert cd.run('git', 'rev-parse', 'HEAD', cwd=apps) == before_head
        assert candidate['development_unchanged'] is True


def run_existing_verification(apps, path, receipt, monkeypatch):
    candidate = cd.load(path)
    created = []
    original_run = cd.run
    def run(*args, **kwargs):
        if args[:2] == ('kubectl', 'create'):
            created.append(json.loads(kwargs['payload'])); return ''
        if args[0] == 'kubectl' and 'wait' in args: return ''
        if args[0] == 'kubectl' and 'logs' in args: return 'PASS: existing integration checks'
        return original_run(*args, **kwargs)
    monkeypatch.setattr(cd, 'run', run)
    monkeypatch.setattr(cd, 'live_candidate', lambda image: image == candidate['image'])
    monkeypatch.setattr(cd, 'kubectl', lambda *args: {'metadata': {'uid': 'same-check-job'}, 'status': {'succeeded': 1}})
    cd.verify(apps, path, receipt, 600)
    assert created == [cd.verification_job(candidate)]  # No test cases removed from the Job.


def test_no_build_still_runs_existing_job_but_never_promotes(apps, tmp_path, monkeypatch):
    monkeypatch.setattr(cd, 'resolve_digest', lambda *args: pytest.fail('no registry lookup for an unbuilt tag'))
    path, receipt = tmp_path / 'candidate.json', tmp_path / 'receipt.json'
    cd.prepare(apps, 'build-84-abcdef012345', 'c' * 40, path, canary=True, build_images='')
    before = cd.production_fingerprint(apps)
    run_existing_verification(apps, path, receipt, monkeypatch)
    delivery = tmp_path / 'delivery.json'
    cd.promote(apps, path, receipt, delivery, canary=True)
    assert not delivery.exists()
    assert cd.production_fingerprint(apps) == before


def test_notifier_only_promotion_preserves_verified_csms_and_unchanged_bundle(apps, tmp_path, monkeypatch):
    monkeypatch.setattr(cd, 'resolve_digest', lambda ref: N if ref.startswith(cd.APP_IMAGES['notifier'] + ':') else pytest.fail(ref))
    path, receipt, delivery = tmp_path / 'candidate.json', tmp_path / 'receipt.json', tmp_path / 'delivery.json'
    baseline = cd.application_digest_snapshot(apps, cd.PROD)
    cd.prepare(apps, 'build-84-abcdef012345', 'c' * 40, path, canary=True, build_images='chargeops-notifier')
    run_existing_verification(apps, path, receipt, monkeypatch)
    cd.promote(apps, path, receipt, delivery, canary=True)
    expected = dict(baseline); expected[cd.APP_IMAGES['notifier']] = N
    assert cd.application_digest_snapshot(apps, cd.PROD) == expected
    assert cd.load(path)['release_images'] == cd.load(delivery)['release_images'] == expected
    assert cd.csms_image(apps, cd.PROD) == cd.csms_image(apps, cd.DEV) == A


def test_change_to_unchanged_digest_invalidates_bound_receipt(apps, tmp_path, monkeypatch):
    monkeypatch.setattr(cd, 'resolve_digest', lambda ref: N)
    path, receipt = tmp_path / 'candidate.json', tmp_path / 'receipt.json'
    cd.prepare(apps, 'build-84-abcdef012345', 'c' * 40, path, build_images='chargeops-notifier')
    run_existing_verification(apps, path, receipt, monkeypatch)
    candidate = cd.load(path)
    candidate['release_images'][cd.APP_IMAGES['ai-gateway']] = cd.APP_IMAGES['ai-gateway'] + '@sha256:' + 'f' * 64
    cd.save(path, candidate)
    with pytest.raises(cd.DeliveryPreflightError, match='different candidate'):
        cd.promote(apps, path, receipt, tmp_path / 'delivery.json')


def test_repeated_no_image_builds_use_fresh_jobs_and_never_rollback_current_dev(apps, tmp_path, monkeypatch):
    paths = [tmp_path / 'first.json', tmp_path / 'second.json']
    monkeypatch.setattr(cd, 'resolve_digest', lambda *args: pytest.fail('no new tag should be read'))
    for tag, path in zip(('build-84-abcdef012345', 'build-85-abcdef012345'), paths):
        cd.prepare(apps, tag, 'c' * 40, path, build_images='')
    candidates = [cd.load(p) for p in paths]
    assert candidates[0]['image'] == candidates[1]['image'] == A
    assert cd.verification_job(candidates[0])['metadata']['name'] != cd.verification_job(candidates[1])['metadata']['name']
    monkeypatch.setattr(cd, 'ensure_clean', lambda *args: pytest.fail('verification-only recovery cannot write GitOps'))
    monkeypatch.setattr(cd, 'kubectl', lambda *args: pytest.fail('verification-only recovery cannot touch Kubernetes'))
    cd.recover_dev(apps, paths[0], tmp_path / 'recovery.json')
