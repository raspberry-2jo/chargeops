"""Run the actual Jenkins shell blocks with a Docker stub and real temporary Git repos."""
import ast
import importlib.util
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
PIPELINE = (ROOT / "Jenkinsfile").read_text(encoding="utf-8")
BLOCKS = re.findall(r"sh(?:\(script:)?\s*'''(.*?)'''", PIPELINE, re.DOTALL)
BUILD = next(block for block in BLOCKS if "build_if_selected()" in block)
DIFF = next(block for block in BLOCKS if "GIT_PREVIOUS_SUCCESSFUL_COMMIT" in block)
spec = importlib.util.spec_from_file_location("ci_validator", ROOT / "scripts" / "ci.py")
ci = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = ci
spec.loader.exec_module(ci)
IMAGES = ci.image_definitions()


@pytest.fixture(scope="module")
def bash():
    windows_bash = Path("C:/Program Files/Git/bin/bash.exe")
    executable = str(windows_bash) if os.name == "nt" and windows_bash.exists() else shutil.which("bash")
    if not executable:
        pytest.skip("Bash is required to execute Jenkins shell checks")
    return executable


def build_with_stub(bash, tmp_path, names, publish=True, branch="kim_chanju", change_id="", fail=False):
    log = tmp_path / "docker-calls.txt"
    env = os.environ.copy()
    env.update(CALL_LOG=log.as_posix(), BUILD_IMAGES=" ".join(names),
               REGISTRY="registry.chargeops.lab:5000/chargeops", IMAGE_TAG="build-12-abc",
               PUBLISH_IMAGES=str(publish).lower(), PUBLISH_BRANCH="kim_chanju",
               CHECKOUT_BRANCH=branch, CHANGE_ID=change_id, FAIL_BUILD=str(int(fail)))
    stub = '''docker() {
      printf '%s\t' "$@" >> "$CALL_LOG"
      printf '\n' >> "$CALL_LOG"
      if [ "$1" = build ] && [ "$FAIL_BUILD" = 1 ]; then return 31; fi
    }
'''
    result = subprocess.run([bash], input=stub + BUILD, cwd=ROOT, env=env, text=True, capture_output=True)
    calls = [line.rstrip("\t").split("\t") for line in log.read_text().splitlines()] if log.exists() else []
    return result, calls


def test_shell_blocks_have_valid_syntax(bash):
    assert len(BLOCKS) == 4
    for block in BLOCKS:
        subprocess.run([bash, "-n"], input=block, text=True, check=True)


def test_build_contexts_and_app_syntax_are_valid():
    ci.validate_inputs()


def test_groovy_image_map_matches_actual_build_declarations():
    mappings = dict(re.findall(r"'([^']+)': '(chargeops[^']+)'", PIPELINE))
    assert len(mappings) == 6
    assert set(mappings.values()) == {image.name for image in IMAGES}
    for folder, image in mappings.items():
        assert next(item for item in IMAGES if item.name == image).dockerfile == f"apps/{folder}/Dockerfile"
    assert "APP_ROOT = 'apps'" in PIPELINE
    assert "PUBLISH_BRANCH = 'kim_chanju'" in PIPELINE
    assert "!env.CHANGE_ID" in PIPELINE


@pytest.mark.parametrize("image", IMAGES, ids=lambda image: image.name)
def test_each_service_builds_only_its_dockerfile(bash, tmp_path, image):
    result, calls = build_with_stub(bash, tmp_path, [image.name], publish=False)
    assert result.returncode == 0, result.stderr
    assert len(calls) == 1
    assert calls[0][:2] == ["build", "--pull"]
    assert calls[0][calls[0].index("-f") + 1] == image.dockerfile
    assert calls[0][-1] == "apps"


def test_all_builds_finish_before_any_image_is_published(bash, tmp_path):
    result, calls = build_with_stub(bash, tmp_path, [image.name for image in IMAGES])
    assert result.returncode == 0, result.stderr
    assert [call[0] for call in calls] == ["build"] * 6 + ["push"] * 6 + ["tag", "push"] * 6
    assert all(call[-1].startswith("registry.chargeops.lab:5000/chargeops/") for call in calls[6:])


def test_build_failure_prevents_all_pushes(bash, tmp_path):
    result, calls = build_with_stub(bash, tmp_path, [image.name for image in IMAGES], fail=True)
    assert result.returncode == 31
    assert [call[0] for call in calls] == ["build"]


@pytest.mark.parametrize("branch,change_id", [("dev", ""), ("kim_chanju", "42")])
def test_wrong_branch_and_pr_cannot_publish(bash, tmp_path, branch, change_id):
    result, calls = build_with_stub(bash, tmp_path, [IMAGES[0].name], branch=branch, change_id=change_id)
    assert result.returncode != 0 and calls == []
    assert "configured non-PR branch" in result.stderr


@pytest.mark.parametrize("name", ["typo", "chargeops-alert-router", "chargeops-mock-csms"])
def test_unknown_or_removed_image_never_reaches_docker(bash, tmp_path, name):
    result, calls = build_with_stub(bash, tmp_path, [name])
    assert result.returncode != 0 and calls == []
    assert "Unknown image" in result.stderr


def test_no_selection_does_not_execute_docker(bash, tmp_path):
    result, calls = build_with_stub(bash, tmp_path, [], publish=False)
    assert result.returncode == 0 and calls == []


def test_diff_detects_root_pipeline_apps_and_tests_but_ignores_other_infra(bash, tmp_path):
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=tmp_path, text=True).strip()

    git("init", "-q")
    git("config", "user.name", "CI test")
    git("config", "user.email", "ci-test@example.invalid")
    (tmp_path / "initial.txt").write_text("initial")
    git("add", ".")
    git("-c", "core.hooksPath=/dev/null", "commit", "-qm", "initial")
    base = git("rev-parse", "HEAD")
    included = ["Jenkinsfile", "apps/ai-gateway/health.py", "apps/common/db.py", "tests/test_ci.py"]
    for name in included + ["infra/app/Jenkinsfile", "docs/note.md", "db/change.sql"]:
        file = tmp_path / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("changed\n")
    git("add", ".")
    git("-c", "core.hooksPath=/dev/null", "commit", "-qm", "changed")
    env = os.environ.copy()
    env.update(APP_ROOT="apps", GIT_PREVIOUS_SUCCESSFUL_COMMIT=base)
    result = subprocess.run([bash], input=DIFF, cwd=tmp_path, env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert set(result.stdout.splitlines()) == set(included)
    env["GIT_PREVIOUS_SUCCESSFUL_COMMIT"] = "missing-commit"
    first = subprocess.run([bash], input=DIFF, cwd=tmp_path, env=env, text=True, capture_output=True)
    assert first.returncode == 0 and first.stdout.strip() == "__BUILD_ALL__"


@pytest.mark.parametrize("folder", ["ai-gateway", "chatops-bot", "notifier"])
def test_shared_imports_are_packaged_in_the_build_context(folder):
    source_dir = ROOT / "apps" / folder
    for source in source_dir.glob("*.py"):
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("common."):
                assert (ROOT / "apps").joinpath(*node.module.split(".")).with_suffix(".py").is_file()
                assert "COPY common/ ./common/" in (source_dir / "Dockerfile").read_text()


def test_bot_policy_preserves_approval_constraints():
    policy_spec = importlib.util.spec_from_file_location("bot_policy_test", ROOT / "apps/common/policy.py")
    policy = importlib.util.module_from_spec(policy_spec)
    sys.modules[policy_spec.name] = policy
    policy_spec.loader.exec_module(policy)
    assert not policy.decide("hard_reset", "Viewer").allowed
    assert policy.decide("hard_reset", "Lead").approvals_needed == 1
    assert not policy.can_approve("hard_reset", "Lead", "U1", "U1", []).allowed
    assert policy.can_approve("hard_reset", "Lead", "U1", "U2", []).allowed
