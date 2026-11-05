"""Read version metadata from explicit trees. No process execution."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from guard import GuardError, assert_version_text


_HEX = re.compile(r"^[0-9a-fA-F]{40,64}$")


def read_versions(
    source: Path | None,
    target: Path | None,
    *,
    source_declared: str | None,
    target_declared: str | None,
    allow_skew: bool,
    allow_unknown: bool,
) -> dict:
    source_py = _pyproject_version(source)
    target_py = _pyproject_version(target)
    source_git = _git_head(source)
    target_git = _git_head(target)
    declared_source = assert_version_text(source_declared, kind="source-version")
    declared_target = assert_version_text(target_declared, kind="target-version")
    left = declared_source or source_py
    right = declared_target or target_py
    if left and right and left == right:
        compatible: object = True
        blocks = False
    elif left and right:
        compatible = False
        blocks = not allow_skew
    else:
        compatible = "unknown"
        blocks = not allow_unknown
    return {
        "schema": "1",
        "policy": "equal_or_explicit_override",
        "source_pyproject": source_py,
        "target_pyproject": target_py,
        "source_git_head": source_git,
        "target_git_head": target_git,
        "source_declared": declared_source,
        "target_declared": declared_target,
        "compared_source": left,
        "compared_target": right,
        "compatible": compatible,
        "blocks_apply": blocks,
        "allow_skew": allow_skew,
        "allow_unknown": allow_unknown,
    }


def _pyproject_version(root: Path | None) -> str | None:
    if root is None or not root.is_dir() or root.is_symlink():
        return None
    path = root / "pyproject.toml"
    if not path.is_file() or path.is_symlink():
        return None
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return None
    project = data.get("project")
    if not isinstance(project, dict):
        return None
    version = project.get("version")
    if not isinstance(version, str):
        return None
    try:
        return assert_version_text(version, kind="pyproject")
    except GuardError:
        return None


def _git_head(root: Path | None) -> str | None:
    if root is None or not root.is_dir() or root.is_symlink():
        return None
    git = root / ".git"
    if not git.exists() or git.is_symlink():
        return None
    if git.is_file():
        return None
    head = git / "HEAD"
    if not head.is_file() or head.is_symlink():
        return None
    line = head.read_text(encoding="utf-8", errors="replace").strip()
    if _HEX.fullmatch(line):
        return line.lower()
    if not line.startswith("ref: "):
        return None
    ref = line.split(" ", 1)[1].strip()
    if not ref.startswith("refs/") or ".." in Path(ref).parts:
        return None
    ref_path = git / ref
    if not ref_path.is_file() or ref_path.is_symlink():
        return None
    value = ref_path.read_text(encoding="utf-8", errors="replace").strip()
    if _HEX.fullmatch(value):
        return value.lower()
    return None
