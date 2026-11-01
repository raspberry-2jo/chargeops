"""Exercise unset BUILD_IMAGES and the real Jenkins stage guard expressions."""
import os
import re
import subprocess
from types import SimpleNamespace

import pytest

from test_ci import PIPELINE, BLOCKS, BUILD, bash


def test_every_shell_image_reference_handles_an_unset_variable():
    # Cover single-quoted sh steps as well as the multiline blocks exercised below.
    assert not re.search(r'\$BUILD_IMAGES\b|\$\{BUILD_IMAGES(?!:-\})', PIPELINE)
    for block in BLOCKS:
        assert not re.search(r'\$BUILD_IMAGES\b|\$\{BUILD_IMAGES(?!:-\})', block)
    assert "env.HAS_SELECTED_IMAGES = (!selection.selected.isEmpty()).toString()" in PIPELINE
    assert 'open-ci-pr' not in PIPELINE


def test_zero_images_regression_reset_and_build_with_variable_absent(bash, tmp_path):
    reset = next(b for b in BLOCKS if 'RESET_OPTION=' in b)
    env = dict(os.environ, PUBLISH_IMAGES='false', COMMAND_LOG=(tmp_path / 'calls').as_posix())
    env.pop('BUILD_IMAGES', None)
    stub = '''python3.12() { :; }
    mkdir -p .ci-venv/bin
    cat > .ci-venv/bin/python <<'PYTHON_STUB'
#!/bin/sh
printf '%s\\n' "$*" >> "$COMMAND_LOG"
PYTHON_STUB
    chmod +x .ci-venv/bin/python
    docker() { echo 'unexpected docker invocation' >&2; return 99; }
'''
    result = subprocess.run([bash], input=stub + reset + '\n' + BUILD,
                            cwd=tmp_path, env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    calls = (tmp_path / 'calls').read_text()
    assert 'scripts/env_cd.py reset --preserve-delivery' in calls


def guard(stage, env, params):
    block = PIPELINE.split("stage('" + stage + "')", 1)[1].split("stage('", 1)[0]
    expression = re.search(r'when\s*\{\s*expression\s*\{(.*?)\}', block, re.S).group(1)
    expression = expression.replace('&&', ' and ').replace('||', ' or ')
    expression = expression.replace('env.SOURCE_COMMIT?.trim()', 'env.SOURCE_COMMIT.strip()')
    return bool(eval(' '.join(expression.split()), {'__builtins__': {}}, {'env': env, 'params': params}))


@pytest.mark.parametrize('selection,canary', [('', False), ('chargeops-csms', True), ('chargeops-notifier', False)])
def test_stage_execution_matrix(selection, canary):
    env = SimpleNamespace(HAS_SELECTED_IMAGES=str(bool(selection)).lower(), PUBLISH_IMAGES='true',
                          HAS_OPS_CHANGES=str(bool(selection)).lower(), CHECKOUT_BRANCH='kim_chanju', PUBLISH_BRANCH='kim_chanju', SOURCE_COMMIT='c' * 40)
    params = SimpleNamespace(ECR_MIRROR=True, ENABLE_ENVIRONMENT_CD=True)
    for name in ('Docker Build: changed images only', 'Trivy Quality Gate', 'Registry Push: scanned images only',
                 'ECR mirror push', 'Create source PR for dev approval', 'Wait for source PR approval and merge',
                 'Promote verified digest to ops GitOps'):
        assert guard(name, env, params) is bool(selection), name
    assert guard('Development delivery and integration test', env, params)
    assert (bool(selection) and 'chargeops-csms' in selection.split()) is canary
    assert "if (env.RUN_CSMS_CANARY == 'true') {\n            stage('Ops Canary" in PIPELINE
