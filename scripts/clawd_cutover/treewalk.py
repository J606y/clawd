"""Walk explicit trees for inventory and copy. Does not mutate trees.

Not named select.py: that would shadow the stdlib module.
"""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from guard import GuardError, normalize_lexical


SKIP_DIR_NAMES = frozenset({
    ".git",
    "__pycache__",
    ".venv",
    "node_modules",
    ".dev-home",
    "artifacts",
})
SECRET_DIR_NAMES = frozenset({
    "secrets",
    "credentials",
    ".ssh",
    ".aws",
    ".gnupg",
    "private_keys",
})
SECRET_FILE_NAMES = frozenset({
    ".env",
    "credentials",
    "credentials.json",
    "auth.json",
    "token.json",
    "tokens.json",
    "config.yaml",
    "config.yml",
    "id_rsa",
    "id_ed25519",
    "id_ecdsa",
    "cookies.txt",
    "service-account.json",
    "api-server.key",
    "sidecar.key",
})
SECRET_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".kdbx")


@dataclass
class Policy:
    include_skipped: bool = False
    profile: str | None = None
    copy_credentials: bool = False
    read_secret_bytes: bool = False
    max_files: int = 100_000
    max_file_bytes: int = 32 * 1024 * 1024


def is_secret_name(name: str) -> bool:
    lower = name.lower()
    if lower in SECRET_FILE_NAMES or lower in SECRET_DIR_NAMES:
        return True
    if lower.startswith(".env"):
        return True
    return lower.endswith(SECRET_SUFFIXES)


def empty_summary() -> dict:
    return {
        "files": 0,
        "bytes": 0,
        "redacted": 0,
        "skipped_dirs": 0,
        "symlinks": 0,
        "special": 0,
        "too_large": 0,
        "profile_siblings_unnamed": 0,
        "missing": False,
        "crossed_filesystem": 0,
    }


def iter_tree(root: Path, policy: Policy) -> tuple[list[dict], dict]:
    summary = empty_summary()
    resources: list[dict] = []
    if not root.exists():
        summary["missing"] = True
        return resources, summary
    if root.is_symlink() or not root.is_dir():
        raise GuardError(f"树根必须是真实目录: {root}")
    root_dev = root.stat().st_dev
    seen = 0
    walk_errors: list[OSError] = []

    def _onerror(err: OSError) -> None:
        walk_errors.append(err)

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False, onerror=_onerror):
        try:
            rel_dir = Path(dirpath).relative_to(root).as_posix()
        except ValueError as exc:
            raise GuardError(f"遍历逃出树根: {dirpath}") from exc
        if rel_dir == ".":
            rel_dir = ""
        _prune(Path(dirpath), dirnames, rel_dir, root_dev, policy, summary, resources)
        for name in filenames:
            seen += 1
            if seen > policy.max_files:
                raise GuardError("文件数超过 --max-files，已停止")
            rel = name if not rel_dir else f"{rel_dir}/{name}"
            resources.append(_file_entry(Path(dirpath) / name, rel, policy, summary))
    if walk_errors:
        raise GuardError("遍历遇到不可读目录，拒绝继续")
    return resources, summary


def _prune(
    directory: Path,
    dirnames: list[str],
    rel_dir: str,
    root_dev: int,
    policy: Policy,
    summary: dict,
    resources: list[dict],
) -> None:
    kept: list[str] = []
    in_profiles = rel_dir == "profiles" or rel_dir.endswith("/profiles")
    for name in list(dirnames):
        child_rel = name if not rel_dir else f"{rel_dir}/{name}"
        child = directory / name
        if name in {".hermes", ".ssh"}:
            _skip_dir(resources, summary, child_rel, "banned_nested", redacted=True)
            continue
        if in_profiles and (policy.profile is None or name != policy.profile):
            summary["profile_siblings_unnamed"] += 1
            continue
        if name in SKIP_DIR_NAMES and not policy.include_skipped:
            _skip_dir(resources, summary, child_rel, "default_skip", redacted=False)
            continue
        if is_secret_name(name) and not policy.copy_credentials:
            _skip_dir(resources, summary, child_rel, "secret_name", redacted=True)
            continue
        try:
            st = child.lstat()
        except OSError as exc:
            resources.append(_problem(child_rel, f"stat_failed:{exc.errno}"))
            continue
        if stat.S_ISLNK(st.st_mode):
            summary["symlinks"] += 1
            resources.append(_entry(child_rel, "symlink", redacted=False, skip_reason="symlink"))
            continue
        if not stat.S_ISDIR(st.st_mode):
            summary["special"] += 1
            resources.append(_entry(child_rel, "special", redacted=False, skip_reason="special"))
            continue
        if st.st_dev != root_dev:
            summary["crossed_filesystem"] += 1
            _skip_dir(resources, summary, child_rel, "crossed_filesystem", redacted=False)
            continue
        lexical = normalize_lexical(str(child))
        if name == ".hermes" or lexical.endswith("/.hermes") or "/.hermes/" in lexical:
            _skip_dir(resources, summary, child_rel, "banned_nested", redacted=True)
            continue
        kept.append(name)
    dirnames[:] = kept


def _file_entry(path: Path, rel: str, policy: Policy, summary: dict) -> dict:
    try:
        st = path.lstat()
    except OSError as exc:
        return _problem(rel, f"stat_failed:{exc.errno}")
    if stat.S_ISLNK(st.st_mode):
        summary["symlinks"] += 1
        return _entry(rel, "symlink", redacted=False, skip_reason="symlink")
    if not stat.S_ISREG(st.st_mode):
        summary["special"] += 1
        return _entry(rel, "special", redacted=False, skip_reason="special")
    secret = is_secret_name(path.name)
    if secret and not (policy.copy_credentials and policy.read_secret_bytes):
        summary["redacted"] += 1
        return _entry(
            rel,
            "file",
            redacted=True,
            skip_reason="secret_name",
            mode=st.st_mode & 0o777,
            size=st.st_size,
        )
    if st.st_size > policy.max_file_bytes:
        summary["too_large"] += 1
        return _entry(
            rel,
            "file",
            redacted=False,
            skip_reason="too_large",
            mode=st.st_mode & 0o777,
            size=st.st_size,
        )
    digest = _hash_file(path)
    summary["files"] += 1
    summary["bytes"] += st.st_size
    if secret:
        summary["redacted"] += 1
    return _entry(
        rel,
        "file",
        redacted=secret,
        skip_reason=None,
        mode=st.st_mode & 0o777,
        size=st.st_size,
        sha256=digest,
    )


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _skip_dir(resources: list[dict], summary: dict, rel: str, reason: str, *, redacted: bool) -> None:
    summary["skipped_dirs"] += 1
    if redacted:
        summary["redacted"] += 1
    resources.append(_entry(rel, "dir", redacted=redacted, skip_reason=reason, skipped=True))


def _problem(rel: str, reason: str) -> dict:
    return _entry(rel, "unreadable", redacted=False, skip_reason=reason, skipped=True)


def _entry(
    rel: str,
    kind: str,
    *,
    redacted: bool,
    skip_reason: str | None,
    skipped: bool | None = None,
    mode: int | None = None,
    size: int | None = None,
    sha256: str | None = None,
) -> dict:
    if skipped is None:
        skipped = skip_reason is not None
    return {
        "relpath": rel,
        "kind": kind,
        "bytes": size,
        "sha256": sha256,
        "mode": None if mode is None else f"{mode:04o}",
        "redacted": redacted,
        "skipped": skipped,
        "skip_reason": skip_reason,
    }
