"""Targets a browser tool created, keyed by the tool's own task id.

This is not the panel shadow store. The writer is the browser tool at the
moment it creates a target. Persistence is one file under the active profile
home. ``PANEL_LIVE_TASK_DB`` is never opened. A missing profile, identity,
endpoint, or target fails closed — nothing here guesses the visible page or
falls back to desktop VNC.
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

_ID_RE = re.compile(r"[A-Za-z0-9_.:@-]{1,200}")
_LEASE_WAIT_S = 300.0
_CLEANUP_WAIT_S = 300.0
_MAX_ENDED = 50
_CLEANUP_SLOTS = threading.BoundedSemaphore(16)
_CLEANUP_PENDING: set[tuple[str, str]] = set()
_CLEANUP_LOCK = threading.Lock()
_DB_LOCK = threading.Lock()


class RegistryError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class IdentityError(RegistryError):
    def __init__(self, message: str) -> None:
        super().__init__("identity_missing", message)


def _stored_grant(value: object) -> str:
    """A daemon-issued grant safe to store. Empty means absent. Never logged."""
    if not isinstance(value, str) or not 16 <= len(value) <= 128 or not value.isascii():
        return ""
    if not all(ch.isalnum() or ch in "-_" for ch in value):
        return ""
    return value


def _harness_binding(transport: str, endpoint: str) -> bool:
    return transport == "harness" or endpoint == "harness"


def _scrub(text: str, grants: list[str]) -> str:
    cleaned = text
    for grant in grants:
        if grant:
            cleaned = cleaned.replace(grant, "")
    return cleaned[:300]


def _production(path: Path) -> bool:
    text = os.path.normpath(os.path.expanduser(str(path)))
    banned = (
        os.path.normpath("/root/.hermes"),
        os.path.normpath(str(Path.home() / ".hermes")),
    )
    return any(text == item or text.startswith(item + os.sep) for item in banned)


def profile_home() -> Path:
    """Active profile. Context override, else CLAWD_HOME, else HERMES_HOME.

    Unset means the platform default ``~/.hermes``. That is refused, not used.
    The path is not resolved when it is a production home, so this function
    does not touch ``/root/.hermes``.
    """
    from hermes_constants import get_hermes_home_override

    raw = (get_hermes_home_override() or os.environ.get("CLAWD_HOME") or os.environ.get("HERMES_HOME") or "").strip()
    if not raw:
        raise RegistryError("profile_unset", "profile home is unset; refusing ~/.hermes")
    home = Path(os.path.expanduser(raw))
    if not home.is_absolute() or _production(home):
        raise RegistryError("profile_refused", "refusing a relative or production profile home")
    return home


def registry_dir() -> Path:
    return profile_home() / "browser-tasks"


def registry_path() -> Path:
    return registry_dir() / "registry.sqlite3"


def gate_path() -> Path:
    return registry_dir() / "exec.lock"


def valid_id(value: object) -> str:
    if not isinstance(value, str) or _ID_RE.fullmatch(value) is None or value in {".", ".."}:
        raise IdentityError("task, session, turn, or target identity is missing or invalid")
    return value


def resolve_call_identity(task_id: object, session_id: object) -> tuple[str, str, str]:
    """Tool-call identity already on the handler kwargs and the approval context.

    Does not read the gateway session key, the process environment session, or
    whichever session the panel currently has selected.
    """
    from tools.approval_context import _approval_session_id, _approval_turn_id

    observed = _approval_session_id.get() or ""
    passed = session_id if isinstance(session_id, str) else ""
    if observed and passed and observed != passed:
        raise IdentityError("session identity from the tool call and the turn context disagree")
    try:
        return valid_id(task_id), valid_id(passed or observed), valid_id(_approval_turn_id.get() or "")
    except IdentityError as exc:
        raise IdentityError("missing task, session, or turn identity") from exc


def _connect(read_only: bool = False) -> sqlite3.Connection:
    path = registry_path()
    if read_only and not path.exists():
        raise RegistryError("registry_missing", "no browser target has been registered in this profile")
    if not read_only:
        _prepare_dir(path)
    conn = sqlite3.connect(path, timeout=5.0, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    if read_only:
        conn.execute("PRAGMA query_only = ON")
    return conn


def _prepare_dir(path: Path) -> None:
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(parent, 0o700)
    if not path.exists():
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
    os.chmod(path, 0o600)
    conn = sqlite3.connect(path, timeout=5.0, isolation_level=None)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS created_targets (
                target_id TEXT NOT NULL,
                browser_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                turn_id TEXT NOT NULL,
                endpoint TEXT NOT NULL,
                status TEXT NOT NULL,
                cleanup_error TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (target_id, browser_id)
            )"""
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_created_session ON created_targets (session_id, updated_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_created_task ON created_targets (task_id, session_id)")
        existing = {row[1] for row in conn.execute("PRAGMA table_info(created_targets)")}
        for name, ddl in (
            ("transport", "TEXT NOT NULL DEFAULT ''"),
            ("daemon_dir", "TEXT NOT NULL DEFAULT ''"),
            ("daemon_stem", "TEXT NOT NULL DEFAULT ''"),
            ("daemon_pid", "INTEGER NOT NULL DEFAULT 0"),
            ("task_grant", "TEXT NOT NULL DEFAULT ''"),
        ):
            if name not in existing:
                conn.execute(f"ALTER TABLE created_targets ADD COLUMN {name} {ddl}")
    finally:
        conn.close()


def register_created(*, task_id: str, session_id: str, turn_id: str, target_id: str,
                     browser_id: str, endpoint: str, transport: str = "", daemon_dir: str = "",
                     daemon_stem: str = "", daemon_pid: int = 0, task_grant: str = "") -> None:
    """Record a target this process just created. Refuses a target owned by someone else.

    A harness row without a daemon-issued ``task_grant`` is not written. The
    value is not returned by ``session_view`` or ``task_connection``.
    """
    task_id, session_id, turn_id, target_id = (valid_id(task_id), valid_id(session_id),
                                                valid_id(turn_id), valid_id(target_id))
    endpoint, browser_id = _accept_endpoint(endpoint, browser_id)
    transport = transport if transport in {"", "loopback", "harness", "supervisor"} else ""
    daemon_dir = daemon_dir if isinstance(daemon_dir, str) else ""
    daemon_stem = daemon_stem if isinstance(daemon_stem, str) else ""
    daemon_pid = daemon_pid if type(daemon_pid) is int and 0 < daemon_pid < (1 << 31) else 0
    harness = _harness_binding(transport, endpoint)
    grant = _stored_grant(task_grant) if harness else ""
    if harness and not grant:
        raise RegistryError("task_grant_required", "harness target was not authorized by the daemon")
    now = time.time()
    with _DB_LOCK:
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            owner = conn.execute(
                "SELECT task_id, session_id FROM created_targets WHERE target_id = ? AND browser_id = ?",
                (target_id, browser_id),
            ).fetchone()
            if owner is not None and (owner["task_id"] != task_id or owner["session_id"] != session_id):
                conn.execute("ROLLBACK")
                raise RegistryError("target_taken", "target already belongs to another task")
            conn.execute(
                """INSERT INTO created_targets
                   (target_id, browser_id, task_id, session_id, turn_id, endpoint, status,
                    cleanup_error, created_at, updated_at, transport, daemon_dir, daemon_stem, daemon_pid,
                    task_grant)
                   VALUES (?, ?, ?, ?, ?, ?, 'active', '', ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(target_id, browser_id) DO UPDATE SET
                     status = 'active', turn_id = excluded.turn_id, endpoint = excluded.endpoint,
                     updated_at = excluded.updated_at,
                     transport = CASE WHEN excluded.transport != '' THEN excluded.transport ELSE created_targets.transport END,
                     daemon_dir = CASE WHEN excluded.daemon_dir != '' THEN excluded.daemon_dir ELSE created_targets.daemon_dir END,
                     daemon_stem = CASE WHEN excluded.daemon_stem != '' THEN excluded.daemon_stem ELSE created_targets.daemon_stem END,
                     daemon_pid = CASE WHEN excluded.daemon_pid != 0 THEN excluded.daemon_pid ELSE created_targets.daemon_pid END,
                     task_grant = CASE WHEN excluded.task_grant != '' THEN excluded.task_grant ELSE created_targets.task_grant END
                   WHERE created_targets.task_id = excluded.task_id
                     AND created_targets.session_id = excluded.session_id
                     AND created_targets.status = 'active'""",
                (target_id, browser_id, task_id, session_id, turn_id, endpoint, now, now,
                 transport, daemon_dir, daemon_stem, daemon_pid, grant),
            )
            conn.execute("COMMIT")
            stored = conn.execute(
                """SELECT status FROM created_targets
                   WHERE target_id = ? AND browser_id = ? AND task_id = ? AND session_id = ?""",
                (target_id, browser_id, task_id, session_id),
            ).fetchone()
            stored_status = None if stored is None else stored["status"]
        except RegistryError:
            raise
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise RegistryError("registry_unavailable", "browser target registry write failed") from None
        finally:
            conn.close()
    if stored_status != "active":
        raise RegistryError("target_closed", "refusing to resurrect a closing or ended target")


def harness_task_grant(target_id: str, expected_pid: int) -> str:
    """Daemon grant for one active harness target. Internal IPC use only.

    Not a public view. ``session_view``, ``task_connection``, and live JSON
    must not call this or include the return value.
    """
    if type(expected_pid) is not int or not 0 < expected_pid < (1 << 31):
        raise RegistryError("browser_replaced", "daemon identity changed")
    try:
        target_id = valid_id(target_id)
    except IdentityError:
        raise RegistryError("task_grant_required", "daemon task grant is not registered for this target") from None
    try:
        conn = _connect(read_only=True)
    except RegistryError:
        raise RegistryError("task_grant_required", "daemon task grant is not registered for this target") from None
    try:
        rows = conn.execute(
            "SELECT * FROM created_targets WHERE target_id = ? AND status = 'active'",
            (target_id,),
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        raise RegistryError("task_grant_required", "daemon task grant is not registered for this target")
    matched = [row for row in rows if int(_field(row, "daemon_pid") or 0) == expected_pid]
    if not matched:
        raise RegistryError("browser_replaced", "daemon identity changed")
    grants = {_stored_grant(_field(row, "task_grant")) for row in matched}
    grants.discard("")
    if len(grants) != 1:
        raise RegistryError("task_grant_required", "daemon task grant is not registered for this target")
    return next(iter(grants))


def owns(session_id: str, task_id: str, target_id: str, browser_id: str) -> bool:
    if not all(isinstance(value, str) and value for value in (session_id, task_id, target_id, browser_id)):
        return False
    try:
        conn = _connect(read_only=True)
    except RegistryError:
        return False
    try:
        row = conn.execute(
            """SELECT 1 FROM created_targets
               WHERE session_id = ? AND task_id = ? AND target_id = ? AND browser_id = ?""",
            (session_id, task_id, target_id, browser_id),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def _field(row: sqlite3.Row, name: str, default: object = "") -> object:
    return row[name] if name in row.keys() else default


def _view_from_rows(rows: list[sqlite3.Row]) -> dict[str, Any] | None:
    if not rows:
        return None
    latest = max(rows, key=lambda row: float(row["updated_at"]))
    active = [row for row in rows if row["status"] == "active" and row["target_id"]]
    chosen = max(active, key=lambda row: float(row["updated_at"])) if active else latest
    status = "active" if active else ("closing" if any(row["status"] == "closing" for row in rows) else "ended")
    grants = [str(_field(row, "task_grant") or "") for row in rows]
    errors = [_scrub(str(row["cleanup_error"]), grants) for row in rows if row["cleanup_error"]]
    return {
        "taskId": chosen["task_id"],
        "sessionId": chosen["session_id"],
        "turnId": chosen["turn_id"],
        "status": status,
        "targetId": chosen["target_id"],
        "browserId": chosen["browser_id"],
        "endpoint": chosen["endpoint"],
        "cleanupError": "; ".join(errors)[:300],
        "updatedAt": float(chosen["updated_at"]),
        "transport": str(_field(chosen, "transport") or ""),
        "daemonDir": str(_field(chosen, "daemon_dir") or ""),
        "daemonStem": str(_field(chosen, "daemon_stem") or ""),
        "daemonPid": int(_field(chosen, "daemon_pid") or 0),
    }


def session_view(session_id: str, task_id: str = "") -> dict[str, Any] | None:
    """Latest registration for this session, optionally this task. No other session."""
    session_id = valid_id(session_id)
    if task_id:
        task_id = valid_id(task_id)
    profile_home()
    if not registry_path().exists():
        return None
    conn = _connect(read_only=True)
    try:
        if task_id:
            rows = conn.execute(
                "SELECT * FROM created_targets WHERE session_id = ? AND task_id = ?",
                (session_id, task_id),
            ).fetchall()
        else:
            latest = conn.execute(
                """SELECT task_id FROM created_targets WHERE session_id = ?
                   ORDER BY updated_at DESC LIMIT 1""",
                (session_id,),
            ).fetchone()
            if latest is None:
                return None
            rows = conn.execute(
                "SELECT * FROM created_targets WHERE session_id = ? AND task_id = ?",
                (session_id, latest["task_id"]),
            ).fetchall()
        return _view_from_rows(rows)
    finally:
        conn.close()


def open_and_register(cdp_url: str, *, task_id: str, session_id: str, turn_id: str) -> dict[str, str]:
    """Create a blank page on ``cdp_url`` and register that exact id."""
    from tools.browser_task_cdp import CdpError, close_exact, create_page, http_base, read_browser_id

    task_id, session_id, turn_id = valid_id(task_id), valid_id(session_id), valid_id(turn_id)
    try:
        endpoint = http_base(cdp_url)
        browser_id = read_browser_id(endpoint)
        target_id = create_page(endpoint)
        if read_browser_id(endpoint) != browser_id:
            raise CdpError("browser_replaced", "browser identity changed while creating the target")
    except CdpError as exc:
        raise RegistryError(exc.code, str(exc)) from exc
    try:
        register_created(task_id=task_id, session_id=session_id, turn_id=turn_id, target_id=target_id,
                         browser_id=browser_id, endpoint=endpoint)
    except Exception:
        close_exact(endpoint, target_id, browser_id)
        raise
    return {"targetId": target_id, "browserId": browser_id, "endpoint": endpoint}


def _accept_endpoint(endpoint: str, browser_id: str) -> tuple[str, str]:
    """Loopback HTTP, a signed ``wss`` URL, or the harness locator marker. Never a guessed page."""
    if endpoint == "harness":
        if not isinstance(browser_id, str) or not browser_id.startswith("daemon:"):
            raise RegistryError("browser_id_missing", "daemon connection receipt has no process id")
        valid_id(browser_id[7:])
        return endpoint, browser_id
    if isinstance(endpoint, str) and endpoint.startswith(("http://127.0.0.1:", "http://localhost:",
                                                           "https://127.0.0.1:", "https://localhost:")):
        if not isinstance(browser_id, str) or not browser_id.startswith("/devtools/browser/"):
            raise RegistryError("browser_id_missing", "browser identity is missing")
        return endpoint, browser_id
    from tools.browser_task_cdp import CdpError, validate_cloud_browser_id, validate_cloud_ws
    try:
        return validate_cloud_ws(endpoint), validate_cloud_browser_id(browser_id)
    except CdpError as exc:
        raise RegistryError(exc.code if exc.code != "cdp_endpoint_invalid" else "endpoint_missing", str(exc)) from exc


def active_binding(session_id: str, task_id: str, target_id: str) -> tuple[str, str] | None:
    """``(browser_id, endpoint)`` for an active row, else None. Does not scan the browser."""
    if not all(isinstance(value, str) and value for value in (session_id, task_id, target_id)):
        return None
    try:
        conn = _connect(read_only=True)
    except RegistryError:
        return None
    try:
        row = conn.execute(
            """SELECT browser_id, endpoint FROM created_targets
               WHERE session_id = ? AND task_id = ? AND target_id = ? AND status = 'active'""",
            (session_id, task_id, target_id),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return str(row["browser_id"]), str(row["endpoint"])


def receipt_path(task_id: str) -> Path:
    """Private file the harness writes after cloud autospawn. Not a page list."""
    digest = hashlib.sha256(valid_id(task_id).encode("utf-8")).hexdigest()[:32]
    path = registry_dir() / "receipts" / f"{digest}.txt"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    if not path.exists():
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
    os.chmod(path, 0o600)
    return path


def spool_path(task_id: str) -> Path:
    digest = hashlib.sha256(valid_id(task_id).encode("utf-8")).hexdigest()[:32]
    path = registry_dir() / "spool" / f"{digest}.txt"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.exists():
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
    return path


def _spool_entries(text: str) -> tuple[list[str], list[tuple[str, str]]]:
    """Split spool lines into bare ids and ``target_id<TAB>grant`` pairs.

    Invalid ids are ignored. A tab with an empty grant is bare, not authorized.
    """
    bare: list[str] = []
    granted: list[tuple[str, str]] = []
    for line in text.splitlines():
        raw = line.strip()
        if not raw:
            continue
        grant_text = ""
        tid_text = raw
        if "\t" in raw:
            tid_text, grant_text = raw.split("\t", 1)
            tid_text = tid_text.strip()
            grant_text = grant_text.strip()
        try:
            target_id = valid_id(tid_text)
        except IdentityError:
            continue
        if grant_text:
            granted.append((target_id, grant_text))
        else:
            bare.append(target_id)
    return bare, granted


def register_spooled_text(text: str, *, task_id: str, session_id: str, turn_id: str, browser_id: str, endpoint: str,
                          transport: str = "", daemon_dir: str = "", daemon_stem: str = "", daemon_pid: int = 0,
                          task_grant: str = "") -> None:
    """Register spool text already read by the caller. Does not touch the file.

    Each harness ``target_id<TAB>grant`` line keeps its own grant. One grant is
    never copied onto another id. Bare harness ids cannot share a grant. The
    caller wipes the file before this runs so a grant is not left on disk.
    """
    bare, granted = _spool_entries(text)
    if not bare and not granted:
        return
    harness = _harness_binding(transport, endpoint)
    if harness and (len(bare) > 1 or (bare and granted)):
        raise RegistryError(
            "task_grant_required",
            "harness spool has more than one target and no per-target daemon grant",
        )
    for target_id, grant in granted:
        register_created(task_id=task_id, session_id=session_id, turn_id=turn_id, target_id=target_id,
                         browser_id=browser_id, endpoint=endpoint, transport=transport,
                         daemon_dir=daemon_dir, daemon_stem=daemon_stem, daemon_pid=daemon_pid,
                         task_grant=grant)
    shared = task_grant if harness else ""
    for target_id in bare:
        register_created(task_id=task_id, session_id=session_id, turn_id=turn_id, target_id=target_id,
                         browser_id=browser_id, endpoint=endpoint, transport=transport,
                         daemon_dir=daemon_dir, daemon_stem=daemon_stem, daemon_pid=daemon_pid,
                         task_grant=shared)


def adopt_spool(path: Path, *, task_id: str, session_id: str, turn_id: str, browser_id: str, endpoint: str,
               transport: str = "", daemon_dir: str = "", daemon_stem: str = "", daemon_pid: int = 0,
               task_grant: str = "") -> None:
    """Register ids the tool's own createTarget calls returned. Ignore anything else.

    A harness line may carry its own daemon grant after a tab. That grant is not
    copied onto any other id. Bare harness ids still cannot share one grant, and
    a mix of bare and per-page lines is not written. Loopback lines stay bare
    ids; a client-supplied grant is not stored for them. A refused harness spool
    is left on disk so the caller can see that nothing was authorized.
    """
    if not path.is_file():
        return
    text = path.read_text(encoding="utf-8")
    bare, _granted = _spool_entries(text)
    harness = _harness_binding(transport, endpoint)
    if harness and (len(bare) > 1 or (bare and _granted)):
        raise RegistryError(
            "task_grant_required",
            "harness spool has more than one target and no per-target daemon grant",
        )
    path.write_text("", encoding="utf-8")
    register_spooled_text(text, task_id=task_id, session_id=session_id, turn_id=turn_id,
                          browser_id=browser_id, endpoint=endpoint, transport=transport,
                          daemon_dir=daemon_dir, daemon_stem=daemon_stem, daemon_pid=daemon_pid,
                          task_grant=task_grant)


def _open_gate(blocking_timeout: float | None) -> Any:
    import fcntl

    path = gate_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    gate = os.fdopen(fd, "w")
    try:
        if blocking_timeout is None:
            fcntl.flock(gate, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return gate
        deadline = time.monotonic() + blocking_timeout
        while True:
            try:
                fcntl.flock(gate, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return gate
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    gate.close()
                    return None
                time.sleep(0.05)
    except BlockingIOError:
        gate.close()
        return None
    except Exception:
        gate.close()
        raise


def acquire_exec_gate(timeout_s: float = _LEASE_WAIT_S):
    """Block until the manual lease releases, or return None. Do not run without it."""
    try:
        return _open_gate(timeout_s)
    except RegistryError:
        raise
    except OSError as exc:
        raise RegistryError("gate_unavailable", "browser execution lock is unavailable") from exc


def acquire_lease_gate():
    """Non-blocking gate for a panel takeover. None means the tool still holds it."""
    try:
        return _open_gate(None)
    except RegistryError as exc:
        raise
    except OSError as exc:
        raise RegistryError("gate_unavailable", "browser execution lock is unavailable") from exc


def manual_gate_held() -> bool:
    try:
        path = gate_path()
    except RegistryError:
        return False
    if not path.exists():
        return False
    gate = acquire_lease_gate()
    if gate is None:
        return True
    gate.close()
    return False


def _mark_ended(task_id: str, cleanup_error: str) -> None:
    now = time.time()
    with _DB_LOCK:
        conn = _connect()
        try:
            try:
                grant_rows = conn.execute(
                    "SELECT task_grant FROM created_targets WHERE task_id = ?",
                    (task_id,),
                ).fetchall()
                grants = [str(row[0]) for row in grant_rows if row[0]]
            except sqlite3.OperationalError:
                grants = []
            cleanup_error = _scrub(cleanup_error, grants)
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """UPDATE created_targets SET status = 'ended', cleanup_error = ?, updated_at = ?
                   WHERE task_id = ? AND status != 'ended'""",
                (cleanup_error[:300], now, task_id),
            )
            conn.execute(
                """DELETE FROM created_targets WHERE status = 'ended' AND (task_id, session_id) NOT IN (
                       SELECT task_id, session_id FROM created_targets WHERE status = 'ended'
                       ORDER BY updated_at DESC LIMIT ?
                   )""",
                (_MAX_ENDED,),
            )
            conn.execute("COMMIT")
        finally:
            conn.close()


def _rows_for_task(task_id: str) -> list[sqlite3.Row]:
    conn = _connect(read_only=True)
    try:
        return conn.execute(
            "SELECT * FROM created_targets WHERE task_id = ? AND status != 'ended'",
            (task_id,),
        ).fetchall()
    finally:
        conn.close()


def active_cloud_endpoint(task_id: str) -> str:
    """Signed websocket already stored for this task. Does not read a process environment."""
    try:
        task_id = valid_id(task_id)
        conn = _connect(read_only=True)
    except (RegistryError, IdentityError):
        return ""
    try:
        row = conn.execute(
            """SELECT endpoint FROM created_targets
               WHERE task_id = ? AND status = 'active' AND endpoint LIKE 'wss://%'
               ORDER BY updated_at DESC LIMIT 1""",
            (task_id,),
        ).fetchone()
    finally:
        conn.close()
    return "" if row is None else str(row["endpoint"])


def active_task_targets(task_id: str) -> list[dict[str, Any]]:
    """Active pages this task created. No other task, and no browser page scan."""
    try:
        task_id = valid_id(task_id)
        if not registry_path().exists():
            return []
        conn = _connect(read_only=True)
    except (RegistryError, IdentityError):
        return []
    try:
        rows = conn.execute(
            """SELECT * FROM created_targets
               WHERE task_id = ? AND status = 'active'
               ORDER BY updated_at DESC""",
            (task_id,),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "targetId": str(row["target_id"]),
            "browserId": str(row["browser_id"]),
            "sessionId": str(row["session_id"]),
            "endpoint": str(row["endpoint"]),
            "transport": str(_field(row, "transport") or ""),
            "daemonDir": str(_field(row, "daemon_dir") or ""),
            "daemonStem": str(_field(row, "daemon_stem") or ""),
            "daemonPid": int(_field(row, "daemon_pid") or 0),
        }
        for row in rows
    ]


def task_connection(task_id: str) -> dict[str, Any] | None:
    """Daemon locator for an active task. Empty when this task has no stored receipt."""
    try:
        task_id = valid_id(task_id)
        if not registry_path().exists():
            return None
        conn = _connect(read_only=True)
    except (RegistryError, IdentityError):
        return None
    try:
        row = conn.execute(
            """SELECT * FROM created_targets
               WHERE task_id = ? AND status = 'active'
               ORDER BY updated_at DESC LIMIT 1""",
            (task_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return {
        "endpoint": str(row["endpoint"]),
        "browserId": str(row["browser_id"]),
        "targetId": str(row["target_id"]),
        "transport": str(_field(row, "transport") or ""),
        "daemonDir": str(_field(row, "daemon_dir") or ""),
        "daemonStem": str(_field(row, "daemon_stem") or ""),
        "daemonPid": int(_field(row, "daemon_pid") or 0),
    }


def _close_rows(task_id: str, rows: list[sqlite3.Row]) -> None:
    from tools.browser_task_cdp import close_exact

    failed: list[str] = []
    for row in rows:
        try:
            endpoint = str(row["endpoint"])
            daemon_dir = str(_field(row, "daemon_dir") or "")
            daemon_stem = str(_field(row, "daemon_stem") or "")
            daemon_pid = int(_field(row, "daemon_pid") or 0)
            if daemon_dir and daemon_stem and daemon_pid:
                from tools.browser_task_cdp import close_harness_target
                error = close_harness_target(daemon_dir, daemon_stem, row["target_id"], daemon_pid)
            elif endpoint.startswith("wss://"):
                from tools.browser_supervisor import close_owned_target
                error = close_owned_target(row["task_id"], row["target_id"], row["browser_id"], endpoint)
            elif endpoint == "harness":
                error = f"{row['target_id']} (daemon locator missing)"
            else:
                error = close_exact(endpoint, row["target_id"], row["browser_id"])
        except Exception as exc:
            error = f"{row['target_id']} ({type(exc).__name__})"
        if error:
            failed.append(error)
    cleanup = f"Failed to close targets: {', '.join(failed)}" if failed else ""
    _mark_ended(task_id, cleanup)


def close_owned(task_id: object) -> None:
    """Close pages registered to this task id only. Missing id closes nothing."""
    if not isinstance(task_id, str) or not task_id:
        return
    try:
        task_id = valid_id(task_id)
        rows = _rows_for_task(task_id)
    except RegistryError:
        return
    if not rows:
        return
    key = (str(registry_path()), task_id)
    with _CLEANUP_LOCK:
        if key in _CLEANUP_PENDING:
            return
        if not _CLEANUP_SLOTS.acquire(blocking=False):
            _mark_ended(task_id, "Cleanup capacity exhausted; targets preserved")
            return
        _CLEANUP_PENDING.add(key)

    def finish() -> None:
        with _CLEANUP_LOCK:
            _CLEANUP_PENDING.discard(key)
            _CLEANUP_SLOTS.release()

    try:
        gate = acquire_lease_gate()
    except Exception:
        finish()
        _mark_ended(task_id, "browser execution lock is unavailable; targets preserved")
        return
    if gate is None:
        def deferred() -> None:
            try:
                gate = acquire_exec_gate(_CLEANUP_WAIT_S)
                if gate is None:
                    _mark_ended(task_id, "Manual lease did not release; targets preserved")
                    return
                try:
                    fresh = _rows_for_task(task_id)
                    _close_rows(task_id, fresh)
                finally:
                    gate.close()
            except Exception as exc:
                _mark_ended(task_id, f"Deferred cleanup failed: {type(exc).__name__}")
            finally:
                finish()

        threading.Thread(target=deferred, name="browser-target-cleanup", daemon=True).start()
        return
    try:
        _close_rows(task_id, rows)
    finally:
        gate.close()
        finish()
