import importlib.util
from pathlib import Path
import stat
import sys
from types import SimpleNamespace

import pytest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('exception_check',ROOT/'apps/common/runtime_security.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def test_hardening_removes_reviewed_tools(tmp_path,monkeypatch):
    tool=tmp_path/'perl';tool.write_text('binary fixture')
    monkeypatch.setattr(m,'PATTERNS',(str(tool),))
    monkeypatch.setattr(m.os,'geteuid',lambda:0,raising=False)
    monkeypatch.setattr(m,'privileged_files',lambda:iter(()))
    result=m.harden()
    assert not tool.exists() and result['removed']==[str(tool)]


def test_runtime_requires_nonroot_no_tools_and_no_suid(tmp_path,monkeypatch):
    tool=tmp_path/'mount'
    monkeypatch.setattr(m,'PATTERNS',(str(tool),))
    monkeypatch.setattr(m.os,'geteuid',lambda:0,raising=False)
    with pytest.raises(RuntimeError,match='UID'):m.check('chargeops-ai-gateway')
    monkeypatch.setattr(m.os,'geteuid',lambda:10001)
    tool.write_text('fixture')
    with pytest.raises(RuntimeError,match='tools remain'):m.check('chargeops-ai-gateway')
    tool.unlink()
    monkeypatch.setattr(m,'privileged_files',lambda:iter([(tool,stat.S_ISUID)]))
    with pytest.raises(RuntimeError,match='SUID/SGID'):m.check('chargeops-ai-gateway')
    monkeypatch.setattr(m,'privileged_files',lambda:iter(()))
    monkeypatch.setitem(sys.modules,'ai_gateway.runtime_check',SimpleNamespace(check=lambda:None))
    monkeypatch.setattr(m.util,'find_spec',lambda name:None)
    def missing(name):raise m.metadata.PackageNotFoundError(name)
    monkeypatch.setattr(m.metadata,'distribution',missing)
    monkeypatch.setattr(m.metadata,'version',lambda name:{'msgpack':'1.2.3','urllib3':'2.8.0'}[name])
    monkeypatch.setattr(m.importlib,'import_module',lambda name:None)
    monkeypatch.setattr(m.Path,'rglob',lambda self,pattern:iter(()))
    assert m.check('chargeops-ai-gateway')['reviewed_tools_absent'] is True


def test_build_hardening_precedes_nonroot_verification():
    dockerfile=(ROOT/'apps/ai-gateway/Dockerfile').read_text()
    assert dockerfile.index('runtime_security --harden') < dockerfile.index('USER 10001:10001')
    assert dockerfile.index('USER 10001:10001') < dockerfile.index('RUN python -m runtime_security chargeops-ai-gateway')


@pytest.mark.parametrize('directory,service',[
    ('ai-gateway','chargeops-ai-gateway'),('chatops-bot','chargeops-chatops-bot'),
    ('csms','chargeops-csms'),('notifier','chargeops-notifier'),
    ('virtual-charger','chargeops-vcharger'),('collector','chargeops-collector')])
def test_each_image_checks_dependencies_then_removes_tools_and_verifies_its_profile(directory,service):
    dockerfile=(ROOT/'apps'/directory/'Dockerfile').read_text()
    assert dockerfile.index('pip check')<dockerfile.index('pip uninstall')
    assert dockerfile.index('runtime_security --harden')<dockerfile.index('USER 10001:10001')
    assert dockerfile.index('USER 10001:10001')<dockerfile.index('RUN python -m runtime_security '+service)
    requirements=(ROOT/'apps'/directory/'requirements.txt').read_text()
    assert all(pin in requirements for pin in ('setuptools==84.2.1','msgpack==1.2.3','urllib3==2.8.0'))
    assert service in m.PROFILES


def test_unknown_runtime_profile_cannot_get_evidence():
    with pytest.raises(RuntimeError,match='Unknown runtime'):m.check('unknown')
