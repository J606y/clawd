"""Read-only source and build fingerprints. Missing artifacts stay incomplete."""

from __future__ import annotations

import hashlib
from pathlib import Path

from argv_contract import source_text
from stages import classify_distribution_handoff


def utc_now() -> str:
    import time

    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class TrustedArchiveHandoff:
    """Wheel and sdist paths from this run's classified package stage.

    This is not a directory. ``artifact_report`` must not list it.
    """

    def __init__(self, isolation: Path, wheel: str | None, sdist: str | None) -> None:
        self.isolation = Path(isolation)
        self.wheel = wheel if isinstance(wheel, str) and wheel else None
        self.sdist = sdist if isinstance(sdist, str) and sdist else None


def source_identity(repo: Path, captured_at: str) -> dict:
    version = _pyproject_version(repo)
    head = _git_head(repo)
    complete = bool(version and head)
    fingerprint = None
    if complete:
        fingerprint = hashlib.sha256(f"{version}\n{head}\n".encode()).hexdigest()
    return {
        "repo": str(repo),
        "pyproject_version": version,
        "git_head": head,
        "fingerprint": fingerprint,
        "captured_at": captured_at,
        "complete": complete,
        "method": "pyproject.toml and .git/HEAD read; git was not executed",
    }


def artifact_report(repo: Path, *, wheel_dir: Path | TrustedArchiveHandoff | None = None) -> dict:
    """Hash checkout dist files and this run's handed archives.

    A directory argument is not searched. Existence without a content hash
    is not complete. Archive protections stay in ``classify_distribution_handoff``.
    """
    items = []
    blockers = []
    for rel in ("ui-tui/dist/entry.js", "apps/panel/dist/index.html"):
        row, blocker = _file_row(repo, rel)
        items.append(row)
        if blocker:
            blockers.append(blocker)
    archive_items, archive_blockers = _trusted_archives(repo, wheel_dir)
    items.extend(archive_items)
    blockers.extend(archive_blockers)
    return {
        "items": items,
        "complete": not blockers and bool(items) and all(item.get("sha256") and item.get("content_hashed") for item in items),
        "blockers": blockers,
        "deploy_support_verified": False,
        "note": "未应用的 systemd/nginx 模板不是构建产物，也不证明部署支持",
        "method": "sha256 of trusted stage wheel and sdist paths; directory listing is not used",
    }


def _trusted_archives(repo: Path, wheel_dir: Path | TrustedArchiveHandoff | None) -> tuple[list[dict], list[str]]:
    if not isinstance(wheel_dir, TrustedArchiveHandoff):
        return [], ["no trusted stage archive paths; refusing to list the isolation directory"]
    isolation = wheel_dir.isolation
    wheel = wheel_dir.wheel
    sdist = wheel_dir.sdist
    if isolation.is_symlink() or not isolation.is_dir():
        return [], ["isolation directory missing or symlink; refusing to list archives"]
    if not wheel and not sdist:
        return [], ["no trusted stage archive paths; refusing to list the isolation directory"]
    if not wheel or not sdist:
        missing = "sdist" if wheel else "wheel"
        return [], [f"one trusted archive path missing ({missing}); refusing to hash the other as complete"]
    handoff = classify_distribution_handoff(wheel, sdist, isolation)
    if not handoff.get("may_start_manifest_test"):
        return [], [str(handoff.get("detail") or "trusted paths failed archive protections")]
    items = []
    blockers = []
    for kind, raw in (("wheel", wheel), ("sdist", sdist)):
        row, blocker = _archive_row(repo, isolation, Path(raw), kind)
        items.append(row)
        if blocker:
            blockers.append(blocker)
    return items, blockers


def _archive_row(repo: Path, isolation: Path, path: Path, kind: str) -> tuple[dict, str | None]:
    label = str(path)
    refused = {
        "path": label,
        "kind": kind,
        "sha256": None,
        "present": False,
        "content_hashed": False,
    }
    if path.is_symlink() or path.parent.is_symlink():
        return refused, f"refusing symlink archive: {path}"
    if not path.is_file():
        return refused, f"{kind} was not created as a regular file"
    if not _under(isolation, path) or not _under(repo, path):
        return refused, f"{kind} is outside the checkout"
    rel = path.absolute().relative_to(repo.absolute()).as_posix()
    try:
        digest = _sha256(path)
    except OSError as exc:
        return {**refused, "path": rel}, f"hash failed {rel}: {type(exc).__name__}"
    if not digest:
        return {**refused, "path": rel}, f"hash failed {rel}"
    return {
        "path": rel,
        "kind": kind,
        "sha256": digest,
        "present": True,
        "content_hashed": True,
    }, None


def _file_row(repo: Path, rel: str) -> tuple[dict, str | None]:
    path = repo / rel
    if path.is_symlink() or not path.is_file():
        return {"path": rel, "sha256": None, "present": False, "content_hashed": False}, f"missing {rel}"
    try:
        digest = _sha256(path)
    except OSError as exc:
        return {"path": rel, "sha256": None, "present": False, "content_hashed": False}, f"hash failed {rel}: {type(exc).__name__}"
    return {"path": rel, "sha256": digest, "present": True, "content_hashed": True}, None


def _under(root: Path, path: Path) -> bool:
    try:
        path.absolute().relative_to(root.absolute())
    except (OSError, ValueError):
        return False
    return True


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _pyproject_version(repo: Path) -> str | None:
    text = source_text(repo / "pyproject.toml", 4000)
    for line in text.splitlines():
        if line.startswith("version") and "=" in line:
            value = line.split("=", 1)[1].strip().strip('"').strip("'")
            if value and all(ch.isalnum() or ch in "._+-" for ch in value):
                return value
    return None


def _git_head(repo: Path) -> str | None:
    git = repo / ".git"
    if not git.is_dir() or git.is_symlink():
        return None
    head = git / "HEAD"
    if not head.is_file() or head.is_symlink():
        return None
    line = head.read_text(encoding="utf-8", errors="replace").strip()
    if len(line) in {40, 64} and all(ch in "0123456789abcdefABCDEF" for ch in line):
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
    if len(value) in {40, 64} and all(ch in "0123456789abcdefABCDEF" for ch in value):
        return value.lower()
    return None
