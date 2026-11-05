"""Record the real folder-migration contract. Do not import or call it.

The implementation lives in clawd_projects/migration.py. This module only
checks that file as text. It does not open a database or guess a path.
"""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
EXPECTED_REL = "clawd_projects/migration.py"
EXPECTED_FUNCTIONS = (
    "preview_folder_migration",
    "plan_folder_migration",
    "execute_folder_migration",
    "rollback_folder_migration",
)
RECEIPT_CONTRACT = {
    "called_by_cutover": False,
    "execute_success": "success is true and status is applied",
    "rollback_success": "success is true and status is rolled_back",
    "preview_or_plan_success": "read completed only; applied is false; not a migration success",
    "partial": "status partial and success false",
    "confirm": "execute and rollback require confirm is true",
    "home": "explicit isolated CLAWD_HOME or its profiles child; production homes refused",
    "mappings": "explicit folder_id plus primary_path; no guessed paths",
    "source_write": "panel_folders.db is not rewritten by migration",
}


def interface_note() -> dict:
    return {
        "owner": "project-agent",
        "implemented_here": False,
        "module": EXPECTED_REL,
        "functions": list(EXPECTED_FUNCTIONS),
        "absent_module": "clawd_projects/folder_migration.py",
        "contract": "docs/clawd/project-migration-implementation.md",
        "receipt": dict(RECEIPT_CONTRACT),
        "rules": [
            "无路径的旧文件夹不得猜测绑定",
            "仅显式 folder_id + primary_path 后才可升级",
            "保留会话 ID",
            "归档或解绑不得删除会话、记忆或目录",
            "home 必须是显式隔离 CLAWD_HOME",
            "预览或核对的 success 不是迁移成功",
            "本工具不调用 migration.py",
        ],
    }


def migration_status(choice: str | None) -> dict:
    base = interface_note()
    base["choice"] = choice
    if choice is None:
        base["status"] = "unset"
        base["blocks_apply"] = True
        return base
    if choice == "no":
        base["status"] = "explicitly_skipped"
        base["blocks_apply"] = False
        base["note"] = "这次目录切换明确不含旧文件夹迁移；未读生产路径"
        return base
    if choice != "yes":
        base["status"] = "invalid"
        base["blocks_apply"] = True
        return base
    path = REPO_ROOT / EXPECTED_REL
    if not path.is_file():
        base["status"] = "pending_project_agent"
        base["blocks_apply"] = True
        return base
    text = path.read_text(encoding="utf-8", errors="replace")
    missing = [name for name in EXPECTED_FUNCTIONS if f"def {name}" not in text]
    base["missing_functions"] = missing
    if missing:
        base["status"] = "pending_project_agent"
        base["blocks_apply"] = True
        return base
    base["status"] = "interface_present_not_called"
    base["blocks_apply"] = True
    base["note"] = "真实模块已出现，本工具仍不导入、不调用；apply 继续阻塞"
    return base
