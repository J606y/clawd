"""CLI execution projection and pidfd stop. Does not delete leases.

Busy/idle comes from ``read_session_turn_lease``. A stop is sent only when the
lineage-root holder is a proven isolated CLI incarnation. Unknown holders, this
process, a shared pid, and the gateway stay busy and are never signalled.
API runs are not this path: they keep ``stop_run`` and its generation check.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import select
import signal
import threading
import time
from pathlib import Path
from typing import Any

from hermes_state_runtime import RuntimeStoreError

_HOLDER = re.compile(
    r"pid=([1-9][0-9]*):pidns=([1-9][0-9]*):turn=(.+):platform=(cli|api_server|subagent|unknown)"
)
_STOP_LOCK = threading.Lock()
_STAGES = ((signal.SIGINT, 0.4), (signal.SIGTERM, 0.6), (signal.SIGKILL, 0.6))


def _epoch(value: Any) -> bool:
    return type(value) in (int, float) and value == value and 0 <= value < 253402300800


def _parse_holder(holder: Any) -> tuple[int, str, str, str] | None:
    if not isinstance(holder, str):
        return None
    match = _HOLDER.fullmatch(holder)
    if match is None:
        return None
    return int(match.group(1)), match.group(2), match.group(3), match.group(4)


def _execution_id(lease_key: str, holder: str, acquired: float, started: int) -> str:
    payload = [lease_key, holder, acquired, started]
    return hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()


def _row(db, lease_key: str) -> Any:
    with db._read_ctx() as conn:
        return conn.execute(
            "SELECT holder, acquired_at, expires_at FROM session_turn_leases WHERE conversation_id=?",
            (lease_key,),
        ).fetchone()


def _shared_pid(db, lease_key: str, pid: int, now: float) -> bool:
    prefix = f"pid={pid}:"
    with db._read_ctx() as conn:
        rows = conn.execute(
            "SELECT conversation_id, holder, expires_at FROM session_turn_leases WHERE expires_at > ?",
            (now,),
        ).fetchall()
    for row in rows:
        if row["conversation_id"] == lease_key:
            continue
        holder = row["holder"] or ""
        if not holder.startswith(prefix):
            continue
        parsed = _parse_holder(holder)
        if parsed is not None and parsed[0] == pid:
            return True
    return False


def _process(pid: int, acquired: float) -> tuple[bool, int]:
    """``(alive, start_ticks)``. Unreadable identity is not proof of death."""
    try:
        text = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    except FileNotFoundError:
        return False, 0
    except OSError as exc:
        raise RuntimeStoreError("storage_unavailable", detail="lease process unreadable") from exc
    tail = text.rpartition(")")[2].split()
    if len(tail) < 20 or not tail[19].isdigit():
        raise RuntimeStoreError("storage_unavailable", detail="lease process unreadable")
    started = int(tail[19])
    started_at = time.time() - time.clock_gettime(time.CLOCK_BOOTTIME) + started / os.sysconf("SC_CLK_TCK")
    alive = tail[0] not in ("Z", "X") and started_at <= acquired + 0.05
    return alive, started


def _isolated_cli(pid: int) -> bool:
    try:
        command = Path(f"/proc/{pid}/cmdline").read_bytes()
        cgroup = Path(f"/proc/{pid}/cgroup").read_text(encoding="ascii", errors="replace")
    except OSError as exc:
        raise RuntimeStoreError("storage_unavailable", detail="cli identity unreadable") from exc
    return b"hermes_cli.main" in command and "hermes-gateway.service" not in cgroup


def _stoppable(db, session_id: str, lease: dict[str, Any], row) -> dict[str, Any] | None:
    if row is None or not lease.get("busy"):
        return None
    holder = row["holder"]
    acquired = row["acquired_at"]
    if not _epoch(acquired):
        raise RuntimeStoreError("storage_unavailable", detail="invalid lease timestamp")
    parsed = _parse_holder(holder)
    if parsed is None:
        return None
    pid, _ns, turn, platform = parsed
    from hermes_state_pidns import holder_pid_checkable

    if platform != "cli" or not holder_pid_checkable(holder or ""):
        return None
    session = db.get_session(session_id)
    if not session or session.get("source") != "cli":
        return None
    lease_key = lease["lease_key"]
    if f":turn={lease_key}:" not in holder and f":turn={session_id}:" not in holder:
        return None
    if pid in (1, os.getpid(), os.getppid()):
        return None
    if _shared_pid(db, lease_key, pid, time.time()):
        return None
    alive, started = _process(pid, float(acquired))
    if not alive or not _isolated_cli(pid):
        return None
    return {
        "id": _execution_id(lease_key, holder, float(acquired), started),
        "kind": "cli",
        "started_at": float(acquired),
        "pid": pid,
        "holder": holder,
        "started": started,
    }


def project_session_execution(db, session_id: str) -> dict[str, Any]:
    """Lineage-root lease plus a CLI stop descriptor. Failure raises; it is not idle."""
    from hermes_state_lease_read import read_session_turn_lease

    lease = read_session_turn_lease(db, session_id)
    view: dict[str, Any] = {
        "busy": bool(lease["busy"]),
        "execution": None,
        "lease_key": lease.get("lease_key"),
        "holder_state": lease.get("holder_state"),
    }
    if not lease["busy"]:
        return view
    target = _stoppable(db, session_id, lease, _row(db, lease["lease_key"]))
    if target is not None:
        view["execution"] = {key: target[key] for key in ("id", "kind", "started_at")}
    return view


def _signal_cli(db, session_id: str, execution_id: str, target: dict[str, Any]) -> dict[str, Any]:
    if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
        raise RuntimeStoreError("storage_unavailable", detail="pidfd stop unavailable")
    fd = os.pidfd_open(target["pid"])
    try:
        for sig, grace in _STAGES:
            current = project_session_execution(db, session_id).get("execution")
            if not current or not hmac.compare_digest(current["id"], execution_id):
                raise RuntimeStoreError("execution_changed", detail="execution changed during stop")
            try:
                signal.pidfd_send_signal(fd, sig)
            except ProcessLookupError:
                break
            if select.select([fd], [], [], grace)[0]:
                break
        else:
            fresh = project_session_execution(db, session_id)
            if fresh.get("execution") and hmac.compare_digest(fresh["execution"]["id"], execution_id):
                raise RuntimeStoreError("execution_stop_timeout", detail="cli stop timed out; lease kept")
        fresh = project_session_execution(db, session_id)
        return {"status": "signalled", "busy": bool(fresh["busy"]), "execution": fresh.get("execution")}
    finally:
        os.close(fd)


def stop_cli_execution(db, session_id: str, execution_id: str) -> dict[str, Any]:
    """Signal one proven CLI incarnation. Does not delete the lease or touch other holders."""
    if not isinstance(execution_id, str) or re.fullmatch(r"[0-9a-f]{64}", execution_id) is None:
        raise RuntimeStoreError("invalid_params")
    with _STOP_LOCK:
        lease_view = project_session_execution(db, session_id)
        current = lease_view.get("execution")
        if not current or not hmac.compare_digest(current["id"], execution_id):
            raise RuntimeStoreError("unknown_execution", detail="cli execution is not a proven stoppable holder")
        from hermes_state_lease_read import read_session_turn_lease

        lease = read_session_turn_lease(db, session_id)
        target = _stoppable(db, session_id, lease, _row(db, lease["lease_key"]))
        if target is None or not hmac.compare_digest(target["id"], execution_id):
            raise RuntimeStoreError("unknown_execution", detail="cli execution changed before stop")
        return _signal_cli(db, session_id, execution_id, target)
