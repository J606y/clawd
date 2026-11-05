"""Manifest shape shared by the schema file and the emitter.

verification.tool_tests is locked to not_run. This tool has no flag that
marks its own tests passed.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from guard import GuardError, assert_report_clean, normalize_lexical, parent_exists


SCHEMA_VERSION = "1"
TOOL_NAME = "clawd-cutover-prepare"
REQUIRED_KEYS = (
    "schema_version",
    "tool",
    "mode",
    "executed",
    "dry_run",
    "created_at",
    "source",
    "target",
    "allow_roots",
    "resources",
    "resource_summary",
    "versions",
    "backup",
    "migration",
    "secrets",
    "profiles",
    "gates",
    "acceptance",
    "systemd_nginx",
    "verification",
    "actions",
    "recovery",
    "result",
)
MODES = frozenset({
    "inventory",
    "plan",
    "backup",
    "restore",
    "apply",
    "rollback",
    "render-templates",
    "gates",
})


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def verification_block() -> dict:
    return {
        "tool_tests": "not_run",
        "authoring_executed": False,
        "acceptance_assumed": False,
        "note": "交付时未运行本工具、未运行配套测试、未构建。本字段不能改写成通过。",
    }


def systemd_block() -> dict:
    return {
        "applied": False,
        "templates_only": True,
        "systemctl_invoked": False,
        "nginx_invoked": False,
        "note": "示例未应用。本工具不调用系统服务命令。",
    }


def blank_manifest(mode: str) -> dict:
    if mode not in MODES:
        raise GuardError(f"未知 mode: {mode}")
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "mode": mode,
        "executed": False,
        "dry_run": True,
        "created_at": utc_now(),
        "source": {"path": None, "missing": None},
        "target": {"path": None, "missing": None},
        "allow_roots": [],
        "resources": [],
        "resource_summary": {},
        "versions": {
            "schema": SCHEMA_VERSION,
            "policy": "equal_or_explicit_override",
            "compatible": "unknown",
            "blocks_apply": True,
        },
        "backup": {"prepared": False, "path": None},
        "migration": {},
        "secrets": {
            "copied": False,
            "values_written": False,
            "auto_copy": False,
        },
        "profiles": {
            "selection": "explicit_none",
            "guessed": False,
            "sibling_names_recorded": False,
        },
        "gates": [],
        "acceptance": {
            "required_for_apply": True,
            "status": "missing",
            "blocks_apply": True,
            "producer_seen": None,
            "note": "scripts/clawd_acceptance 由另一代理准备；未假定已验证。",
        },
        "systemd_nginx": systemd_block(),
        "verification": verification_block(),
        "actions": [],
        "recovery": {"phase": "not_started", "directories_removed": False},
        "result": {"ok": False, "exit_class": "plan"},
    }


def validate_manifest(data: object) -> None:
    if not isinstance(data, dict):
        raise GuardError("清单必须是对象")
    missing = [key for key in REQUIRED_KEYS if key not in data]
    if missing:
        raise GuardError("清单缺少字段: " + ",".join(missing))
    extra = [key for key in data if key not in REQUIRED_KEYS]
    if extra:
        raise GuardError("清单含未声明字段: " + ",".join(extra))
    if data["schema_version"] != SCHEMA_VERSION:
        raise GuardError("schema_version 必须为 1")
    if data["mode"] not in MODES:
        raise GuardError("mode 非法")
    verification = data["verification"]
    if not isinstance(verification, dict) or verification.get("tool_tests") != "not_run":
        raise GuardError("拒绝把工具测试标为已运行")
    systemd = data["systemd_nginx"]
    if not isinstance(systemd, dict) or systemd.get("applied") is not False:
        raise GuardError("拒绝把 systemd/nginx 标为已应用")
    if systemd.get("systemctl_invoked") is not False or systemd.get("nginx_invoked") is not False:
        raise GuardError("拒绝记录已调用系统服务命令")
    assert_report_clean(data)


def write_raw_json(path: Path, payload: dict) -> None:
    """Write a new JSON file. Refuse an existing path. Never removes a directory."""
    if not isinstance(payload, dict):
        raise GuardError("JSON 根必须是对象")
    assert_report_clean(payload)
    if path.exists() or path.is_symlink():
        raise GuardError(f"拒绝覆盖已存在的输出: {path}")
    parent_exists(path)
    tmp = path.with_name(f".clawd-partial-{path.name}-{os.getpid()}")
    if tmp.exists() or tmp.is_symlink():
        raise GuardError(f"拒绝覆盖已存在的临时输出: {tmp}")
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    try:
        with tmp.open("x", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except Exception:
        discard_partial(tmp)
        raise


def write_new_json(path: Path, payload: dict) -> None:
    validate_manifest(payload)
    write_raw_json(path, payload)


def discard_partial(path: Path) -> None:
    if not path.name.startswith(".clawd-partial-"):
        raise GuardError("拒绝删除非本工具临时文件")
    if path.is_symlink() or path.is_dir():
        raise GuardError("拒绝删除目录或符号链接")
    if path.is_file():
        path.unlink()


def ban_overlap(path: Path) -> bool:
    from guard import ban_reason

    return ban_reason(normalize_lexical(str(path))) is not None


def load_json_object(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise GuardError(f"JSON 必须是真实文件: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GuardError(f"无法读取 JSON: {path.name}") from exc
    if not isinstance(data, dict):
        raise GuardError("JSON 根必须是对象")
    return data
