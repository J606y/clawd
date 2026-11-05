#!/usr/bin/env python3
"""Restore local backup generated before update.
Reads index.patch, worktree.patch (or legacy local-changes.patch), and untracked.tar.gz
and applies them to the target repository.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import tarfile


def run_git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-c", "safe.directory=*", "-C", str(repo), *args],
        capture_output=True,
        check=check,
        stdin=subprocess.DEVNULL,
    )


def restore_backup(repo: Path, backup_dir: Path) -> dict[str, int]:
    if not (repo / ".git").exists():
        raise RuntimeError(f"Target directory {repo} is not a git repository")
    if not backup_dir.is_dir():
        raise RuntimeError(f"Backup directory {backup_dir} does not exist")

    index_patch = backup_dir / "index.patch"
    worktree_patch = backup_dir / "worktree.patch"
    legacy_patch = backup_dir / "local-changes.patch"
    untracked_archive = backup_dir / "untracked.tar.gz"

    index_bytes = 0
    worktree_bytes = 0
    untracked_count = 0

    # 1. Restore staged index patch if present
    # Applying to both cached and working tree puts staged files into index cleanly
    if index_patch.exists() and index_patch.stat().st_size > 0:
        index_bytes = index_patch.stat().st_size
        # Apply to working tree and index first
        p = run_git(repo, "apply", "--index", "--binary", str(index_patch), check=False)
        if p.returncode != 0:
            # Fallback to apply --cached if worktree differs
            p_cached = run_git(repo, "apply", "--cached", "--binary", str(index_patch), check=False)
            if p_cached.returncode != 0:
                err = p.stderr.decode("utf-8", errors="replace")
                raise RuntimeError(f"Failed to apply index patch: {err}")

    # 2. Restore worktree patch (or legacy local-changes.patch)
    target_worktree_patch = worktree_patch if worktree_patch.exists() else legacy_patch
    if target_worktree_patch.exists() and target_worktree_patch.stat().st_size > 0:
        worktree_bytes = target_worktree_patch.stat().st_size
        p = run_git(repo, "apply", "--binary", str(target_worktree_patch), check=False)
        if p.returncode != 0:
            err = p.stderr.decode("utf-8", errors="replace")
            raise RuntimeError(f"Failed to apply worktree patch: {err}")

    # 3. Restore untracked files
    if untracked_archive.exists() and untracked_archive.stat().st_size > 0:
        with tarfile.open(untracked_archive, "r:gz") as tar:
            for member in tar.getmembers():
                dest = (repo / member.name).resolve()
                if not dest.is_relative_to(repo.resolve()):
                    raise RuntimeError(f"Path traversal detected in archive: {member.name}")
                target_path = repo / member.name
                if target_path.exists() or target_path.is_symlink():
                    raise RuntimeError(f"Untracked restore refused: target already exists: {member.name}")
                if sys.version_info >= (3, 12):
                    tar.extract(member, path=repo, filter="data")
                else:
                    tar.extract(member, path=repo)
                untracked_count += 1

    return {
        "index_bytes": index_bytes,
        "worktree_bytes": worktree_bytes,
        "untracked_count": untracked_count,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Restore Hermes local backup")
    parser.add_argument("--repo", type=Path, required=True, help="Path to git repository")
    parser.add_argument("--backup-dir", type=Path, required=True, help="Path to backup directory")
    args = parser.parse_args()

    try:
        stats = restore_backup(args.repo, args.backup_dir)
        print(f"Restore succeeded: {stats}")
    except Exception as exc:
        print(f"Restore failed: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
