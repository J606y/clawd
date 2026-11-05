"""Snapshot a session's project binding onto a child before the copy is released.

``projects.db`` and ``state.db`` are separate databases. This write is not a
cross-database transaction and must not be described as one. A crash after
``projects.db`` commits and before the state commit leaves an orphan
``project_sessions`` row for an id with no session. That is not a runnable copy.
A retry of the same child id keeps a row this call already wrote and does not
re-read the parent. Callers must not invoke the copy again for a session row
that was already published: a child with no project row is indistinguishable
from "not yet snapshotted", so a second call would copy the parent's current
project onto an explicit unbind.

The copy is the parent's current ``project_sessions`` row only. Ancestors are
not walked. An explicit unbind (no row) stays unbound on that first copy. A
later change to the parent's project does not rewrite a child that already has
its own row.

Profile location is the parent directory of the ``state.db`` file being
written. Process home, URL, and ``parent_session_id`` ancestors are not
consulted. Compensation deletes only a ``write_token`` this call just wrote,
and only when session, project, and token still match. It does not delete
other sessions, consumption rows, or a binding this call did not write.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from hermes_cli import projects_db as pdb


class ProjectInheritanceError(RuntimeError):
    """The parent's binding could not be proven and copied. Do not release the child."""


def state_db_profile_home(db_path: Path) -> Path:
    """Profile home that owns ``<home>/state.db``.

    Do not use ``get_hermes_home()``. A multiplexed gateway, a test override, or
    a sidecar whose import-time ``HERMES_HOME`` differs from the file it is
    writing would bind the wrong profile and release an unbound copy.
    """
    return Path(db_path).expanduser().resolve().parent


def trusted_state_db_home(db_path: Path) -> Path:
    """Profile home proved by a real ``state.db`` path.

    The file name must be ``state.db`` and the file must already exist. A
    missing path, another database, a process home, or a URL is not identity.
    """
    path = Path(db_path).expanduser()
    if path.name != "state.db" or not path.is_file():
        raise ProjectInheritanceError(
            "project profile is taken only from an existing state.db file; "
            "refusing to guess from process home, URL, or ancestors"
        )
    return state_db_profile_home(path)


@dataclass(frozen=True)
class ChildProjectCopy:
    """One snapshot attempt.

    ``write_token`` is set only when this call inserted the binding. That is
    the only token a caller may conditionally release. An existing snapshot,
    an ordinary parent, or a cross-profile ordinary parent has ``wrote=False``
    and ``write_token=None``. Those rows are not this call's to delete.
    """

    project_id: Optional[str]
    write_token: Optional[str] = None
    wrote: bool = False


def _home_key(path: Path) -> str:
    from hermes_constants import hermes_home_key

    return hermes_home_key(path)


def _read_binding(home: Path, session_id: str) -> Optional[str]:
    db_path = Path(home) / "projects.db"
    if not db_path.is_file():
        return None
    try:
        with pdb.connect_closing(db_path) as conn:
            return pdb.get_session_project_id(conn, session_id)
    except Exception as exc:
        raise ProjectInheritanceError(
            f"could not read project binding for {session_id} in {home}"
        ) from exc


def release_owned_child_binding(
    child_session_id: str,
    *,
    state_db_path: Path,
    project_id: str,
    write_token: str,
) -> bool:
    """Conditional release of a token this call proved it wrote.

    ``project_id`` is the expected current project, not a new target and not
    ``None``. The delete matches session, project, and token together. A
    mismatch deletes nothing. Consumption rows are left in place. This does
    not make the surrounding state commit atomic.
    """
    home = trusted_state_db_home(state_db_path)
    sid = str(child_session_id or "").strip()
    pid = str(project_id or "").strip()
    token = str(write_token or "").strip()
    if not sid or not pid or not token:
        raise ProjectInheritanceError(
            "conditional release requires the session, project, and write_token proved by this call"
        )
    db_path = home / "projects.db"
    if not db_path.is_file():
        raise ProjectInheritanceError(
            "cannot conditionally release a binding whose projects.db is missing"
        )
    try:
        with pdb.connect_closing(db_path) as conn:
            result = pdb.bind_session_project(
                conn, sid, pid, expected_write_token=token,
            )
    except ProjectInheritanceError:
        raise
    except Exception as exc:
        raise ProjectInheritanceError(
            f"conditional release failed for {sid}; binding was not reported as removed"
        ) from exc
    return bool(getattr(result, "applied", result))


def _release_owned_or_chain(
    child_id: str,
    state_db_path: Path,
    project_id: str,
    write_token: Optional[str],
) -> None:
    """Conditional release. A release failure is not turned into success."""
    if not write_token or not project_id:
        return
    if not Path(state_db_path).expanduser().is_file():
        raise ProjectInheritanceError(
            "cannot conditionally release without the state.db that proved this profile"
        )
    release_owned_child_binding(
        child_id,
        state_db_path=state_db_path,
        project_id=project_id,
        write_token=write_token,
    )


def _copy_parent_binding(
    parent_session_id: str,
    child_session_id: str,
    *,
    hermes_home: Path,
    parent_home: Optional[Path] = None,
    state_db_path: Optional[Path] = None,
) -> ChildProjectCopy:
    parent_id = str(parent_session_id or "").strip()
    child_id = str(child_session_id or "").strip()
    if not parent_id or not child_id:
        raise ProjectInheritanceError("parent and child session ids are required")
    if parent_id == child_id:
        raise ProjectInheritanceError("refusing to inherit a session onto itself")

    child_home = Path(hermes_home)
    source_home = Path(parent_home) if parent_home is not None else child_home
    if _home_key(source_home) != _home_key(child_home):
        if _read_binding(source_home, parent_id):
            raise ProjectInheritanceError(
                "cross-profile project inheritance is refused; child not released"
            )
        return ChildProjectCopy(project_id=None)

    db_path = child_home / "projects.db"
    if not db_path.is_file():
        return ChildProjectCopy(project_id=None)

    release_path = Path(state_db_path) if state_db_path is not None else (child_home / "state.db")
    wrote = False
    token: Optional[str] = None
    parent_pid: Optional[str] = None
    try:
        with pdb.connect_closing(db_path) as conn:
            existing = pdb.get_session_project_id(conn, child_id)
            if existing:
                return ChildProjectCopy(project_id=existing)
            parent_pid = pdb.get_session_project_id(conn, parent_id)
            if not parent_pid:
                return ChildProjectCopy(project_id=None)
            if pdb.get_project(conn, parent_pid) is None:
                raise ProjectInheritanceError(
                    f"parent project {parent_pid} is missing in this profile"
                )
            wrote = True
            binding = pdb.bind_session_project(conn, child_id, parent_pid)
            token = getattr(binding, "write_token", None)
            written = pdb.get_session_project_id(conn, child_id)
            if written != parent_pid:
                raise ProjectInheritanceError(
                    "project binding was not visible after write"
                )
            return ChildProjectCopy(
                project_id=written,
                write_token=str(token) if token else None,
                wrote=True,
            )
    except ProjectInheritanceError:
        if wrote and token and parent_pid:
            try:
                _release_owned_or_chain(child_id, release_path, parent_pid, token)
            except Exception as release_exc:
                raise ProjectInheritanceError(
                    "snapshot failed and conditional release of this call's write_token also failed"
                ) from release_exc
        raise
    except Exception as exc:
        if wrote and token and parent_pid:
            try:
                _release_owned_or_chain(child_id, release_path, parent_pid, token)
            except Exception as release_exc:
                raise ProjectInheritanceError(
                    "could not persist project binding, and conditional release also failed"
                ) from release_exc
        action = "persist" if wrote else "read"
        raise ProjectInheritanceError(
            f"could not {action} project binding for {child_id}"
        ) from exc


def inherit_session_project(
    parent_session_id: str,
    child_session_id: str,
    *,
    hermes_home: Path,
    parent_home: Optional[Path] = None,
) -> Optional[str]:
    """Copy ``parent_session_id``'s current project id onto ``child_session_id``.

    Returns the project id now on the child, or ``None`` when the parent is
    ordinary (no binding row) in the same profile. Does not create
    ``projects.db`` when that file is absent: a missing store cannot hold a
    binding.

    Cross-profile (``parent_home`` differs from ``hermes_home``): if the parent
    is bound, raise and write nothing. If the parent is ordinary, return
    ``None`` and write nothing. Never insert a foreign project id.

    A child that already has a row keeps it. That is the crash-recovery and
    "source changed after the branch" rule. This function never clears a binding
    and never walks ``parent_session_id`` ancestors.
    """
    return _copy_parent_binding(
        parent_session_id,
        child_session_id,
        hermes_home=hermes_home,
        parent_home=parent_home,
    ).project_id


def copy_parent_project_before_release(
    parent_session_id: str,
    child_session_id: str,
    *,
    state_db_path: Path,
    parent_state_db_path: Optional[Path] = None,
) -> ChildProjectCopy:
    """Snapshot before a child session row is published.

    ``state_db_path`` is the child profile's ``state.db``. The parent's project
    is read from that file's directory unless ``parent_state_db_path`` names a
    different existing ``state.db``. Process home is not a fallback. This
    commits ``projects.db`` on its own; it does not commit ``state.db``.
    """
    child_home = trusted_state_db_home(state_db_path)
    parent_home = (
        trusted_state_db_home(parent_state_db_path)
        if parent_state_db_path is not None
        else None
    )
    return _copy_parent_binding(
        parent_session_id,
        child_session_id,
        hermes_home=child_home,
        parent_home=parent_home,
        state_db_path=state_db_path,
    )
