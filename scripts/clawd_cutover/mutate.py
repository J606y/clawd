"""Backup, restore, apply, and rollback.

No directory is removed. Rename runs only onto an absent path. A failed
swap renames the previous tree back and leaves staging where it is.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import uuid
from pathlib import Path

from guard import GuardError, assert_not_banned, parent_exists
from manifest import discard_partial, utc_now, write_raw_json
from treewalk import Policy, iter_tree


def new_id() -> str:
    return uuid.uuid4().hex[:16]


def backup_tree(source: Path, backup_dir: Path, policy: Policy) -> dict:
    _refuse(source, "source")
    _refuse(backup_dir, "backup-dir")
    if not source.is_dir() or source.is_symlink():
        raise GuardError("backup 的 source 必须是真实目录")
    if backup_dir.exists() or backup_dir.is_symlink():
        raise GuardError("backup-dir 必须不存在，拒绝覆盖未知内容")
    parent_exists(backup_dir)
    resources, summary = iter_tree(source, policy)
    _raise_if_incomplete(summary, resources)
    backup_dir.mkdir(mode=0o700, parents=False)
    files_root = backup_dir / "files"
    files_root.mkdir(mode=0o700)
    copied = _copy_selected(source, files_root, resources)
    write_raw_json(
        backup_dir / "backup-manifest.json",
        {
            "format": "clawd-cutover-backup",
            "format_version": "1",
            "created_at": utc_now(),
            "source": str(source),
            "credentials_copied": _creds(policy),
            "profile": policy.profile,
            "files": copied,
            "directories_removed": False,
        },
    )
    return {
        "prepared": True,
        "path": str(backup_dir),
        "file_count": len(copied),
        "credentials_copied": _creds(policy),
        "profile": policy.profile,
        "directories_removed": False,
    }


def apply_tree(source: Path, target: Path, backup_dir: Path, policy: Policy) -> dict:
    _refuse(source, "source")
    _refuse(target, "target")
    _refuse(backup_dir, "backup-dir")
    if not source.is_dir() or source.is_symlink():
        raise GuardError("apply 的 source 必须是真实目录")
    if target.is_symlink():
        raise GuardError("拒绝替换符号链接目标")
    if target.exists() and not target.is_dir():
        raise GuardError("target 已存在且不是目录")
    parent_exists(target)
    if backup_dir.exists() or backup_dir.is_symlink():
        raise GuardError("backup-dir 必须不存在，拒绝覆盖未知内容")
    parent_exists(backup_dir)
    resources, summary = iter_tree(source, policy)
    _raise_if_incomplete(summary, resources)
    if not any(_copyable(item) for item in resources):
        raise GuardError("source 在当前策略下没有可复制文件，拒绝切换")
    op_id = new_id()
    staging = target.parent / f"{target.name}.clawd-staging-{op_id}"
    previous = target.parent / f"{target.name}.clawd-previous-{op_id}"
    _refuse(staging, "staging")
    _refuse(previous, "previous")
    for slot in (staging, previous):
        if slot.exists() or slot.is_symlink():
            raise GuardError(f"槽位已存在，拒绝覆盖: {slot.name}")
    had_target = target.exists()
    backup_dir.mkdir(mode=0o700, parents=False)
    source_backup = backup_dir / "source"
    source_backup.mkdir(mode=0o700)
    source_files = _copy_selected(source, source_backup, resources)
    target_files: list[dict] = []
    if had_target:
        target_backup = backup_dir / "target"
        target_backup.mkdir(mode=0o700)
        target_resources, _ignored = iter_tree(target, policy)
        target_files = _copy_selected(target, target_backup, target_resources)
    write_raw_json(
        backup_dir / "backup-manifest.json",
        {
            "format": "clawd-cutover-backup",
            "format_version": "1",
            "created_at": utc_now(),
            "source": str(source),
            "target": str(target),
            "credentials_copied": _creds(policy),
            "profile": policy.profile,
            "source_files": source_files,
            "target_files": target_files,
            "directories_removed": False,
            "note": "target 文件副本受同一跳过策略限制；完整回滚依赖 previous 目录改名，不删除目录",
        },
    )
    staging.mkdir(mode=0o700)
    staged = _copy_selected(source, staging, resources)
    journal = {
        "format": "clawd-cutover-journal",
        "format_version": "1",
        "id": op_id,
        "phase": "pre_swap",
        "target": str(target),
        "staging": str(staging),
        "previous": str(previous) if had_target else None,
        "had_target": had_target,
        "steps": [],
        "directories_removed": False,
        "created_at": utc_now(),
    }
    write_raw_json(backup_dir / "journal-pre.json", journal)
    if had_target != target.exists():
        raise GuardError("target 在准备期间发生变化，已停止且未改名")
    try:
        if had_target:
            _rename_onto_absent(target, previous)
            journal["steps"].append({"op": "rename", "src": str(target), "dst": str(previous)})
        _rename_onto_absent(staging, target)
        journal["steps"].append({"op": "rename", "src": str(staging), "dst": str(target)})
        _fsync_dir(target.parent)
    except Exception as exc:
        recovery = _recover_apply(journal, target, previous)
        journal["phase"] = "failed"
        journal["recovery"] = recovery
        _write_failure_journal(backup_dir, journal)
        raise GuardError(f"apply 失败，恢复状态={recovery}；未删除目录") from exc
    journal["phase"] = "swapped"
    journal["file_count"] = len(staged)
    try:
        write_raw_json(backup_dir / "journal-swapped.json", journal)
    except GuardError:
        journal["sidecar_journal"] = "not_written"
    return {
        "phase": "swapped",
        "id": op_id,
        "previous": journal["previous"],
        "backup": str(backup_dir),
        "file_count": len(staged),
        "credentials_copied": _creds(policy),
        "directories_removed": False,
    }


def rollback_tree(target: Path, previous: Path | None, journal_path: Path) -> dict:
    _refuse(target, "target")
    _refuse(journal_path, "journal")
    if previous is not None:
        _refuse(previous, "previous")
    if journal_path.is_symlink() or not journal_path.is_file():
        raise GuardError("journal 必须是真实文件")
    if target.is_symlink() or (previous is not None and previous.is_symlink()):
        raise GuardError("拒绝在符号链接上回滚")
    if not target.is_dir():
        raise GuardError("rollback 的 target 必须是真实目录")
    try:
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GuardError("journal 不是可读 JSON") from exc
    if not isinstance(journal, dict):
        raise GuardError("journal 根必须是对象")
    if journal.get("format") != "clawd-cutover-journal":
        raise GuardError("journal 格式不匹配")
    if journal.get("target") != str(target):
        raise GuardError("journal 路径与参数不一致，拒绝回滚")
    phase = journal.get("phase")
    if phase == "swapped":
        pass
    elif phase == "pre_swap" and _swap_visible(journal, target):
        pass
    else:
        raise GuardError("journal 不是已完成切换，拒绝回滚")
    recorded = journal.get("previous")
    if recorded is None:
        if previous is not None or journal.get("had_target") is not False:
            raise GuardError("首次安装回滚不得传入 previous")
    else:
        if previous is None or recorded != str(previous) or not previous.is_dir():
            raise GuardError("journal 的 previous 与参数不一致，拒绝回滚")
    op_id = new_id()
    failed = target.parent / f"{target.name}.clawd-failed-{op_id}"
    _refuse(failed, "failed-slot")
    if failed.exists() or failed.is_symlink():
        raise GuardError("failed 槽位已存在，拒绝覆盖")
    parent_exists(failed)
    _rename_onto_absent(target, failed)
    try:
        if previous is not None:
            _rename_onto_absent(previous, target)
        _fsync_dir(target.parent)
    except Exception as exc:
        restored = "needs_manual_recovery"
        if not target.exists() and failed.is_dir() and not failed.is_symlink():
            _rename_onto_absent(failed, target)
            restored = "restored_target_name"
        raise GuardError(f"rollback 失败，恢复状态={restored}；未删除目录") from exc
    result = {
        "format": "clawd-cutover-journal",
        "format_version": "1",
        "phase": "rolled_back",
        "id": op_id,
        "target": str(target),
        "previous_consumed": None if previous is None else str(previous),
        "failed_slot": str(failed),
        "from_journal": journal_path.name,
        "directories_removed": False,
        "created_at": utc_now(),
    }
    try:
        write_raw_json(journal_path.parent / f"journal-rollback-{op_id}.json", result)
    except GuardError:
        result["sidecar_journal"] = "not_written"
    return {
        "phase": "rolled_back",
        "id": op_id,
        "failed_slot": str(failed),
        "directories_removed": False,
    }


def restore_tree(backup_dir: Path, target: Path) -> dict:
    _refuse(backup_dir, "backup-dir")
    _refuse(target, "target")
    if backup_dir.is_symlink() or not backup_dir.is_dir():
        raise GuardError("backup-dir 必须是真实目录")
    manifest_path = backup_dir / "backup-manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise GuardError("缺少 backup-manifest.json")
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GuardError("无法读取备份清单") from exc
    if not isinstance(raw, dict) or raw.get("format") != "clawd-cutover-backup":
        raise GuardError("备份清单格式不匹配")
    files = raw.get("files")
    if files is None:
        files = raw.get("source_files")
    if not isinstance(files, list) or not files:
        raise GuardError("备份清单没有文件列表")
    if target.is_symlink():
        raise GuardError("拒绝向符号链接恢复")
    created_root = False
    if not target.exists():
        parent_exists(target)
        target.mkdir(mode=0o700, parents=False)
        created_root = True
    elif not target.is_dir():
        raise GuardError("restore 目标已存在且不是目录")
    files_root = backup_dir / "files"
    if not files_root.is_dir():
        files_root = backup_dir / "source"
    if not files_root.is_dir() or files_root.is_symlink():
        raise GuardError("备份中没有 files/ 或 source/ 目录")
    planned = _plan_restore(files_root, target, files)
    created: list[dict] = []
    try:
        for item in planned:
            if item["action"] != "create":
                continue
            _create_file(files_root / item["relpath"], target / item["relpath"], target, item.get("mode"))
            created.append(item)
    except Exception as exc:
        recovery = _undo_created(target, created)
        raise GuardError(f"restore 失败，已回撤新建文件={recovery}；未删除目录") from exc
    return {
        "phase": "restored",
        "created_files": len(created),
        "skipped_identical": sum(1 for item in planned if item["action"] == "skip_identical"),
        "created_root": created_root,
        "directories_removed": False,
    }


def _plan_restore(files_root: Path, target: Path, files: list) -> list[dict]:
    planned: list[dict] = []
    for item in files:
        if not isinstance(item, dict):
            raise GuardError("备份文件项非法")
        rel = item.get("relpath")
        digest = item.get("sha256")
        if not isinstance(rel, str) or not isinstance(digest, str):
            raise GuardError("备份文件项缺少 relpath 或 sha256")
        if rel.startswith("/") or ".." in Path(rel).parts or rel == "":
            raise GuardError("备份相对路径非法")
        src = files_root / rel
        dst = target / rel
        if not _is_within(files_root, src) or not _is_within(target, dst):
            raise GuardError("备份路径逃出根目录")
        if not src.is_file() or src.is_symlink():
            raise GuardError("备份缺少真实文件")
        if _sha256(src) != digest:
            raise GuardError("备份哈希不一致")
        if dst.exists() or dst.is_symlink():
            if dst.is_symlink() or not dst.is_file():
                raise GuardError("目标占用且不是可跳过的文件")
            if _sha256(dst) != digest:
                raise GuardError("拒绝覆盖已有不同内容")
            planned.append({"relpath": rel, "action": "skip_identical", "sha256": digest})
            continue
        planned.append({
            "relpath": rel,
            "action": "create",
            "sha256": digest,
            "mode": item.get("mode"),
        })
    return planned


def _copy_selected(source: Path, dest_root: Path, resources: list[dict]) -> list[dict]:
    copied: list[dict] = []
    for item in resources:
        if not _copyable(item):
            continue
        rel = item["relpath"]
        src = source / rel
        dst = dest_root / rel
        if not _is_within(source, src) or not _is_within(dest_root, dst):
            raise GuardError("复制路径逃出根目录")
        if src.is_symlink() or not src.is_file():
            raise GuardError("拒绝复制非普通文件")
        _create_file(src, dst, dest_root, item.get("mode"))
        if _sha256(dst) != item["sha256"]:
            raise GuardError("复制后哈希不一致")
        copied.append({
            "relpath": rel,
            "sha256": item["sha256"],
            "bytes": item["bytes"],
            "mode": item["mode"],
            "redacted": bool(item["redacted"]),
        })
    return copied


def _create_file(src: Path, dst: Path, dest_root: Path, mode: object) -> None:
    if dst.exists() or dst.is_symlink():
        raise GuardError("拒绝覆盖已存在路径")
    _ensure_dir(dest_root, dst.parent)
    partial = dst.with_name(f".clawd-partial-{dst.name}-{os.getpid()}")
    if partial.exists() or partial.is_symlink():
        raise GuardError("临时文件已存在，拒绝覆盖")
    try:
        with src.open("rb") as incoming, partial.open("xb") as outgoing:
            while True:
                chunk = incoming.read(1024 * 1024)
                if not chunk:
                    break
                outgoing.write(chunk)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        if isinstance(mode, str) and len(mode) == 4 and all(ch in "01234567" for ch in mode):
            os.chmod(partial, int(mode, 8) & 0o777)
        os.replace(partial, dst)
    except Exception:
        discard_partial(partial)
        raise


def _ensure_dir(dest_root: Path, directory: Path) -> None:
    if directory == dest_root:
        return
    if not _is_within(dest_root, directory):
        raise GuardError("拒绝在复制根之外创建目录")
    if directory.exists() or directory.is_symlink():
        if directory.is_symlink() or not directory.is_dir():
            raise GuardError("复制路径被非目录占用")
        return
    _ensure_dir(dest_root, directory.parent)
    try:
        directory.mkdir(mode=0o700, parents=False)
    except FileExistsError as exc:
        raise GuardError("目录已存在，拒绝覆盖") from exc


def _undo_created(target: Path, created: list[dict]) -> str:
    for item in reversed(created):
        path = target / item["relpath"]
        if path.is_symlink() or path.is_dir() or not path.is_file():
            return "needs_manual_recovery"
        if _sha256(path) != item["sha256"]:
            return "needs_manual_recovery"
        path.unlink()
    return "removed_created_files"


def _recover_apply(journal: dict, target: Path, previous: Path) -> str:
    steps = journal.get("steps") or []
    staging = journal.get("staging")
    moved_away = any(step.get("dst") == str(previous) for step in steps)
    swapped = any(step.get("dst") == str(target) and step.get("src") == staging for step in steps)
    if swapped:
        return "needs_manual_recovery"
    if not moved_away:
        return "unchanged"
    if target.exists() or target.is_symlink():
        return "needs_manual_recovery"
    if previous.is_dir() and not previous.is_symlink():
        _rename_onto_absent(previous, target)
        return "restored_previous"
    return "needs_manual_recovery"


def _swap_visible(journal: dict, target: Path) -> bool:
    staging = journal.get("staging")
    if not staging or Path(staging).exists():
        return False
    if not target.is_dir() or target.is_symlink():
        return False
    if journal.get("had_target"):
        previous = journal.get("previous")
        if not isinstance(previous, str):
            return False
        path = Path(previous)
        return path.is_dir() and not path.is_symlink()
    return True


def _rename_onto_absent(src: Path, dst: Path) -> None:
    if dst.exists() or dst.is_symlink():
        raise GuardError("拒绝替换已存在路径")
    try:
        os.rename(src, dst)
    except OSError as exc:
        if exc.errno == errno.EXDEV:
            raise GuardError("跨文件系统重命名被拒绝，不使用复制后删除") from exc
        raise


def _write_failure_journal(backup_dir: Path, journal: dict) -> None:
    path = backup_dir / f"journal-failed-{journal.get('id', 'unknown')}.json"
    try:
        write_raw_json(path, journal)
    except GuardError:
        return


def _raise_if_incomplete(summary: dict, resources: list[dict]) -> None:
    if summary["symlinks"] or summary["special"] or summary["too_large"] or summary["crossed_filesystem"]:
        raise GuardError("树含符号链接、特殊文件、超限文件或跨文件系统目录，拒绝继续")
    for item in resources:
        reason = item.get("skip_reason") or ""
        if item.get("kind") == "unreadable" or str(reason).startswith("stat_failed"):
            raise GuardError("树含不可读项，拒绝继续")


def _copyable(item: dict) -> bool:
    return item.get("kind") == "file" and not item.get("skipped") and bool(item.get("sha256"))


def _is_within(root: Path, path: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
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


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        try:
            os.fsync(fd)
        except OSError as exc:
            if exc.errno not in {errno.EINVAL, errno.ENOTSUP}:
                raise
    finally:
        os.close(fd)


def _creds(policy: Policy) -> bool:
    return bool(policy.copy_credentials and policy.read_secret_bytes)


def _refuse(path: Path, kind: str) -> None:
    assert_not_banned(str(path), kind=kind)
