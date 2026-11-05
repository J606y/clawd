"""Offline cutover preparation. Plan mode unless an exact confirm token is passed.

Usage:
    python3 scripts/clawd_cutover/cli.py gates
    python3 scripts/clawd_cutover/cli.py plan --allow-root DIR --source DIR --target DIR --migration-allowed no

There is no default data directory. Mutating commands do not change files
without an exact confirm token. This program does not install units or sites.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from guard import GuardError, assert_report_clean, checked_path  # noqa: E402
from manifest import validate_manifest, verification_block, write_new_json  # noqa: E402
from mutate import apply_tree, backup_tree, restore_tree, rollback_tree  # noqa: E402
from planbuild import (  # noqa: E402
    MUTATING,
    blocked_ids,
    build_manifest,
    confirmed,
    execution_policy,
    gate_catalog,
)
from templates_cmd import render_templates  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--allow-root", action="append", dest="allow_roots", help="显式隔离根，可重复。无默认值。")
    common.add_argument("--source", help="显式绝对路径。无默认值。")
    common.add_argument("--target", help="显式绝对路径。无默认值。")
    common.add_argument("--backup-dir", help="必须尚不存在的显式备份目录。")
    common.add_argument("--previous", help="已有目标被换下后的目录。首次安装回滚不要传。")
    common.add_argument("--journal", help="journal-swapped.json 的显式路径。")
    common.add_argument("--out", help="清单输出。不得已存在。省略则写到标准输出。")
    common.add_argument("--out-dir", help="模板输出目录。不得是系统配置目录。")
    common.add_argument("--migration-allowed", choices=("yes", "no"), help="必须显式选择。yes 也不调用旧文件夹迁移。")
    common.add_argument("--profile", help="显式 profile 名。省略表示不处理 profile，不是默认 profile。")
    common.add_argument("--copy-credentials", action="store_true", help="还须确认词 COPY_CREDENTIALS。默认不复制凭据。")
    common.add_argument("--confirm", action="append", dest="confirm", help="精确确认词。可重复。")
    common.add_argument("--acceptance-report", help="最终验收 JSON。本工具不生成通过结果。")
    common.add_argument("--allow-version-skew", action="store_true")
    common.add_argument("--allow-version-unknown", action="store_true")
    common.add_argument("--include-skipped", action="store_true", help="连同默认跳过的重目录一起纳入。")
    common.add_argument("--source-version")
    common.add_argument("--target-version")
    common.add_argument("--max-files", type=int, default=100000)
    common.add_argument("--max-file-bytes", type=int, default=32 * 1024 * 1024)
    parser = argparse.ArgumentParser(
        prog="clawd-cutover",
        description="离线准备 clawd 切换与回滚。默认只出计划，不改目标。",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("gates", help="打印确认闸门，不读业务目录", parents=[common])
    sub.add_parser("inventory", help="只读清单", parents=[common])
    sub.add_parser("plan", help="只读计划", parents=[common])
    sub.add_parser("backup", help="复制备份。缺少 BACKUP 时只出计划", parents=[common])
    sub.add_parser("restore", help="向空目录或相同哈希恢复。缺少 RESTORE 时只出计划", parents=[common])
    sub.add_parser("apply", help="隔离切换。缺少 APPLY 时只出计划", parents=[common])
    sub.add_parser("rollback", help="目录改名回滚。缺少 ROLLBACK 时只出计划", parents=[common])
    sub.add_parser("render-templates", help="复制未应用模板。缺少 RENDER_TEMPLATES 时只出计划", parents=[common])
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    opts = parser.parse_args(argv)
    try:
        return dispatch(opts)
    except GuardError as exc:
        print(str(exc), file=sys.stderr)
        return exc.exit_code
    except Exception:
        print("未预期失败，已停止；未删除目录", file=sys.stderr)
        return 1


def dispatch(opts) -> int:
    if opts.max_files < 1 or opts.max_file_bytes < 1:
        raise GuardError("max-files 与 max-file-bytes 必须为正数")
    opts.allow_roots = list(opts.allow_roots or [])
    opts.confirm = list(opts.confirm or [])
    if opts.cmd == "gates":
        for row in gate_catalog():
            print(f"{row['id']}\t{row['detail']}")
        return 0
    data = build_manifest(opts, opts.cmd)
    roots = list(data["allow_roots"])
    _ensure_out_absent(opts, roots)
    if opts.cmd == "plan" and data["migration"].get("choice") not in {"yes", "no"}:
        _mark(data, executed=False, exit_class="blocked")
        _emit(data, opts, roots)
        print("blocked: migration_choice", file=sys.stderr)
        return 2
    if opts.cmd in MUTATING and not confirmed(opts, opts.cmd):
        _mark(data, executed=False, exit_class="plan")
        _emit(data, opts, roots)
        print("plan-only: no files changed", file=sys.stderr)
        return 0
    blocked = blocked_ids(data)
    if opts.cmd in MUTATING and blocked:
        _mark(data, executed=False, exit_class="blocked")
        _emit(data, opts, roots)
        print("blocked: " + ",".join(blocked), file=sys.stderr)
        return 2
    if opts.cmd in {"inventory", "plan"}:
        _mark(data, executed=False, exit_class="plan")
        _emit(data, opts, roots)
        print("plan-only: no files changed", file=sys.stderr)
        return 0
    try:
        outcome = _execute(opts, roots)
    except GuardError as exc:
        raise GuardError(str(exc), exit_code=1) from exc
    _mark(data, executed=True, exit_class="executed", outcome=outcome)
    _emit(data, opts, roots)
    print("executed; tool tests remain not_run", file=sys.stderr)
    return 0


def _ensure_out_absent(opts, roots: list[str]) -> None:
    if not opts.out:
        return
    path = checked_path(opts.out, roots, kind="out")
    if path.exists() or path.is_symlink():
        raise GuardError("拒绝覆盖已存在的输出")


def _execute(opts, roots: list[str]) -> dict:
    policy = execution_policy(opts)
    if opts.cmd == "backup":
        return backup_tree(
            checked_path(opts.source, roots, kind="source"),
            checked_path(opts.backup_dir, roots, kind="backup"),
            policy,
        )
    if opts.cmd == "restore":
        return restore_tree(
            checked_path(opts.backup_dir, roots, kind="backup"),
            checked_path(opts.target, roots, kind="target"),
        )
    if opts.cmd == "apply":
        return apply_tree(
            checked_path(opts.source, roots, kind="source"),
            checked_path(opts.target, roots, kind="target"),
            checked_path(opts.backup_dir, roots, kind="backup"),
            policy,
        )
    if opts.cmd == "rollback":
        previous = None
        if opts.previous:
            previous = checked_path(opts.previous, roots, kind="previous")
        return rollback_tree(
            checked_path(opts.target, roots, kind="target"),
            previous,
            checked_path(opts.journal, roots, kind="journal"),
        )
    if opts.cmd == "render-templates":
        status = render_templates(checked_path(opts.out_dir, roots, kind="out_dir"))
        return {
            "phase": "templates_rendered",
            "applied": False,
            "directories_removed": False,
            "template_status": status,
        }
    raise GuardError(f"未知命令: {opts.cmd}")


def _mark(data: dict, *, executed: bool, exit_class: str, outcome: dict | None = None) -> None:
    data["executed"] = executed
    data["dry_run"] = not executed
    data["verification"] = verification_block()
    data["systemd_nginx"]["applied"] = False
    data["systemd_nginx"]["systemctl_invoked"] = False
    data["systemd_nginx"]["nginx_invoked"] = False
    data["secrets"]["values_written"] = False
    data["profiles"]["guessed"] = False
    data["recovery"] = {
        "phase": (outcome or {}).get("phase", "not_started"),
        "directories_removed": False,
    }
    if outcome and outcome.get("backup"):
        data["backup"]["prepared"] = True
        data["backup"]["path"] = outcome["backup"]
    if outcome and outcome.get("prepared"):
        data["backup"]["prepared"] = True
        data["backup"]["path"] = outcome.get("path")
    data["result"] = {"ok": executed, "exit_class": exit_class}
    if outcome:
        data["result"]["phase"] = outcome.get("phase")
        data["result"]["id"] = outcome.get("id")
        data["result"]["file_count"] = outcome.get("file_count")
        data["result"]["previous"] = outcome.get("previous")
        data["result"]["failed_slot"] = outcome.get("failed_slot")
        data["secrets"]["copied"] = bool(outcome.get("credentials_copied"))


def _emit(data: dict, opts, roots: list[str]) -> None:
    validate_manifest(data)
    assert_report_clean(data)
    if opts.out:
        write_new_json(checked_path(opts.out, roots, kind="out"), data)
        return
    print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
