import pytest
from types import SimpleNamespace

from test_application_delivery import cd, repo, apps, A, B, N, OLD_N
from test_selective_delivery import run_existing_verification
from test_canary_cd import canary, application
from test_canary_scope import regular, ops_app
from test_empty_image_pipeline import guard


@pytest.mark.parametrize('build_csms,dev_ahead,expected', [(False, True, True),
                                                        (False, False, False), (True, False, True)])
def test_ops_digest_difference_controls_canary_and_delivery(apps, tmp_path, monkeypatch, build_csms, dev_ahead, expected):
    if dev_ahead:
        cd.pin_image(apps, cd.DEV, B)
        cd.commit(apps, cd.DEV, 'previous build reached Dev only')
    monkeypatch.setattr(cd, 'resolve_digest', lambda ref: B if ref.startswith(cd.IMAGE + ':') else N)
    path, receipt, delivery = (tmp_path / p for p in ('candidate.json', 'receipt.json', 'delivery.json'))
    selected = 'chargeops-csms' if build_csms else 'chargeops-notifier'
    cd.prepare(apps, 'build-92-abcdef012345', 'c' * 40, path, canary=True, build_images=selected)
    candidate = cd.load(path)
    assert cd.csms_canary_scope(candidate) is expected
    plan = cd.ops_plan(apps, candidate)
    assert plan['canary'] is expected
    assert ('chargeops-csms' in plan['changed_images']) is expected
    assert ('canary_fingerprint' in candidate) is expected
    if expected:
        with pytest.raises(cd.DeliveryPreflightError, match='changed non-CSMS'):
            regular.preflight(apps, path)
    run_existing_verification(apps, path, receipt, monkeypatch)
    cd.promote(apps, path, receipt, delivery, canary=expected)
    result = cd.load(delivery)
    assert result['canary'] is expected
    assert result['ops_changes'] == plan['changed_images']
    assert result['images'] == plan['images']
    assert cd.application_digest_snapshot(apps, cd.PROD) == candidate['release_images']
    if expected:
        monkeypatch.setattr(canary, 'sync', lambda *a: 'marker')
        calls = []
        monkeypatch.setattr(canary, 'wait', lambda *a: calls.append(a))
        canary.deliver(apps, delivery, 1200)
        assert calls and cd.load(delivery)['phase'] == 'succeeded'
    else:
        before = delivery.read_bytes()
        monkeypatch.setattr(canary, 'sync', lambda *a: pytest.fail('unchanged CSMS must not Sync'))
        canary.deliver(apps, delivery, 1200)
        assert delivery.read_bytes() == before
        monkeypatch.setattr(regular, 'application', ops_app)
        synced = []
        monkeypatch.setattr(regular, 'sync', lambda revision, workloads: synced.append(workloads) or 'marker')
        monkeypatch.setattr(regular, 'wait', lambda *a: None)
        regular.deliver(apps, delivery, 1200)
        assert synced and all(w['name'] != 'csms' for w in synced[0])
        assert cd.load(delivery)['phase'] == 'succeeded'


def test_no_ops_digest_changes_preserve_git_and_delivery_history(apps, tmp_path, monkeypatch):
    monkeypatch.setattr(cd, 'resolve_digest', lambda ref: OLD_N)
    path, receipt, delivery = (tmp_path / p for p in ('candidate.json', 'receipt.json', 'delivery.json'))
    cd.prepare(apps, 'build-92-abcdef012345', 'c' * 40, path, canary=True, build_images='chargeops-notifier')
    run_existing_verification(apps, path, receipt, monkeypatch)
    before_head = cd.run('git', 'rev-parse', 'HEAD', cwd=apps)
    cd.save(delivery, {'phase': 'succeeded', 'history': 'previous build'})
    saved = delivery.read_bytes()
    assert cd.ops_plan(apps, cd.load(path))['changed_images'] == []
    monkeypatch.setattr(cd, 'commit', lambda *a, **kw: pytest.fail('No empty GitOps commit'))
    cd.promote(apps, path, receipt, delivery, canary=True)
    assert delivery.read_bytes() == saved
    assert cd.run('git', 'rev-parse', 'HEAD', cwd=apps) == before_head


def test_pipeline_skips_ops_when_build_selection_exists_but_ops_plan_is_empty():
    env = SimpleNamespace(HAS_SELECTED_IMAGES='true', HAS_OPS_CHANGES='false', PUBLISH_IMAGES='true',
                          CHECKOUT_BRANCH='kim_chanju', PUBLISH_BRANCH='kim_chanju', SOURCE_COMMIT='c' * 40)
    params = SimpleNamespace(ENABLE_ENVIRONMENT_CD=True)
    assert not guard('Promote verified digest to ops GitOps', env, params)


def test_inherited_csms_change_cannot_bypass_canary_authorization(apps, tmp_path, monkeypatch):
    cd.pin_image(apps, cd.DEV, B)
    cd.commit(apps, cd.DEV, 'Dev ahead')
    monkeypatch.setattr(cd, 'resolve_digest', lambda ref: N)
    path, receipt = tmp_path / 'candidate.json', tmp_path / 'receipt.json'
    cd.prepare(apps, 'build-92-abcdef012345', 'c' * 40, path, canary=True, build_images='chargeops-notifier')
    run_existing_verification(apps, path, receipt, monkeypatch)
    with pytest.raises(cd.DeliveryPreflightError, match='requires Canary authorization'):
        cd.promote(apps, path, receipt, tmp_path / 'delivery.json', canary=False)
    assert cd.csms_image(apps, cd.PROD) == A


def test_csms_rebuilt_with_identical_digest_is_not_a_canary_change(apps, tmp_path, monkeypatch):
    monkeypatch.setattr(cd, 'resolve_digest', lambda ref: A)
    path = tmp_path / 'candidate.json'
    cd.prepare(apps, 'build-92-abcdef012345', 'c' * 40, path, canary=True, build_images='chargeops-csms')
    assert cd.ops_plan(apps, cd.load(path))['canary'] is False


def test_failed_inherited_csms_canary_restores_ops_even_when_csms_was_not_built(apps, tmp_path, monkeypatch):
    cd.pin_image(apps, cd.DEV, B)
    cd.commit(apps, cd.DEV, 'Dev ahead of Ops')
    monkeypatch.setattr(cd, 'resolve_digest', lambda ref: N)
    path, receipt, delivery = (tmp_path / p for p in ('candidate.json', 'receipt.json', 'delivery.json'))
    cd.prepare(apps, 'build-92-abcdef012345', 'c' * 40, path, canary=True, build_images='chargeops-notifier')
    run_existing_verification(apps, path, receipt, monkeypatch)
    cd.promote(apps, path, receipt, delivery, canary=True)
    monkeypatch.setattr(canary, 'check_application', application)
    aborted = []
    monkeypatch.setattr(canary, 'abort_if_current', aborted.append)
    monkeypatch.setattr(canary, 'sync', lambda *a: 'marker')
    def wait(image, *args):
        if image == B:
            raise cd.DeliveryPreflightError('synthetic inherited Canary failure')
    monkeypatch.setattr(canary, 'wait', wait)
    with pytest.raises(cd.DeliveryPreflightError, match='synthetic inherited'):
        canary.deliver(apps, delivery, 1200)
    assert aborted == [B]
    assert cd.csms_image(apps, cd.PROD) == A and cd.csms_image(apps, cd.DEV) == B
    assert cd.application_digest_snapshot(apps, cd.PROD)[cd.APP_IMAGES['notifier']] == N
    assert cd.load(delivery)['phase'] == 'recovered'
