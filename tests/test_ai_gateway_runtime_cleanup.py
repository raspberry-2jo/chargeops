import importlib.util
from importlib import metadata
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('gateway_runtime_check',ROOT/'apps/ai-gateway/runtime_check.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def distribution(name):
    if name in m.BUILD_TOOLS:raise metadata.PackageNotFoundError(name)
    return SimpleNamespace(version='test-version')


def importer(name):
    return SimpleNamespace(app=SimpleNamespace(routes=[SimpleNamespace(path=p) for p in ('/healthz','/cicd-healthz')]))


def test_final_runtime_verifies_tool_absence_and_application_import():
    result=m.check(find_spec=lambda name:None,distribution=distribution,importer=importer)
    assert result['build_tools_absent']==['pip','setuptools','wheel']
    assert set(result['runtime_imports'])==set(m.RUNTIME_PACKAGES.values())
    assert result['db_llm_verified'] is False


@pytest.mark.parametrize('tool',m.BUILD_TOOLS)
def test_remaining_module_is_a_failure(tool):
    with pytest.raises(RuntimeError,match='module remains'):
        m.check(find_spec=lambda name:object() if name==tool else None,
                distribution=distribution,importer=importer)


@pytest.mark.parametrize('tool',m.BUILD_TOOLS)
def test_remaining_metadata_is_a_failure(tool):
    def lookup(name):
        return SimpleNamespace(version='old') if name==tool else distribution(name)
    with pytest.raises(RuntimeError,match='metadata remains'):
        m.check(find_spec=lambda name:None,distribution=lookup,importer=importer)


def test_removal_does_not_hide_missing_runtime_dependency():
    def broken(name):
        if name=='requests':raise ModuleNotFoundError(name)
        return importer(name)
    with pytest.raises(ModuleNotFoundError):
        m.check(find_spec=lambda name:None,distribution=distribution,importer=broken)


def test_dockerfile_checks_dependencies_before_removal_and_runtime_after_user_switch():
    dockerfile=(ROOT/'apps/ai-gateway/Dockerfile').read_text()
    assert dockerfile.index('pip check') < dockerfile.index('pip uninstall -y pip setuptools wheel')
    assert dockerfile.index('USER 10001:10001') < dockerfile.index('RUN python -m ai_gateway.runtime_check')
    assert 'setuptools==84.2.1' in (ROOT/'apps/ai-gateway/requirements.txt').read_text()
