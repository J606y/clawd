"""Explicit legacy-folder to directory-project migration.

Reads only the active isolated home's ``panel_folders.db``. Never guesses a
workspace path, never copies credentials or session transcripts, and never
deletes folder rows, directories, memories, or sessions.

projects.db, state.db, and panel_folders.db are not one transaction. A unique
create intent is persisted before ``create_project``. That API does not accept
an explicit project id. A project is adopted only when slug, description
marker, name, and path all match the persisted intent — never by path alone.
Binding recovery and rollback use a pre-generated ``write_token`` persisted
before the bind and read back from ``get_session_write_token``. ``bound_at``
is not that proof. Writes go through ``commit_session_binding`` so the core
idle check still runs; this module does not write ``project_sessions`` itself.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

from hermes_cli import projects_db as pdb
from hermes_constants import explicit_clawd_owned_home, get_hermes_home, resolve_clawd_home

from clawd_projects.service import (
    BindingTokenUnsupported,
    SessionBusyError,
    SessionStoreCheckError,
    commit_session_binding,
    is_session_busy,
    prepare_project_directory,
    read_session_write_token,
)

RECOVERY_TEXT = (
    "projects.db、state.db 与 panel_folders.db 不能跨库原子提交。"
    "现有 create_project 不接受显式 project id，创建前先把唯一意图（slug 与 description 标记）写入回执。"
    "创建后、记下 project_id 前中断时，只有 slug、标记、名称和目录全部吻合才证明是本次创建，不按路径认领，也不并入冲突项目。"
    "相同幂等键恢复不重复创建。"
    "逐条绑定前先把预先生成的 write_token 写入回执，再经 commit_session_binding 做核心空闲检查并绑定。"
    "确认恢复只认读回的 write_token 与本回执 token 一致，不认 bound_at，不按路径认领。"
    "条件回滚把期望 project_id 与 expected_write_token 交给同一次 commit_session_binding；"
    "DB 在一条 DELETE 里同时比较 session、project、write_token。token 缺失或被同秒重绑替换时不解绑。"
    "绑定已落地但回执没有预先持久的 token 时，不能证明是本次写入，不认领、不回滚、不覆盖。"
    "回滚不删除目录、项目、记忆、会话或旧文件夹，也不归档。归档不释放路径。"
    "同一目录重试必须用新幂等键，并显式 confirm_reuse=true 指向原回执；只复用该回执证明创建且仍为空、字段未改的项目。"
    "不复制凭据。计划阶段的空闲结果不能代替绑定当时的核心再检查。"
)

_LEDGER = "project_folder_migrations.db"
_LOCK = "project_folder_migrations.lock"
_LOCKS_GUARD = threading.Lock()
_THREAD_LOCKS: dict[str, threading.Lock] = {}
_MAX_KEY = 512
_INTENT_PREFIX = "clawd-migration-intent:"
# Verified against hermes_cli.projects_db.create_project: no project_id parameter.
_EXPLICIT_PROJECT_ID_SUPPORTED = False


class MigrationError(Exception):
    """Fail-closed migration refusal. success is never implied."""

    def __init__(
        self,
        code: str,
        message: str,
        status: int = 400,
        details: Optional[dict[str, Any]] = None,
        receipt: Optional[dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details
        self.receipt = receipt


class OwnershipConflict(ValueError):
    """Raised inside the state write callback. Must stay a ValueError so
    ``commit_session_binding`` re-raises it instead of relabeling it idle-check failure.
    """

    def __init__(
        self,
        session_id: str,
        expected: Optional[str],
        current: Optional[str],
        expected_write_token: Optional[str] = None,
        current_write_token: Optional[str] = None,
        expected_bound_at: Optional[int] = None,
        current_bound_at: Optional[int] = None,
    ):
        super().__init__("ownership_conflict")
        self.session_id = session_id
        self.expected = expected
        self.current = current
        self.expected_write_token = expected_write_token
        self.current_write_token = current_write_token
        self.expected_bound_at = expected_bound_at
        self.current_bound_at = current_bound_at


def _now() -> int:
    return int(time.time())


def _within(child: Path, parent: Path) -> bool:
    try:
        c = child.resolve()
        p = parent.resolve()
    except OSError:
        c, p = child, parent
    if c == p:
        return True
    try:
        c.relative_to(p)
    except ValueError:
        return False
    return True


def _production_hermes_roots() -> list[Path]:
    roots = [Path("/root/.hermes")]
    try:
        roots.append(Path.home() / ".hermes")
    except RuntimeError:
        pass
    return roots


def _exact(path: Path, root: Path) -> bool:
    try:
        return path.resolve() == root.resolve()
    except OSError:
        return path == root


def _blocked_workspace(path: Path, home: Path) -> bool:
    """Refuse data roots and credential/system locations, not every child of CLAWD_HOME."""
    if path == Path("/") or _exact(path, home):
        return True
    blocked = _production_hermes_roots() + [
        Path("/root/.ssh"),
        Path("/etc"),
        Path("/usr/local/lib/hermes-agent"),
    ]
    try:
        blocked.append(Path.home() / ".ssh")
    except RuntimeError:
        pass
    return any(_within(path, root) for root in blocked)


def _assert_scope(home: Path) -> Path:
    if not home.is_dir():
        raise MigrationError("scope_refused", "隔离目录不存在，未创建目录，也未读取其他数据库", 403)
    for root in _production_hermes_roots():
        if _within(home, root):
            raise MigrationError("scope_refused", "拒绝生产 Hermes 数据目录，未读取该库", 403)
    clawd = resolve_clawd_home()
    if not explicit_clawd_owned_home(str(home), clawd):
        raise MigrationError(
            "scope_refused",
            "迁移只限当前隔离 CLAWD_HOME 或其 profile，未读取其他数据库",
            403,
        )
    return home


def resolve_migration_home(hermes_home: Optional[Path] = None) -> Path:
    home = Path(hermes_home) if hermes_home is not None else get_hermes_home()
    return _assert_scope(home)


def _folders_db(home: Path) -> Path:
    raw = home / "panel_folders.db"
    if raw.is_symlink():
        try:
            target = raw.resolve()
        except OSError as exc:
            raise MigrationError("folders_unavailable", "旧文件夹库符号链接无法确认，已拒绝", 503) from exc
        if not _within(target, home):
            raise MigrationError("scope_refused", "panel_folders.db 指向隔离目录之外，已拒绝", 403)
        return target
    return raw


def _validate_path_syntax(raw: Any) -> str:
    text = str(raw or "").strip()
    if not text:
        raise MigrationError("missing_primary_path", "必须显式提供绝对目录，不会猜测路径", 400)
    if "\0" in text or text.startswith("~") or not os.path.isabs(text) or ".." in Path(text).parts:
        raise MigrationError("invalid_directory", "目录必须是绝对路径，不解析 ~ 或相对路径", 400)
    return os.path.normpath(text)


def _refuse_workspace(path: str, home: Path) -> None:
    lexical = Path(path)
    if _blocked_workspace(lexical, home):
        raise MigrationError("forbidden_path", "该目录不能作为迁移目标，未写入项目", 400)
    if lexical.exists():
        try:
            resolved = lexical.resolve()
        except OSError as exc:
            raise MigrationError("invalid_directory", "无法确认目录，已拒绝", 400) from exc
        if _blocked_workspace(resolved, home):
            raise MigrationError("forbidden_path", "目录解析后落在禁止位置，已拒绝", 400)
        if not lexical.is_dir():
            raise MigrationError("invalid_directory", "primary_path 必须是目录，不是文件", 400)


@contextmanager
def _migration_lock(home: Path) -> Iterator[None]:
    key = str(home.resolve())
    with _LOCKS_GUARD:
        thread_lock = _THREAD_LOCKS.setdefault(key, threading.Lock())
    with thread_lock:
        lock_path = home / _LOCK
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            os.chmod(lock_path, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)


def _read_folders(home: Path) -> tuple[Path, list[dict[str, Any]]]:
    path = _folders_db(home)
    if not path.exists():
        raise MigrationError(
            "folders_missing",
            "当前隔离目录没有 panel_folders.db，未猜测其他路径",
            404,
            details={"source_missing": True, "folders_db": str(home / "panel_folders.db")},
        )
    if not path.is_file():
        raise MigrationError("folders_unavailable", "panel_folders.db 不是文件，未当作空列表", 503)
    uri = path.resolve().as_uri() + "?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=3)
    except sqlite3.Error as exc:
        raise MigrationError("folders_unavailable", "旧文件夹库不可读，未当作空列表", 503) from exc
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if "folders" not in tables or "assignments" not in tables:
            raise MigrationError("folders_unavailable", "旧文件夹库缺少表，未当作没有文件夹", 503)
        folder_rows = conn.execute("SELECT id, name, collapsed FROM folders ORDER BY rowid").fetchall()
        assign_rows = conn.execute(
            "SELECT session_id, folder_id FROM assignments ORDER BY rowid"
        ).fetchall()
    except MigrationError:
        raise
    except sqlite3.Error as exc:
        raise MigrationError("folders_unavailable", "旧文件夹库读取失败，未当作空列表", 503) from exc
    finally:
        conn.close()
    by_folder: dict[str, list[str]] = {}
    for row in assign_rows:
        by_folder.setdefault(str(row["folder_id"]), []).append(str(row["session_id"]))
    folders = []
    for row in folder_rows:
        fid = str(row["id"] or "").strip()
        if not fid:
            continue
        name = str(row["name"] or "").strip() or fid
        folders.append({
            "folder_id": fid,
            "name": name,
            "collapsed": bool(row["collapsed"]),
            "session_ids": by_folder.get(fid, []),
        })
    return path, folders


def _ledger_path(home: Path) -> Path:
    return home / _LEDGER


def _ledger_connect(home: Path, *, create: bool) -> Optional[sqlite3.Connection]:
    path = _ledger_path(home)
    if not path.exists() and not create:
        return None
    conn = sqlite3.connect(path, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS migration_receipts (
            id TEXT PRIMARY KEY,
            idempotency_key TEXT NOT NULL UNIQUE,
            fingerprint TEXT NOT NULL,
            status TEXT NOT NULL,
            success INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            body_json TEXT NOT NULL
        )"""
    )
    os.chmod(path, 0o600)
    return conn


def _load_receipts(home: Path) -> list[dict[str, Any]]:
    conn = _ledger_connect(home, create=False)
    if conn is None:
        return []
    try:
        rows = conn.execute("SELECT body_json FROM migration_receipts").fetchall()
    finally:
        conn.close()
    out = []
    for row in rows:
        try:
            parsed = json.loads(row["body_json"])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            out.append(parsed)
    return out


def _load_by_key(home: Path, key: str) -> Optional[dict[str, Any]]:
    conn = _ledger_connect(home, create=False)
    if conn is None:
        return None
    try:
        row = conn.execute(
            "SELECT body_json FROM migration_receipts WHERE idempotency_key = ?",
            (key,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    parsed = json.loads(row["body_json"])
    if not isinstance(parsed, dict):
        raise MigrationError("ledger_corrupt", "迁移回执损坏，未继续写入", 503)
    return parsed


def _load_by_id(home: Path, migration_id: str) -> Optional[dict[str, Any]]:
    conn = _ledger_connect(home, create=False)
    if conn is None:
        return None
    try:
        row = conn.execute(
            "SELECT body_json FROM migration_receipts WHERE id = ?",
            (migration_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    parsed = json.loads(row["body_json"])
    if not isinstance(parsed, dict):
        raise MigrationError("ledger_corrupt", "迁移回执损坏，未继续写入", 503)
    return parsed


def _save_receipt(home: Path, receipt: dict[str, Any]) -> None:
    receipt["updated_at"] = _now()
    receipt["success"] = bool(receipt.get("success"))
    body = json.dumps(receipt, ensure_ascii=False, sort_keys=True)
    conn = _ledger_connect(home, create=True)
    if conn is None:
        raise MigrationError("ledger_failed", "无法写入迁移回执，未报告成功", 500)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """INSERT INTO migration_receipts
               (id, idempotency_key, fingerprint, status, success, updated_at, body_json)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 idempotency_key = excluded.idempotency_key,
                 fingerprint = excluded.fingerprint,
                 status = excluded.status,
                 success = excluded.success,
                 updated_at = excluded.updated_at,
                 body_json = excluded.body_json""",
            (
                receipt["migration_id"],
                receipt["idempotency_key"],
                receipt["fingerprint"],
                receipt["status"],
                1 if receipt["success"] else 0,
                receipt["updated_at"],
                body,
            ),
        )
        conn.commit()
    except sqlite3.Error as exc:
        raise MigrationError(
            "ledger_failed",
            "迁移回执写入失败，未报告成功",
            500,
            receipt=receipt,
        ) from exc
    finally:
        conn.close()


def _public(receipt: dict[str, Any], *, replay: bool = False) -> dict[str, Any]:
    out = json.loads(json.dumps(receipt))
    out["idempotent_replay"] = replay
    out["cross_db_atomic"] = False
    out["recovery"] = RECOVERY_TEXT
    out["guessed_paths"] = False
    out["source_write_attempted"] = False
    out["explicit_project_id_supported"] = _EXPLICIT_PROJECT_ID_SUPPORTED
    out["archive_frees_path"] = False
    out["credentials_migrated"] = False
    if out.get("success") is True and out.get("status") == "applied":
        out["http_status"] = 200
    elif out.get("success") is True and out.get("status") == "rolled_back":
        out["http_status"] = 200
    else:
        out["success"] = False
        out["http_status"] = int(out.get("http_status") or 409)
    return out


def _present(home: Path, receipt: dict[str, Any], *, replay: bool = False) -> dict[str, Any]:
    out = _public(receipt, replay=replay)
    out["reuse_eligible"] = _reuse_eligible(home, receipt)
    return out


def _fail_receipt(home: Path, receipt: dict[str, Any], status: str, message: str) -> dict[str, Any]:
    receipt["status"] = status
    receipt["success"] = False
    receipt["applied"] = False
    receipt["message"] = message
    receipt["http_status"] = 409
    _save_receipt(home, receipt)
    return _present(home, receipt)


def _current_project_id(home: Path, session_id: str) -> Optional[str]:
    db_path = home / "projects.db"
    if not db_path.exists():
        return None
    with pdb.connect_closing(db_path) as conn:
        pid = pdb.get_session_project_id(conn, session_id)
    return str(pid) if pid else None


def _session_binding(home: Path, session_id: str) -> Optional[pdb.SessionBinding]:
    db_path = home / "projects.db"
    if not db_path.exists():
        return None
    with pdb.connect_closing(db_path) as conn:
        return pdb.get_session_binding(conn, session_id)


def _observed_write_token(home: Path, session_id: str) -> Optional[str]:
    """Service read path. A missing reader is a hard failure, not an absent token."""
    try:
        return read_session_write_token(session_id, hermes_home=home)
    except BindingTokenUnsupported as exc:
        raise MigrationError(
            "write_token_unsupported",
            "projects db 没有 get_session_write_token，拒绝把 token 当成不存在",
            503,
        ) from exc


def _project_at_path(home: Path, path: str) -> Optional[pdb.Project]:
    db_path = home / "projects.db"
    if not db_path.exists():
        return None
    with pdb.connect_closing(db_path) as conn:
        return pdb.find_by_primary_path(conn, path, include_archived=True)


def _intent_description(migration_id: str, folder_id: str, token: str) -> str:
    return f"{_INTENT_PREFIX}{migration_id}:{folder_id}:{token}"


def _intent_of(folder: dict[str, Any]) -> Optional[dict[str, str]]:
    token = folder.get("intent_token")
    slug = folder.get("intended_slug")
    name = folder.get("name")
    path = folder.get("primary_path")
    migration_id = folder.get("intent_migration_id")
    folder_id = folder.get("folder_id")
    if (
        not isinstance(token, str) or not token
        or not isinstance(slug, str) or not slug
        or not isinstance(name, str) or not name
        or not isinstance(path, str) or not path
        or not isinstance(migration_id, str) or not migration_id
        or not isinstance(folder_id, str) or not folder_id
    ):
        return None
    return {
        "token": token,
        "slug": slug,
        "name": name,
        "path": path,
        "migration_id": migration_id,
        "folder_id": folder_id,
        "description": _intent_description(migration_id, folder_id, token),
    }


def _memory_dir_untouched(home: Path, project_id: str) -> bool:
    """Empty check only. Does not call project-memory helpers owned by another lane."""
    pid = str(project_id or "")
    if (
        not pid
        or pid in {".", ".."}
        or "/" in pid
        or "\\" in pid
        or "\0" in pid
        or ".." in pid
    ):
        return False
    mem = (home / "projects" / pid / "memory")
    if not mem.exists():
        return True
    if not mem.is_dir():
        return False
    try:
        return not any(mem.iterdir())
    except OSError:
        return False


def _prove_created_project(home: Path, folder: dict[str, Any]) -> Optional[pdb.Project]:
    """Prove a project belongs to this receipt. Path match alone is not proof."""
    intent = _intent_of(folder)
    if intent is None:
        return None
    db_path = home / "projects.db"
    if not db_path.exists():
        return None
    with pdb.connect_closing(db_path) as conn:
        by_slug = pdb.get_project(conn, intent["slug"])
        by_path = pdb.find_by_primary_path(conn, intent["path"], include_archived=True)
    if by_slug is None or by_path is None or by_slug.id != by_path.id:
        return None
    if by_slug.archived or by_slug.slug != intent["slug"] or by_slug.name != intent["name"]:
        return None
    if (by_slug.description or "") != intent["description"]:
        return None
    recorded = folder.get("project_id")
    if recorded and by_slug.id != recorded:
        return None
    return by_slug


def _project_untouched_empty(home: Path, project: pdb.Project) -> bool:
    if project.archived or len(project.folders) != 1:
        return False
    db_path = home / "projects.db"
    with pdb.connect_closing(db_path) as conn:
        if pdb.list_project_sessions(conn, project.id):
            return False
    return _memory_dir_untouched(home, project.id)


def _reuse_eligible(home: Path, receipt: dict[str, Any]) -> bool:
    if receipt.get("status") != "rolled_back":
        return False
    folders = [folder for folder in receipt.get("folders") or [] if isinstance(folder, dict) and folder.get("project_id")]
    if not folders:
        return False
    try:
        for folder in folders:
            project = _prove_created_project(home, folder)
            if project is None or project.id != folder.get("project_id") or not _project_untouched_empty(home, project):
                return False
    except (sqlite3.Error, OSError, ValueError):
        return False
    return True


def _reuse_offer_for_folder(home: Path, folder_id: str) -> Optional[dict[str, Any]]:
    best = None
    best_at = -1
    for receipt in _load_receipts(home):
        if receipt.get("status") != "rolled_back":
            continue
        for folder in receipt.get("folders") or []:
            if not isinstance(folder, dict) or folder.get("folder_id") != folder_id:
                continue
            try:
                project = _prove_created_project(home, folder)
            except (sqlite3.Error, OSError, ValueError):
                continue
            if project is None or project.id != folder.get("project_id") or not _project_untouched_empty(home, project):
                continue
            updated = int(receipt.get("updated_at") or 0)
            if updated < best_at:
                continue
            best_at = updated
            best = {
                "migration_id": receipt.get("migration_id"),
                "recorded_primary_path": folder.get("primary_path"),
                "project_id": project.id,
                "requires_confirmation": True,
                "empty_proven": True,
                "archive_frees_path": False,
            }
    return best


def _occupant_conflict(folder: dict[str, Any], owner: pdb.Project) -> dict[str, Any]:
    intent = _intent_of(folder)
    message = "目录已属于其他项目，未并入"
    if intent is not None and (owner.description or "") == intent["description"] and owner.archived:
        message = "本次意图对应的项目已被归档。归档不释放路径。未自动恢复，也未并入其他项目。"
    elif intent is not None:
        message = "目录上的项目与已持久的创建意图不符，拒绝按路径认领"
    return {
        "code": "path_conflict",
        "folder_id": folder.get("folder_id"),
        "project_id": owner.id,
        "message": message,
    }


def _allocate_slug(home: Path) -> str:
    db_path = home / "projects.db"
    for _ in range(8):
        slug = "mig" + secrets.token_hex(8)
        if not db_path.exists():
            return slug
        with pdb.connect_closing(db_path) as conn:
            if pdb.get_project(conn, slug) is None:
                return slug
    raise MigrationError("intent_unavailable", "无法分配唯一创建意图，未创建项目", 503)


def _ensure_intent(home: Path, receipt: dict[str, Any], folder: dict[str, Any]) -> None:
    if _intent_of(folder) is not None:
        return
    folder["intent_token"] = secrets.token_hex(16)
    folder["intended_slug"] = _allocate_slug(home)
    folder["intent_migration_id"] = receipt["migration_id"]
    folder["intent_persisted_at"] = _now()
    _save_receipt(home, receipt)


def _applied_receipt_for_folder(home: Path, folder_id: str) -> Optional[dict[str, Any]]:
    found = None
    for receipt in _load_receipts(home):
        if receipt.get("status") != "applied" or receipt.get("success") is not True:
            continue
        for folder in receipt.get("folders") or []:
            if folder.get("folder_id") == folder_id and folder.get("project_verified"):
                found = receipt
    return found


def preview_folder_migration(hermes_home: Optional[Path] = None) -> dict[str, Any]:
    """Read-only inventory. Missing db is source_missing, not an empty success migration."""
    home = resolve_migration_home(hermes_home)
    try:
        path, folders = _read_folders(home)
    except MigrationError as exc:
        if exc.code == "folders_missing":
            return {
                "operation": "preview",
                "read_only": True,
                "applied": False,
                "success": True,
                "source_missing": True,
                "source_preserved": True,
                "source_write_attempted": False,
                "guessed_paths": False,
                "cross_db_atomic": False,
                "explicit_project_id_supported": False,
                "archive_frees_path": False,
                "home": str(home),
                "folders_db": str(home / "panel_folders.db"),
                "folders": [],
                "http_status": 200,
                "message": exc.message,
                "recovery": RECOVERY_TEXT,
            }
        raise
    listed = []
    for folder in folders:
        applied = _applied_receipt_for_folder(home, folder["folder_id"])
        recorded = None
        migration_id = None
        project_id = None
        if applied:
            migration_id = applied.get("migration_id")
            for item in applied.get("folders") or []:
                if item.get("folder_id") == folder["folder_id"]:
                    recorded = item.get("primary_path")
                    project_id = item.get("project_id")
        not_migrated = []
        if project_id:
            for sid in folder["session_ids"]:
                if _current_project_id(home, sid) != project_id:
                    not_migrated.append(sid)
        listed.append({
            "folder_id": folder["folder_id"],
            "name": folder["name"],
            "collapsed": folder["collapsed"],
            "session_ids": list(folder["session_ids"]),
            "primary_path": None,
            "recorded_primary_path": recorded,
            "path_source": "migration_receipt" if recorded else None,
            "status": "migrated" if applied else "needs_explicit_path",
            "migration_id": migration_id,
            "project_id": project_id,
            "not_migrated_session_ids": not_migrated,
            "reuse_offer": None if applied else _reuse_offer_for_folder(home, folder["folder_id"]),
            "guessed": False,
        })
    return {
        "operation": "preview",
        "read_only": True,
        "applied": False,
        "success": True,
        "source_missing": False,
        "source_preserved": True,
        "source_write_attempted": False,
        "guessed_paths": False,
        "cross_db_atomic": False,
        "explicit_project_id_supported": False,
        "archive_frees_path": False,
        "home": str(home),
        "folders_db": str(path),
        "folders": listed,
        "http_status": 200,
        "recovery": RECOVERY_TEXT,
    }


def _parse_mappings(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not raw:
        raise MigrationError("invalid_mappings", "mappings 必须是非空数组，不会迁移全部文件夹", 400)
    seen: set[str] = set()
    paths: set[str] = set()
    parsed = []
    for item in raw:
        if not isinstance(item, dict):
            raise MigrationError("invalid_mappings", "每个映射必须是对象", 400)
        folder_id = str(item.get("folder_id") or "").strip()
        if not folder_id or "\0" in folder_id:
            raise MigrationError("invalid_mappings", "folder_id 不能为空", 400)
        if folder_id in seen:
            raise MigrationError("invalid_mappings", "同一请求不能重复映射同一个文件夹", 400)
        seen.add(folder_id)
        if "name" in item and item["name"] is not None:
            raise MigrationError("invalid_mappings", "项目名称以旧文件夹记录为准，不接受请求内改名", 400)
        if "session_ids" in item:
            raise MigrationError("invalid_mappings", "会话列表以旧文件夹归属为准，不接受请求内增删", 400)
        if "project_id" in item:
            raise MigrationError("invalid_mappings", "不接受请求指定 project_id，不会并入任意已有项目", 400)
        path = _validate_path_syntax(item.get("primary_path"))
        if path in paths:
            raise MigrationError("duplicate_mapping_path", "同一请求不能把多个文件夹映射到同一目录", 400)
        paths.add(path)
        create_directory = item.get("create_directory")
        if create_directory is None:
            create_directory = False
        if not isinstance(create_directory, bool):
            raise MigrationError("invalid_mappings", "create_directory 必须是布尔值，缺省不会创建目录", 400)
        parsed.append({
            "folder_id": folder_id,
            "primary_path": path,
            "create_directory": create_directory,
        })
    return parsed


def _fingerprint(
    mappings: list[dict[str, Any]],
    folder_sessions: dict[str, list[str]],
    reuse_migration_id: Optional[str] = None,
) -> str:
    payload = {
        "mappings": [
            {
                "folder_id": item["folder_id"],
                "primary_path": item["primary_path"],
                "create_directory": item["create_directory"] is True,
                "session_ids": list(folder_sessions[item["folder_id"]]),
            }
            for item in mappings
        ],
        "reuse_migration_id": reuse_migration_id,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _require_key(key: Optional[str], fingerprint: str) -> str:
    if key is None or str(key).strip() == "":
        return "map-" + fingerprint[:32]
    text = str(key).strip()
    if len(text) > _MAX_KEY or "\0" in text or any(ord(ch) < 32 for ch in text):
        raise MigrationError("invalid_idempotency_key", "idempotency_key 不合法", 400)
    return text


def _parse_reuse_id(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise MigrationError("invalid_reuse", "reuse_migration_id 必须是字符串", 400)
    text = raw.strip()
    if not text or len(text) > 128 or "\0" in text or any(ord(ch) < 32 for ch in text):
        raise MigrationError("invalid_reuse", "reuse_migration_id 不合法", 400)
    return text


def _require_reuse_request(reuse_id: Optional[str], confirm_reuse: bool) -> Optional[str]:
    if reuse_id is None and confirm_reuse is not True:
        return None
    if confirm_reuse is not True:
        raise MigrationError("reuse_confirm_required", "复用空项目必须显式 confirm_reuse=true，不会默认并入", 400)
    if not reuse_id:
        raise MigrationError("reuse_confirm_required", "confirm_reuse 必须同时给出原回执 id", 400)
    return reuse_id


def _resolve_reuse(
    home: Path,
    reuse_id: str,
    mappings: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    source = _load_by_id(home, reuse_id)
    if source is None:
        raise MigrationError("migration_not_found", "复用的原回执不存在，未并入其他项目", 404)
    if source.get("status") != "rolled_back":
        raise MigrationError(
            "reuse_refused",
            "只有已回滚的原回执可以显式复用。未完成的迁移请用原幂等键恢复，不会并入其他项目",
            409,
            receipt=source,
        )
    source_folders = {
        folder.get("folder_id"): folder
        for folder in source.get("folders") or []
        if isinstance(folder, dict)
    }
    proven: dict[str, dict[str, Any]] = {}
    for item in mappings:
        folder = source_folders.get(item["folder_id"])
        if not isinstance(folder, dict) or not folder.get("project_id") or not folder.get("intent_token"):
            raise MigrationError(
                "reuse_refused",
                "原回执没有该文件夹的创建证明，拒绝复用",
                409,
                details={"folder_id": item["folder_id"]},
            )
        if not folder.get("project_created") and not folder.get("reused_from"):
            raise MigrationError(
                "reuse_refused",
                "原回执没有证明创建该项目，拒绝按路径复用",
                409,
                details={"folder_id": item["folder_id"]},
            )
        proof_folder = dict(folder)
        proof_folder["primary_path"] = item["primary_path"]
        project = _prove_created_project(home, proof_folder)
        owner = _project_at_path(home, item["primary_path"])
        if (
            project is None
            or owner is None
            or project.id != owner.id
            or project.id != folder.get("project_id")
        ):
            raise MigrationError(
                "reuse_refused",
                "原回执不能证明该目录上的项目是这次创建的，拒绝并入",
                409,
                details={"folder_id": item["folder_id"]},
            )
        if not _project_untouched_empty(home, project):
            raise MigrationError(
                "reuse_refused",
                "该项目已非空或字段已变化，不能当作空项目复用",
                409,
                details={"folder_id": item["folder_id"], "project_id": project.id},
            )
        proven[item["folder_id"]] = {"project": project, "source": folder}
    return proven


def _require_session_ready(session_db: Any, session_id: str) -> None:
    if session_db is None or not hasattr(session_db, "get_session") or not hasattr(session_db, "_execute_write"):
        raise MigrationError("session_check_failed", "无法核实会话存储，已拒绝迁移", 503)
    try:
        row = session_db.get_session(session_id)
    except Exception as exc:
        raise MigrationError("session_check_failed", "无法核实会话是否存在，已拒绝", 503) from exc
    if not row:
        raise MigrationError("session_not_found", f"会话不存在: {session_id}", 404, details={"session_id": session_id})
    try:
        busy = is_session_busy(session_db, session_id)
    except SessionStoreCheckError as exc:
        raise MigrationError("session_check_failed", "无法确认会话是否空闲，已拒绝", 503, details={"session_id": session_id}) from exc
    if busy:
        raise MigrationError("session_busy", "会话正在执行，不能迁移归属", 409, details={"session_id": session_id})


def _check_mapping_targets(
    home: Path,
    mappings: list[dict[str, Any]],
    folders_by_id: dict[str, dict[str, Any]],
    *,
    allow_project_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    conflicts = []
    for item in mappings:
        folder = folders_by_id.get(item["folder_id"])
        if folder is None:
            raise MigrationError(
                "folder_not_found",
                f"旧文件夹不存在: {item['folder_id']}",
                404,
                details={"folder_id": item["folder_id"]},
            )
        _refuse_workspace(item["primary_path"], home)
        path = Path(item["primary_path"])
        if path.exists() and not path.is_dir():
            raise MigrationError("invalid_directory", "primary_path 必须是目录，不是文件", 400)
        if not path.exists() and item["create_directory"] is not True:
            raise MigrationError(
                "invalid_directory",
                "目录不存在。未创建。需要显式 create_directory=true 才会创建",
                400,
                details={"primary_path": item["primary_path"]},
            )
        owner = _project_at_path(home, item["primary_path"])
        if owner is not None and owner.id != allow_project_id:
            conflicts.append({
                "code": "path_conflict",
                "folder_id": item["folder_id"],
                "primary_path": item["primary_path"],
                "project_id": owner.id,
                "message": "该目录已属于其他项目，拒绝覆盖或并入",
            })
    return conflicts


def _conflict_status(conflicts: list[dict[str, Any]]) -> tuple[int, str]:
    codes = {item.get("code") for item in conflicts}
    if codes == {"session_not_found"}:
        return 404, "session_not_found"
    if codes and codes <= {"session_busy", "session_not_found"}:
        return 409, "session_busy"
    return 409, "migration_conflict"


def _session_plan(
    home: Path,
    session_db: Any,
    session_ids: list[str],
    *,
    expected_project_id: Optional[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Inspect every session before any caller writes. Store failures fail the batch."""
    if session_ids and (
        session_db is None
        or not hasattr(session_db, "get_session")
        or not hasattr(session_db, "_execute_write")
    ):
        raise MigrationError("session_check_failed", "无法核实会话存储，已拒绝迁移", 503)
    checks = []
    conflicts = []
    for sid in session_ids:
        try:
            row = session_db.get_session(sid)
        except Exception as exc:
            raise MigrationError("session_check_failed", "无法核实会话是否存在，已拒绝", 503) from exc
        if not row:
            conflicts.append({
                "code": "session_not_found",
                "session_id": sid,
                "message": "会话不存在，未写入",
            })
            continue
        try:
            busy = is_session_busy(session_db, sid)
        except SessionStoreCheckError as exc:
            raise MigrationError("session_check_failed", "无法确认会话是否空闲，已拒绝", 503) from exc
        if busy:
            conflicts.append({
                "code": "session_busy",
                "session_id": sid,
                "message": "会话正在执行，不能迁移归属",
            })
            continue
        current = _current_project_id(home, sid)
        if current is not None and current != expected_project_id:
            conflicts.append({
                "code": "ownership_conflict",
                "session_id": sid,
                "current_project_id": current,
                "message": "会话已有其他项目归属，拒绝覆盖",
            })
            continue
        checks.append({
            "session_id": sid,
            "previous_project_id": current,
            "idle": True,
            "exists": True,
        })
    return checks, conflicts


def plan_folder_migration(
    mappings: Any,
    session_db: Any,
    hermes_home: Optional[Path] = None,
    *,
    reuse_migration_id: Any = None,
    confirm_reuse: bool = False,
) -> dict[str, Any]:
    """Dry-run. Does not create directories, projects, bindings, or ledger rows."""
    home = resolve_migration_home(hermes_home)
    parsed = _parse_mappings(mappings)
    reuse_id = _require_reuse_request(_parse_reuse_id(reuse_migration_id), confirm_reuse is True)
    _path, folders = _read_folders(home)
    by_id = {folder["folder_id"]: folder for folder in folders}
    proven = _resolve_reuse(home, reuse_id, parsed) if reuse_id else {}
    conflicts: list[dict[str, Any]] = []
    for item in parsed:
        allow = proven[item["folder_id"]]["project"].id if item["folder_id"] in proven else None
        conflicts.extend(_check_mapping_targets(home, [item], by_id, allow_project_id=allow))
    planned = []
    for item in parsed:
        folder = by_id[item["folder_id"]]
        checks, session_conflicts = _session_plan(home, session_db, folder["session_ids"], expected_project_id=None)
        conflicts.extend(session_conflicts)
        planned.append({
            "folder_id": folder["folder_id"],
            "name": folder["name"],
            "primary_path": item["primary_path"],
            "create_directory": item["create_directory"],
            "session_ids": list(folder["session_ids"]),
            "session_checks": checks,
            "would_create_project": item["folder_id"] not in proven,
            "reuse_confirmed": item["folder_id"] in proven,
            "guessed": False,
        })
    success = not conflicts
    status, code = _conflict_status(conflicts) if conflicts else (200, "")
    return {
        "operation": "plan",
        "read_only": True,
        "applied": False,
        "success": success,
        "error": code or None,
        "directories_created": False,
        "source_preserved": True,
        "source_write_attempted": False,
        "guessed_paths": False,
        "cross_db_atomic": False,
        "explicit_project_id_supported": False,
        "archive_frees_path": False,
        "conflicts": conflicts,
        "folders": planned,
        "home": str(home),
        "http_status": status,
        "recovery": RECOVERY_TEXT,
        "message": "只读核对完成，未写入" if success else "核对发现冲突，未写入",
    }


class _OwnershipFence:
    def __init__(
        self,
        inner: Any,
        session_id: str,
        expected: Optional[str],
        expected_write_token: Optional[str],
        home: Path,
    ):
        self._inner = inner
        self._session_id = session_id
        self._expected = expected
        self._expected_write_token = expected_write_token
        self._home = home

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def _execute_write(self, fn: Any) -> Any:
        def wrapped(conn: Any) -> Any:
            current = _session_binding(self._home, self._session_id)
            current_id = current.project_id if current is not None else None
            current_token = current.write_token if current is not None else None
            if current_id != self._expected or current_token != self._expected_write_token:
                raise OwnershipConflict(
                    self._session_id,
                    self._expected,
                    current_id,
                    self._expected_write_token,
                    current_token,
                    current.bound_at if current is not None else None,
                    current.bound_at if current is not None else None,
                )
            return fn(conn)

        return self._inner._execute_write(wrapped)


def _verify_binding(home: Path, session_id: str, project_id: Optional[str], write_token: Optional[str]) -> bool:
    current = _session_binding(home, session_id)
    current_id = current.project_id if current is not None else None
    current_token = current.write_token if current is not None else None
    return current_id == project_id and current_token == write_token


def _create_project(home: Path, name: str, primary_path: str, *, slug: str, description: str) -> str:
    """Existing create API generates the id. Callers must not invent one."""
    with pdb.connect_closing(home / "projects.db") as conn:
        return pdb.create_project(
            conn,
            name=name,
            slug=slug,
            description=description,
            primary_path=primary_path,
            allow_duplicate_path=False,
        )


def _verify_project(home: Path, project_id: str, primary_path: str) -> bool:
    db_path = home / "projects.db"
    if not db_path.exists() or not project_id:
        return False
    with pdb.connect_closing(db_path) as conn:
        proj = pdb.get_project(conn, project_id)
        owner = pdb.find_by_primary_path(conn, primary_path, include_archived=True)
    return proj is not None and bool(proj.primary_path) and owner is not None and owner.id == project_id


def _new_receipt(
    *,
    migration_id: str,
    key: str,
    fingerprint: str,
    home: Path,
    folders_db: str,
    planned: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "operation": "execute",
        "migration_id": migration_id,
        "idempotency_key": key,
        "fingerprint": fingerprint,
        "status": "applying",
        "success": False,
        "read_only": False,
        "applied": False,
        "cross_db_atomic": False,
        "explicit_project_id_supported": False,
        "archive_frees_path": False,
        "credentials_migrated": False,
        "source_preserved": True,
        "source_write_attempted": False,
        "guessed_paths": False,
        "directories_created": False,
        "home": str(home),
        "folders_db": folders_db,
        "recovery": RECOVERY_TEXT,
        "conflicts": [],
        "folders": planned,
        "created_at": _now(),
        "updated_at": _now(),
    }


def _finish_applied(receipt: dict[str, Any]) -> None:
    receipt["status"] = "applied"
    receipt["applied"] = True
    receipt["success"] = False
    for folder in receipt["folders"]:
        if not folder.get("project_verified"):
            receipt["status"] = "partial"
            receipt["applied"] = False
            receipt["success"] = False
            return
        for sess in folder["sessions"]:
            if not sess.get("verified") or not sess.get("write_token"):
                receipt["status"] = "partial"
                receipt["applied"] = False
                receipt["success"] = False
                return
    receipt["success"] = True


def _bind_expected(
    home: Path,
    session_db: Any,
    session_id: str,
    project_id: str,
    expected: Optional[str],
    expected_write_token: Optional[str],
    write_token: str,
) -> None:
    fence = _OwnershipFence(session_db, session_id, expected, expected_write_token, home)
    commit_session_binding(
        session_id,
        project_id,
        hermes_home=home,
        session_db=fence,
        force=False,
        write_token=write_token,
    )


def _release_expected(
    home: Path,
    session_db: Any,
    session_id: str,
    expected_project_id: str,
    expected_write_token: str,
) -> None:
    """Conditional release through the idle gate. project_id is the expected project."""
    commit_session_binding(
        session_id,
        expected_project_id,
        hermes_home=home,
        session_db=session_db,
        force=False,
        expected_write_token=expected_write_token,
    )


def _allow_project_id(home: Path, folder: dict[str, Any]) -> Optional[str]:
    proven = _prove_created_project(home, folder)
    return proven.id if proven is not None else None


def _adopt_or_create(
    home: Path,
    receipt: dict[str, Any],
    folder: dict[str, Any],
    source: dict[str, Any],
) -> Optional[dict[str, Any]]:
    if folder.get("project_id"):
        if _prove_created_project(home, folder) is None:
            folder["error"] = "project_verify_failed"
            folder["project_verified"] = False
            return _fail_receipt(home, receipt, "partial", "已记录的项目与创建意图不一致，未继续绑定，也未按路径认领")
        folder["project_verified"] = True
        folder["ownership_proof"] = "intent_token"
        return None
    if folder.get("reused_from"):
        return _fail_receipt(home, receipt, "reuse_refused", "复用回执缺少已证明的项目，未创建替代项目")
    _ensure_intent(home, receipt, folder)
    existed = Path(folder["primary_path"]).exists()
    try:
        resolved = prepare_project_directory(
            folder["primary_path"],
            create_directory=folder.get("create_directory") is True,
        )
    except ValueError as exc:
        folder["error"] = str(exc)
        return _fail_receipt(home, receipt, "partial", str(exc))
    folder["primary_path"] = resolved
    if not existed and Path(resolved).is_dir():
        folder["directory_created"] = True
        receipt["directories_created"] = True
    _save_receipt(home, receipt)
    proven = _prove_created_project(home, folder)
    owner = _project_at_path(home, resolved)
    if proven is not None:
        folder["project_id"] = proven.id
        folder["project_created"] = True
        folder["project_adopted"] = True
        folder["project_verified"] = True
        folder["ownership_proof"] = "intent_token"
        _save_receipt(home, receipt)
        return None
    if owner is not None:
        conflict = _occupant_conflict(folder, owner)
        folder["error"] = "path_conflict"
        folder["conflicting_project_id"] = owner.id
        receipt["conflicts"].append(conflict)
        return _fail_receipt(home, receipt, "partial", conflict["message"])
    intent = _intent_of(folder)
    if intent is None:
        return _fail_receipt(home, receipt, "create_unproven", "创建意图未持久，未创建项目")
    db_path = home / "projects.db"
    if db_path.exists():
        with pdb.connect_closing(db_path) as conn:
            taken = pdb.get_project(conn, intent["slug"])
        if taken is not None:
            folder["error"] = "intent_occupied"
            return _fail_receipt(
                home,
                receipt,
                "create_unproven",
                "创建意图 slug 已被其他项目占用，未创建，也未按路径认领",
            )
    try:
        pid = _create_project(
            home,
            source["name"],
            resolved,
            slug=intent["slug"],
            description=intent["description"],
        )
    except ValueError:
        proven = _prove_created_project(home, folder)
        if proven is not None:
            folder["project_id"] = proven.id
            folder["project_created"] = True
            folder["project_adopted"] = True
            folder["project_verified"] = True
            folder["ownership_proof"] = "intent_token"
            _save_receipt(home, receipt)
            return None
        folder["error"] = "path_conflict"
        return _fail_receipt(home, receipt, "partial", "创建项目失败，未按路径并入已有项目")
    proved = _prove_created_project(home, {**folder, "project_id": pid})
    if proved is None or proved.id != pid:
        folder["unproven_project_id"] = pid
        folder["proven"] = False
        return _fail_receipt(
            home,
            receipt,
            "create_unproven",
            "项目已创建但无法用预先持久的意图证明归属，未认领，未绑定",
        )
    folder["project_id"] = pid
    folder["project_created"] = True
    folder["project_adopted"] = False
    folder["project_verified"] = True
    folder["ownership_proof"] = "intent_token"
    _save_receipt(home, receipt)
    return None


def _note_current(sess: dict[str, Any], binding: Optional[pdb.SessionBinding]) -> None:
    sess["current_project_id"] = binding.project_id if binding is not None else None
    sess["current_write_token"] = binding.write_token if binding is not None else None
    sess["current_bound_at"] = binding.bound_at if binding is not None else None


def _mark_committed(sess: dict[str, Any], project_id: str, binding: pdb.SessionBinding, token: str) -> None:
    sess["written_project_id"] = project_id
    sess["write_token"] = token
    sess["write_bound_at"] = binding.bound_at
    sess["write_fingerprint"] = f"{project_id}:{token}"
    sess["applied"] = True
    sess["verified"] = True
    sess["bind_state"] = "committed"
    sess["ownership_proof"] = "write_token"


def _prepare_bind(home: Path, receipt: dict[str, Any], folder: dict[str, Any], sess: dict[str, Any]) -> str:
    sid = sess["session_id"]
    target = folder.get("project_id")
    binding = _session_binding(home, sid)
    current_id = binding.project_id if binding is not None else None
    current_token = binding.write_token if binding is not None else None
    try:
        observed = _observed_write_token(home, sid)
    except MigrationError as exc:
        sess["error"] = exc.code
        sess["message"] = exc.message
        return "reader_failed"
    if observed != current_token:
        sess["error"] = "write_token_mismatch"
        _note_current(sess, binding)
        return "reader_failed"
    pending = sess.get("pending_write_token")
    proven = sess.get("write_token")
    if sess.get("verified") and proven:
        if current_id == sess.get("written_project_id") and current_token == proven:
            return "skip"
        sess["error"] = "ownership_diverged"
        _note_current(sess, binding)
        return "diverged"
    if pending and binding is not None and current_id == target and current_token == pending:
        _mark_committed(sess, str(target), binding, pending)
        _save_receipt(home, receipt)
        return "skip"
    if current_id == target:
        sess["error"] = "bind_unproven"
        sess["bind_state"] = "unproven"
        _note_current(sess, binding)
        return "unproven"
    if sess.get("bind_state") == "pending":
        if current_id != sess.get("previous_project_id") or current_token != sess.get("previous_write_token"):
            sess["error"] = "ownership_conflict"
            _note_current(sess, binding)
            return "conflict"
        if not pending:
            sess["error"] = "bind_unproven"
            return "unproven"
    else:
        if current_id is not None:
            sess["error"] = "ownership_conflict"
            _note_current(sess, binding)
            return "conflict"
        token = pdb.new_write_token()
        sess["bind_state"] = "pending"
        sess["previous_project_id"] = current_id
        sess["previous_write_token"] = current_token
        sess["previous_bound_at"] = binding.bound_at if binding is not None else None
        sess["pending_write_token"] = token
        sess["bind_intent_at"] = _now()
        _save_receipt(home, receipt)
    return "ready"


def _apply_saved(
    home: Path,
    session_db: Any,
    receipt: dict[str, Any],
    folders_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    for folder in receipt["folders"]:
        source = folders_by_id.get(folder["folder_id"])
        if source is None:
            folder["error"] = "folder_not_found"
            return _fail_receipt(home, receipt, "partial", "旧文件夹已不存在，未继续写入")
        failed = _adopt_or_create(home, receipt, folder, source)
        if failed is not None:
            return failed
        for sess in folder["sessions"]:
            action = _prepare_bind(home, receipt, folder, sess)
            if action == "skip":
                continue
            if action == "unproven":
                receipt["conflicts"].append({
                    "code": "bind_unproven",
                    "session_id": sess["session_id"],
                    "message": "绑定可能已落地，但回执没有可对上的 write_token，不能证明是本次迁移，未认领也未覆盖",
                })
                return _fail_receipt(
                    home,
                    receipt,
                    "bind_unproven",
                    "绑定已可能落地，但回执没有可对上的 write_token。不能证明归属，未回滚，也未覆盖。",
                )
            if action == "reader_failed":
                return _fail_receipt(home, receipt, "partial", sess.get("message") or "write_token 读口失败，未写入")
            if action in {"conflict", "diverged"}:
                receipt["conflicts"].append({
                    "code": sess.get("error") or "ownership_conflict",
                    "session_id": sess["session_id"],
                    "current_project_id": sess.get("current_project_id"),
                    "message": "归属与回执中的 write_token 不一致，未覆盖",
                })
                return _fail_receipt(home, receipt, "partial", "归属已变化，未覆盖")
            sid = sess["session_id"]
            token = sess.get("pending_write_token")
            if not token:
                sess["error"] = "bind_unproven"
                return _fail_receipt(home, receipt, "bind_unproven", "绑定 token 未预先写入回执，未绑定")
            expected = sess.get("previous_project_id")
            expected_token = sess.get("previous_write_token")
            try:
                # Plan-time idle is not authorization. Recheck immediately, then again
                # inside commit_session_binding's require_idle.
                _require_session_ready(session_db, sid)
                _bind_expected(home, session_db, sid, folder["project_id"], expected, expected_token, token)
            except OwnershipConflict as exc:
                sess["error"] = "ownership_conflict"
                sess["current_project_id"] = exc.current
                sess["current_write_token"] = exc.current_write_token
                sess["current_bound_at"] = exc.current_bound_at
                receipt["conflicts"].append({
                    "code": "ownership_conflict",
                    "session_id": sid,
                    "current_project_id": exc.current,
                    "message": "归属在绑定锁内已变化，未覆盖",
                })
                return _fail_receipt(home, receipt, "partial", "归属在绑定锁内已变化，未覆盖")
            except SessionBusyError:
                sess["error"] = "session_busy"
                return _fail_receipt(home, receipt, "partial", "会话正在执行，绑定当时的核心再检查已拒绝")
            except LookupError:
                sess["error"] = "session_not_found"
                return _fail_receipt(home, receipt, "partial", "会话不存在，未继续绑定")
            except BindingTokenUnsupported as exc:
                sess["error"] = "write_token_unsupported"
                sess["message"] = str(exc)
                return _fail_receipt(home, receipt, "partial", "绑定入口不接受 write_token，已拒绝且未绕过空闲检查")
            except ValueError as exc:
                sess["error"] = "bind_rejected"
                sess["message"] = str(exc)
                return _fail_receipt(home, receipt, "partial", "绑定被拒绝，未报告成功")
            except (SessionStoreCheckError, MigrationError) as exc:
                sess["error"] = "session_check_failed"
                sess["message"] = str(exc)
                return _fail_receipt(home, receipt, "partial", "无法确认会话空闲，已拒绝")
            except Exception as exc:
                sess["error"] = "bind_failed"
                sess["message"] = str(exc)
                return _fail_receipt(home, receipt, "partial", "绑定失败，未报告成功")
            binding = _session_binding(home, sid)
            try:
                observed = _observed_write_token(home, sid)
            except MigrationError as exc:
                sess["error"] = exc.code
                return _fail_receipt(home, receipt, "partial", exc.message)
            if (
                binding is None
                or binding.project_id != folder["project_id"]
                or binding.write_token != token
                or observed != token
            ):
                sess["error"] = "bind_unproven" if binding is not None and binding.project_id == folder["project_id"] else "verify_failed"
                _note_current(sess, binding)
                if sess["error"] == "bind_unproven":
                    return _fail_receipt(
                        home,
                        receipt,
                        "bind_unproven",
                        "绑定读回的 write_token 与预先持久的 token 不一致，未认领，未覆盖",
                    )
                return _fail_receipt(home, receipt, "partial", "绑定读回与预期不符，未记为成功")
            _mark_committed(sess, folder["project_id"], binding, token)
            _save_receipt(home, receipt)
    _finish_applied(receipt)
    _save_receipt(home, receipt)
    return _present(home, receipt)


def _verify_applied_replay(home: Path, receipt: dict[str, Any], folders_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    out = _present(home, receipt, replay=True)
    mismatches = []
    for folder in out.get("folders") or []:
        source = folders_by_id.get(folder.get("folder_id"))
        current_ids = source["session_ids"] if source else []
        recorded = [sess.get("session_id") for sess in folder.get("sessions") or []]
        extra = [sid for sid in current_ids if sid not in recorded]
        if extra:
            folder["not_migrated_session_ids"] = extra
        if _prove_created_project(home, folder) is None:
            mismatches.append({"folder_id": folder.get("folder_id"), "code": "project_diverged"})
        for sess in folder.get("sessions") or []:
            if not sess.get("applied"):
                continue
            binding = _session_binding(home, sess["session_id"])
            current_id = binding.project_id if binding is not None else None
            current_token = binding.write_token if binding is not None else None
            if (
                not sess.get("write_token")
                or current_id != sess.get("written_project_id")
                or current_token != sess.get("write_token")
            ):
                mismatches.append({
                    "session_id": sess["session_id"],
                    "code": "ownership_diverged",
                    "current_project_id": current_id,
                    "current_write_token": current_token,
                })
    if mismatches:
        out["success"] = False
        out["status"] = "diverged"
        out["applied"] = False
        out["http_status"] = 409
        out["conflicts"] = mismatches
        out["message"] = "相同幂等键的回执与当前写入指纹不一致，未重新覆盖"
        out["idempotent_replay"] = False
    else:
        out["message"] = "相同映射已完成，未再次写入"
    return out


def _resume_statuses() -> set[str]:
    return {"applying", "partial", "bind_unproven", "create_unproven"}


def execute_folder_migration(
    mappings: Any,
    session_db: Any,
    *,
    confirm: bool,
    idempotency_key: Optional[str] = None,
    hermes_home: Optional[Path] = None,
    reuse_migration_id: Any = None,
    confirm_reuse: bool = False,
) -> dict[str, Any]:
    if confirm is not True:
        raise MigrationError("confirm_required", "执行迁移必须显式 confirm=true", 400)
    home = resolve_migration_home(hermes_home)
    parsed = _parse_mappings(mappings)
    reuse_id = _require_reuse_request(_parse_reuse_id(reuse_migration_id), confirm_reuse is True)
    with _migration_lock(home):
        folders_path, folders = _read_folders(home)
        by_id = {folder["folder_id"]: folder for folder in folders}
        sessions = {item["folder_id"]: list(by_id[item["folder_id"]]["session_ids"]) for item in parsed if item["folder_id"] in by_id}
        missing = [item["folder_id"] for item in parsed if item["folder_id"] not in by_id]
        if missing:
            raise MigrationError("folder_not_found", "旧文件夹不存在，未写入", 404, details={"folder_ids": missing})
        fingerprint = _fingerprint(parsed, sessions, reuse_id)
        key = _require_key(idempotency_key, fingerprint)
        existing = _load_by_key(home, key)
        if existing is not None:
            if existing.get("fingerprint") != fingerprint and existing.get("status") != "applied":
                raise MigrationError(
                    "idempotency_conflict",
                    "相同幂等键对应的映射已变化，未覆盖原回执",
                    409,
                    details={"migration_id": existing.get("migration_id")},
                    receipt=existing,
                )
            if existing.get("status") == "rolled_back":
                raise MigrationError(
                    "idempotency_conflict",
                    "该幂等键已回滚，不会自动再次迁移。请使用新的幂等键；同一目录重试还需确认复用原回执的空项目",
                    409,
                    receipt=existing,
                )
            if existing.get("status") == "applied":
                replay = _verify_applied_replay(home, existing, by_id)
                if existing.get("fingerprint") != fingerprint and replay.get("success") is True:
                    replay["ignored_mapping_change"] = True
                    replay["message"] = "该幂等键已完成旧映射，本次不同路径未执行，也未覆盖"
                return replay
            if existing.get("status") in _resume_statuses():
                resume_conflicts: list[dict[str, Any]] = []
                for item in parsed:
                    for folder in existing["folders"]:
                        if folder["folder_id"] == item["folder_id"]:
                            resume_conflicts.extend(
                                _check_mapping_targets(
                                    home,
                                    [item],
                                    by_id,
                                    allow_project_id=_allow_project_id(home, folder),
                                )
                            )
                if resume_conflicts:
                    raise MigrationError(
                        "migration_conflict",
                        "续跑核对发现冲突，未覆盖已有归属，也未认领无法证明的项目",
                        409,
                        details={"conflicts": resume_conflicts},
                        receipt=existing,
                    )
                return _apply_saved(home, session_db, existing, by_id)
            raise MigrationError("idempotency_conflict", "该幂等键状态未知，未继续写入", 409, receipt=existing)

        proven = _resolve_reuse(home, reuse_id, parsed) if reuse_id else {}
        conflicts: list[dict[str, Any]] = []
        for item in parsed:
            allow = proven[item["folder_id"]]["project"].id if item["folder_id"] in proven else None
            conflicts.extend(_check_mapping_targets(home, [item], by_id, allow_project_id=allow))
        planned_folders = []
        for item in parsed:
            source = by_id[item["folder_id"]]
            checks, session_conflicts = _session_plan(
                home, session_db, source["session_ids"], expected_project_id=None
            )
            conflicts.extend(session_conflicts)
            planned = {
                "folder_id": source["folder_id"],
                "name": source["name"],
                "primary_path": item["primary_path"],
                "create_directory": item["create_directory"] is True,
                "project_id": None,
                "project_created": False,
                "project_verified": False,
                "project_adopted": False,
                "directory_created": False,
                "guessed": False,
                "sessions": [
                    {
                        "session_id": check["session_id"],
                        "previous_project_id": check["previous_project_id"],
                        "previous_write_token": None,
                        "previous_bound_at": None,
                        "pending_write_token": None,
                        "written_project_id": None,
                        "write_token": None,
                        "write_bound_at": None,
                        "write_fingerprint": None,
                        "bind_state": None,
                        "applied": False,
                        "verified": False,
                    }
                    for check in checks
                ],
            }
            reused = proven.get(item["folder_id"])
            if reused is not None:
                source_folder = reused["source"]
                planned["project_id"] = reused["project"].id
                planned["reused_from"] = reuse_id
                planned["reuse_confirmed"] = True
                planned["intent_token"] = source_folder.get("intent_token")
                planned["intended_slug"] = source_folder.get("intended_slug")
                planned["intent_migration_id"] = source_folder.get("intent_migration_id")
                planned["ownership_proof"] = "receipt_intent"
            planned_folders.append(planned)
        if conflicts:
            status, code = _conflict_status(conflicts)
            raise MigrationError(
                code,
                "迁移前核对失败，未创建项目，也未改归属",
                status,
                details={"conflicts": conflicts},
            )
        receipt = _new_receipt(
            migration_id="m_" + secrets.token_hex(8),
            key=key,
            fingerprint=fingerprint,
            home=home,
            folders_db=str(folders_path),
            planned=planned_folders,
        )
        _save_receipt(home, receipt)
        return _apply_saved(home, session_db, receipt, by_id)


def _rollback_success(receipt: dict[str, Any]) -> None:
    receipt["success"] = False
    receipt["fully_restored"] = False
    if receipt.get("status") != "rolled_back":
        return
    applied = []
    for folder in receipt.get("folders") or []:
        for sess in folder.get("sessions") or []:
            if sess.get("applied"):
                applied.append(sess)
    if any(sess.get("rollback") not in {"restored", "already_restored", "skipped_modified"} for sess in applied):
        return
    receipt["success"] = True
    receipt["fully_restored"] = all(sess.get("rollback") in {"restored", "already_restored"} for sess in applied)


def rollback_folder_migration(
    migration_id: str,
    session_db: Any,
    *,
    confirm: bool,
    hermes_home: Optional[Path] = None,
) -> dict[str, Any]:
    if confirm is not True:
        raise MigrationError("confirm_required", "回滚必须显式 confirm=true", 400)
    mid = str(migration_id or "").strip()
    if not mid:
        raise MigrationError("migration_not_found", "缺少 migration_id", 404)
    home = resolve_migration_home(hermes_home)
    with _migration_lock(home):
        receipt = _load_by_id(home, mid)
        if receipt is None:
            raise MigrationError("migration_not_found", "迁移回执不存在", 404)
        allowed = _resume_statuses() | {"applied", "rolled_back", "rolling_back", "rollback_incomplete"}
        if receipt.get("status") not in allowed:
            raise MigrationError("rollback_refused", "该回执没有可回滚的写入", 409, receipt=receipt)
        receipt["status"] = "rolling_back"
        receipt["success"] = False
        receipt["operation"] = "rollback"
        _save_receipt(home, receipt)
        for folder in receipt.get("folders") or []:
            for sess in folder.get("sessions") or []:
                sid = sess.get("session_id")
                if sess.get("rollback") == "restored":
                    continue
                binding = _session_binding(home, sid)
                current_id = binding.project_id if binding is not None else None
                current_token = binding.write_token if binding is not None else None
                if not sess.get("applied"):
                    pending = sess.get("pending_write_token")
                    if (
                        pending
                        and binding is not None
                        and current_id == folder.get("project_id")
                        and current_token == pending
                    ):
                        _mark_committed(sess, str(folder.get("project_id")), binding, pending)
                        _save_receipt(home, receipt)
                    elif sess.get("bind_state") in {"pending", "unproven"} and current_id == folder.get("project_id"):
                        sess["rollback"] = "rollback_unproven"
                        _note_current(sess, binding)
                        receipt["message"] = "有绑定无法用 write_token 证明是本次写入，未解绑，避免覆盖后来的人工操作。"
                        return _fail_receipt(home, receipt, "rollback_incomplete", receipt["message"])
                    else:
                        sess["rollback"] = "not_applied"
                        continue
                written = sess.get("written_project_id")
                token = sess.get("write_token")
                if not token:
                    sess["rollback"] = "rollback_unproven"
                    return _fail_receipt(
                        home,
                        receipt,
                        "rollback_incomplete",
                        "已标记绑定缺少 write_token，不能只因项目 id 或 bound_at 相同就回滚",
                    )
                if binding is None:
                    sess["rollback"] = "already_restored"
                    continue
                if current_id != written or current_token != token:
                    sess["rollback"] = "skipped_modified"
                    _note_current(sess, binding)
                    continue
                try:
                    _require_session_ready(session_db, sid)
                    _release_expected(home, session_db, sid, str(written), token)
                except SessionBusyError:
                    sess["rollback"] = "session_busy"
                    return _fail_receipt(home, receipt, "rollback_incomplete", "会话正在执行，回滚当时的核心再检查已拒绝")
                except LookupError:
                    sess["rollback"] = "session_missing"
                    return _fail_receipt(home, receipt, "rollback_incomplete", "会话缺失，未报告回滚成功")
                except BindingTokenUnsupported as exc:
                    sess["rollback"] = "write_token_unsupported"
                    sess["message"] = str(exc)
                    return _fail_receipt(home, receipt, "rollback_incomplete", "条件回滚入口不接受 expected_write_token，未无条件解绑")
                except (SessionStoreCheckError, MigrationError, Exception) as exc:
                    sess["rollback"] = "restore_failed"
                    sess["message"] = str(exc)
                    return _fail_receipt(home, receipt, "rollback_incomplete", "回滚失败，未报告成功")
                restored = _session_binding(home, sid)
                try:
                    observed = _observed_write_token(home, sid)
                except MigrationError as exc:
                    sess["rollback"] = "verify_failed"
                    sess["message"] = exc.message
                    return _fail_receipt(home, receipt, "rollback_incomplete", exc.message)
                if restored is not None or observed is not None:
                    if restored is not None and restored.write_token != token:
                        sess["rollback"] = "skipped_modified"
                        _note_current(sess, restored)
                        continue
                    sess["rollback"] = "verify_failed"
                    return _fail_receipt(home, receipt, "rollback_incomplete", "条件解绑读回失败，未报告成功")
                sess["rollback"] = "restored"
                _save_receipt(home, receipt)
        receipt["status"] = "rolled_back"
        receipt["source_preserved"] = True
        receipt["source_write_attempted"] = False
        receipt["project_archived"] = False
        receipt["directories_deleted"] = False
        receipt["archive_frees_path"] = False
        receipt["message"] = (
            "回滚已处理本回执写入且 write_token 仍吻合的归属。token 变化的归属已跳过，未覆盖。"
            "未删除目录、记忆、会话或旧文件夹。未自动归档。归档不释放路径。"
            "同一目录重试需新幂等键，并确认复用本回执证明的空项目。"
        )
        _rollback_success(receipt)
        _save_receipt(home, receipt)
        return _present(home, receipt)


def get_migration_receipt(migration_id: str, hermes_home: Optional[Path] = None) -> dict[str, Any]:
    home = resolve_migration_home(hermes_home)
    receipt = _load_by_id(home, str(migration_id or "").strip())
    if receipt is None:
        raise MigrationError("migration_not_found", "迁移回执不存在", 404)
    return _present(home, receipt)
