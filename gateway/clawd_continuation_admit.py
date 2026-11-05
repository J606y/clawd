"""Admit one continuation turn through the API adapter. Not a user self-POST.

The continuation service commits a pending tier on the real authority store
and freezes that re-read spec before calling this module. When
``FROZEN_LAUNCH`` is set, this module fingerprints and launches that exact
spec and does not refresh again: a second commit would let a later pin
diverge from the journal body, or miss a run already reserved under it.

A direct caller that has not set the token still commits under the admission
lock before reserve. The idempotency fingerprint stays the caller spec so a
retry finds the reserved run. This module does not rewrite the journal, does
not invent a route, does not guess a profile, and does not read GET handlers.
"""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import logging
import os
import time
import uuid
from typing import Any, Optional

from gateway.clawd_continuation_decision import CONTINUATION_TEXT, DISPLAY_KIND

logger = logging.getLogger(__name__)
_TERMINAL = frozenset({"completed", "failed", "cancelled", "interrupted"})
# Explicit non-model stand-in. Absence of this marker means the adapter's _run_agent path.
TEST_EXECUTOR_MARKER = "test-not-llm"
# Set by the continuation service after it has frozen the spec it will launch.
# A second refresh in that window would diverge from the journal body.
FROZEN_LAUNCH = contextvars.ContextVar("clawd_continuation_frozen_launch", default=False)


class ContinuationAdmitError(RuntimeError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def continuation_fingerprint(spec: dict[str, Any]) -> str:
    """Idempotency fingerprint. ``model_options`` is included, so a new tier is a different run."""
    return _fingerprint(spec)


def continuation_scope(profile: str) -> str:
    return _scope(profile)


def continuation_reservation(adapter: Any, profile: str, spec: dict[str, Any]) -> str:
    """``missing``, ``same``, ``conflict``, or ``unknown``.

    ``unknown`` is not proof that no run was reserved. Callers must not rewrite
    a frozen body unless the answer is ``missing``.
    """
    store = getattr(adapter, "_run_idempotency_store", None)
    if store is None or getattr(store, "durable", True) is False:
        return "unknown"
    key = spec.get("request_key") if isinstance(spec, dict) else None
    if not isinstance(key, str) or not key:
        return "unknown"
    try:
        outcome, _record = store.lookup(_scope(profile), key, _fingerprint(spec))
    except Exception:
        return "unknown"
    if outcome == "reused":
        return "same"
    if outcome == "conflict":
        return "conflict"
    if outcome == "missing":
        return "missing"
    return "unknown"


def _fingerprint(spec: dict[str, Any]) -> str:
    payload = {
        "input": spec.get("input"), "session_id": spec.get("session_id"),
        "model": spec.get("model"), "provider": spec.get("provider"),
        "model_options": spec.get("model_options") or {}, "locked": bool(spec.get("locked")),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _scope(profile: str) -> str:
    return hashlib.sha256(f"clawd-continuation\0{profile or 'default'}".encode()).hexdigest()


def _pid_alive(pid: int) -> bool:
    if not pid or pid == os.getpid():
        return pid == os.getpid()
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _ensure_instruction(db: Any, session_id: str, spec: dict[str, Any]) -> None:
    rows = db.get_messages(session_id, limit=1, latest=True)
    tail = rows[-1] if rows else None
    if isinstance(tail, dict) and tail.get("display_kind") == DISPLAY_KIND and tail.get("content") == CONTINUATION_TEXT:
        return
    db.append_message(
        session_id, "user", content=CONTINUATION_TEXT, display_kind=DISPLAY_KIND,
        display_metadata={
            "continuation": True, "source": "clawd_continuation",
            "notice_ids": list(spec.get("notice_ids") or []),
        },
    )


def _history(db: Any, session_id: str) -> list[dict[str, Any]]:
    rows = db.get_messages_as_conversation(session_id)
    history = [dict(row) for row in rows if isinstance(row, dict)]
    if not history or history[-1].get("content") != CONTINUATION_TEXT:
        raise ContinuationAdmitError("continuation_row_missing")
    return history


def executor_marker(adapter: Any) -> Optional[str]:
    """Return the test marker, or None when the real adapter turn must run.

    A set hook without the marker is refused. It must not fall through to a user self-POST.
    """
    hook = getattr(adapter, "clawd_continuation_executor", None)
    if hook is None:
        return None
    marker = getattr(hook, "clawd_executor_marker", None)
    if marker != TEST_EXECUTOR_MARKER:
        raise ContinuationAdmitError("unmarked_executor")
    return str(marker)


def _launch(adapter: Any, run_id: str, spec: dict[str, Any], history: list[dict[str, Any]], session_id: str) -> None:
    marker = executor_marker(adapter)
    transport = "in_process"

    async def _go() -> None:
        adapter._set_run_status(
            run_id, "running", session_id=session_id, model=spec["model"], continuation=True,
            continuation_transport=transport, continuation_executor=marker or "adapter_run_agent",
        )
        kwargs = dict(
            user_message=CONTINUATION_TEXT, conversation_history=history, session_id=session_id,
            gateway_session_key=spec.get("session_key") or session_id,
            cwd=spec.get("cwd") or "",
            requested_model=spec["model"], requested_provider=spec["provider"],
            model_options=dict(spec.get("model_options") or {}), session_model=spec["model"],
            confirmed_runtime_lock=bool(spec.get("locked")),
            route_source="session_model_lock" if spec.get("locked") else "raw_request",
            session_history_delivery="1", resume_unanswered_turn=True, history_from_session=False,
        )
        try:
            if marker:
                result, _usage = await adapter.clawd_continuation_executor(**kwargs)
            else:
                result, _usage = await adapter._run_agent(**kwargs)
        except Exception as exc:
            adapter._set_run_status(
                run_id, "failed", error=type(exc).__name__, session_id=session_id,
                continuation_transport=transport, continuation_executor=marker or "adapter_run_agent",
            )
            logger.warning("clawd continuation run %s failed: %s", run_id, type(exc).__name__)
            return
        output = str(result.get("final_response") or "")[:500] if isinstance(result, dict) else ""
        adapter._set_run_status(
            run_id, "completed", output=output, session_id=session_id, model=spec["model"],
            continuation_transport=transport, continuation_executor=marker or "adapter_run_agent",
        )

    task = asyncio.create_task(_go())
    if hasattr(adapter, "_track_background_task"):
        adapter._track_background_task(task)
    tasks = getattr(adapter, "_active_run_tasks", None)
    if isinstance(tasks, dict):
        tasks[run_id] = task


def _reusable(adapter: Any, record: dict[str, Any]) -> bool:
    status = record.get("status") if isinstance(record.get("status"), dict) else {}
    if status.get("status") in _TERMINAL:
        return True
    run_id = str(record.get("run_id") or "")
    active = getattr(adapter, "_active_run_tasks", {}) or {}
    task = active.get(run_id)
    if task is not None and not task.done():
        return True
    owner = int(record.get("owner_pid") or 0)
    return bool(owner) and owner != os.getpid() and _pid_alive(owner)


def _pending_visible(db: Any, session_id: str) -> bool:
    """Whether the caller-supplied row still has a pending pin.

    A missing reader is not treated as "no pending". That would launch a turn
    while blind to the pin. This does not open another profile.
    """
    getter = getattr(db, "get_session", None)
    if not callable(getter):
        raise ContinuationAdmitError("ownership_unconfirmed")
    try:
        session = getter(session_id)
    except Exception as exc:
        raise ContinuationAdmitError("ownership_unconfirmed") from exc
    if not isinstance(session, dict):
        return False
    from clawd_server.effort_service import describe_session_effort
    return describe_session_effort(session, running=None).get("source") == "pending"


def _inherit_current(authority: Any, session_id: str):
    """Re-read ``authority.db`` and freeze through the decision helper.

    Returns ``(runtime, reason)``. Does not patch a caller snapshot and does
    not look up a profile by name.
    """
    from gateway.clawd_continuation_decision import inherit_runtime
    try:
        session = authority.db.get_session(session_id)
    except Exception:
        return None, "ownership_unconfirmed"
    if not isinstance(session, dict):
        return None, "ownership_unconfirmed"
    route = None
    getter = getattr(authority.db, "get_recent_session_model_route", None)
    if callable(getter):
        try:
            route = getter(session_id)
        except Exception:
            return None, "ownership_unconfirmed"
    return inherit_runtime(session, route)


def _route_matches(runtime: Any, spec: dict[str, Any]) -> bool:
    if not isinstance(runtime, dict):
        return False
    return (
        runtime.get("model") == spec.get("model")
        and runtime.get("provider") == spec.get("provider")
        and bool(runtime.get("locked")) is bool(spec.get("locked"))
    )


def _spec_with_latest_options(spec: dict[str, Any], runtime: dict[str, Any]) -> dict[str, Any]:
    """Caller route fields stay. Only ``model_options`` comes from the re-read."""
    refreshed = dict(spec)
    refreshed["model_options"] = dict(runtime.get("model_options") or {})
    return refreshed


async def refresh_continuation_spec(adapter: Any, spec: dict[str, Any], db: Any) -> dict[str, Any]:
    """Commit pending under the admission lock, then return the latest spec.

    No authority and no visible pending: the caller spec is returned unchanged.
    That is not a successful commit. No authority and a visible pending pin:
    refuse, do not launch, do not guess a profile. A route mismatch refuses
    before the commit, so the pin stays and model/provider are not overwritten.
    """
    from clawd_server.effort_service import EffortServiceError
    from gateway.session_api_turn import commit_pending_if_any, profile_admission_lock
    from hermes_state_runtime import RuntimeStoreError

    session_id = str(spec.get("session_id") or "")
    try:
        async with profile_admission_lock(adapter, session_id) as held:
            if held is None:
                if _pending_visible(db, session_id):
                    raise ContinuationAdmitError("effort_admission_required")
                return dict(spec)
            authority, sid = held
            if db is not authority.db:
                raise ContinuationAdmitError("profile_mismatch")
            runtime, reason = _inherit_current(authority, sid)
            if not _route_matches(runtime, spec):
                raise ContinuationAdmitError(reason or "provider_unresolved")
            await asyncio.to_thread(commit_pending_if_any, authority, sid)
            runtime, reason = _inherit_current(authority, sid)
            if not _route_matches(runtime, spec):
                raise ContinuationAdmitError(reason or "provider_unresolved")
            return _spec_with_latest_options(spec, runtime)
    except ContinuationAdmitError:
        raise
    except EffortServiceError as exc:
        if exc.code == "effort_not_idle":
            raise ContinuationAdmitError("busy") from exc
        raise ContinuationAdmitError(exc.code) from exc
    except RuntimeStoreError as exc:
        if getattr(exc, "reason", None) == "runtime_draining":
            raise ContinuationAdmitError("draining") from exc
        if getattr(exc, "reason", None) == "profile_mismatch":
            raise ContinuationAdmitError("profile_mismatch") from exc
        raise ContinuationAdmitError("effort_run_state_unavailable") from exc


async def admit_continuation(adapter: Any, spec: dict[str, Any], *, db: Any, profile: str) -> str:
    """Reserve the idempotency key, then run the frozen turn. Raises on refusal."""
    if spec.get("input") != CONTINUATION_TEXT:
        raise ContinuationAdmitError("continuation_text_rejected")
    if not spec.get("model") or not spec.get("provider"):
        raise ContinuationAdmitError("provider_unresolved")
    executor_marker(adapter)
    store = getattr(adapter, "_run_idempotency_store", None)
    if store is None or getattr(store, "durable", True) is False:
        raise ContinuationAdmitError("idempotency_unavailable")
    session_id = str(spec["session_id"])
    key = str(spec["request_key"])
    scope = _scope(profile)
    fingerprint = _fingerprint(spec)
    outcome, record = store.lookup(scope, key, fingerprint)
    if outcome == "conflict":
        raise ContinuationAdmitError("idempotency_conflict")
    if outcome == "reused" and record is not None and _reusable(adapter, record):
        return str(record["run_id"])
    limited = adapter._concurrency_limited_response() if hasattr(adapter, "_concurrency_limited_response") else None
    if limited is not None:
        raise ContinuationAdmitError("busy")
    draining = adapter._draining_response() if hasattr(adapter, "_draining_response") else None
    if draining is not None:
        raise ContinuationAdmitError("draining")
    # The service freezes the spec it wants launched and sets FROZEN_LAUNCH.
    # Refreshing again would commit a later pin into this turn or change the
    # launched options away from the journal body. A direct caller still
    # commits here; the fingerprint above stays the caller spec so a reserved
    # run is found before this refresh.
    if not FROZEN_LAUNCH.get():
        spec = await refresh_continuation_spec(adapter, spec, db)
        if spec.get("input") != CONTINUATION_TEXT or not spec.get("model") or not spec.get("provider"):
            raise ContinuationAdmitError("provider_unresolved")
    run_id = str(record["run_id"]) if outcome == "reused" and record is not None else f"run_{uuid.uuid4().hex}"
    if outcome != "reused":
        status = adapter._set_run_status(
            run_id, "queued", created_at=time.time(), session_id=session_id, model=spec["model"], continuation=True,
        )
        reserved, record = store.reserve(
            scope, key, fingerprint, run_id, status,
            owner_pid=getattr(adapter, "_run_owner_pid", 0) or os.getpid(),
            owner_started=getattr(adapter, "_run_owner_started", 0),
        )
        if reserved == "reused" and record is not None and _reusable(adapter, record):
            return str(record["run_id"])
        if reserved != "created":
            raise ContinuationAdmitError("idempotency_conflict")
        ids = getattr(adapter, "_run_idempotency_ids", None)
        if isinstance(ids, set):
            ids.add(run_id)
    try:
        _ensure_instruction(db, session_id, spec)
        history = _history(db, session_id)
    except ContinuationAdmitError:
        if outcome != "reused":
            store.forget(scope, key)
        raise
    _launch(adapter, run_id, spec, history, session_id)
    return run_id
