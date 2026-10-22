"""Validate Docker inputs; selection, build and push live in the root Jenkinsfile."""
from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from pathlib import Path
import re
import shlex

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Image:
    name: str
    dockerfile: str
    context: str


def image_definitions(root: Path = ROOT) -> list[Image]:
    pipeline = (root / "Jenkinsfile").read_text(encoding="utf-8")
    images = [Image(*match) for match in re.findall(
        r"^\s*build_if_selected (\S+) (\S+) (\S+)\s*$", pipeline, re.MULTILINE,
    )]
    if len(images) != 6 or len({image.name for image in images}) != 6:
        raise ValueError("The root pipeline must declare six distinct service images")
    return images


def validate_inputs(root: Path = ROOT) -> None:
    for image in image_definitions(root):
        dockerfile = (root / image.dockerfile).resolve()
        context = (root / image.context).resolve()
        if not dockerfile.is_relative_to(root / "apps") or context != (root / "apps").resolve():
            raise ValueError("Root service images must build from apps/")
        for line in dockerfile.read_text(encoding="utf-8").splitlines():
            if line.startswith("FROM ") and not re.fullmatch(
                r"FROM registry\.chargeops\.lab:5000/chargeops/python:3\.13-slim", line,
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate",))
    parser.parse_args()
    validate_inputs()
    print("6 Docker build contexts, COPY inputs, and app Python syntax validated.")


if __name__ == "__main__":
    main()
