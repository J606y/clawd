"""Persist panel reasoning effort on the existing session row.

The Browser lock already lives in ``model_config.browser_model_lock``. This
module merges ``reasoning_effort`` into that lock when one exists, and records
an idle pin at ``model_config.reasoning_effort`` so continuation can see it
when no confirmed lock is present. It does not open another database and does
not call ``SessionDB.update_session_runtime_lock``: that helper replaces the
whole lock and blanks ``model`` when the argument is omitted.

A running turn keeps its lock. The new level is stored as
``clawd_pending_reasoning_effort`` and committed only while this profile is
idle. ``commit_deferred_effort`` is the runtime entry: it must be called while
the caller holds that profile's admission lock, before the new run snapshots
effort. It does not consult process-global ``_run_statuses``.

The panel API adapter is not a SessionAuthority. ``resolve_execution_state``
may prove ``running`` or ``idle`` only from the adapter object the caller
passes, and only when that adapter's session store is this db and multiplex
is off. A missing map, an unattributed in-flight turn, or a failed read stays
``unknown``. A live lease or an attributable turn is ``running``. Unknown is not idle.
Handler ``_inflight_agent_runs`` is not the executor lifetime: it drops when
the handler task is cancelled while ``run_in_executor`` continues. Idle also
requires ``api_worker_live_count()`` to be a non-negative int no greater than
the attributed agent set. Import or type failure is unknown, not zero.
``commit_deferred_effort`` still requires the admission lock.

A same-request model-lock replace may pass that already-committed tier into
the new lock. The helper changes only ``reasoning_effort``. It does not copy
a still-pending pin, and it does not replace model, provider, route, or any
other option key.

``patch_session_model_config`` merges top-level keys inside ``BEGIN IMMEDIATE``,
but a caller-built ``browser_model_lock`` replaces the whole lock with a stale
snapshot. That loses a concurrent model/provider update, and a later read
cannot undo it. This module therefore read-modify-writes the current document
inside ``SessionDB._execute_write``. Core has no public nested CAS; do not add
one from this lane.
"""

from __future__ import annotations

import json
import time
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any, Optional, cast

PENDING_KEY = "clawd_pending_reasoning_effort"
PANEL_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max", "ultra"})

# Precise requirement for the core owner. This lane must not edit hermes_state.
CORE_TRANSACTION_REQUIREMENT = (
    "Keep SessionDB._execute_write(fn): fn(conn) runs inside BEGIN IMMEDIATE, "
    "commit is owned by the helper, and any exception rolls the statement back. "
    "Do not add a nested CAS in this lane. patch_session_model_config has none: "
    "supplying browser_model_lock replaces that whole key with the caller's object. "
    "If _execute_write is removed, add a public method that, in one such transaction, "
    "reads sessions.model_config for the exact id, applies a pure callback to the whole "
    "dict, and UPDATEs that same row. The callback must see the lock committed by the "
    "previous writer, and must not require the caller to pass a prebuilt browser_model_lock."
)


class EffortServiceError(Exception):
    """Explicit refusal. Callers must not turn this into an empty success."""

    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _config_from_raw(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise EffortServiceError(
                "model_config_unreadable", "会话模型配置无法读取，档位未写入", 409,
            ) from exc
        if not isinstance(parsed, dict):
            raise EffortServiceError(
                "model_config_unreadable", "会话模型配置无法读取，档位未写入", 409,
            )
        return dict(parsed)
    if raw in (None, ""):
        return {}
    raise EffortServiceError("model_config_unreadable", "会话模型配置无法读取，档位未写入", 409)


def _config(session: dict[str, Any]) -> dict[str, Any]:
    return _config_from_raw(session.get("model_config"))


def _lock(config: dict[str, Any]) -> Optional[dict[str, Any]]:
    lock = config.get("browser_model_lock")
    return dict(lock) if isinstance(lock, dict) else None


def _cell(row: Any, key: str, index: int) -> Any:
    try:
        return row[key]
    except Exception:
        return row[index]


def _panel_effort(value: Any) -> str:
    effort = str(value or "").strip().lower()
    if effort not in PANEL_EFFORTS:
        raise EffortServiceError("invalid_effort", "不是可用的思考强度", 400)
    return effort


def _stored_effort(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    effort = value.strip().lower()
    return effort if effort in PANEL_EFFORTS else None


def describe_session_effort(session: dict[str, Any], *, running: Optional[bool]) -> dict[str, Any]:
    """Read-only view. Pending wins over the lock; the lock is not rewritten here."""
    config = _config(session)
    lock = _lock(config)
    pending = _stored_effort(config.get(PENDING_KEY))
    lock_effort = None
    if lock is not None:
        options = lock.get("model_options")
        if isinstance(options, dict):
            lock_effort = _stored_effort(options.get("reasoning_effort"))
    root = _stored_effort(config.get("reasoning_effort"))
    if pending:
        effort, source, applied = pending, "pending", "next_turn"
    elif lock_effort:
        effort, source, applied = lock_effort, "lock", "stored"
    elif root:
        effort, source, applied = root, "session", "stored"
    else:
        effort, source, applied = None, "unset", "unset"
    return {
        "object": "clawd.session.effort",
        "session_id": session.get("id"),
        "effort": effort,
        "source": source,
        "applied": applied,
        "running": running,
        "lock_model": None if lock is None else lock.get("model") or "",
        "lock_provider": None if lock is None else lock.get("provider") or "",
        "lock_confirmed": bool(lock.get("confirmed")) if lock else False,
        "persisted": source != "unset",
    }


def effort_for_lock_replace(session: dict[str, Any]) -> Optional[str]:
    """Stored tier to keep when a new lock omits ``reasoning_effort``.

    Pending is not stored. Copying it here would commit without an admission
    proof. A pending pin must not hide the tier already in the lock or at the
    root: the replace keeps that stored tier, and leaves the pin for a later
    admission. ``None`` means the replace must not invent a tier.
    """
    config = _config(session)
    lock = _lock(config)
    if lock is not None:
        options = lock.get("model_options")
        if isinstance(options, dict):
            found = _stored_effort(options.get("reasoning_effort"))
            if found:
                return found
    return _stored_effort(config.get("reasoning_effort"))


def model_options_for_lock_replace(
    model_options: Any,
    *,
    stored_effort: Optional[str] = None,
    committed_effort: Optional[str] = None,
) -> dict[str, Any]:
    """Options for one new browser lock.

    A committed tier replaces only ``reasoning_effort``, including when the
    body sent a different explicit tier. Otherwise an omitted tier keeps the
    stored one, and an explicit body tier is left alone. Other keys are copied
    through. This does not write, and it does not accept a pending pin as
    ``stored_effort``.
    """
    options = dict(model_options) if isinstance(model_options, dict) else {}
    committed = _stored_effort(committed_effort)
    if committed:
        options["reasoning_effort"] = committed
        return options
    if _stored_effort(options.get("reasoning_effort")):
        return options
    stored = _stored_effort(stored_effort)
    if stored:
        options["reasoning_effort"] = stored
    return options


def _require_session(db: Any, session_id: str) -> dict[str, Any]:
    session = db.get_session(session_id)
    if not isinstance(session, dict):
        raise EffortServiceError("session_not_found", "会话不存在", 404)
    return session


def read_session_effort(db: Any, session_id: str, *, running: Optional[bool]) -> dict[str, Any]:
    return describe_session_effort(_require_session(db, session_id), running=running)


def _same_store(authority: Any, db: Any) -> bool:
    auth_db = getattr(authority, "db", None)
    if auth_db is None or db is None:
        return False
    if auth_db is db:
        return True
    left = getattr(auth_db, "db_path", None)
    right = getattr(db, "db_path", None)
    if not left or not right:
        return False
    try:
        return Path(left).resolve() == Path(right).resolve()
    except OSError:
        return False


def _watch_ids(authority: Any, db: Any, session_id: str) -> Optional[set[str]]:
    ids = {session_id}
    getter = getattr(db, "get_session", None)
    session = None
    if callable(getter):
        try:
            session = getter(session_id)
        except Exception:
            return None
    if not isinstance(session, dict):
        return ids
    try:
        from gateway.session_authorities import admission_owner
        owner = admission_owner(authority, session_id)
    except Exception:
        return None
    if isinstance(owner, str) and owner:
        ids.add(owner)
    logical = getattr(authority, "logical_owner", None)
    if callable(logical):
        try:
            extra = logical(session_id)
        except Exception:
            return None
        if isinstance(extra, str) and extra:
            ids.add(extra)
    return ids


def _memory_state(authority: Any, session_ids: set[str]) -> str:
    executing = getattr(authority, "executing", None)
    if not isinstance(executing, dict):
        return "unknown"
    if any(sid in executing for sid in session_ids):
        return "running"
    return "idle"


def _lease_state(db: Any, session_id: str) -> str:
    try:
        from hermes_state_lease_read import read_session_turn_lease
        view = read_session_turn_lease(db, session_id)
    except Exception:
        return "unknown"
    if not isinstance(view, dict) or not isinstance(view.get("busy"), bool):
        return "unknown"
    return "running" if view["busy"] else "idle"


def _row_states(rows: Any) -> Optional[set[str]]:
    found: set[str] = set()
    for row in rows:
        try:
            found.add(str(_cell(row, "status", 0) or ""))
        except Exception:
            return None
    return found


def _query_busy(conn: Any, session_ids: set[str]) -> str:
    """Read-only. Does not delete leases or compression locks."""
    try:
        from hermes_state_messages import _stale_holder
    except Exception:
        return "unknown"
    now = time.time()
    for sid in session_ids:
        try:
            lock = conn.execute(
                "SELECT holder, expires_at FROM compression_locks WHERE session_id = ?",
                (sid,),
            ).fetchone()
        except Exception:
            return "unknown"
        if lock is not None:
            try:
                if not _stale_holder(lock, now):
                    return "unknown"
            except Exception:
                return "unknown"
        try:
            admissions = conn.execute(
                "SELECT status FROM session_admissions WHERE target_session_id=? "
                "AND status IN ('queued','started','unknown')",
                (sid,),
            ).fetchall()
            workers = conn.execute(
                "SELECT status FROM worker_executions WHERE session_id=? "
                "AND status IN ('registered','running','unknown')",
                (sid,),
            ).fetchall()
        except Exception:
            return "unknown"
        states = _row_states([*admissions, *workers])
        if states is None:
            return "unknown"
        if "unknown" in states:
            return "unknown"
        if states:
            return "running"
    return "idle"


def _durable_state_on(db: Any, conn: Any, session_id: str, session_ids: set[str]) -> str:
    try:
        key = db._session_turn_lease_key_on_conn(conn, session_id)
        row = conn.execute(
            "SELECT holder, acquired_at, expires_at FROM session_turn_leases WHERE conversation_id=?",
            (key,),
        ).fetchone()
    except Exception:
        return "unknown"
    if row is not None:
        try:
            from hermes_state import _compression_lock_holder_process_is_dead
            from hermes_state_lease_read import lease_view
            holder = _cell(row, "holder", 0)
            acquired = _cell(row, "acquired_at", 1)
            expires = _cell(row, "expires_at", 2)
            dead = bool(_compression_lock_holder_process_is_dead(holder or ""))
            view = lease_view(holder, acquired, expires, time.time(), dead=dead)
        except Exception:
            return "unknown"
        if view.get("busy") is not False:
            return "running" if view.get("busy") is True else "unknown"
    return _query_busy(conn, session_ids)


def profile_execution_state(db: Any, session_id: str, authority: Any) -> str:
    """``running``, ``idle``, or ``unknown`` for this profile only.

    Idle requires this profile's authority ``executing`` map, its turn lease,
    and its admission/worker rows. Process-global run status maps are not
    consulted: the same session id in another profile is not idle proof.
    """
    if authority is None or not _same_store(authority, db):
        return "unknown"
    ids = _watch_ids(authority, db, session_id)
    if not ids:
        return "unknown"
    memory = _memory_state(authority, ids)
    if memory != "idle":
        return memory
    leased = _lease_state(db, session_id)
    if leased != "idle":
        return leased
    reader = getattr(db, "_read_ctx", None)
    if not callable(reader):
        return "unknown"
    ctx = reader()
    if not hasattr(ctx, "__enter__") or not hasattr(ctx, "__exit__"):
        return "unknown"
    try:
        with cast(AbstractContextManager[Any], ctx) as conn:
            return _query_busy(conn, ids)
    except Exception:
        return "unknown"


def _execute_write(db: Any, fn: Any) -> Any:
    writer = getattr(db, "_execute_write", None)
    if not callable(writer):
        raise EffortServiceError(
            "effort_transaction_unavailable",
            "会话库没有同一事务内的读改写，档位未写入",
            503,
        )
    return writer(fn)


def _load_row(conn: Any, session_id: str) -> tuple[dict[str, Any], Any]:
    row = conn.execute("SELECT model_config FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if row is None:
        raise EffortServiceError("session_not_found", "会话不存在", 404)
    raw = _cell(row, "model_config", 0)
    return _config_from_raw(raw), raw


def _store(conn: Any, session_id: str, config: dict[str, Any]) -> None:
    payload = json.dumps(config) if config else None
    cur = conn.execute("UPDATE sessions SET model_config = ? WHERE id = ?", (payload, session_id))
    if getattr(cur, "rowcount", 1) == 0:
        raise EffortServiceError("effort_persistence_failed", "档位没有写入会话", 500)


def _with_effort(lock: dict[str, Any], effort: str) -> dict[str, Any]:
    options = lock.get("model_options")
    if "model_options" in lock and not isinstance(options, dict):
        raise EffortServiceError(
            "model_options_unreadable", "会话模型选项无法读取，档位未写入", 409,
        )
    merged = dict(lock)
    merged_options = dict(options or {})
    merged_options["reasoning_effort"] = effort
    merged["model_options"] = merged_options
    return merged


def _view(session_id: str, config: dict[str, Any], *, running: Optional[bool], persisted: bool) -> dict[str, Any]:
    view = describe_session_effort({"id": session_id, "model_config": config}, running=running)
    view["persisted"] = persisted
    return view


def _write_pending(db: Any, session_id: str, level: str) -> dict[str, Any]:
    def _do(conn: Any) -> dict[str, Any]:
        config, _raw = _load_row(conn, session_id)
        config[PENDING_KEY] = level
        _store(conn, session_id, config)
        view = _view(session_id, config, running=True, persisted=True)
        if view["effort"] != level or view["applied"] != "next_turn":
            raise EffortServiceError("effort_persistence_failed", "运行中的档位没有写入下一回合", 500)
        return view
    return _execute_write(db, _do)


def _commit_config(config: dict[str, Any], level: str) -> dict[str, Any]:
    lock = config.get("browser_model_lock")
    if lock is not None:
        if not isinstance(lock, dict):
            raise EffortServiceError(
                "model_config_unreadable", "会话模型配置无法读取，档位未写入", 409,
            )
        config["browser_model_lock"] = _with_effort(lock, level)
    config["reasoning_effort"] = level
    config.pop(PENDING_KEY, None)
    return config


_LIVE_RUN = frozenset({"queued", "running", "waiting_for_approval", "stopping"})
_TERMINAL_RUN = frozenset({"completed", "failed", "cancelled", "interrupted"})
_DELEGATION_LIVE = frozenset({"running", "stalling", "finalizing"})
_DELEGATION_KEYS = (
    "origin_session_id",
    "parent_session_id",
    "origin_ui_session_id",
    "session_key",
    "session_id",
)


def _count(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _merge_proof(*states: str) -> str:
    """A running proof wins. Any other non-idle fact stays unknown, never idle."""
    if any(state == "running" for state in states):
        return "running"
    if any(state != "idle" for state in states):
        return "unknown"
    return "idle"


def _multiplex_active() -> Optional[bool]:
    try:
        from agent.secret_scope import is_multiplex_active
        return bool(is_multiplex_active())
    except Exception:
        return None


def _db_same(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return False
    if left is right:
        return True
    left_path = getattr(left, "db_path", None)
    right_path = getattr(right, "db_path", None)
    if not left_path or not right_path:
        return False
    try:
        return Path(left_path).resolve() == Path(right_path).resolve()
    except OSError:
        return False


def _adapter_same_store(adapter: Any, db: Any) -> bool:
    opener = getattr(adapter, "_ensure_session_db", None)
    if not callable(opener):
        return False
    try:
        opened = opener()
    except Exception:
        return False
    return _db_same(opened, db)


def _lineage_ids(db: Any, session_id: str) -> Optional[set[str]]:
    lineage = getattr(db, "get_compression_lineage", None)
    tip = getattr(db, "get_compression_tip", None)
    if not callable(lineage) or not callable(tip):
        return None
    try:
        chain = lineage(session_id)
        current = tip(session_id)
    except Exception:
        return None
    if not isinstance(chain, (list, tuple)) or not chain:
        return None
    ids: set[str] = set()
    for item in chain:
        if not isinstance(item, str) or not item:
            return None
        ids.add(item)
    if session_id not in ids:
        return None
    if not isinstance(current, str) or not current:
        return None
    ids.add(current)
    return ids


def _delegation_state(session_ids: set[str]) -> str:
    try:
        from tools.async_delegation import list_async_delegations
        items = list_async_delegations()
    except Exception:
        return "unknown"
    if not isinstance(items, list):
        return "unknown"
    for item in items:
        if not isinstance(item, dict):
            return "unknown"
        if item.get("status") not in _DELEGATION_LIVE:
            continue
        for key in _DELEGATION_KEYS:
            value = item.get(key)
            if isinstance(value, str) and value in session_ids:
                return "running"
    return "idle"


def _in_process_worker_count() -> Optional[int]:
    """Executor threads still inside an API turn, or None if that read fails.

    ``APIServerAdapter._inflight_agent_runs`` is released in the handler
    ``finally`` even when the executor thread is still in the turn. The
    surviving signal is ``api_worker_live_count``. It is process-global, so an
    extra worker that is not one of this adapter's attributed agents stays
    unknown rather than idle. A failed import or a non-count is unknown, not 0.
    """
    try:
        from gateway.platforms.api_server_runs import api_worker_live_count
        value = api_worker_live_count()
    except Exception:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _task_done(task: Any) -> Optional[bool]:
    done = getattr(task, "done", None)
    if not callable(done):
        return None
    try:
        return bool(done())
    except Exception:
        return None


def _adapter_memory_state(adapter: Any, session_ids: set[str]) -> str:
    """Attributable turns on this adapter. A gap is unknown, not idle."""
    statuses = getattr(adapter, "_run_statuses", None)
    agents = getattr(adapter, "_shutdown_interruptible_agents", None)
    active = getattr(adapter, "_active_run_agents", None)
    tasks = getattr(adapter, "_active_run_tasks", None)
    if not all(isinstance(item, dict) for item in (statuses, agents, active, tasks)):
        return "unknown"
    inflight = _count(getattr(adapter, "_inflight_agent_runs", None))
    pending = _count(getattr(adapter, "_pending_agent_requests", None))
    if inflight is None or pending is None:
        return "unknown"
    live: set[str] = set()
    unattributed = pending > 0
    for status in statuses.values():
        if not isinstance(status, dict):
            return "unknown"
        kind = status.get("status")
        sid = status.get("session_id")
        if not isinstance(kind, str):
            return "unknown"
        if kind in _TERMINAL_RUN:
            continue
        if kind in _LIVE_RUN:
            if isinstance(sid, str) and sid:
                live.add(sid)
            else:
                unattributed = True
            continue
        if kind == "unknown" or not isinstance(sid, str) or not sid or sid in session_ids:
            return "unknown"
    seen: set[int] = set()
    for agent in (*agents.values(), *active.values()):
        if agent is None:
            continue
        ident = id(agent)
        if ident in seen:
            continue
        seen.add(ident)
        sid = getattr(agent, "session_id", None)
        if isinstance(sid, str) and sid:
            live.add(sid)
        else:
            unattributed = True
    if inflight > len(seen):
        unattributed = True
    workers = _in_process_worker_count()
    if workers is None:
        return "unknown"
    if workers > len(seen):
        unattributed = True
    for run_id, task in tasks.items():
        finished = _task_done(task)
        if finished is None:
            return "unknown"
        if finished:
            continue
        status = statuses.get(run_id)
        sid = status.get("session_id") if isinstance(status, dict) else None
        kind = status.get("status") if isinstance(status, dict) else None
        if kind in _LIVE_RUN and isinstance(sid, str) and sid:
            live.add(sid)
            continue
        unattributed = True
    if session_ids & live:
        return "running"
    if unattributed:
        return "unknown"
    return "idle"


def _unbound_adapter_state(db: Any, session_id: str, adapter: Any, ids: set[str]) -> str:
    memory = _adapter_memory_state(adapter, ids)
    if memory == "running":
        return "running"
    leased = _lease_state(db, session_id)
    if leased == "running":
        return "running"
    delegated = _delegation_state(ids)
    if delegated == "running":
        return "running"
    if memory != "idle" or leased != "idle" or delegated != "idle":
        return "unknown"
    reader = getattr(db, "_read_ctx", None)
    if not callable(reader):
        return "unknown"
    ctx = reader()
    if not hasattr(ctx, "__enter__") or not hasattr(ctx, "__exit__"):
        return "unknown"
    try:
        with cast(AbstractContextManager[Any], ctx) as conn:
            durable = _query_busy(conn, ids)
    except Exception:
        return "unknown"
    return _merge_proof(durable)


def resolve_execution_state(
    db: Any, session_id: str, authority: Any, adapter: Any = None,
) -> str:
    """``running``, ``idle``, or ``unknown``.

    A same-store authority keeps ``profile_execution_state``. Without one, the
    caller must pass the API adapter that owns this store. Idle requires that
    adapter's turn maps, this profile's lease, admissions, durable workers,
    in-process executor count, and delegations all to be readable and idle,
    and multiplex to be off. A present
    ``gateway_runner`` without a resolved authority is unknown. Unknown is not
    idle. This does not register HTTP routes or bypass ``commit_deferred_effort``.
    """
    if authority is not None and not _same_store(authority, db):
        return "unknown"
    authority_state: Optional[str] = None
    if authority is not None:
        authority_state = profile_execution_state(db, session_id, authority)
        if adapter is None or authority_state == "running":
            return authority_state
    elif adapter is None or getattr(adapter, "gateway_runner", None) is not None:
        return "unknown"
    if not _adapter_same_store(adapter, db):
        return "unknown"
    if authority is None and _multiplex_active() is not False:
        return "unknown"
    ids = _lineage_ids(db, session_id)
    if not ids:
        return "unknown"
    if authority_state is None:
        return _unbound_adapter_state(db, session_id, adapter, ids)
    return _merge_proof(authority_state, _adapter_memory_state(adapter, ids))


def _apply_pending_or_commit(
    db: Any, session_id: str, level: str, session_ids: set[str], *, allow_pending_fallback: bool,
    adapter: Any = None,
) -> dict[str, Any]:
    """Merge into the row read inside the write transaction.

    If the profile is no longer idle, the live lock and ``reasoning_effort``
    are left unchanged. ``allow_pending_fallback`` stores the next-turn pin
    instead; the runtime commit does not, so a racing run keeps its effort.
    """
    def _do(conn: Any) -> dict[str, Any]:
        config, _raw = _load_row(conn, session_id)
        state = _durable_state_on(db, conn, session_id, session_ids)
        if adapter is not None:
            state = _merge_proof(
                state,
                _adapter_memory_state(adapter, session_ids),
                _delegation_state(session_ids),
            )
        if state == "unknown":
            raise EffortServiceError(
                "effort_run_state_unavailable", "无法确认会话是否仍在运行，档位未写入", 503,
            )
        if state != "idle":
            if not allow_pending_fallback:
                raise EffortServiceError(
                    "effort_not_idle", "会话仍在运行或已有新回合，当前档位未改", 409,
                )
            config[PENDING_KEY] = level
            _store(conn, session_id, config)
            view = _view(session_id, config, running=True, persisted=True)
            if view["applied"] != "next_turn" or view["effort"] != level:
                raise EffortServiceError("effort_persistence_failed", "运行中的档位没有写入下一回合", 500)
            return view
        _commit_config(config, level)
        _store(conn, session_id, config)
        view = _view(session_id, config, running=False, persisted=True)
        if view["effort"] != level or view["applied"] != "stored" or view["source"] == "pending":
            raise EffortServiceError("effort_persistence_failed", "档位回读与写入不一致", 500)
        return view
    return _execute_write(db, _do)


def write_session_effort(
    db: Any, session_id: str, effort: Any, *, running: bool, authority: Any = None,
    adapter: Any = None,
) -> dict[str, Any]:
    """Merge effort without replacing a frozen model or provider.

    ``running=True`` records a next-turn pin only. The caller must already have
    decided admission from this profile's authority and lease. A false idle
    decision is rechecked in the write transaction against that authority's
    watch ids; a run that appeared keeps its lock.

    ``adapter`` is optional proof when this process has no authority. It is not
    an idle default. Unknown writes nothing. A running proof stores only the
    next-turn pin, including when the caller passed ``running=False``.
    """
    level = _panel_effort(effort)
    _require_session(db, session_id)
    if authority is not None and not _same_store(authority, db):
        raise EffortServiceError(
            "effort_run_state_unavailable", "无法确认会话是否仍在运行，档位未写入", 503,
        )
    if adapter is not None:
        state = resolve_execution_state(db, session_id, authority, adapter)
        if state == "unknown":
            raise EffortServiceError(
                "effort_run_state_unavailable", "无法确认会话是否仍在运行，档位未写入", 503,
            )
        if state == "running" or running:
            return _write_pending(db, session_id, level)
        ids = _lineage_ids(db, session_id)
        if authority is not None:
            watched = _watch_ids(authority, db, session_id)
            if not watched:
                raise EffortServiceError(
                    "effort_run_state_unavailable", "无法确认会话是否仍在运行，档位未写入", 503,
                )
            ids = watched
        if not ids:
            raise EffortServiceError(
                "effort_run_state_unavailable", "无法确认会话是否仍在运行，档位未写入", 503,
            )
        return _apply_pending_or_commit(
            db, session_id, level, ids, allow_pending_fallback=True, adapter=adapter,
        )
    if running:
        return _write_pending(db, session_id, level)
    ids = {session_id}
    if authority is not None:
        watched = _watch_ids(authority, db, session_id)
        if not watched:
            raise EffortServiceError(
                "effort_run_state_unavailable", "无法确认会话是否仍在运行，档位未写入", 503,
            )
        ids = watched
        memory = _memory_state(authority, ids)
        if memory == "running":
            return _write_pending(db, session_id, level)
        if memory != "idle":
            raise EffortServiceError(
                "effort_run_state_unavailable", "无法确认会话是否仍在运行，档位未写入", 503,
            )
    return _apply_pending_or_commit(db, session_id, level, ids, allow_pending_fallback=True)


def commit_deferred_effort(
    db: Any,
    session_id: str,
    *,
    authority: Any = None,
    admission_held: bool = False,
) -> dict[str, Any]:
    """Commit a next-turn pin without changing a turn that is already running.

    Preconditions, all required or this function writes nothing:

    - ``admission_held`` is true only while the caller holds this profile's
      admission lock for ``session_id`` and will not release it until this
      call returns. A new run must not snapshot effort during the call.
    - ``authority`` is that profile's SessionAuthority, and ``db`` is its store
      (same object or same resolved ``db_path``).
    - The caller invokes this before the new run copies
      ``browser_model_lock.model_options`` or ``reasoning_effort``.
    - Call it on every admission, not only when the panel is open.

    Failure leaves the live lock and ``reasoning_effort`` unchanged:

    - missing lock proof: ``effort_admission_required`` (409), no write
    - executing, lease, admission, or worker still live: ``effort_not_idle``
      (409), no write; pending stays for a later idle admission
    - those reads fail or a compression lock is active: ``effort_run_state_unavailable``
      (503), no write
    - no pending: returns the current view, ``persisted`` false, no write
    - no ``_execute_write``: ``effort_transaction_unavailable`` (503), no write

    Passing ``admission_held=True`` without holding the lock is a caller bug.
    The in-transaction lease check still refuses a run the database can see.
    """
    if admission_held is not True or authority is None or not _same_store(authority, db):
        raise EffortServiceError(
            "effort_admission_required",
            "必须在该 profile 的准入锁内提交延迟档位，当前档位未改",
            409,
        )
    ids = _watch_ids(authority, db, session_id)
    if not ids:
        raise EffortServiceError(
            "effort_run_state_unavailable", "无法确认会话是否仍在运行，档位未写入", 503,
        )
    memory = _memory_state(authority, ids)
    if memory == "running":
        raise EffortServiceError("effort_not_idle", "会话仍在运行，当前档位未改", 409)
    if memory != "idle":
        raise EffortServiceError(
            "effort_run_state_unavailable", "无法确认会话是否仍在运行，档位未写入", 503,
        )

    def _do(conn: Any) -> dict[str, Any]:
        config, _raw = _load_row(conn, session_id)
        pending = _stored_effort(config.get(PENDING_KEY))
        state = _durable_state_on(db, conn, session_id, ids)
        if not pending:
            view = _view(session_id, config, running=False if state == "idle" else None, persisted=False)
            if state == "running":
                view["running"] = True
            return view
        if state == "unknown":
            raise EffortServiceError(
                "effort_run_state_unavailable", "无法确认会话是否仍在运行，档位未写入", 503,
            )
        if state != "idle":
            raise EffortServiceError("effort_not_idle", "会话仍在运行或已有新回合，当前档位未改", 409)
        _commit_config(config, pending)
        _store(conn, session_id, config)
        view = _view(session_id, config, running=False, persisted=True)
        if view["effort"] != pending or view["applied"] != "stored" or view["source"] == "pending":
            raise EffortServiceError("effort_persistence_failed", "延迟档位没有写入会话", 500)
        return view

    return _execute_write(db, _do)
