"""Durable idempotency reservations for API server runs."""

import hmac
import json
import logging
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict

from hermes_cli.sqlite_util import add_column_if_missing


# Keep the extracted store's log records on the API server logger.
logger = logging.getLogger("gateway.platforms.api_server")

TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "interrupted"})

_SELECT_BY_KEY = (
    "SELECT fingerprint, run_id, status_json, owner_pid, owner_started, updated_at "
    "FROM run_idempotency WHERE scope=? AND idempotency_key=?")
_EXTEND_RETENTION_BY_KEY = (
    "UPDATE run_idempotency SET retention_until=MAX(retention_until, ?) "
    "WHERE scope=? AND idempotency_key=? AND fingerprint=?")
_EXTEND_RETENTION_BY_RUN = (
    "UPDATE run_idempotency SET retention_until=MAX(retention_until, ?) "
    "WHERE scope=? AND run_id=?")
# Columns added after the first schema shipped; applied when missing.
_MIGRATIONS = {
    "owner_pid": "INTEGER NOT NULL DEFAULT 0",
    "owner_started": "INTEGER NOT NULL DEFAULT 0",
    "retention_until": "REAL NOT NULL DEFAULT 0",
    "acknowledged_at": "REAL"}


def _encode_status(status: dict[str, Any]) -> str:
    return json.dumps(status, sort_keys=True, separators=(",", ":"))


def _record(run_id, status_json, owner_pid, owner_started, updated_at) -> dict[str, Any]:
    return {
        "run_id": run_id, "status": json.loads(status_json), "owner_pid": int(owner_pid or 0),
        "owner_started": int(owner_started or 0), "updated_at": float(updated_at or 0)}


def _outcome(row, fingerprint):
    """Classify a stored ``(scope, key)`` row against the caller's fingerprint."""
    return ("reused" if hmac.compare_digest(row[0], fingerprint) else "conflict"), _record(*row[1:])


_RUN_ID = re.compile(r"run_[0-9a-f]{32}")
_DISCOVERED_STATUSES = frozenset({
    "queued", "running", "waiting_for_approval", "stopping", "unknown",
    "completed", "cancelled", "failed", "interrupted",
})


def _public_discovered_run(row, session_id: str, *, keyed: bool) -> dict[str, Any] | None:
    """Stored public fields, or None when an unkeyed row belongs to another session."""
    import math
    from hermes_state_runtime import RuntimeStoreError

    run_id, raw, created_at, updated_at, retention = row
    try:
        status = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeStoreError("storage_unavailable") from exc
    if not isinstance(status, dict):
        raise RuntimeStoreError("storage_unavailable")
    owner = status.get("session_id")
    if not isinstance(owner, str):
        raise RuntimeStoreError("storage_unavailable")
    if owner != session_id:
        if keyed:
            raise RuntimeStoreError("session_mismatch")
        return None
    if type(retention) not in (int, float) or not math.isfinite(retention) or retention < 0:
        raise RuntimeStoreError("storage_unavailable")
    if retention and retention <= time.time():
        if keyed:
            raise RuntimeStoreError("storage_unavailable")
        return None
    if (
        not isinstance(run_id, str) or _RUN_ID.fullmatch(run_id) is None
        or status.get("run_id") not in (None, run_id)
        or status.get("status") not in _DISCOVERED_STATUSES
        or type(created_at) not in (int, float) or type(updated_at) not in (int, float)
        or not math.isfinite(created_at) or not math.isfinite(updated_at)
        or created_at < 0 or updated_at < created_at
    ):
        raise RuntimeStoreError("storage_unavailable")
    return {
        "run_id": run_id,
        "session_id": session_id,
        "status": status["status"],
        "created_at": created_at,
        "updated_at": updated_at,
    }


class RunIdempotencyStore:
    """Durable, tenant-scoped reservations for ``POST /v1/runs``: a unique ``(scope, key)`` row
    inserted inside ``BEGIN IMMEDIATE`` so separate workers cannot both admit one request. Only
    fingerprints and public run status are stored — never request bodies or credentials."""

    RETENTION_SECONDS = 24 * 60 * 60
    ACKNOWLEDGED_RETENTION_SECONDS = 24 * 60 * 60

    @property
    def durable(self) -> bool:
        """Whether reservations survive this process."""
        return self._db_path is not None
    def __init__(self, db_path: str | None = None):
        if db_path is None:
            try:
                from hermes_cli.config import get_hermes_home
                db_path = str(get_hermes_home() / "runs_idempotency.db")
            except Exception:
                db_path = ":memory:"
        self._db_path = None if db_path == ":memory:" else db_path
        try:
            self._conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
        except Exception as exc:
            # Docker may create the container object before `docker run` fails to start it (e.g. exit code
            # 125 when the daemon isn't ready, or a timeout mid-pull). That orphan is left in "Created"
            # state — which the exited-only orphan reaper (reap_orphan_containers, status=exited) never
            # catches, so it leaks permanently. Remove it by its known name before re-raising. See #7439.
            logger.warning(
                "Run idempotency storage is unavailable; falling back to "
                "process memory, so replay will not survive a restart: %s", exc)
            self._conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._db_path = None
        from hermes_state_wal import apply_wal_with_fallback
        apply_wal_with_fallback(self._conn, db_label="runs_idempotency.db")
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS run_idempotency (
                scope TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                run_id TEXT NOT NULL,
                status_json TEXT NOT NULL,
                owner_pid INTEGER NOT NULL DEFAULT 0,
                owner_started INTEGER NOT NULL DEFAULT 0,
                retention_until REAL NOT NULL DEFAULT 0,
                acknowledged_at REAL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (scope, idempotency_key)
            )"""
        )
        columns = {str(row[1]) for row in self._conn.execute("PRAGMA table_info(run_idempotency)")}
        for column, ddl in _MIGRATIONS.items():
            if column not in columns:
                add_column_if_missing(self._conn, "run_idempotency", column, f"{column} {ddl}")
        self._conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS run_idempotency_run_id ON run_idempotency(run_id)")
        self._conn.commit()
        self._lock = threading.Lock()
        self._tighten_permissions()

    def _tighten_permissions(self) -> None:
        for suffix in ("", "-wal", "-shm") if self._db_path else ():
            candidate = Path(self._db_path + suffix)
            try:
                if candidate.exists():
                    candidate.chmod(0o600)
            except OSError:
                logger.debug("Failed to restrict run idempotency store permissions", exc_info=True)

    @contextmanager
    def _immediate_txn(self):
        """Hold the lock inside ``BEGIN IMMEDIATE``; the body commits, errors roll back."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield
            except Exception:
                self._conn.rollback()
                raise

    def reserve(self, scope: str, key: str, fingerprint: str, run_id: str, status: dict[str, Any], *,
                owner_pid: int = 0, owner_started: int = 0, retention_until: float = 0):
        """Atomically reserve a key; return ``(outcome, stored_record)``."""
        now = time.time()
        retention_until = max(0.0, float(retention_until or 0))
        encoded = _encode_status(status)
        with self._immediate_txn():
            self._prune_stale_terminal_locked(now)
            row = self._conn.execute(_SELECT_BY_KEY, (scope, key)).fetchone()
            if row is not None:
                if retention_until:
                    self._conn.execute(_EXTEND_RETENTION_BY_KEY, (retention_until, scope, key, fingerprint))
                self._conn.commit()
                return _outcome(row, fingerprint)
            self._conn.execute(
                "INSERT INTO run_idempotency("
                "scope,idempotency_key,fingerprint,run_id,status_json,"
                "owner_pid,owner_started,retention_until,created_at,updated_at"
                ") VALUES(?,?,?,?,?,?,?,?,?,?)",
                (scope, key, fingerprint, run_id, encoded, int(owner_pid or 0), int(owner_started or 0),
                 retention_until, now, now))
            self._conn.commit()
            return "created", _record(run_id, encoded, owner_pid, owner_started, now) | {"status": status}

    def lookup(self, scope: str, key: str, fingerprint: str, *, retention_until: float = 0):
        """Return ``missing``, ``reused`` or ``conflict`` without reserving."""
        now = time.time()
        retention_until = max(0.0, float(retention_until or 0))
        with self._immediate_txn():
            if retention_until:
                self._conn.execute(_EXTEND_RETENTION_BY_KEY, (retention_until, scope, key, fingerprint))
            self._prune_stale_terminal_locked(now)
            row = self._conn.execute(_SELECT_BY_KEY, (scope, key)).fetchone()
            self._conn.commit()
        return ("missing", None) if row is None else _outcome(row, fingerprint)

    def _prune_stale_terminal_locked(self, now: float) -> None:
        """Prune aged replay records only once their stored run is terminal (caller holds the
        lock + transaction): a long or disconnected room turn may outlive the retention window."""
        stale = self._conn.execute(
            """SELECT scope, idempotency_key, status_json
                 FROM run_idempotency
                WHERE acknowledged_at <= ?
                   OR (retention_until > 0 AND retention_until <= ?)
                   OR (retention_until <= 0 AND updated_at < ?)""",
            (now - self.ACKNOWLEDGED_RETENTION_SECONDS, now, now - self.RETENTION_SECONDS),
        ).fetchall()
        for stale_scope, stale_key, stale_status in stale:
            try:
                terminal = json.loads(stale_status).get("status") in TERMINAL_STATUSES
            except Exception:
                terminal = False
            if terminal:
                self._conn.execute(
                    "DELETE FROM run_idempotency WHERE scope=? AND idempotency_key=?", (stale_scope, stale_key))

    def status_for_run(self, scope: str, run_id: str, *, retention_until: float = 0) -> dict[str, Any] | None:
        """Load one durable run status inside its authenticated scope."""
        retention_until = max(0.0, float(retention_until or 0))
        with self._lock:
            if retention_until:
                self._conn.execute(_EXTEND_RETENTION_BY_RUN, (retention_until, scope, run_id))
                self._conn.commit()
            row = self._conn.execute(
                "SELECT status_json, owner_pid, owner_started, updated_at "
                "FROM run_idempotency WHERE scope=? AND run_id=?",
                (scope, run_id)).fetchone()
        if row is None:
            return None
        return {k: v for k, v in _record(None, *row).items() if k != "run_id"}

    def extend_retention(self, scope: str, run_id: str, until: float) -> bool:
        """Persist the latest verified recovery horizon for an active grant."""
        checked_until = max(0.0, float(until or 0))
        if not checked_until:
            return False
        with self._lock:
            changed = self._conn.execute(_EXTEND_RETENTION_BY_RUN, (checked_until, scope, run_id)).rowcount
            self._conn.commit()
        return changed == 1

    def owns_run(self, scope: str, run_id: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM run_idempotency WHERE scope=? AND run_id=?", (scope, run_id)).fetchone()
        return row is not None

    def update_status(self, run_id: str, status: dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE run_idempotency SET status_json=?, updated_at=? WHERE run_id=?",
                (_encode_status(status), time.time(), run_id))
            self._conn.commit()

    def forget(self, scope: str, key: str) -> None:
        """Release a reservation whose run was refused before it existed."""
        with self._lock:
            self._conn.execute("DELETE FROM run_idempotency WHERE scope=? AND idempotency_key=?", (scope, key))
            self._conn.commit()

    def discover_for_session(self, session_id: str, *, idempotency_key: str | None = None, scope: str | None = None) -> dict[str, Any] | None:
        """Read the stored run for a session. Does not rewrite ``status_json`` or prune rows.

        An unreadable, unscoped, or expired keyed row is ``storage_unavailable``, not proof of idle.
        A dead owner does not change the stored status; callers use ``GET /v1/runs/{id}`` for authority.
        ``scope`` binds the lookup to the authenticated tenant. Without it, a caller must already
        be inside that tenant's store.
        """
        from hermes_state_runtime import RuntimeStoreError

        if not isinstance(session_id, str) or not session_id:
            raise RuntimeStoreError("invalid_params")
        scope_sql = " AND scope=?" if scope is not None else ""
        scope_args = (scope,) if scope is not None else ()
        with self._lock:
            if idempotency_key is not None:
                rows = self._conn.execute(
                    "SELECT run_id, status_json, created_at, updated_at, retention_until "
                    f"FROM run_idempotency WHERE idempotency_key=?{scope_sql} LIMIT 2",
                    (idempotency_key, *scope_args),
                ).fetchall()
                if len(rows) > 1:
                    raise RuntimeStoreError("admission_conflict")
                return None if not rows else _public_discovered_run(rows[0], session_id, keyed=True)
            rows = self._conn.execute(
                "SELECT run_id, status_json, created_at, updated_at, retention_until "
                f"FROM run_idempotency WHERE 1=1{scope_sql} ORDER BY created_at DESC, updated_at DESC, run_id DESC",
                scope_args,
            ).fetchall()
            for row in rows:
                found = _public_discovered_run(row, session_id, keyed=False)
                if found is not None:
                    return found
        return None

    def close(self) -> None:
        with self._lock:
            self._conn.close()
