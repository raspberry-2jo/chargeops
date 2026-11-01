import importlib.util
import json
from pathlib import Path
import subprocess
from urllib.error import HTTPError

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('comparison_base', ROOT / 'scripts/comparison_base.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
SHA = 'a' * 40
REMOTE = 'https://github.com/raspberry-2jo/chargeops.git'


def test_checkout_value_wins_and_is_verified(monkeypatch, tmp_path):
    calls = []
    def git(root, *args):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout='')
    monkeypatch.setattr(m, 'git', git)
    monkeypatch.setattr(m, 'api_base', lambda *a: pytest.fail('API must not run'))
    result = m.resolve(tmp_path, SHA, 'http://jenkins/job/ci/')
    assert result['base'] == SHA and result['source'] == 'checkout_vars'
    assert calls == [('cat-file', '-e', SHA + '^{commit}')]


def test_missing_checkout_uses_api_and_verifies_commit(monkeypatch, tmp_path):
    def git(root, *args):
        return subprocess.CompletedProcess(args, 0, stdout=REMOTE if args[0] == 'config' else '')
    monkeypatch.setattr(m, 'git', git)
    monkeypatch.setattr(m, 'api_base', lambda *a: SHA)
    result = m.resolve(tmp_path, '', 'http://jenkins/job/ci/')
    assert result['base'] == SHA and result['source'] == 'jenkins_api'


@pytest.mark.parametrize('code', [404, 403])
def test_no_history_or_api_access_builds_all(monkeypatch, tmp_path, code):
    monkeypatch.setattr(m, 'git', lambda *a: subprocess.CompletedProcess(a, 0, stdout=REMOTE))
    def unavailable(*args):
        raise HTTPError('url', code, '', {}, None)
    monkeypatch.setattr(m, 'api_base', unavailable)
    result = m.resolve(tmp_path, '', 'http://jenkins/job/ci/')
    assert result['base'] == '' and result['reason'] == 'api_http_' + str(code)


@pytest.mark.parametrize('fetched', [False, True])
def test_missing_local_commit_fetches_and_rechecks(monkeypatch, tmp_path, fetched):
    calls = []
    def git(root, *args):
        calls.append(args)
        exists = fetched and sum(c[0] == 'cat-file' for c in calls) > 1
        return subprocess.CompletedProcess(args, 0 if args[0] == 'fetch' or exists else 1, stdout='')
    monkeypatch.setattr(m, 'git', git)
    result = m.resolve(tmp_path, SHA)
    assert result['base'] == (SHA if fetched else '')
    assert calls == [('cat-file', '-e', SHA + '^{commit}'),
                     ('fetch', '--no-tags', 'origin', SHA), ('cat-file', '-e', SHA + '^{commit}')]


def test_no_sources_and_invalid_sha_are_conservative(monkeypatch, tmp_path):
    monkeypatch.setattr(m, 'git', lambda *a: pytest.fail('No valid commit to inspect'))
    assert m.resolve(tmp_path)['base'] == ''
    assert m.resolve(tmp_path, '--upload-pack=bad')['reason'] == 'invalid_commit_sha'


def test_api_selects_source_repository_not_gitops_and_sends_auth(monkeypatch):
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self):
            return json.dumps({'result': 'SUCCESS', 'actions': [
                {'remoteUrls': ['https://github.com/raspberry-2jo/chargeops-gitops.git'],
                 'lastBuiltRevision': {'SHA1': 'b' * 40}},
                {'remoteUrls': [REMOTE], 'lastBuiltRevision': {'SHA1': SHA}}]}).encode()
    class Opener:
        def open(self, request, timeout):
            assert '/lastSuccessfulBuild/api/json?' in request.full_url
            assert request.get_header('Authorization').startswith('Basic ')
            assert timeout == 15
            return Response()
    monkeypatch.setattr(m, 'build_opener', lambda *a: Opener())
    assert m.api_base('http://jenkins/job/ci/', REMOTE, 'reader', 'token') == SHA


def test_pipeline_passes_primary_checkout_result_to_resolver_before_selection():
    pipeline = (ROOT / 'Jenkinsfile').read_text(encoding='utf-8')
    assert "env.CHECKOUT_PREVIOUS_SUCCESSFUL_COMMIT = (checkoutVars.GIT_PREVIOUS_SUCCESSFUL_COMMIT ?: '').trim()" in pipeline
    assert pipeline.index('scripts/comparison_base.py') < pipeline.index('scripts/image_selection.py --base')
    assert 'env.GIT_PREVIOUS_SUCCESSFUL_COMMIT = comparison.base' in pipeline


def test_real_shallow_clone_fetches_previous_commit(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    def run(root, *args):
        return subprocess.check_output(['git', *args], cwd=root, text=True).strip()
    run(source, 'init', '-q')
    run(source, 'config', 'user.name', 'test')
    run(source, 'config', 'user.email', 'test@example.invalid')
    (source / 'app').write_text('old')
    run(source, 'add', '.')
    run(source, '-c', 'core.hooksPath=/dev/null', 'commit', '-qm', 'old')
    old = run(source, 'rev-parse', 'HEAD')
    (source / 'app').write_text('new')
    run(source, 'add', '.')
    run(source, '-c', 'core.hooksPath=/dev/null', 'commit', '-qm', 'new')
    clone = tmp_path / 'clone'
    run(tmp_path, 'clone', '--quiet', '--depth=1', source.as_uri(), str(clone))
    head = run(clone, 'rev-parse', 'HEAD')
    assert m.git(clone, 'cat-file', '-e', old + '^{commit}').returncode != 0
    result = m.resolve(clone, old)
    assert result['base'] == old and result['fetch'] == 'fetched'
    assert run(clone, 'rev-parse', 'HEAD') == head
