"""Project-memory write authorization shared by the HTTP route and the agent tool.

User management edits and agent tool writes are different contracts:

* A person editing project long-term memory is the approver. That path must
  not call ``tools.write_approval.evaluate_gate`` and must not be staged.
  It still requires a trusted principal, the profile that owns the session,
  and a binding row in that profile's ``projects.db``. A raw session id is
  not a credential.
* An agent ``memory`` tool write keeps the existing ``evaluate_gate`` /
  pending-store approval. This module does not replace that gate and does
  not mint a token.

There is no new database. Profile identity comes from the session
authority's ``state.db`` parent. Generation checks call
``SessionAuthority.check_approval_generation``. A running agent's frozen
project scope is an attribute on that agent, not a second store.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from clawd_projects.inheritance import state_db_profile_home
from clawd_projects.service import (
    get_project_by_id,
    get_project_memory_store,
    list_project_archives,
    read_project_instructions,
    resolve_session_project,
    write_project_archive,
    write_project_instructions,
)

USER_EDIT_APPROVAL = "not_required"
USER_EDIT_KIND = "user_project_memory_edit"
USER_READ_KIND = "user_project_memory_read"
USER_EDIT_CAPABILITY = "session:control"
USER_READ_CAPABILITY = "session:read"
SUBAGENT_READONLY = "subagent_readonly"

_STORE_STATUS = {
    "profile_mismatch": 403,
    "permission_denied": 403,
    "not_found": 404,
    "stale_generation": 409,
    "invalid_params": 400,
}


class MemoryAuthorizationError(Exception):
    """Fail-closed refusal. ``code`` is the HTTP error field the route should copy."""

    def __init__(self, code: str, message: str, *, status: int = 403) -> None:
        self.code = code
        self.status = status
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class UserMemoryEdit:
    """Verified user-management scope. Not a bearer token and not an approval id."""

    subject: str
    profile_id: str
    hermes_home: str
    session_id: str
    project_id: str
    generation: int | None
    approval: str = USER_EDIT_APPROVAL
    kind: str = USER_EDIT_KIND


@dataclass(frozen=True)
class FrozenMemoryScope:
    """Project scope captured when the agent was built. Not a writable credential."""

    profile_home_key: str
    hermes_home: str
    session_id: str
    project_id: str | None
    writable: bool


def authorization_error_body(exc: MemoryAuthorizationError) -> dict[str, Any]:
    return {"success": False, "error": exc.code, "message": exc.message}


def _refuse(code: str, message: str, status: int) -> None:
    raise MemoryAuthorizationError(code, message, status=status)


def _actor_fields(actor: Any) -> tuple[str, str, frozenset[str]]:
    if actor is None:
        _refuse("missing_identity", "project memory write requires a trusted principal", 401)
    subject = str(getattr(actor, "subject", "") or "").strip()
    profile_id = str(getattr(actor, "profile_id", "") or "").strip()
    caps = getattr(actor, "capabilities", None)
    if not subject or not profile_id or not isinstance(caps, (set, frozenset)) or not caps:
        _refuse("missing_identity", "principal is missing subject, profile, or capabilities", 401)
    return subject, profile_id, frozenset(caps)


def _authority_home(authority: Any) -> Path:
    db = getattr(authority, "db", None)
    db_path = getattr(db, "db_path", None) if db is not None else None
    if authority is None or db_path is None or not str(getattr(authority, "profile_id", "") or "").strip():
        _refuse("missing_identity", "project memory write requires the profile session authority", 401)
    return state_db_profile_home(Path(db_path))


def _map_store_error(exc: BaseException) -> None:
    from hermes_state_runtime import RuntimeStoreError

    if not isinstance(exc, RuntimeStoreError):
        raise exc
    reason = str(getattr(exc, "reason", "") or "unauthorized")
    if reason == "not_found":
        _refuse("session_not_found", "session is not in this profile", 404)
    if reason == "stale_generation":
        _refuse("stale_generation", "execution generation is not the running claim", 409)
    status = _STORE_STATUS.get(reason, 403)
    code = reason if reason in _STORE_STATUS else "unauthorized"
    _refuse(code, f"session authority refused the write ({reason})", status)


def _same_home(left: Path, right: Path) -> bool:
    from hermes_constants import hermes_home_key

    return hermes_home_key(left) == hermes_home_key(right)


def _bound_project_id(session_id: str, home: Path) -> str | None:
    db_path = Path(home) / "projects.db"
    if not db_path.is_file():
        return None
    project = resolve_session_project(session_id, hermes_home=home)
    return None if project is None else project.id


def authorize_user_project_memory_write(
    actor: Any,
    *,
    session_id: str,
    project_id: str,
    generation: int | None = None,
    authority: Any,
    hermes_home: Path | None = None,
    capability: str = USER_EDIT_CAPABILITY,
) -> UserMemoryEdit:
    """Authorize one user edit of the project bound to ``session_id``.

    Does not stage an agent approval and does not accept ``session_id`` as a
    password. ``generation`` is optional: omit it for an idle or in-turn
    management edit. If it is present it must be the authority's running
    claim (``check_approval_generation``); a stale or non-int value is refused.
    """
    subject, profile_id, caps = _actor_fields(actor)
    sid = str(session_id or "").strip()
    pid = str(project_id or "").strip()
    if not sid or not pid:
        _refuse("missing_identity", "session_id and project_id are required", 401)
    if capability not in caps:
        _refuse("permission_denied", f"principal lacks {capability}", 403)
    if authority is None or str(getattr(authority, "profile_id", "") or "") != profile_id:
        _refuse("profile_mismatch", "principal profile does not match the session authority", 403)
    home = _authority_home(authority)
    if hermes_home is not None and not _same_home(Path(hermes_home), home):
        _refuse("profile_mismatch", "memory home is not the authority profile", 403)
    authorize = getattr(authority, "authorize", None)
    if not callable(authorize):
        _refuse("missing_identity", "session authority cannot authorize this profile", 401)
    from gateway.session_contract import SessionRef

    try:
        authorize(actor, SessionRef(profile_id, sid), capability)
    except Exception as exc:
        _map_store_error(exc)
    if generation is not None:
        if type(generation) is not int:
            _refuse("stale_generation", "execution_generation must be the running claim's int", 409)
        check = getattr(authority, "check_approval_generation", None)
        if not callable(check):
            _refuse("missing_identity", "execution generation cannot be verified", 401)
        try:
            check(sid, generation)
        except Exception as exc:
            _map_store_error(exc)
    bound_id = _bound_project_id(sid, home)
    if not bound_id:
        _refuse("not_bound", "session is not bound to a project in this profile", 403)
    requested = get_project_by_id(pid, hermes_home=home)
    if requested is None:
        _refuse("project_not_found", "project is not in this profile", 404)
    if requested.id != bound_id:
        _refuse("project_mismatch", "session is not bound to this project", 403)
    kind = USER_READ_KIND if capability == USER_READ_CAPABILITY else USER_EDIT_KIND
    return UserMemoryEdit(
        subject=subject,
        profile_id=profile_id,
        hermes_home=str(home),
        session_id=sid,
        project_id=requested.id,
        generation=generation,
        kind=kind,
    )


def authorize_user_project_memory_read(
    actor: Any,
    *,
    session_id: str,
    project_id: str,
    generation: int | None = None,
    authority: Any,
    hermes_home: Path | None = None,
) -> UserMemoryEdit:
    """Same identity rules as a write. Does not stage approval and cannot be applied."""
    return authorize_user_project_memory_write(
        actor,
        session_id=session_id,
        project_id=project_id,
        generation=generation,
        authority=authority,
        hermes_home=hermes_home,
        capability=USER_READ_CAPABILITY,
    )


_SIDE_ACTIONS = frozenset({"set_instructions", "add_archive"})


def _apply_project_side(edit: UserMemoryEdit, operation: dict[str, Any]) -> dict[str, Any]:
    """Instructions and archives stay in this project. Never the global memory store."""
    home = Path(edit.hermes_home)
    action = operation.get("action")
    try:
        if action == "set_instructions":
            content = operation.get("content")
            if not isinstance(content, str):
                return {"success": False, "error": "指示必须是文本，未写入"}
            write_project_instructions(edit.project_id, home, content)
            return {"success": True, "message": "已写入这个项目的指示，没有改全局记忆"}
        if action == "add_archive":
            project = get_project_by_id(edit.project_id, hermes_home=home)
            if project is None:
                return {"success": False, "error": "项目不存在，未写入档案"}
            name = operation.get("name")
            content = operation.get("content")
            if not isinstance(name, str) or not isinstance(content, str):
                return {"success": False, "error": "档案需要文件名和文本，未写入"}
            write_project_archive(project, name, content, home, replace=operation.get("replace") is True)
            return {"success": True, "message": "已写入这个项目的工作目录，没有改全局记忆"}
    except ValueError as exc:
        return {"success": False, "error": str(exc)}
    except OSError:
        return {"success": False, "error": "写入失败，未改全局记忆"}
    return {"success": False, "error": "未知的项目写入，未执行"}


def apply_authorized_project_memory(
    edit: UserMemoryEdit,
    operations: list[dict[str, Any]],
    *,
    actor: Any,
    authority: Any,
) -> dict[str, Any]:
    """Re-check the edit, then write project ``memory`` only.

    Does not call ``evaluate_gate``. A forged edit whose binding is no longer
    true is refused. Global ``MEMORY.md`` / ``USER.md`` are not this function's
    store. A read grant cannot be applied.
    """
    if not isinstance(edit, UserMemoryEdit) or edit.kind != USER_EDIT_KIND or edit.approval != USER_EDIT_APPROVAL:
        _refuse("unauthorized", "refusing an edit that is not a verified user project-memory edit", 403)
    fresh = authorize_user_project_memory_write(
        actor,
        session_id=edit.session_id,
        project_id=edit.project_id,
        generation=edit.generation,
        authority=authority,
        hermes_home=Path(edit.hermes_home),
    )
    if fresh.hermes_home != edit.hermes_home or fresh.project_id != edit.project_id or fresh.profile_id != edit.profile_id:
        _refuse("project_mismatch", "project scope changed before the write", 403)
    if not isinstance(operations, list):
        return {"success": False, "error": "operations array is required"}
    side_ops = [op for op in operations if isinstance(op, dict) and op.get("action") in _SIDE_ACTIONS]
    memory_ops = [op for op in operations if not (isinstance(op, dict) and op.get("action") in _SIDE_ACTIONS)]
    for op in side_ops:
        side_result = _apply_project_side(fresh, op)
        if side_result.get("success") is not True:
            return side_result
    if not memory_ops:
        return {"success": True, "entry_count": 0, "message": "已写入这个项目，没有改全局记忆"}
    store = _project_store(fresh)
    return store.apply_batch("memory", memory_ops)


def load_authorized_project_memory(
    edit: UserMemoryEdit,
    *,
    actor: Any,
    authority: Any,
) -> dict[str, Any]:
    """Re-check a read or write grant, then return project memory entries only."""
    if not isinstance(edit, UserMemoryEdit) or edit.kind not in {USER_EDIT_KIND, USER_READ_KIND}:
        _refuse("unauthorized", "refusing a memory read without a verified project scope", 403)
    fresh = authorize_user_project_memory_write(
        actor,
        session_id=edit.session_id,
        project_id=edit.project_id,
        generation=edit.generation,
        authority=authority,
        hermes_home=Path(edit.hermes_home),
        capability=USER_READ_CAPABILITY if edit.kind == USER_READ_KIND else USER_EDIT_CAPABILITY,
    )
    if fresh.hermes_home != edit.hermes_home or fresh.project_id != edit.project_id:
        _refuse("project_mismatch", "project scope changed before the read", 403)
    store = _project_store(fresh)
    entries = store._entries_for("memory")
    content = "\n§\n".join(entries)
    instructions = ""
    instructions_error = ""
    try:
        instructions = read_project_instructions(fresh.project_id, Path(fresh.hermes_home))
    except ValueError as exc:
        instructions_error = str(exc)
    archives: list[dict[str, str]] | None = None
    archives_error = ""
    try:
        archives = list_project_archives(
            get_project_by_id(fresh.project_id, hermes_home=Path(fresh.hermes_home)),
            Path(fresh.hermes_home),
        )
    except ValueError as exc:
        archives = None
        archives_error = str(exc) or "项目档案无法读取，未当作空列表"
    except OSError:
        archives = None
        archives_error = "项目档案无法读取，未当作空列表"
    return {
        "entries": entries,
        "usage": store._usage("memory"),
        "char_count": len(content),
        "char_limit": store._char_limit("memory"),
        "instructions": instructions,
        "instructions_error": instructions_error,
        "archives": archives,
        "archives_error": archives_error,
    }


def _project_store(edit: UserMemoryEdit) -> Any:
    store = get_project_memory_store(edit.project_id, hermes_home=Path(edit.hermes_home))
    expected = _profile_key(Path(edit.hermes_home))
    if getattr(store, "profile_home_key", None) != expected:
        _refuse("profile_mismatch", "project memory store is not in the authorized profile", 403)
    return store


def _profile_key(home: Path) -> str:
    from hermes_constants import hermes_home_key

    return hermes_home_key(home)


def memory_store_write_block(store: Any) -> dict[str, Any] | None:
    """Tool-boundary refusal for a store marked read-only. Not an approval stage."""
    if store is None or getattr(store, "memory_writes_allowed", True) is not False:
        return None
    return {
        "success": False,
        "staged": False,
        "error": "Subagent memory is read-only. Nothing was written or staged.",
        "code": SUBAGENT_READONLY,
    }


def mark_memory_store_readonly(store: Any) -> None:
    """Mark this store and its inner stores. Does not create a store."""
    if store is None:
        return
    store.memory_writes_allowed = False
    for name in ("project_store", "global_store"):
        inner = getattr(store, name, None)
        if inner is not None and inner is not store:
            inner.memory_writes_allowed = False


def is_subagent_memory_actor(agent: Any) -> bool:
    """True for a delegate child. ``side_agent`` alone is not enough."""
    if getattr(agent, "platform", None) == "subagent":
        return True
    try:
        from agent.delegation_context import is_delegated_child_context
    except Exception:
        return False
    return bool(is_delegated_child_context())


def should_keep_running_memory_freeze(agent: Any) -> bool:
    """A turn that already sealed its scope must not be retargeted."""
    freeze = getattr(agent, "_memory_scope_freeze", None)
    if not isinstance(freeze, FrozenMemoryScope):
        return False
    return bool(str(getattr(agent, "_current_turn_id", "") or ""))


def freeze_agent_memory_scope(
    agent: Any,
    *,
    home: Path,
    session_id: str,
    project_id: str | None,
    writable: bool,
) -> FrozenMemoryScope:
    scope = FrozenMemoryScope(
        profile_home_key=_profile_key(home),
        hermes_home=str(home),
        session_id=str(session_id),
        project_id=project_id,
        writable=writable,
    )
    agent._memory_scope_freeze = scope
    return scope


def memory_schema_refresh_blocked(agent: Any) -> bool:
    """Do not rebuild the memory tool schema while a turn is using the sealed one."""
    return bool(getattr(agent, "_memory_schema_sealed", False) and str(getattr(agent, "_current_turn_id", "") or ""))


def seal_memory_schema(agent: Any) -> None:
    agent._memory_schema_sealed = True
