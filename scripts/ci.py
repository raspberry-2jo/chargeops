"""Root-app CI: select images, check build inputs, and build/push without deploying."""
from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
import os
from pathlib import Path
import re
import shlex
import subprocess


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Image:
    name: str
    folder: str
    dockerfile: str
    context: str


IMAGES = (
    Image("chargeops-chatops-bot", "apps/legacy", "apps/legacy/docker/chatops-bot.Dockerfile", "apps/legacy"),
    Image("chargeops-alert-router", "apps/legacy", "apps/legacy/docker/router.Dockerfile", "apps/legacy"),
    Image("chargeops-mock-csms", "apps/legacy", "apps/legacy/docker/mock-csms.Dockerfile", "apps/legacy"),
    Image("chargeops-ai-gateway", "apps/ai-gateway", "apps/ai-gateway/Dockerfile", "apps"),
    Image("chargeops-v2-chatops-bot", "apps/chatops-bot", "apps/chatops-bot/Dockerfile", "apps"),
    Image("chargeops-v2-csms", "apps/csms", "apps/csms/Dockerfile", "apps"),
    Image("chargeops-v2-notifier", "apps/notifier", "apps/notifier/Dockerfile", "apps"),
    Image("chargeops-v2-vcharger", "apps/virtual-charger", "apps/virtual-charger/Dockerfile", "apps"),
    Image("chargeops-v2-collector", "apps/collector", "apps/collector/Dockerfile", "apps"),
)
LEGACY = {image.name for image in IMAGES if image.folder == "apps/legacy"}
MODERN = {image.name for image in IMAGES} - LEGACY
COMMON_USERS = {"chargeops-ai-gateway", "chargeops-v2-chatops-bot", "chargeops-v2-notifier"}


def select_images(paths: list[str], force: bool = False) -> tuple[bool, list[str]]:
    selected: set[str] = set()
    test_changed = force
    if force:
        selected.update(image.name for image in IMAGES)
    for path in paths:
        if path in {"Jenkinsfile", "scripts/ci.py"}:
            selected.update(image.name for image in IMAGES)
        elif path in {"requirements-dev.txt", "pytest.ini"} or path.startswith("tests/"):
            test_changed = True
        elif path == "apps/.dockerignore":
            selected.update(MODERN)
        elif path.startswith("apps/common/"):
            selected.update(COMMON_USERS)
        elif path.startswith("apps/"):
            # Documentation, SQL checks and test fixtures do not change runtime images.
            name = Path(path).name
            if path.endswith((".md", ".sql")) or name == ".gitkeep":
                continue
            if name.startswith("test_") or "/tests/" in path:
                test_changed = True
                continue
            if path.startswith("apps/legacy/docker/"):
                match = next((image for image in IMAGES if image.dockerfile == path), None)
                selected.update({match.name} if match else LEGACY)
            else:
                matches = [image.name for image in IMAGES if path.startswith(image.folder + "/")]
                selected.update(matches or MODERN)
    return test_changed or bool(selected), [image.name for image in IMAGES if image.name in selected]


def changed_paths(base: str, root: Path = ROOT) -> list[str] | None:
    """None means no valid previous successful commit: build all, including new jobs."""
    if not base:
        return None
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "--end-of-options", base + "^{commit}"],
        cwd=root, capture_output=True, text=True,
    )
    if result.returncode:
        return None
    commit = result.stdout.strip()
    output = subprocess.check_output(
        ["git", "diff", "--name-only", "--no-renames", "-z", commit, "HEAD", "--"], cwd=root,
    )
    return [name for name in output.decode("utf-8").split("\0") if name]


def validate_inputs(root: Path = ROOT) -> None:
    for image in IMAGES:
        dockerfile = root / image.dockerfile
        context = (root / image.context).resolve()
        for line in dockerfile.read_text(encoding="utf-8").splitlines():
            if line.startswith("FROM "):
                if not re.fullmatch(
                    r"FROM registry\.chargeops\.lab:5000/chargeops/python:3\.(12|13)-slim", line,
                ):
                    raise ValueError(f"Unexpected base image in {image.dockerfile}: {line}")
            if line.startswith("COPY "):
                for source in shlex.split(line)[1:-1]:
                    path = (context / source).resolve()
                    if not path.is_relative_to(context) or not path.exists():
                        raise ValueError(f"Missing or out-of-context COPY input: {image.dockerfile}: {source}")
    for source in (root / "apps").rglob("*.py"):
        if not any(part in {".venv", ".ci-venv", "__pycache__"} for part in source.parts):
            ast.parse(source.read_text(encoding="utf-8-sig"), filename=str(source))


def build_images(names: list[str], registry: str, tag: str, publish: bool, root: Path = ROOT) -> None:
    catalog = {image.name: image for image in IMAGES}
    if set(names) - catalog.keys():
        raise ValueError("Unknown image selection")
    if not names:
        return
    if not re.fullmatch(r"[a-z0-9][a-z0-9.:/-]*", registry) or registry.endswith("/"):
        raise ValueError("REGISTRY must be a Docker image prefix without scheme or trailing slash")
    if not re.fullmatch(r"[\w][\w.-]{0,127}", tag, flags=re.ASCII):
        raise ValueError("Invalid IMAGE_TAG")
    if publish and tag == "dev":
        raise ValueError("Use an immutable build tag, not dev, as IMAGE_TAG")

    def docker(*args: str) -> None:
        command = ["docker", *args]
        print(shlex.join(command), flush=True)
        subprocess.run(command, cwd=root, check=True)

    # Build everything selected before publishing any image.
    for name in dict.fromkeys(names):
        image = catalog[name]
        docker("build", "--pull", "-f", image.dockerfile, "-t", f"{registry}/{name}:{tag}", image.context)
    if publish:
        for name in dict.fromkeys(names):
            docker("push", f"{registry}/{name}:{tag}")
        for name in dict.fromkeys(names):
            docker("tag", f"{registry}/{name}:{tag}", f"{registry}/{name}:dev")
            docker("push", f"{registry}/{name}:dev")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("select", "validate", "build"))
    args = parser.parse_args()
    if args.command == "select":
        paths = changed_paths(os.environ.get("GIT_PREVIOUS_SUCCESSFUL_COMMIT", ""))
        changed, selected = select_images(paths or [], force=paths is None or os.environ.get("FORCE_BUILD_ALL") == "true")
        print(str(changed).lower())
        print(" ".join(selected))
    elif args.command == "validate":
        validate_inputs()
        print("Nine Docker build contexts, COPY inputs, and app Python syntax validated.")
    else:
        validate_inputs()
        publish = os.environ.get("PUBLISH_IMAGES") == "true"
        if publish and (
            os.environ.get("CHECKOUT_BRANCH") != os.environ.get("PUBLISH_BRANCH", "kim_chanju")
            or os.environ.get("CHANGE_ID")
        ):
            raise ValueError("Image publication is restricted to the configured non-PR branch")
        build_images(
            os.environ.get("BUILD_IMAGES", "").split(),
            os.environ["REGISTRY"], os.environ["IMAGE_TAG"], publish,
        )


if __name__ == "__main__":
    main()
