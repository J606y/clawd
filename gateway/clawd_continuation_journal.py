"""Sidecar-independent continuation journal. The only durable status source.

Writes are ``BEGIN IMMEDIATE``. A missing or unwritable journal fails closed:
callers must not admit a run. Status reads never insert a request or a run.
Opening adds the receipt reason column and does not delete receipts. Only
``reconcile_legacy_streak_receipts`` may remove a receipt row, and only a
provable success-streak lock. ``replace_inflight_body`` may replace the
payload of a pending or leased request. It does not delete receipts, events,
or the request row, and it does not change the request key, aliases, or
attempt count.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence


class JournalUnavailable(RuntimeError):
    """The continuation identity store cannot be opened or written."""


# Exact decision reasons. Events do not store identities; old NULL receipts
# are attributed by pairing this string with accepted_at.
STREAK_LIMIT_REASON = "streak_limit"
_TERMINAL_SAFETY = frozenset({"cancelled", "attempts_exhausted"})
_UNREADABLE_REASON = "\x00unreadable"


def _as_float(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _recorded_reason(value: Any) -> Optional[str]:
    """NULL and '' are unattributed. Non-strings are unreadable, not streak locks."""
    if value is None:
        return None
    if isinstance(value, str):
        return value or None
    return _UNREADABLE_REASON


def _blocked_at(
    events: Sequence[Mapping[str, Any]], *, session_id: str, instant: float,
) -> list[Mapping[str, Any]]:
    found = []
    for event in events:
        if event.get("session_id") != session_id or event.get("kind") != "blocked":
            continue
        if _as_float(event.get("created_at")) == instant:
            found.append(event)
    return found


def _safety_event_bound(
    event: Mapping[str, Any],
    receipts: Sequence[Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]],
) -> bool:
    """True when a safety-stop event was paired with its own receipt write.

    A later cancel/exhaust event with no bound receipt might have been meant
    for an older identity whose receipt write never landed. A streak receipt
    at that timestamp must not be treated as that binding.
    """
    detail = event.get("detail")
    if detail not in _TERMINAL_SAFETY:
        return False
    instant = _as_float(event.get("created_at"))
    session_id = event.get("session_id")
    if instant is None or not isinstance(session_id, str):
        return False
    mates = [
        row for row in receipts
        if row.get("session_id") == session_id
        and row.get("run_id") is None
        and _as_float(row.get("accepted_at")) == instant
    ]
    if any(_recorded_reason(row.get("reason")) == detail for row in mates):
        return True
    null_mates = [row for row in mates if _recorded_reason(row.get("reason")) is None]
    if not null_mates:
        return False
    details = {item.get("detail") for item in _blocked_at(events, session_id=session_id, instant=instant)}
    return details == {detail}


def _legacy_streak_receipt_releasable(
    receipt: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    receipts: Sequence[Mapping[str, Any]],
) -> bool:
    """Whether one row is a provable success-streak lock.

    ``run_id IS NULL`` is never sufficient. Recorded safety stops and admitted
    runs stay. A NULL reason needs a same-timestamp streak_limit event and no
    same-timestamp safety stop. A later safety stop that cannot be bound to
    its own receipt blocks release.
    """
    if receipt.get("run_id") is not None:
        return False
    reason = _recorded_reason(receipt.get("reason"))
    if reason not in (None, STREAK_LIMIT_REASON):
        return False
    instant = _as_float(receipt.get("accepted_at"))
    session_id = receipt.get("session_id")
    if instant is None or not isinstance(session_id, str) or not isinstance(receipt.get("identity"), str):
        return False
    same = _blocked_at(events, session_id=session_id, instant=instant)
    if any(item.get("detail") in _TERMINAL_SAFETY for item in same):
        return False
    if reason is None and not any(item.get("detail") == STREAK_LIMIT_REASON for item in same):
        return False
    for event in events:
        if event.get("session_id") != session_id or event.get("kind") != "blocked":
            continue
        if event.get("detail") not in _TERMINAL_SAFETY:
            continue
        created = _as_float(event.get("created_at"))
        if created is None or created <= instant:
            continue
        if not _safety_event_bound(event, receipts, events):
            return False
    return True


class ContinuationJournal:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._conn: Optional[sqlite3.Connection] = None

    def open(self) -> None:
        if self._conn is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(fd)
        try:
            conn = sqlite3.connect(self.path, timeout=5, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS receipts (
                    identity TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    accepted_at REAL NOT NULL,
                    run_id TEXT,
                    reason TEXT
                );
                CREATE TABLE IF NOT EXISTS requests (
                    session_id TEXT PRIMARY KEY,
                    request_key TEXT NOT NULL,
                    aliases_json TEXT NOT NULL,
                    body_json TEXT NOT NULL,
                    attempts INTEGER NOT NULL,
                    next_attempt_at REAL NOT NULL,
                    state TEXT NOT NULL,
                    last_error TEXT,
                    lease_holder TEXT,
                    lease_expires_at REAL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS watch (
                    session_id TEXT PRIMARY KEY,
                    until_at REAL NOT NULL
                );
                """
            )
            self._ensure_receipt_reason(conn)
            conn.commit()
        except sqlite3.Error as exc:
            raise JournalUnavailable(f"continuation journal unreadable: {exc}") from exc
        self._conn = conn

    def close(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            conn.close()

    def _require(self) -> sqlite3.Connection:
        if self._conn is None:
            raise JournalUnavailable("continuation journal is not open")
        return self._conn

    def _ensure_receipt_reason(self, conn: sqlite3.Connection) -> None:
        """Add the reason column. Does not update or delete existing rows."""
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(receipts)")}
        if "reason" in cols:
            return
        try:
            conn.execute("ALTER TABLE receipts ADD COLUMN reason TEXT")
        except sqlite3.OperationalError as exc:
            if "duplicate column" not in str(exc).lower():
                raise

    def event(self, session_id: str, kind: str, detail: str, now: float) -> None:
        conn = self._require()
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                "INSERT INTO events (session_id, kind, detail, created_at) VALUES (?, ?, ?, ?)",
                (session_id, kind, detail[:500], now),
            )
            conn.commit()
        except sqlite3.Error as exc:
            conn.rollback()
            raise JournalUnavailable(str(exc)) from exc

    def receipted(self, identities: list[str]) -> frozenset[str]:
        if not identities:
            return frozenset()
        conn = self._require()
        marks = ",".join("?" for _ in identities)
        rows = conn.execute(
            f"SELECT identity FROM receipts WHERE identity IN ({marks})", identities,
        ).fetchall()
        return frozenset(row["identity"] for row in rows)

    def receipt_record(self, identity: str) -> Optional[dict[str, Any]]:
        """Read one receipt. Does not insert, delete, or admit."""
        if not identity:
            return None
        conn = self._require()
        self._ensure_receipt_reason(conn)
        row = conn.execute(
            "SELECT identity, session_id, accepted_at, run_id, reason FROM receipts WHERE identity = ?",
            (identity,),
        ).fetchone()
        if row is None:
            return None
        return {
            "identity": row["identity"],
            "session_id": row["session_id"],
            "accepted_at": row["accepted_at"],
            "run_id": row["run_id"],
            "reason": row["reason"],
        }

    def add_receipts(
        self,
        identities: list[str],
        session_id: str,
        run_id: Optional[str],
        now: float,
        *,
        reason: Optional[str] = None,
    ) -> None:
        """Consume identities. Omit reason only for a legacy unattributed row.

        New callers must pass a non-empty reason. NULL reason is not a license
        to clear the row later.
        """
        if reason is not None and (not isinstance(reason, str) or not reason):
            raise JournalUnavailable("receipt reason must be a non-empty string when set")
        stored_reason = None if reason is None else reason[:500]
        conn = self._require()
        self._ensure_receipt_reason(conn)
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.executemany(
                "INSERT OR REPLACE INTO receipts (identity, session_id, accepted_at, run_id, reason) "
                "VALUES (?, ?, ?, ?, ?)",
                [(identity, session_id, now, run_id, stored_reason) for identity in identities],
            )
            conn.execute("DELETE FROM requests WHERE session_id = ?", (session_id,))
            conn.commit()
        except sqlite3.Error as exc:
            conn.rollback()
            raise JournalUnavailable(str(exc)) from exc

    def reconcile_legacy_streak_receipts(self, session_id: str) -> int:
        """Remove provable success-streak locks for one session.

        Does not delete the database file, events, requests, or every
        ``run_id IS NULL`` row. Returns how many receipt rows were removed.
        A common evaluate takes the read path and does not take a write lock.
        """
        if not session_id:
            return 0
        conn = self._require()
        self._ensure_receipt_reason(conn)
        if not self._streak_candidates(conn, session_id):
            return 0
        conn.execute("BEGIN IMMEDIATE")
        try:
            doomed = self._streak_candidates(conn, session_id)
            removed = 0
            for identity in doomed:
                cur = conn.execute(
                    "DELETE FROM receipts WHERE identity = ? AND session_id = ? AND run_id IS NULL "
                    "AND (reason IS NULL OR reason = ?)",
                    (identity, session_id, STREAK_LIMIT_REASON),
                )
                if cur.rowcount and cur.rowcount > 0:
                    removed += cur.rowcount
            conn.commit()
        except sqlite3.Error as exc:
            conn.rollback()
            raise JournalUnavailable(str(exc)) from exc
        return removed

    def _streak_candidates(self, conn: sqlite3.Connection, session_id: str) -> list[str]:
        receipt_rows = conn.execute(
            "SELECT identity, session_id, accepted_at, run_id, reason FROM receipts WHERE session_id = ?",
            (session_id,),
        ).fetchall()
        if not receipt_rows:
            return []
        event_rows = conn.execute(
            "SELECT session_id, kind, detail, created_at FROM events WHERE session_id = ?",
            (session_id,),
        ).fetchall()
        receipts = [dict(row) for row in receipt_rows]
        events = [dict(row) for row in event_rows]
        return [
            row["identity"]
            for row in receipts
            if _legacy_streak_receipt_releasable(row, events, receipts)
        ]

    def load_request(self, session_id: str) -> Optional[dict[str, Any]]:
        row = self._require().execute("SELECT * FROM requests WHERE session_id = ?", (session_id,)).fetchone()
        if row is None:
            return None
        return {
            "request_key": row["request_key"],
            "aliases": json.loads(row["aliases_json"]),
            "body": json.loads(row["body_json"]),
            "attempts": int(row["attempts"]),
            "next_attempt_at": float(row["next_attempt_at"]),
            "state": row["state"],
            "last_error": row["last_error"],
            "lease_holder": row["lease_holder"],
            "lease_expires_at": row["lease_expires_at"],
        }

    def compatible_inflight(self, session_id: str, aliases: list[str]) -> Optional[dict[str, Any]]:
        """Return a pending or leased freeze whose aliases are covered, else None.

        Does not insert or rewrite. ``freeze`` still returns that stored body
        unchanged when the same alias set comes back.
        """
        if not session_id:
            return None
        conn = self._require()
        try:
            row = conn.execute("SELECT * FROM requests WHERE session_id = ?", (session_id,)).fetchone()
        except sqlite3.Error as exc:
            raise JournalUnavailable(str(exc)) from exc
        if row is None:
            return None
        try:
            planned = json.loads(row["aliases_json"])
            body = json.loads(row["body_json"])
        except (TypeError, ValueError) as exc:
            raise JournalUnavailable("continuation request unreadable") from exc
        if not isinstance(planned, list) or not isinstance(body, dict):
            raise JournalUnavailable("continuation request unreadable")
        if set(planned) <= set(aliases) and row["state"] in {"pending", "leased"}:
            return {
                "request_key": row["request_key"],
                "aliases": planned,
                "body": body,
                "attempts": int(row["attempts"]),
                "state": row["state"],
            }
        return None

    def replace_inflight_body(
        self, session_id: str, request_key: str, body: dict[str, Any], now: float,
    ) -> bool:
        """Replace the payload of one pending or leased request.

        Does not change the request key, aliases, or attempt count, and does
        not delete receipts, events, or the request row. Returns False when
        the row is missing, terminal, or a different key. Callers must not
        use this when a run is already reserved under the previous body:
        ``freeze`` will not rewrite that alias, and neither does a missed key.
        """
        if not session_id or not request_key or not isinstance(body, dict):
            return False
        conn = self._require()
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT state, request_key FROM requests WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if row is None or row["state"] not in {"pending", "leased"} or row["request_key"] != request_key:
                conn.rollback()
                return False
            conn.execute(
                "UPDATE requests SET body_json = ?, updated_at = ? "
                "WHERE session_id = ? AND request_key = ? AND state IN ('pending', 'leased')",
                (json.dumps(body, ensure_ascii=False), now, session_id, request_key),
            )
            conn.commit()
        except sqlite3.Error as exc:
            conn.rollback()
            raise JournalUnavailable(str(exc)) from exc
        return True

    def freeze(self, session_id: str, key: str, aliases: list[str], body: dict[str, Any], now: float) -> dict[str, Any]:
        """Keep an already-admitted payload. A new batch must not rewrite it.

        A pending or leased row whose aliases are covered is returned as stored.
        A new effort tier must not be written by calling ``freeze`` again.
        """
        conn = self._require()
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute("SELECT * FROM requests WHERE session_id = ?", (session_id,)).fetchone()
            if row is not None:
                planned = json.loads(row["aliases_json"])
                if set(planned) <= set(aliases) and row["state"] in {"pending", "leased"}:
                    conn.commit()
                    return {
                        "request_key": row["request_key"],
                        "aliases": planned,
                        "body": json.loads(row["body_json"]),
                        "attempts": int(row["attempts"]),
                    }
                conn.execute("DELETE FROM requests WHERE session_id = ?", (session_id,))
            conn.execute(
                "INSERT INTO requests (session_id, request_key, aliases_json, body_json, attempts, "
                "next_attempt_at, state, last_error, lease_holder, lease_expires_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 0, ?, 'pending', NULL, NULL, NULL, ?, ?)",
                (session_id, key, json.dumps(aliases), json.dumps(body, ensure_ascii=False), now, now, now),
            )
            conn.commit()
        except sqlite3.Error as exc:
            conn.rollback()
            raise JournalUnavailable(str(exc)) from exc
        return {"request_key": key, "aliases": list(aliases), "body": body, "attempts": 0}

    def claim(self, session_id: str, holder: str, now: float, ttl: float) -> bool:
        conn = self._require()
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT lease_holder, lease_expires_at, state FROM requests WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if row is None:
                conn.rollback()
                return False
            expires = row["lease_expires_at"]
            live = row["lease_holder"] and isinstance(expires, (int, float)) and float(expires) > now
            if live and row["lease_holder"] != holder:
                conn.rollback()
                return False
            if row["state"] in {"admitted", "exhausted"}:
                conn.rollback()
                return False
            conn.execute(
                "UPDATE requests SET state = 'leased', lease_holder = ?, lease_expires_at = ?, updated_at = ? "
                "WHERE session_id = ?",
                (holder, now + ttl, now, session_id),
            )
            conn.commit()
        except sqlite3.Error as exc:
            conn.rollback()
            raise JournalUnavailable(str(exc)) from exc
        return True

    def release(self, session_id: str, holder: str, *, state: str = "pending") -> None:
        conn = self._require()
        conn.execute(
            "UPDATE requests SET state = ?, lease_holder = NULL, lease_expires_at = NULL, updated_at = ? "
            "WHERE session_id = ? AND lease_holder = ?",
            (state, 0.0, session_id, holder),
        )
        conn.commit()

    def record_attempt(self, session_id: str, attempts: int, error: str, next_at: float, now: float, state: str) -> None:
        """Persist the retry budget before the caller treats the attempt as spent."""
        conn = self._require()
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                "UPDATE requests SET attempts = ?, last_error = ?, next_attempt_at = ?, state = ?, updated_at = ? "
                "WHERE session_id = ?",
                (attempts, error[:500], next_at, state, now, session_id),
            )
            conn.commit()
        except sqlite3.Error as exc:
            conn.rollback()
            raise JournalUnavailable(str(exc)) from exc

    def watch(self, session_id: str, until_at: float) -> None:
        conn = self._require()
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                "INSERT INTO watch (session_id, until_at) VALUES (?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET until_at = MAX(until_at, excluded.until_at)",
                (session_id, until_at),
            )
            conn.commit()
        except sqlite3.Error as exc:
            conn.rollback()
            raise JournalUnavailable(str(exc)) from exc

    def watched(self, now: float) -> list[str]:
        rows = self._require().execute("SELECT session_id FROM watch WHERE until_at > ?", (now,)).fetchall()
        return [row["session_id"] for row in rows]

    def pending_sessions(self, now: float) -> list[str]:
        rows = self._require().execute(
            "SELECT session_id FROM requests WHERE state IN ('pending', 'leased') AND "
            "(lease_expires_at IS NULL OR lease_expires_at <= ? OR next_attempt_at <= ?)",
            (now, now),
        ).fetchall()
        return [row["session_id"] for row in rows]

    def status(self, session_id: str) -> dict[str, Any]:
        """Read-only. Must not create a request or a run."""
        conn = self._require()
        request = conn.execute("SELECT * FROM requests WHERE session_id = ?", (session_id,)).fetchone()
        event = conn.execute(
            "SELECT kind, detail, created_at FROM events WHERE session_id = ? ORDER BY id DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        payload: dict[str, Any] = {"session_id": session_id, "request": None, "last_event": None}
        if request is not None:
            payload["request"] = {
                "request_key": request["request_key"],
                "state": request["state"],
                "attempts": int(request["attempts"]),
                "last_error": request["last_error"],
                "next_attempt_at": request["next_attempt_at"],
            }
        if event is not None:
            payload["last_event"] = {
                "kind": event["kind"], "detail": event["detail"], "created_at": event["created_at"],
            }
        return payload
