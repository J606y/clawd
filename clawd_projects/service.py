"""clawd project management and project memory domain services.

Encapsulates project resolution, session-to-project bindings, workspace directories,
and project-scoped memory store instantiation.
"""

from __future__ import annotations

import hashlib
import inspect
import errno
import logging
import os
import re
import secrets
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from hermes_cli import projects_db as pdb
from hermes_constants import get_hermes_home

from clawd_projects.inheritance import (  # noqa: F401  (re-export)
    ProjectInheritanceError,
    inherit_session_project,
    state_db_profile_home,
)

# Project ids are a single path segment (``p_`` + hex, or a test slug). Reject
# separators and dot-segments so a memory dir cannot escape CLAWD_HOME/projects.
_PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_BUSY_REASONS = frozenset({"session_busy", "unknown_execution"})


_log = logging.getLogger(__name__)


class SessionBusyError(RuntimeError):
    """The session holds a live turn lease, admission, or compression lock."""

    def __init__(self, message: str = "session_busy"):
        super().__init__(message)


class SessionStoreCheckError(RuntimeError):
    """Idle/existence could not be verified. Callers must fail closed."""


class ProjectBindingReleaseError(RuntimeError):
    """Project membership could not be released after the session row was deleted.

    ``projects.db`` and ``state.db`` are separate databases. Callers must not
    report the delete as successful when this is raised.
    """

    def __init__(
        self, message: str, *, session_ids: Optional[list[str]] = None, live_rows: bool = False,
    ):
        super().__init__(message)
        self.session_ids = list(session_ids or [])
        # True when a row may still exist, so callers must not drop its routes or binding.
        self.live_rows = bool(live_rows)


class BindingTokenUnsupported(RuntimeError):
    """A write token was required and the projects DB cannot honor it yet.

    Do not treat this as "no token". Dropping ``write_token`` or
    ``expected_write_token`` would skip the ABA check the DB owner is adding.
    """


@dataclass(frozen=True)
class BindingWriteResult:
    """Bind outcome. ``write_token`` is DB read-back, never a token this module mints."""

    applied: bool
    write_token: Optional[str] = None


def get_clawd_projects_dir(hermes_home: Optional[Path] = None) -> Path:
    """Return the base projects directory for clawd (CLAWD_HOME/projects or $HERMES_HOME/projects)."""
    home = hermes_home or get_hermes_home()
    return home / "projects"


def _safe_project_id(project_id: str) -> str:
    pid = str(project_id or "").strip()
    if (
        not pid
        or pid in {".", ".."}
        or "/" in pid
        or "\\" in pid
        or "\0" in pid
        or ".." in pid
        or not _PROJECT_ID_RE.fullmatch(pid)
    ):
        raise ValueError("project_id must be a single safe path segment")
    return pid


def get_project_memory_dir(project_id: str, hermes_home: Optional[Path] = None) -> Path:
    """Return CLAWD_HOME/projects/<id>/memory/, rejecting path escape."""
    pid = _safe_project_id(project_id)
    base = get_clawd_projects_dir(hermes_home).resolve()
    mem = (base / pid / "memory").resolve()
    if os.path.commonpath([str(base), str(mem)]) != str(base):
        raise ValueError("project memory path escapes the projects directory")
    return mem


def prepare_project_directory(primary_path: str, *, create_directory: bool = False) -> str:
    """Validate a project workspace directory.

    Relative paths and ``~`` are rejected rather than resolved. Missing directories
    are created only when ``create_directory`` is explicitly true. An existing file
    is never treated as a workspace.
    """
    raw = str(primary_path or "").strip()
    if not raw:
        raise ValueError("primary_path is required")
    if "\0" in raw or raw.startswith("~") or not os.path.isabs(raw):
        raise ValueError("primary_path must be an absolute path; relative paths are not resolved")
    parts = Path(raw).parts
    if ".." in parts:
        raise ValueError("primary_path must not contain '..'")
    path = Path(raw)
    if path.exists():
        if not path.is_dir():
            raise ValueError("primary_path must be a directory, not a file")
        return str(path)
    if not create_directory:
        raise ValueError("primary_path does not exist; pass create_directory=true to create it")
    path.mkdir(parents=True, exist_ok=True)
    if not path.is_dir():
        raise ValueError("primary_path must be a directory, not a file")
    return str(path)


def _contained(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
    except (OSError, ValueError):
        return False
    return True


def _workspace_root(home: Path) -> Path:
    """``<profile>/projects/workspaces``. Not ``<profile>/projects/<id>/memory``."""
    if not home.is_absolute():
        raise ValueError("profile home must be absolute; workspace was not allocated")
    projects = home / "projects"
    root = projects / "workspaces"
    if projects.is_symlink() and not _contained(projects, home):
        raise ValueError("projects directory escapes the profile home")
    if root.exists() and (root.is_symlink() or not _contained(root, projects)):
        raise ValueError("workspace root escapes the projects directory")
    if not _contained(root, home):
        raise ValueError("workspace root escapes the profile home")
    return root


def _reject_memory_workspace(path: Path, home: Path) -> None:
    projects = (home / "projects").resolve()
    try:
        rel = path.resolve().relative_to(projects)
    except (OSError, ValueError) as exc:
        raise ValueError("workspace path escapes the projects directory") from exc
    if "memory" in rel.parts or path.name == "memory":
        raise ValueError("workspace must not be a project memory directory")


def allocate_project_workspace(hermes_home: Optional[Path] = None) -> str:
    """Create a new empty workspace for one project. Never reuses or overwrites.

    Concurrent callers get distinct leaves (``mkdir`` without ``exist_ok``).
    An existing directory, file, or symlink is left untouched.
    """
    home = hermes_home or get_hermes_home()
    root = _workspace_root(home)
    root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink() or not root.is_dir() or not _contained(root, home / "projects"):
        raise ValueError("workspace root is not a real directory under projects")
    for _ in range(16):
        leaf = root / ("w_" + secrets.token_hex(8))
        try:
            leaf.mkdir(mode=0o700, exist_ok=False)
        except FileExistsError:
            continue
        if leaf.is_symlink() or not leaf.is_dir() or not _contained(leaf, root):
            raise ValueError("allocated workspace escaped the projects directory")
        _reject_memory_workspace(leaf, home)
        if any(leaf.iterdir()):
            continue
        return str(leaf)
    raise RuntimeError("could not allocate a unique project workspace")


def system_workspace_for_folder(home: Path, folder_id: str) -> str:
    """Stable system path for one legacy folder. Does not create or guess a user dir."""
    fid = str(folder_id or "")
    if not fid.strip() or "\0" in fid:
        raise ValueError("folder_id is required")
    segment = "f_" + hashlib.sha256(fid.encode("utf-8")).hexdigest()[:32]
    path = _workspace_root(home) / segment
    if path.name != segment or not _contained(path, home / "projects"):
        raise ValueError("folder workspace escaped the projects directory")
    _reject_memory_workspace(path, home)
    return str(path)


def get_project_by_id(project_id: str, hermes_home: Optional[Path] = None) -> Optional[pdb.Project]:
    """Look up a project by id/slug in the current or specified profile home."""
    db_path = (hermes_home or get_hermes_home()) / "projects.db"
    with pdb.connect_closing(db_path) as conn:
        return pdb.get_project(conn, project_id)


def resolve_session_project(session_id: str, hermes_home: Optional[Path] = None) -> Optional[pdb.Project]:
    """Return the Project bound to session_id, or None if unassigned (general chat)."""
    sid = str(session_id or "").strip()
    if not sid:
        return None
    db_path = (hermes_home or get_hermes_home()) / "projects.db"
    with pdb.connect_closing(db_path) as conn:
        pid = pdb.get_session_project_id(conn, sid)
        if not pid:
            return None
        return pdb.get_project(conn, pid)


def _projects_db_path(hermes_home: Optional[Path]) -> Path:
    return (hermes_home or get_hermes_home()) / "projects.db"


def _binding_kwargs(fn: Any, supplied: dict[str, str]) -> dict[str, str]:
    """Forward token kwargs the live DB callable accepts. Do not guess a signature.

    A supplied token the callable cannot take is a hard refusal. Calling
    without it would unbind or overwrite without the ABA condition.
    """
    if not supplied:
        return {}
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError) as exc:
        raise BindingTokenUnsupported(
            "projects db bind signature cannot be verified; refusing to drop write_token"
        ) from exc
    accepts_any = any(item.kind is inspect.Parameter.VAR_KEYWORD for item in signature.parameters.values())
    forwarded: dict[str, str] = {}
    for name, value in supplied.items():
        if accepts_any or name in signature.parameters:
            forwarded[name] = value
            continue
        raise BindingTokenUnsupported(
            f"projects db bind does not accept {name}; refusing to drop it"
        )
    return forwarded


def _binding_result(result: Any) -> BindingWriteResult:
    """Normalize a bool (current DB) or a future read-back that carries write_token."""
    if isinstance(result, BindingWriteResult):
        return result
    if isinstance(result, dict):
        token = result.get("write_token")
        applied = result.get("applied", result.get("success", result.get("bound", True)))
        return BindingWriteResult(
            applied=bool(applied),
            write_token=None if token is None else str(token),
        )
    token = getattr(result, "write_token", None)
    if token is not None or any(hasattr(result, name) for name in ("applied", "success")):
        applied = getattr(result, "applied", getattr(result, "success", bool(result)))
        return BindingWriteResult(applied=bool(applied), write_token=None if token is None else str(token))
    return BindingWriteResult(applied=bool(result), write_token=None)


def _write_binding(
    session_id: str,
    project_id: Optional[str],
    hermes_home: Optional[Path],
    *,
    write_token: Optional[str] = None,
    expected_write_token: Optional[str] = None,
) -> BindingWriteResult:
    supplied = {
        name: value
        for name, value in (("write_token", write_token), ("expected_write_token", expected_write_token))
        if value is not None
    }
    with pdb.connect_closing(_projects_db_path(hermes_home)) as conn:
        kwargs = _binding_kwargs(pdb.bind_session_project, supplied)
        return _binding_result(pdb.bind_session_project(conn, session_id, project_id, **kwargs))


def _session_exists(conn: Any, session_id: str) -> bool:
    row = conn.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone()
    return row is not None


def _classify_idle_error(exc: BaseException) -> None:
    """Map a core idle-check failure. Unknown errors are not 'idle'."""
    from hermes_state_mutation_guards import MUTATION_GUARD_REFUSALS
    from hermes_state_runtime import RuntimeStoreError

    if isinstance(exc, MUTATION_GUARD_REFUSALS):
        raise SessionBusyError("session_busy") from exc
    if isinstance(exc, RuntimeStoreError):
        if exc.reason in _BUSY_REASONS:
            raise SessionBusyError("session_busy") from exc
        raise SessionStoreCheckError(f"idle check refused: {exc.reason}") from exc
    raise SessionStoreCheckError("idle check failed") from exc


def is_session_busy(session_db: Any, session_id: str) -> bool:
    """True when the core idle guard refuses the session.

    Uses ``require_idle`` exception types (``RuntimeStoreError`` reason
    ``session_busy`` / ``unknown_execution``, plus turn-lease and compression
    errors). Unknown failures raise ``SessionStoreCheckError`` (fail closed).
    This does not prove the session row exists.
    """
    sid = str(session_id or "").strip()
    if session_db is None or not sid or not hasattr(session_db, "_execute_write"):
        raise SessionStoreCheckError("session store unavailable")
    from hermes_state_mutation_guards import require_idle

    try:
        session_db._execute_write(lambda conn: require_idle(session_db, conn, [sid]))
    except SessionBusyError:
        return True
    except Exception as exc:
        try:
            _classify_idle_error(exc)
        except SessionBusyError:
            return True
        raise
    return False


def commit_session_binding(
    session_id: str,
    project_id: Optional[str],
    hermes_home: Optional[Path] = None,
    session_db: Optional[Any] = None,
    force: bool = False,
    *,
    write_token: Optional[str] = None,
    expected_write_token: Optional[str] = None,
) -> BindingWriteResult:
    """Bind or unbind a session. Same idle gate as before; tokens are forwarded, not minted.

    Without ``force``, the session row must exist and ``require_idle`` must pass.
    The idle check and the projects.db write run inside the state store's write
    callback so a concurrent turn-lease write blocks on the same lock. That is
    not a single cross-database commit: do not treat the pair as atomic.

    ``force`` is only for a child session id reserved before its state row exists.
    It still refuses to copy a project id into a different profile — callers must
    pass that profile's ``hermes_home`` and must not call this with a foreign id.

    ``write_token`` and ``expected_write_token`` are optional. When either is set
    and the live ``bind_session_project`` does not accept that parameter, this
    raises ``BindingTokenUnsupported`` and does not write. ``expected_write_token``
    is the conditional unbind/rollback check; it is never dropped.
    """
    sid = str(session_id or "").strip()
    if not sid:
        raise ValueError("session_id must not be empty")
    token_kwargs = {"write_token": write_token, "expected_write_token": expected_write_token}
    if force:
        return _write_binding(sid, project_id, hermes_home, **token_kwargs)
    if session_db is None or not hasattr(session_db, "_execute_write"):
        raise SessionStoreCheckError("session store unavailable; refusing to bind")

    from hermes_state_mutation_guards import require_idle

    def _guard(conn: Any) -> BindingWriteResult:
        if not _session_exists(conn, sid):
            raise LookupError(f"session not found: {sid}")
        require_idle(session_db, conn, [sid])
        # Held under the state write lock. Not a cross-db atomic transaction.
        return _write_binding(sid, project_id, hermes_home, **token_kwargs)

    try:
        return session_db._execute_write(_guard)
    except LookupError:
        raise
    except BindingTokenUnsupported:
        raise
    except ValueError as exc:
        from hermes_state_runtime import RuntimeStoreError
        if not isinstance(exc, RuntimeStoreError):
            raise
        _classify_idle_error(exc)
    except (SessionBusyError, SessionStoreCheckError):
        raise
    except Exception as exc:
        _classify_idle_error(exc)
    raise SessionStoreCheckError("idle check failed")


def bind_session_to_project(
    session_id: str,
    project_id: Optional[str],
    hermes_home: Optional[Path] = None,
    session_db: Optional[Any] = None,
    force: bool = False,
    *,
    write_token: Optional[str] = None,
    expected_write_token: Optional[str] = None,
) -> bool:
    """Bind or unbind. Returns the applied flag. Use ``commit_session_binding`` to read ``write_token``."""
    return commit_session_binding(
        session_id,
        project_id,
        hermes_home=hermes_home,
        session_db=session_db,
        force=force,
        write_token=write_token,
        expected_write_token=expected_write_token,
    ).applied


def release_project_bindings_after_delete(
    session_ids: list[str],
    *,
    state_db_path: Path,
    session_db: Any,
) -> None:
    """Unbind ids whose session rows are already gone.

    Missing ``projects.db`` means there is no membership. An already-absent
    binding is not an error. A write failure raises ``ProjectBindingReleaseError``.

    Writes nothing if any id still has a session row, so a live turn cannot be
    unbound around the delete guard. Profile home is the ``state.db`` parent,
    not the process home. This is not a cross-database transaction.
    """
    ids = [str(sid).strip() for sid in session_ids if str(sid or "").strip()]
    if not ids:
        return
    if session_db is None or not hasattr(session_db, "_execute_write"):
        raise ProjectBindingReleaseError(
            "session store unavailable; refusing to release project bindings",
            session_ids=ids,
            live_rows=True,
        )
    from clawd_projects.inheritance import ProjectInheritanceError, trusted_state_db_home

    try:
        home = trusted_state_db_home(state_db_path)
    except ProjectInheritanceError as exc:
        raise ProjectBindingReleaseError(
            "project profile could not be taken from state.db; bindings not released",
            session_ids=ids,
            live_rows=True,
        ) from exc

    present: list[str] = []

    def _check(conn: Any) -> None:
        for sid in ids:
            if _session_exists(conn, sid):
                present.append(sid)

    try:
        session_db._execute_write(_check)
    except ProjectBindingReleaseError:
        raise
    except Exception as exc:
        raise ProjectBindingReleaseError(
            "could not prove session rows are gone; project bindings not released",
            session_ids=ids,
            live_rows=True,
        ) from exc
    if present:
        raise ProjectBindingReleaseError(
            "refusing to release project bindings while session rows still exist",
            session_ids=present,
            live_rows=True,
        )

    db_path = home / "projects.db"
    if not db_path.is_file():
        return
    try:
        with pdb.connect_closing(db_path) as conn:
            for sid in ids:
                pdb.bind_session_project(conn, sid, None)
    except Exception as exc:
        raise ProjectBindingReleaseError(
            "project binding release failed; delete must not be reported as successful",
            session_ids=ids,
        ) from exc


def split_absent_session_ids(
    session_ids: list[str], *, session_db: Any,
) -> tuple[list[str], list[str]]:
    """Split recorded ids into ``(gone, still_present)`` under the state write lock.

    Does not unbind. Still-present ids, busy or not, stay out of the gone list.
    Profile is the session store the caller already resolved, not the process home.
    """
    ids = [str(sid).strip() for sid in session_ids if str(sid or "").strip()]
    if not ids:
        return [], []
    if session_db is None or not hasattr(session_db, "_execute_write"):
        raise ProjectBindingReleaseError(
            "session store unavailable; refusing to release project bindings",
            session_ids=ids,
            live_rows=True,
        )
    gone: list[str] = []
    present: list[str] = []

    def _split(conn: Any) -> None:
        for sid in ids:
            if _session_exists(conn, sid):
                present.append(sid)
            else:
                gone.append(sid)

    try:
        session_db._execute_write(_split)
    except ProjectBindingReleaseError:
        raise
    except Exception as exc:
        raise ProjectBindingReleaseError(
            "could not prove session rows are gone; project bindings not released",
            session_ids=ids,
            live_rows=True,
        ) from exc
    return gone, present


def read_session_write_token(session_id: str, hermes_home: Optional[Path] = None) -> Optional[str]:
    """Read the binding token back from the projects DB.

    The reserved reader is ``get_session_write_token(conn, session_id)``.
    The current projects DB exposes that signature. ``None`` is the reader's
    value for an unbound session or a legacy NULL row. It is not proof the
    token was checked for rollback, and it is not used when the attribute or
    signature is missing: that still raises ``BindingTokenUnsupported``.
    """
    reader = getattr(pdb, "get_session_write_token", None)
    if not callable(reader):
        raise BindingTokenUnsupported(
            "projects db does not expose get_session_write_token; refusing to report write_token as absent"
        )
    try:
        signature = inspect.signature(reader)
    except (TypeError, ValueError) as exc:
        raise BindingTokenUnsupported("write_token reader signature cannot be verified") from exc
    if "session_id" not in signature.parameters and not any(
        item.kind is inspect.Parameter.VAR_POSITIONAL for item in signature.parameters.values()
    ):
        raise BindingTokenUnsupported("write_token reader does not take session_id")
    sid = str(session_id or "").strip()
    if not sid:
        raise ValueError("session_id must not be empty")
    with pdb.connect_closing(_projects_db_path(hermes_home)) as conn:
        params = signature.parameters
        if "conn" in params or "connection" in params:
            token = reader(conn, sid)
        elif any(item.kind is inspect.Parameter.VAR_POSITIONAL for item in params.values()):
            token = reader(conn, sid)
        else:
            raise BindingTokenUnsupported("write_token reader parameter names are not the reserved contract")
    return None if token is None else str(token)


def get_project_memory_store(
    project_id: str,
    *,
    memory_char_limit: int = 2200,
    hermes_home: Optional[Path] = None,
    memory_enabled: bool = True,
) -> Any:
    """Instantiate a MemoryStore for one profile's project memory dir.

    No process-wide cache. ``profile_home_key`` stamps the home this object
    reads; a later lookup must pass that same home and must not reuse a store
    built for another profile.
    """
    from hermes_constants import hermes_home_key
    from tools.memory_tool import MemoryStore

    home = hermes_home or get_hermes_home()
    mem_dir = get_project_memory_dir(project_id, hermes_home=home)
    store = MemoryStore(
        memory_char_limit=memory_char_limit,
        user_char_limit=1375,
        memory_enabled=memory_enabled,
        user_profile_enabled=False,  # Project memory never holds USER profile
        memory_dir=mem_dir,
    )
    store.profile_home_key = hermes_home_key(home)
    store.memory_writes_allowed = True
    store.load_from_disk()
    return store

_INSTRUCTION_FILE = "INSTRUCTIONS.md"
_INSTRUCTION_LIMIT = 8000
_ARCHIVE_LIMIT = 100_000
_ARCHIVE_LIST_LIMIT = 40
_FORBIDDEN_WORKSPACE = {
    "/",
    "/root",
    "/root/.hermes",
    "/root/.clawd",
    "/usr",
    "/etc",
    "/bin",
    "/tmp",
    "/var",
    "/home",
    "/opt",
}


def _reject_threat(content: str) -> None:
    from tools.threat_patterns import first_threat_message

    if first_threat_message(content, scope="strict"):
        raise ValueError("内容未通过安全检查，未写入这个项目")


def project_instructions_path(project_id: str, hermes_home: Path) -> Path:
    """Instructions live beside this project's memory, never in global MEMORY.md."""
    mem = get_project_memory_dir(project_id, hermes_home=hermes_home).resolve()
    path = (mem / _INSTRUCTION_FILE).resolve()
    if path.parent != mem or path.name != _INSTRUCTION_FILE:
        raise ValueError("项目指示路径越界，未写入")
    return path


def read_project_instructions(project_id: str, hermes_home: Path) -> str:
    path = project_instructions_path(project_id, hermes_home)
    if not path.exists():
        return ""
    if path.is_symlink() or not path.is_file():
        raise ValueError("项目指示不是普通文件，已拒绝读取")
    text = path.read_text(encoding="utf-8").strip()
    if len(text) > _INSTRUCTION_LIMIT:
        text = text[:_INSTRUCTION_LIMIT].strip()
    if text:
        _reject_threat(text)
    return text


def write_project_instructions(project_id: str, hermes_home: Path, content: str) -> str:
    if not isinstance(content, str):
        raise ValueError("指示必须是文本，未写入")
    text = content.replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(text) > _INSTRUCTION_LIMIT:
        raise ValueError("指示超过长度上限，未写入")
    if text:
        _reject_threat(text)
    path = project_instructions_path(project_id, hermes_home)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise ValueError("拒绝跟随项目指示的符号链接")
    if not text:
        if path.is_file() and not path.is_symlink():
            path.unlink()
        return ""
    # Unique temp in the same directory so os.replace is atomic and two writers
    # cannot tear one INSTRUCTIONS.md.tmp into each other. This writes the file
    # only. A sealed ProjectMemoryStore snapshot is not reloaded here.
    token = secrets.token_hex(8)
    tmp = path.with_name(f".{_INSTRUCTION_FILE}.{token}.tmp")
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if nofollow:
        flags |= nofollow
    fd = os.open(tmp, flags, 0o600)
    sent = 0
    payload = (text + "\n").encode("utf-8")
    try:
        while sent < len(payload):
            wrote = os.write(fd, payload[sent:])
            if wrote <= 0:
                raise OSError("short write")
            sent += wrote
        os.fsync(fd)
    except Exception:
        os.close(fd)
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    os.close(fd)
    try:
        if path.is_symlink():
            raise ValueError("拒绝跟随项目指示的符号链接")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return text


def _archive_name(name: str) -> str:
    raw = str(name or "").strip()
    if not raw or raw in {".", ".."} or raw.startswith("."):
        raise ValueError("档案名必须是工作目录中的单个文件名")
    if "/" in raw or "\\" in raw or "\x00" in raw or ".." in raw:
        raise ValueError("档案名不能是路径")
    if len(raw) > 80:
        raise ValueError("档案名过长，未写入")
    return raw


def _project_workspace(project: pdb.Project, hermes_home: Path) -> Path:
    raw = str(getattr(project, "primary_path", "") or "").strip()
    if not raw.startswith("/") or "~" in raw or "\x00" in raw or ".." in raw.split("/"):
        raise ValueError("项目没有可用的工作目录，未猜测路径")
    raw_path = Path(raw)
    if raw_path.is_symlink():
        raise ValueError("项目工作目录不可用，未写入")
    root = raw_path.resolve()
    if str(root) in _FORBIDDEN_WORKSPACE or root.is_symlink() or not root.is_dir():
        raise ValueError("项目工作目录不可用，未写入")
    home = Path(hermes_home).resolve()
    memories = (home / "memories").resolve()
    if root == home or root == memories or _contained(root, memories):
        raise ValueError("拒绝把档案写入全局记忆目录")
    hermes_data = Path("/root/.hermes")
    if root == hermes_data or _contained(root, hermes_data):
        raise ValueError("拒绝写入其他产品的数据目录")
    mem = get_project_memory_dir(project.id, hermes_home=home).resolve()
    if root == mem or _contained(root, mem) or _contained(mem, root):
        raise ValueError("拒绝把档案写入项目记忆目录")
    return root


def _archive_nofollow() -> int:
    flag = getattr(os, "O_NOFOLLOW", 0)
    if not flag:
        raise ValueError("无法在不跟随符号链接的情况下写入档案")
    return flag


def _open_archive_dir(root: Path) -> int:
    """Open the workspace directory without following its final symlink.

    The returned fd is the identity used for every later create, replace, and
    cleanup. A path swap between lstat and open is a refusal, not a write.
    """
    nofollow = _archive_nofollow()
    cloexec = getattr(os, "O_CLOEXEC", 0)
    try:
        expected = os.lstat(root)
    except OSError as exc:
        raise ValueError("项目工作目录不可用，未写入") from exc
    if stat.S_ISLNK(expected.st_mode) or not stat.S_ISDIR(expected.st_mode):
        raise ValueError("项目工作目录不可用，未写入")
    fd = os.open(os.fspath(root), os.O_RDONLY | os.O_DIRECTORY | nofollow | cloexec)
    try:
        got = os.fstat(fd)
    except OSError:
        os.close(fd)
        raise
    if got.st_dev != expected.st_dev or got.st_ino != expected.st_ino or not stat.S_ISDIR(got.st_mode):
        os.close(fd)
        raise ValueError("项目工作目录身份已变化，未写入")
    return fd


def _dest_stat(dir_fd: int, filename: str) -> os.stat_result | None:
    try:
        return os.stat(filename, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _reject_dest(info: os.stat_result | None, *, replace: bool) -> None:
    if info is None:
        return
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ValueError("拒绝覆盖符号链接或非文件")
    if replace is not True:
        raise ValueError("同名档案已存在，未覆盖")


def _release_archive(
    fd: int | None,
    dir_fd: int | None,
    tmp_name: str | None,
    *,
    drop_tmp: bool,
) -> BaseException | None:
    """Close owned fds and unlink only this call's temp name. Never the destination."""
    first: BaseException | None = None
    if fd is not None:
        try:
            os.close(fd)
        except OSError as exc:
            first = exc
    if drop_tmp and tmp_name and dir_fd is not None:
        if "/" in tmp_name or tmp_name in {".", ".."} or not tmp_name.startswith(".archive-"):
            first = first or OSError("拒绝清理非本次临时档案")
        else:
            try:
                os.unlink(tmp_name, dir_fd=dir_fd)
            except FileNotFoundError:
                pass
            except OSError as exc:
                if first is None:
                    first = exc
                else:
                    _log.error("archive temp unlink failed after another cleanup error")
    if dir_fd is not None:
        try:
            os.close(dir_fd)
        except OSError as exc:
            if first is None:
                first = exc
            else:
                _log.error("archive directory fd close failed after another cleanup error")
    return first


def list_project_archives(project: Optional[pdb.Project], hermes_home: Path) -> list[dict[str, str]]:
    """Real archive names. A read error raises; [] is only a truly empty directory."""
    if project is None:
        raise ValueError("项目不存在，未读取档案")
    root = _project_workspace(project, hermes_home)
    try:
        children = sorted(root.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        raise OSError("项目档案无法读取，未当作空列表") from exc
    rows: list[dict[str, str]] = []
    for child in children:
        if child.name.startswith(".") or child.name.endswith(".tmp"):
            continue
        if child.parent != root:
            raise OSError("项目档案无法读取，未当作空列表")
        try:
            info = child.lstat()
        except OSError as exc:
            raise OSError("项目档案无法读取，未当作空列表") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            continue
        rows.append({"name": child.name})
        if len(rows) >= _ARCHIVE_LIST_LIMIT:
            break
    return rows


def write_project_archive(
    project: pdb.Project,
    name: str,
    content: str,
    hermes_home: Path,
    *,
    replace: bool = False,
) -> str:
    if not isinstance(content, str):
        raise ValueError("档案必须是文本，未写入")
    text = content.replace("\r\n", "\n").replace("\r", "\n")
    if len(text) > _ARCHIVE_LIMIT:
        raise ValueError("档案超过长度上限，未写入")
    if text:
        _reject_threat(text)
    filename = _archive_name(name)
    if "/" in filename or "\\" in filename or "\x00" in filename or filename.startswith("."):
        raise ValueError("档案名不能是路径")
    root = _project_workspace(project, hermes_home)
    payload = text.encode("utf-8")
    dir_fd: int | None = None
    fd: int | None = None
    tmp_name: str | None = None
    owned_tmp = False
    try:
        dir_fd = _open_archive_dir(root)
        _reject_dest(_dest_stat(dir_fd, filename), replace=replace)
        tmp_name = f".archive-{secrets.token_hex(16)}.tmp"
        if tmp_name == filename or "/" in tmp_name or not tmp_name.startswith(".archive-"):
            raise ValueError("档案临时名无效，未写入")
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | _archive_nofollow()
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        fd = os.open(tmp_name, flags, 0o600, dir_fd=dir_fd)
        owned_tmp = True
        os.fchmod(fd, 0o600)
        sent = 0
        while sent < len(payload):
            wrote = os.write(fd, payload[sent:])
            if wrote <= 0:
                raise OSError("档案短写，未发布")
            sent += wrote
        if sent != len(payload):
            raise OSError("档案短写，未发布")
        os.fsync(fd)
        os.fchmod(fd, 0o600)
        written = os.fstat(fd)
        if not stat.S_ISREG(written.st_mode):
            raise ValueError("拒绝跟随符号链接")
        again = _dest_stat(dir_fd, filename)
        _reject_dest(again, replace=replace)
        try:
            if replace is True and again is not None:
                os.rename(tmp_name, filename, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
            else:
                os.link(
                    tmp_name,
                    filename,
                    src_dir_fd=dir_fd,
                    dst_dir_fd=dir_fd,
                    follow_symlinks=False,
                )
        except FileExistsError as exc:
            raise ValueError("同名档案已存在，未覆盖") from exc
        verify_flags = os.O_RDONLY | _archive_nofollow()
        if hasattr(os, "O_CLOEXEC"):
            verify_flags |= os.O_CLOEXEC
        try:
            verify = os.open(filename, verify_flags, dir_fd=dir_fd)
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise ValueError("拒绝跟随符号链接") from exc
            raise
        try:
            got = os.fstat(verify)
            if (
                not stat.S_ISREG(got.st_mode)
                or got.st_dev != written.st_dev
                or got.st_ino != written.st_ino
            ):
                raise ValueError("拒绝跟随符号链接")
            os.fchmod(verify, 0o600)
        finally:
            os.close(verify)
        return filename
    finally:
        cleanup_error = _release_archive(fd, dir_fd, tmp_name, drop_tmp=owned_tmp)
        pending = sys.exc_info()[1]
        if cleanup_error is not None and pending is None:
            raise cleanup_error
        if cleanup_error is not None:
            _log.error("archive cleanup failed after %s", type(pending).__name__)
