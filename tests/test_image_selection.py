import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
spec = importlib.util.spec_from_file_location('image_selection', ROOT / 'scripts/image_selection.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


def git(root, *args):
    return subprocess.check_output(['git', *args], cwd=root, text=True).strip()


@pytest.fixture
def source(tmp_path):
    shutil.copy2(ROOT / 'Jenkinsfile', tmp_path / 'Jenkinsfile')
    shutil.copytree(ROOT / 'apps', tmp_path / 'apps', ignore=shutil.ignore_patterns('__pycache__', '.venv'))
    git(tmp_path, 'init', '-q')
    git(tmp_path, 'config', 'user.name', 'CI test')
    git(tmp_path, 'config', 'user.email', 'ci-test@example.invalid')
    git(tmp_path, 'add', '.')
    git(tmp_path, '-c', 'core.hooksPath=/dev/null', 'commit', '-qm', 'base')
    return tmp_path, git(tmp_path, 'rev-parse', 'HEAD')


def change(source, paths):
    root, base = source
    for name in paths:
        p = root / name; p.parent.mkdir(parents=True, exist_ok=True)
        # Keep executable Dockerfile declarations valid; changes are Git inputs.
        p.write_text((p.read_text(encoding='utf-8') if p.exists() else '') + '\n# changed\n', encoding='utf-8')
    git(root, 'add', '.')
    git(root, '-c', 'core.hooksPath=/dev/null', 'commit', '-qm', 'change')
    return m.select(root, base)


@pytest.mark.parametrize('paths,expected', [
    (['apps/csms/app.py'], {'chargeops-csms'}),
    (['apps/common/runtime_security.py'], {i.name for i in m.image_definitions()}),
    (['apps/common/policy.py'], {'chargeops-ai-gateway', 'chargeops-chatops-bot', 'chargeops-notifier'}),
    (['scripts/env_cd.py', 'tests/test_new.py', 'docs/readme.md', 'Jenkinsfile'], set()),
    (['apps/collector/Dockerfile'], {'chargeops-collector'}),
    (['apps/collector/unused.py'], set()),
    (['apps/.dockerignore'], {i.name for i in m.image_definitions()}),
])
def test_actual_copy_dependencies_select_exact_apps(source, paths, expected):
    result = change(source, paths)
    assert set(result['selected']) == expected
    for name in expected:
        assert set(result['reasons'][name]) <= set(paths)


@pytest.mark.parametrize('base', ['', 'not-a-commit'])
def test_missing_successful_commit_builds_all(source, base):
    result = m.select(source[0], base)
    assert len(result['selected']) == 6 and result['fallback']


def test_force_all_and_uncertain_parser_are_conservative(source):
    root, base = source
    assert len(m.select(root, base, force_all=True)['selected']) == 6
    p = root / 'apps/collector/Dockerfile'
    p.write_text(p.read_text() + '\nCOPY ${UNKNOWN_PATH} /app/\n')
    assert len(m.select(root, base)['selected']) == 6


def test_deleted_copied_file_still_selects_owner(source):
    root, base = source
    git(root, 'rm', 'apps/csms/app.py')
    git(root, '-c', 'core.hooksPath=/dev/null', 'commit', '-qm', 'delete')
    assert m.select(root, base)['selected'] == ['chargeops-csms']


def test_json_copy_and_multiline_flags_preserve_exact_file_dependency(source):
    root, base = source
    p = root / 'apps/collector/Dockerfile'
    p.write_text('FROM python:3\nCOPY --chown=10001:10001 ' + chr(92) + '\n ["common/runtime_security.py", "/app/runtime_security.py"]\n')
    image = next(i for i in m.image_definitions(root) if i.name == 'chargeops-collector')
    assert m.copy_inputs(root, image) == [('apps/common/runtime_security.py', False)]


def test_from_change_selects_only_that_dockerfile_owner(source):
    root, base = source
    path = root / 'apps/csms/Dockerfile'
    path.write_text(path.read_text().replace('python:3.13-slim', 'python:3.14-slim'))
    git(root, 'add', '.')
    git(root, '-c', 'core.hooksPath=/dev/null', 'commit', '-qm', 'base image')
    result = m.select(root, base)
    assert result['selected'] == ['chargeops-csms']
    assert result['reasons']['chargeops-csms'] == ['apps/csms/Dockerfile']
