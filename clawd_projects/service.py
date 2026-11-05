"""clawd project management and project memory domain services.

Encapsulates project resolution, session-to-project bindings, workspace directories,
and project-scoped memory store instantiation.
"""

from __future__ import annotations

import inspect
import os
import re
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


class SessionBusyError(RuntimeError):
    """The session holds a live turn lease, admission, or compression lock."""

    def __init__(self, message: str = "session_busy"):
        super().__init__(message)


class SessionStoreCheckError(RuntimeError):
    """Idle/existence could not be verified. Callers must fail closed."""


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
