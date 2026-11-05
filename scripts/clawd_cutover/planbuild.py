"""Build a redacted plan manifest. This module does not mutate trees."""

from __future__ import annotations

from pathlib import Path

from acceptance_gate import evaluate_report

from guard import (
    GuardError,
    assert_profile,
    assert_under,
    checked_allow_roots,
    checked_path,
    confirm_has,
    distinct_trees,
    normalize_lexical,
)
from manifest import blank_manifest
from migration_iface import migration_status
from treewalk import Policy, empty_summary, iter_tree
from versions import read_versions


MUTATING = frozenset({"backup", "restore", "apply", "rollback", "render-templates"})
CONFIRM_FOR = {
    "backup": "backup",
    "restore": "restore",
    "apply": "apply",
    "rollback": "rollback",
    "render-templates": "render",
}


def gate_catalog() -> list[dict]:
    return [
        {"id": "explicit_paths", "detail": "source/target/backup/journal 均须显式绝对路径，无默认值"},
        {"id": "paths_checked", "detail": "路径在 allow-root 内，且不是生产 home、/etc、系统安装或原面板检出"},
        {"id": "distinct_trees", "detail": "source、target、backup 不得相同或嵌套"},
        {"id": "target_parent", "detail": "target 父目录必须已在 allow-root 内且已存在"},
        {"id": "migration_choice", "detail": "--migration-allowed 必须显式 yes 或 no；yes 只记录 clawd_projects/migration.py，不调用"},
        {"id": "credentials_policy", "detail": "默认不复制凭据文件，不把秘密写入报告"},
        {"id": "profile_policy", "detail": "不猜 profile；未给 --profile 则不进入 profiles 子树，也不记录兄弟名"},
        {"id": "version_compatible", "detail": "版本缺失或不一致则拒绝有副作用的 apply，除非显式放行"},
        {"id": "acceptance_passed", "detail": "apply 只接受完整验收报告：schema、退出结果、时间、源代码指纹和构建产物必须重核；短 pass JSON 不行"},
        {"id": "confirm_token", "detail": "变更命令需要精确确认词；缺少时保持计划模式"},
        {"id": "backup_slot", "detail": "执行 backup/apply 时 backup-dir 必须尚不存在"},
        {"id": "tree_complete", "detail": "source 不得含符号链接、特殊文件、超限文件或不可读目录"},
        {"id": "source_nonempty", "detail": "apply 的 source 在当前策略下必须有可复制内容"},
        {"id": "systemd_nginx_not_applied", "detail": "单元和站点文件只是未应用模板"},
        {"id": "folder_migration_not_invoked", "detail": "本工具不调用 clawd_projects/migration.py"},
    ]


def build_manifest(opts, mode: str) -> dict:
    data = blank_manifest(mode)
    roots = checked_allow_roots(list(opts.allow_roots or []))
    data["allow_roots"] = roots
    paths = _collect_paths(opts, roots, mode)
    data["source"] = _endpoint(paths.get("source"))
    data["target"] = _endpoint(paths.get("target"))
    migration = migration_status(getattr(opts, "migration_allowed", None))
    data["migration"] = migration
    profile = assert_profile(getattr(opts, "profile", None))
    data["profiles"] = {
        "selection": profile or "explicit_none",
        "guessed": False,
        "sibling_names_recorded": False,
    }
    data["secrets"] = {
        "copied": False,
        "values_written": False,
        "auto_copy": False,
        "copy_requested": bool(getattr(opts, "copy_credentials", False)),
        "confirm_present": confirm_has(list(opts.confirm or []), "copy_credentials"),
    }
    source_path = paths.get("source")
    target_path = paths.get("target")
    policy = _plan_policy(opts, profile)
    source_resources, source_summary = _walk(source_path, policy)
    target_resources, target_summary = _walk(target_path, policy)
    data["resources"] = _stamp(source_resources, "source") + _stamp(target_resources, "target")
    data["resource_summary"] = {"source": source_summary, "target": target_summary}
    data["versions"] = read_versions(
        source_path,
        target_path,
        source_declared=getattr(opts, "source_version", None),
        target_declared=getattr(opts, "target_version", None),
        allow_skew=bool(getattr(opts, "allow_version_skew", False)),
        allow_unknown=bool(getattr(opts, "allow_version_unknown", False)),
    )
    data["acceptance"] = evaluate_report(paths.get("acceptance"), mode=mode)
    data["backup"] = {
        "prepared": False,
        "path": str(paths["backup"]) if paths.get("backup") else None,
        "absent": _absent(paths.get("backup")),
    }
    data["gates"] = _gates(opts, mode, paths, data, source_summary)
    data["actions"] = _actions(mode, data)
    data["result"] = {"ok": False, "exit_class": "plan"}
    return data


def blocked_ids(data: dict) -> list[str]:
    return [item["id"] for item in data["gates"] if item.get("blocks_apply")]


def confirmed(opts, mode: str) -> bool:
    name = CONFIRM_FOR.get(mode)
    if name is None:
        return False
    if not confirm_has(list(opts.confirm or []), name):
        return False
    if getattr(opts, "copy_credentials", False) and mode in {"backup", "apply"}:
        return confirm_has(list(opts.confirm or []), "copy_credentials")
    return True


def execution_policy(opts) -> Policy:
    profile = assert_profile(getattr(opts, "profile", None))
    copy = bool(getattr(opts, "copy_credentials", False)) and confirm_has(
        list(opts.confirm or []), "copy_credentials"
    )
    return Policy(
        include_skipped=bool(getattr(opts, "include_skipped", False)),
        profile=profile,
        copy_credentials=copy,
        read_secret_bytes=copy,
        max_files=int(getattr(opts, "max_files", 100000)),
        max_file_bytes=int(getattr(opts, "max_file_bytes", 32 * 1024 * 1024)),
    )


def _plan_policy(opts, profile: str | None) -> Policy:
    return Policy(
        include_skipped=bool(getattr(opts, "include_skipped", False)),
        profile=profile,
        copy_credentials=False,
        read_secret_bytes=False,
        max_files=int(getattr(opts, "max_files", 100000)),
        max_file_bytes=int(getattr(opts, "max_file_bytes", 32 * 1024 * 1024)),
    )


def _collect_paths(opts, roots: list[str], mode: str) -> dict[str, Path]:
    required = {
        "inventory": ("source", "target"),
        "plan": ("source", "target"),
        "backup": ("source", "backup"),
        "restore": ("backup", "target"),
        "apply": ("source", "target", "backup"),
        "rollback": ("target", "journal"),
        "render-templates": ("out_dir",),
    }.get(mode, ())
    raw_map = {
        "source": getattr(opts, "source", None),
        "target": getattr(opts, "target", None),
        "backup": getattr(opts, "backup_dir", None),
        "previous": getattr(opts, "previous", None),
        "journal": getattr(opts, "journal", None),
        "out_dir": getattr(opts, "out_dir", None),
        "acceptance": getattr(opts, "acceptance_report", None),
        "out": getattr(opts, "out", None),
    }
    for name in required:
        if not raw_map.get(name):
            raise GuardError(f"缺少显式路径 --{name.replace('_', '-')}")
    found: dict[str, Path] = {}
    for name, raw in raw_map.items():
        if not raw:
            continue
        found[name] = checked_path(raw, roots, kind=name)
    distinct_trees(*[(name, str(path)) for name, path in found.items()])
    if mode in {"apply", "restore", "rollback"} and "target" in found:
        parent = normalize_lexical(str(found["target"].parent))
        assert_under(parent, roots, kind="target-parent")
    if mode == "apply" and found["target"].parent.is_symlink():
        raise GuardError("target 父目录不能是符号链接")
    return found


def _endpoint(path: Path | None) -> dict:
    if path is None:
        return {"path": None, "missing": None}
    return {"path": str(path), "missing": not path.exists()}


def _walk(path: Path | None, policy: Policy) -> tuple[list[dict], dict]:
    if path is None:
        return [], empty_summary()
    if not path.exists():
        summary = empty_summary()
        summary["missing"] = True
        return [], summary
    return iter_tree(path, policy)


def _stamp(resources: list[dict], role: str) -> list[dict]:
    stamped = []
    for item in resources:
        row = dict(item)
        row["role"] = role
        stamped.append(row)
    return stamped


def _gates(opts, mode: str, paths: dict[str, Path], data: dict, source_summary: dict) -> list[dict]:
    copy_ok = (not data["secrets"]["copy_requested"]) or data["secrets"]["confirm_present"]
    backup_path = paths.get("backup")
    backup_ok = True
    if mode in {"backup", "apply"}:
        backup_ok = (
            backup_path is not None
            and _absent(backup_path)
            and backup_path.parent.is_dir()
            and not backup_path.parent.is_symlink()
        )
    tree_ok = not (
        source_summary.get("symlinks")
        or source_summary.get("special")
        or source_summary.get("too_large")
        or source_summary.get("crossed_filesystem")
    )
    nonempty = source_summary.get("files", 0) > 0 or (
        data["secrets"]["copy_requested"] and source_summary.get("redacted", 0) > 0
    )
    parent_ok = True
    if mode in {"apply", "restore", "rollback"}:
        parent_ok = "target" in paths and paths["target"].parent.is_dir()
    rows = [
        _gate("explicit_paths", True, blocking=True, detail="required paths present"),
        _gate("paths_checked", True, blocking=True, detail="lexical and resolved checks passed"),
        _gate("distinct_trees", True, blocking=True, detail="trees do not overlap"),
        _gate("target_parent", parent_ok, blocking=mode in {"apply", "restore", "rollback"}, detail="parent directory"),
        _gate(
            "migration_choice",
            data["migration"].get("choice") in {"yes", "no"},
            blocking=mode in {"plan", "apply"},
            detail=str(data["migration"].get("status")),
        ),
        _gate("credentials_policy", copy_ok, blocking=True, detail="credentials stay out unless confirmed"),
        _gate("profile_policy", data["profiles"]["guessed"] is False, blocking=True, detail="profile not guessed"),
        _gate(
            "version_compatible",
            data["versions"].get("blocks_apply") is False,
            blocking=mode == "apply",
            detail=str(data["versions"].get("compatible")),
        ),
        _gate(
            "acceptance_passed",
            data["acceptance"].get("status") == "passed",
            blocking=mode == "apply",
            detail=str(data["acceptance"].get("status")),
        ),
        _gate("confirm_token", confirmed(opts, mode) if mode in MUTATING else True, blocking=mode in MUTATING, detail="exact confirm token"),
        _gate("backup_slot", backup_ok, blocking=mode in {"backup", "apply"}, detail="backup dir must be absent"),
        _gate("tree_complete", tree_ok and not source_summary.get("missing"), blocking=mode == "apply", detail="source walk"),
        _gate("source_nonempty", nonempty and not source_summary.get("missing"), blocking=mode == "apply", detail="copyable content"),
        _gate("systemd_nginx_not_applied", True, blocking=True, detail="templates only"),
        _gate(
            "folder_migration_not_invoked",
            data["migration"].get("blocks_apply") is False or data["migration"].get("choice") == "no",
            blocking=mode == "apply",
            detail="migration is not called",
        ),
    ]
    if mode == "apply" and data["migration"].get("choice") == "yes":
        for row in rows:
            if row["id"] == "folder_migration_not_invoked":
                row["ok"] = False
                row["blocks_apply"] = True
    return rows


def _gate(gid: str, ok: bool, *, blocking: bool, detail: str) -> dict:
    return {
        "id": gid,
        "ok": bool(ok),
        "blocks_apply": bool(blocking and not ok),
        "detail": detail,
    }


def _actions(mode: str, data: dict) -> list[str]:
    choice = data["migration"].get("choice")
    return [
        f"mode={mode}",
        "default is plan; missing confirm does not change target",
        "credential file contents are not written",
        "profiles are not guessed",
        f"migration choice={choice}; folder migration is not called",
        "systemd and nginx templates are not applied",
        "apply would backup, stage, then rename onto an absent name",
        "failure recovery renames the previous tree back and does not remove directories",
    ]


def _absent(path: Path | None) -> bool:
    if path is None:
        return False
    return not path.exists() and not path.is_symlink()
