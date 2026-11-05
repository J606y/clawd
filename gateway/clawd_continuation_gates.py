"""Code gates for continuation. Unknown evidence fails closed.

These checks run before any run is admitted. A prompt that says "wait if the
user must confirm" is not a gate and is not used.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Optional

from gateway.clawd_continuation_decision import Gates


def _probe(fn, session_id: str) -> Optional[bool]:
    try:
        value = fn(session_id)
    except Exception:
        return None
    if value is None:
        return None
    return bool(value)


def clarify_pending(session_id: str) -> Optional[bool]:
    try:
        from tools.clarify_gateway import get_pending_for_session
    except Exception:
        return None
    return _probe(lambda sid: get_pending_for_session(sid) is not None, session_id)


def approval_pending(session_id: str, run_statuses: dict[str, Any]) -> Optional[bool]:
    for status in run_statuses.values():
        if not isinstance(status, dict):
            continue
        if status.get("session_id") == session_id and status.get("status") == "waiting_for_approval":
            return True
    try:
        from tools.approval import get_pending_gateway_approval
    except Exception:
        return None
    return _probe(lambda sid: get_pending_gateway_approval(sid) is not None, session_id)


def plan_pending(session_id: str, adapter: Any) -> Optional[bool]:
    probe = getattr(adapter, "clawd_plan_approval_pending", None)
    if not callable(probe):
        return False
    return _probe(probe, session_id)


def collect_gates(adapter: Any, session_id: str) -> Gates:
    statuses = getattr(adapter, "_run_statuses", {}) or {}
    approval = approval_pending(session_id, statuses)
    clarify = clarify_pending(session_id)
    plan = plan_pending(session_id, adapter)
    unknown = approval is None or clarify is None or plan is None
    return Gates(
        approval=bool(approval),
        clarify=bool(clarify),
        plan=bool(plan),
        unknown=unknown,
    )


def turn_lease_expires(db: Any, session_id: str, now: float) -> Optional[float]:
    try:
        row = db._read_one(
            "SELECT expires_at FROM session_turn_leases WHERE conversation_id = ?",
            (session_id,),
        )
    except Exception:
        return now + 1.0
    if not row:
        return None
    expires = row["expires_at"] if isinstance(row, dict) else row[0]
    if isinstance(expires, (int, float)) and float(expires) > now:
        return float(expires)
    return None


def latest_run_status(adapter: Any, session_id: str, db: Any) -> tuple[Optional[str], bool]:
    """``(status, known)``. An unreadable run index is unknown, not 'not cancelled'."""
    newest: tuple[float, str] | None = None
    for status in (getattr(adapter, "_run_statuses", {}) or {}).values():
        if not isinstance(status, dict) or status.get("session_id") != session_id:
            continue
        stamp = float(status.get("updated_at") or status.get("created_at") or 0)
        name = str(status.get("status") or "")
        if newest is None or stamp >= newest[0]:
            newest = (stamp, name)
    durable, ok = _durable_latest(db, session_id)
    if not ok:
        return None, False
    if durable is not None and (newest is None or durable[0] >= newest[0]):
        newest = durable
    return (newest[1] if newest else None), True


def _durable_latest(db: Any, session_id: str) -> tuple[Optional[tuple[float, str]], bool]:
    path = getattr(db, "db_path", None)
    if path is None:
        return None, True
    store = Path(path).parent / "runs_idempotency.db"
    if not store.exists():
        return None, True
    try:
        conn = sqlite3.connect(f"file:{store}?mode=ro", uri=True, timeout=3)
    except sqlite3.Error:
        return None, False
    try:
        conn.execute("PRAGMA query_only = 1")
        row = conn.execute(
            "SELECT json_extract(status_json, '$.status'), COALESCE(updated_at, created_at) "
            "FROM run_idempotency WHERE json_extract(status_json, '$.session_id') = ? "
            "ORDER BY COALESCE(updated_at, created_at) DESC LIMIT 1",
            (session_id,),
        ).fetchone()
    except sqlite3.Error:
        return None, False
    finally:
        conn.close()
    if row is None or not isinstance(row[0], str):
        return None, True
    stamp = float(row[1]) if isinstance(row[1], (int, float)) else 0.0
    return (stamp, row[0]), True
