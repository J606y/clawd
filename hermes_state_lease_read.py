"""Read-only turn-lease projection. Does not reclaim, rewrite, or open a second database.

The lease key is the compression lineage root (``SessionDB._session_turn_lease_key_on_conn``).
Liveness reuses ``_compression_lock_holder_process_is_dead``: unknown holders, this process, and
foreign pid namespaces stay busy until TTL. A probe that cannot prove death is not idle.
"""

from __future__ import annotations

import time
from typing import Any

from hermes_state_runtime import RuntimeStoreError


_SKIP_SOURCES = frozenset({"cron", "webhook"})
_DELEGATION_BUSY = frozenset({"running", "stalling", "finalizing"})
_WALK_CAP = 10


def _epoch(value: Any) -> bool:
    return type(value) in (int, float) and value == value and 0 <= value < 253402300800


def lease_view(holder: Any, acquired: Any, expires: Any, now: float, *, dead: bool) -> dict[str, Any]:
    """Classify one lease row. ``dead`` must come from the core holder probe, not a private /proc parse."""
    if not _epoch(acquired) or not _epoch(expires) or acquired > expires:
        raise RuntimeStoreError("storage_unavailable", detail="invalid lease timestamp")
    if float(expires) <= now:
        return {"busy": False, "acquired_at": None, "holder_state": "expired"}
    if dead:
        return {"busy": False, "acquired_at": None, "holder_state": "dead"}
    # False from the core probe includes alive, unknown format, this process, and foreign pidns.
    return {"busy": True, "acquired_at": float(acquired), "holder_state": "held"}


def read_session_turn_lease(db, session_id: str, *, now: float | None = None) -> dict[str, Any]:
    """Lineage-root lease for ``session_id``. Read-only. Store failure raises; it does not report idle."""
    if not isinstance(session_id, str) or not session_id or len(session_id) > 512:
        raise RuntimeStoreError("invalid_params")
    from hermes_state import _compression_lock_holder_process_is_dead

    clock = time.time() if now is None else now
    try:
        with db._read_ctx() as conn:
            key = db._session_turn_lease_key_on_conn(conn, session_id)
            row = conn.execute(
                "SELECT holder, acquired_at, expires_at FROM session_turn_leases WHERE conversation_id=?",
                (key,),
            ).fetchone()
    except RuntimeStoreError:
        raise
    except Exception as exc:
        raise RuntimeStoreError("storage_unavailable", detail="lease read failed") from exc
    base = {"session_id": session_id, "lease_key": key}
    if row is None:
        return {**base, "busy": False, "acquired_at": None, "holder_state": "absent"}
    try:
        dead = _compression_lock_holder_process_is_dead(row["holder"] or "")
    except Exception as exc:
        raise RuntimeStoreError("storage_unavailable", detail="lease holder probe failed") from exc
    return {**base, **lease_view(row["holder"], row["acquired_at"], row["expires_at"], clock, dead=dead)}


def _summary(session: dict[str, Any]) -> dict[str, Any]:
    last_active = session.get("last_active")
    if last_active is None:
        last_active = session.get("last_activity_at")
    return {
        "id": session.get("id"),
        "source": session.get("source"),
        "title": session.get("title"),
        "model": session.get("model"),
        "started_at": session.get("started_at"),
        "last_active": last_active,
        "message_count": int(session.get("message_count") or 0),
        "preview": session.get("preview"),
        "pinned": bool(session.get("pinned")),
        "archived": bool(session.get("archived")),
    }


def _visible_root(db, session_id: str, seen: set[str]) -> str | None:
    current = session_id
    for _ in range(_WALK_CAP):
        if not current or current in seen:
            raise RuntimeStoreError("storage_unavailable", detail="lease session cycle")
        seen.add(current)
        session = db.get_session(current)
        if not session:
            raise RuntimeStoreError("storage_unavailable", detail="lease session missing")
        source = session.get("source")
        if source == "subagent":
            parent = session.get("parent_session_id")
            if not isinstance(parent, str) or not parent:
                return None
            current = parent
            continue
        if source in _SKIP_SOURCES or session.get("ended_at") is not None:
            return None
        return current
    raise RuntimeStoreError("storage_unavailable", detail="lease parent walk exceeded")


def list_busy_sessions(db) -> dict[str, Any]:
    """Sessions whose lineage-root lease is busy, plus in-process delegation parents.

    Does not open a private SQLite connection and does not rewrite lease rows. A failed lease
    or delegation read raises instead of returning an empty list.
    """
    now = time.time()
    try:
        with db._read_ctx() as conn:
            rows = conn.execute(
                "SELECT conversation_id, holder, acquired_at, expires_at FROM session_turn_leases"
            ).fetchall()
    except Exception as exc:
        raise RuntimeStoreError("storage_unavailable", detail="lease scan failed") from exc
    from hermes_state import _compression_lock_holder_process_is_dead

    busy_keys: list[str] = []
    for row in rows:
        try:
            dead = _compression_lock_holder_process_is_dead(row["holder"] or "")
        except Exception as exc:
            raise RuntimeStoreError("storage_unavailable", detail="lease holder probe failed") from exc
        view = lease_view(row["holder"], row["acquired_at"], row["expires_at"], now, dead=dead)
        if view["busy"]:
            busy_keys.append(row["conversation_id"])
    try:
        from tools.async_delegation import list_async_delegations

        delegations = list_async_delegations()
    except Exception as exc:
        raise RuntimeStoreError("storage_unavailable", detail="delegation list unavailable") from exc
    extra: list[str] = []
    for item in delegations:
        if not isinstance(item, dict) or item.get("status") not in _DELEGATION_BUSY:
            continue
        for key in ("origin_session_id", "parent_session_id"):
            parent = item.get(key)
            if isinstance(parent, str) and parent:
                extra.append(parent)
    found: dict[str, dict[str, Any]] = {}
    for session_id in [*busy_keys, *extra]:
        root = _visible_root(db, session_id, set())
        if root is None or root in found:
            continue
        session = db.get_session(root)
        if not session or not session.get("id"):
            raise RuntimeStoreError("storage_unavailable", detail="busy session unreadable")
        found[root] = _summary(session)
    return {"data": list(found.values())}
