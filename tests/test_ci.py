"""Exercise branch-independent selection and the publication command boundary."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ci.py"
spec = importlib.util.spec_from_file_location("chargeops_ci", SCRIPT)
ci = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = ci
spec.loader.exec_module(ci)


@pytest.mark.parametrize("paths,expected", [
    (["apps/ai-gateway/health.py"], {"chargeops-ai-gateway"}),
    (["apps/csms/app.py"], {"chargeops-v2-csms"}),
    (["apps/collector/collector_v1.py"], {"chargeops-v2-collector"}),
    (["apps/chatops-bot/requirements.txt"], {"chargeops-v2-chatops-bot"}),
    (["apps/virtual-charger/Dockerfile"], {"chargeops-v2-vcharger"}),
    (["apps/notifier/app.py"], {"chargeops-v2-notifier"}),
    (["apps/common/db.py"], ci.COMMON_USERS),
    (["apps/legacy/app/csms.py"], ci.LEGACY),
    (["apps/legacy/docker/router.Dockerfile"], {"chargeops-alert-router"}),
    (["apps/.dockerignore"], ci.MODERN),
    (["apps/legacy/.dockerignore"], ci.LEGACY),
    (["Jenkinsfile"], {image.name for image in ci.IMAGES}),
    (["scripts/ci.py"], {image.name for image in ci.IMAGES}),
])
def test_runtime_changes_select_their_consumers(paths, expected):
    run_tests, selected = ci.select_images(paths)
    assert run_tests
    assert set(selected) == expected


def test_non_app_changes_do_not_build_or_test_apps():
    assert ci.select_images([
        "docs/infra/root-app-cicd.md", "db/migrations/new.sql", "infra/ansible/patroni.yml",
        "infra/app/apps/csms/app.py", "apps/collector/check_queries.sql",
    ]) == (False, [])


def test_test_only_changes_run_checks_without_republishing_images():
    assert ci.select_images(["apps/legacy/tests/test_csms.py", "pytest.ini", "requirements-dev.txt"]) == (True, [])


def test_force_build_and_shared_changes_do_not_duplicate_images():
    run_tests, selected = ci.select_images(["apps/common/db.py", "apps/ai-gateway/app.py"], force=True)
    assert run_tests and len(selected) == len(set(selected)) == 9


def test_real_git_diff_handles_renames_and_invalid_previous_commits(tmp_path):
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=tmp_path, text=True).strip()

    git("init", "-q")
    git("config", "user.name", "CI test")
    git("config", "user.email", "ci-test@example.invalid")
    source = tmp_path / "apps" / "ai-gateway"
    source.mkdir(parents=True)
    (source / "health.py").write_text("marker = 'old'\n")
    git("add", ".")
    git("-c", "core.hooksPath=/dev/null", "commit", "-qm", "initial")
    base = git("rev-parse", "HEAD")
    (source / "health.py").rename(source / "probe.py")
    git("add", "-A")
    git("-c", "core.hooksPath=/dev/null", "commit", "-qm", "rename")
    assert set(ci.changed_paths(base, tmp_path)) == {"apps/ai-gateway/health.py", "apps/ai-gateway/probe.py"}
    assert ci.changed_paths("missing-commit", tmp_path) is None
    assert ci.changed_paths("", tmp_path) is None


def test_build_contexts_and_app_syntax_are_valid():
    ci.validate_inputs()


def test_publish_builds_everything_before_pushing_immutable_and_dev_tags(monkeypatch):
    calls = []
    monkeypatch.setattr(ci.subprocess, "run", lambda command, **kwargs: calls.append(command))
    ci.build_images(["chargeops-ai-gateway", "chargeops-v2-csms"], "registry.chargeops.lab:5000/chargeops", "build-12-abc", True)
    assert [command[1] for command in calls] == ["build", "build", "push", "push", "tag", "push", "tag", "push"]
    assert calls[0][-1] == "apps"
    assert calls[0][calls[0].index("-f") + 1] == "apps/ai-gateway/Dockerfile"
    assert calls[2][-1] == "registry.chargeops.lab:5000/chargeops/chargeops-ai-gateway:build-12-abc"
    assert calls[-1][-1] == "registry.chargeops.lab:5000/chargeops/chargeops-v2-csms:dev"


def test_nonpublish_build_never_pushes(monkeypatch):
    calls = []
    monkeypatch.setattr(ci.subprocess, "run", lambda command, **kwargs: calls.append(command))
    ci.build_images(["chargeops-mock-csms"], "registry.chargeops.lab:5000/chargeops", "build-12-abc", False)
    assert len(calls) == 1
    assert calls[0][1] == "build" and calls[0][-1] == "apps/legacy"


def test_build_failure_never_retags_or_pushes(monkeypatch):
    calls = []

    def fail(command, **kwargs):
        calls.append(command)
        raise subprocess.CalledProcessError(31, command)

    monkeypatch.setattr(ci.subprocess, "run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        ci.build_images(["chargeops-ai-gateway"], "registry.chargeops.lab:5000/chargeops", "build-12-abc", True)
    assert [command[1] for command in calls] == ["build"]


@pytest.mark.parametrize("branch,change_id", [("dev", ""), ("kim_chanju", "42")])
def test_cli_rejects_publication_from_wrong_branch_or_pr(branch, change_id):
    env = os.environ.copy()
    env.update(PUBLISH_IMAGES="true", CHECKOUT_BRANCH=branch, CHANGE_ID=change_id, PUBLISH_BRANCH="kim_chanju")
    result = subprocess.run([sys.executable, str(SCRIPT), "build"], env=env, text=True, capture_output=True)
    assert result.returncode != 0
    assert "restricted to the configured non-PR branch" in result.stderr


def test_unknown_image_is_rejected_before_docker(monkeypatch):
    def unexpected_call(*args, **kwargs):
        pytest.fail("Docker must not execute for an unknown image")

    monkeypatch.setattr(ci.subprocess, "run", unexpected_call)
    with pytest.raises(ValueError, match="Unknown image"):
        ci.build_images(["typo"], "registry.chargeops.lab:5000/chargeops", "build-12-abc", True)
