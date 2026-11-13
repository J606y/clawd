"""Clawd-native kanban board reads for the panel.

Installed by clawd_server.admin_native onto the panel_core module, the same
way clawd_server.update_native is: list_kanban_tasks / get_kanban_task
become clawd-native implementations. The panel frontend reads them through
the panel-core routes GET /kanban/tasks and GET /kanban/tasks/<id>.

Read-only: the kanban DB is opened read-only (SQLite URI mode=ro), so a
panel read can never create, migrate, or write the board. A missing DB, a
DB without the kanban schema, a bad status filter, or an unknown task id
are all explicit ApiErrors, never a 500.
"""

from __future__ import annotations

import sqlite3
import urllib.parse
from pathlib import Path
from typing import Any

from hermes_cli import kanban_db


def _lossy_text(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value


def _db_path() -> Path:
    """Default-board kanban.db. Respects the HERMES_KANBAN_DB pin and the
    current-board pointer, exactly like the kanban CLI."""
    return kanban_db.kanban_db_path()


def _connect_ro(mod: Any, path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise mod.ApiError(
            503, "kanban_unavailable", "看板数据库不存在，请确认看板已初始化"
        )
    uri = "file:" + urllib.parse.quote(str(path)) + "?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        raise mod.ApiError(
            503, "kanban_unavailable", f"看板数据库打不开：{exc}"
        ) from exc
    conn.row_factory = sqlite3.Row
    conn.text_factory = _lossy_text
    try:
        has_tasks = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'tasks'"
        ).fetchone()
    except sqlite3.Error as exc:
        conn.close()
        raise mod.ApiError(
            503, "kanban_unavailable", f"看板数据库读不到：{exc}"
        ) from exc
    if not has_tasks:
        conn.close()
        raise mod.ApiError(
            503, "kanban_unavailable", "看板数据库里没有任务表，请确认看板已初始化"
        )
    return conn


def _task_summary(task: kanban_db.Task) -> dict[str, Any]:
    return {
        "id": task.id,
        "title": task.title,
        "status": task.status,
        "assignee": task.assignee,
        "priority": task.priority,
        "created_at": task.created_at,
        "started_at": task.started_at,
        "completed_at": task.completed_at,
        "workspace": task.workspace_path,
        "session_id": task.session_id,
    }


def list_kanban_tasks(mod: Any, status: str | None = None) -> dict[str, Any]:
    """All non-archived tasks, optionally filtered by one status."""
    if status is not None:
        status = str(status).strip()
        if not status:
            status = None
    if status is not None and status not in kanban_db.VALID_STATUSES:
        raise mod.ApiError(
            400,
            "invalid_status",
            "status 须是 " + ", ".join(sorted(kanban_db.VALID_STATUSES)) + " 之一",
        )
    try:
        path = _db_path()
    except ValueError as exc:
        raise mod.ApiError(
            503, "kanban_unavailable", f"看板位置解析失败：{exc}"
        ) from exc
    conn = _connect_ro(mod, path)
    try:
        try:
            tasks = kanban_db.list_tasks(conn, status=status)
        except ValueError as exc:
            raise mod.ApiError(400, "invalid_status", str(exc)) from exc
        except sqlite3.Error as exc:
            raise mod.ApiError(503, "kanban_unavailable", f"看板读不到：{exc}") from exc
    finally:
        conn.close()
    return {
        "tasks": [_task_summary(t) for t in tasks],
        "statuses": sorted(kanban_db.VALID_STATUSES),
    }


def get_kanban_task(mod: Any, task_id: str) -> dict[str, Any]:
    """One task: body, comments, events, and the linked session id."""
    if (
        not isinstance(task_id, str)
        or not task_id
        or "/" in task_id
        or task_id in (".", "..")
    ):
        raise mod.ApiError(400, "invalid_task_id", "task_id 不合法")
    try:
        path = _db_path()
    except ValueError as exc:
        raise mod.ApiError(
            503, "kanban_unavailable", f"看板位置解析失败：{exc}"
        ) from exc
    conn = _connect_ro(mod, path)
    try:
        try:
            task = kanban_db.get_task(conn, task_id)
        except sqlite3.Error as exc:
            raise mod.ApiError(503, "kanban_unavailable", f"看板读不到：{exc}") from exc
        if task is None:
            raise mod.ApiError(
                404, "kanban_task_not_found", f"看板任务不存在：{task_id}"
            )
        try:
            comments = kanban_db.list_comments(conn, task_id)
            events = kanban_db.list_events(conn, task_id)
        except sqlite3.Error as exc:
            raise mod.ApiError(503, "kanban_unavailable", f"看板读不到：{exc}") from exc
    finally:
        conn.close()
    detail = _task_summary(task)
    detail["body"] = task.body
    detail["comments"] = [
        {"id": c.id, "author": c.author, "body": c.body, "created_at": c.created_at}
        for c in comments
    ]
    detail["events"] = [
        {
            "id": e.id,
            "kind": e.kind,
            "payload": e.payload,
            "created_at": e.created_at,
            "run_id": e.run_id,
        }
        for e in events
    ]
    return detail


def install_kanban(mod: Any) -> None:
    """Bind clawd-native kanban functions onto the panel_core module."""
    mod.list_kanban_tasks = lambda status=None: list_kanban_tasks(mod, status)
    mod.get_kanban_task = lambda task_id: get_kanban_task(mod, task_id)
    mod._clawd_kanban_native = True
