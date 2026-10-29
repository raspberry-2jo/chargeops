import importlib.util
from pathlib import Path
import stat
import sys
from types import SimpleNamespace

import pytest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('exception_check',ROOT/'apps/ai-gateway/exception_check.py')
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
    with pytest.raises(RuntimeError,match='UID'):m.check()
    monkeypatch.setattr(m.os,'geteuid',lambda:10001)
    tool.write_text('fixture')
    with pytest.raises(RuntimeError,match='tools remain'):m.check()
    tool.unlink()
    monkeypatch.setattr(m,'privileged_files',lambda:iter([(tool,stat.S_ISUID)]))
    with pytest.raises(RuntimeError,match='SUID/SGID'):m.check()
    monkeypatch.setattr(m,'privileged_files',lambda:iter(()))
    monkeypatch.setitem(sys.modules,'ai_gateway.runtime_check',SimpleNamespace(check=lambda:None))
    assert m.check()['reviewed_tools_absent'] is True


def test_build_hardening_precedes_nonroot_verification():
    dockerfile=(ROOT/'apps/ai-gateway/Dockerfile').read_text()
    assert dockerfile.index('exception_check --harden') < dockerfile.index('USER 10001:10001')
    assert dockerfile.index('USER 10001:10001') < dockerfile.index('RUN python -m ai_gateway.exception_check\n')
