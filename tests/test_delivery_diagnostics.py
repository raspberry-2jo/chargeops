"""Kubernetes failures must be actionable without printing child credentials."""
from types import SimpleNamespace

import pytest

from test_env_cd import cd, ROOT


@pytest.mark.parametrize('stderr,expected', [
    ('Error from server (Forbidden): private-token', 'Forbidden'),
    ('Error from server (NotFound): private-token', 'NotFound'),
    ('You must be logged in to the server (Unauthorized): private-token', 'Unauthorized'),
    ('Unable to connect: x509: certificate signed by unknown authority: private-token', 'TLS'),
    ('Unable to connect: dial tcp: connection refused: private-token', 'connection'),
    ('private-token some unknown error', 'Kubernetes command failed'),
])
def test_kubectl_failures_keep_safe_error_kind_instead_of_generic_runtimeerror(monkeypatch, capsys, stderr, expected):
    monkeypatch.setattr(cd.subprocess, 'run', lambda *a, **kw:
                        SimpleNamespace(returncode=1, stdout='private-token', stderr=stderr))
    with pytest.raises(cd.DeliveryPreflightError) as error:
        cd.run('kubectl', '-n', 'argocd', 'get', 'application', 'chargeops-chatops-dev', '-o', 'json')
    assert expected in str(error.value)
    assert 'private-token' not in str(error.value)
    assert 'private-token' not in capsys.readouterr().out


def test_dev_status_is_mutable_and_failure_publication_is_retained():
    pipeline = (ROOT / 'Jenkinsfile').read_text(encoding='utf-8')
    environment = pipeline.split('  environment {', 1)[1].split('  stages {', 1)[0]
    assert 'DEV_CHECK_STATE' not in environment
    assert "env.DEV_CHECK_STATE = 'not_started'" in pipeline
    assert "env.DEV_CHECK_STATE = 'pending'" in pipeline
    assert "if (env.DEV_CHECK_STATE == 'pending'" in pipeline
    assert "try { githubStatus('failure') }" in pipeline
