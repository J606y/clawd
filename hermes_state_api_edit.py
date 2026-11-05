"""API edit branch: copy the display prefix, do not end the source.

Idempotency is the request id in ``state_meta``, not a panel-private JSON blob.
Guards are the existing transaction checks (idle, lineage-root lease, compression
lock, display order). Input origin is not consulted and does not authorize the edit.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

from hermes_state_mutation_guards import require_idle
from hermes_state_runtime import RuntimeStoreError

EDIT_PREFIX = "gateway.api.edit_branch.v1."
_FIELDS = ("sessionId", "fromEnd", "expectedContent", "expectedTimestamp", "requestId", "model", "provider")
_REQUEST_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_MAX_ROWS = 10000
_MAX_BYTES = 8 * 1024 * 1024
_UNSAFE = (
    "--- 附件：",
    "[附件已上传：",
    "[screenshot",
    "[out-of-band user message",
    "[steer",
    "<user_steering",
    "[user steering",
)


def validate_edit_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != set(_FIELDS):
        raise RuntimeStoreError("invalid_params")
    for key, limit in (("sessionId", 256), ("model", 512), ("provider", 128)):
        value = payload[key]
        if not isinstance(value, str) or not value.strip() or len(value) > limit or "\x00" in value:
            raise RuntimeStoreError("invalid_params")
    stamp = payload["expectedTimestamp"]
    if (
        type(payload["fromEnd"]) is not int
        or payload["fromEnd"] < 1
        or not isinstance(payload["expectedContent"], str)
        or type(stamp) not in (int, float)
        or not math.isfinite(stamp)
        or _REQUEST_ID.fullmatch(payload["requestId"]) is None
    ):
        raise RuntimeStoreError("invalid_params")
    return payload


def _digest(payload: dict[str, Any]) -> str:
    body = {key: payload[key] for key in _FIELDS}
    encoded = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _shown(db, content: Any) -> Any:
    return db._decode_content(content)


def _plain(db, row) -> bool:
    content = _shown(db, row["content"])
    if row["role"] != "user" or not isinstance(content, str):
        return False
    lower = content.lower()
    if any(marker in lower for marker in _UNSAFE):
        return False
    try:
        if isinstance(json.loads(content), (dict, list)):
            return False
    except ValueError:
        pass
    kind = row["display_kind"] if "display_kind" in row.keys() else None
    if kind not in (None, "", "text", "message", "user", "steer"):
        return False
    for key in ("api_content", "display_metadata", "tool_calls", "tool_call_id"):
        if key in row.keys() and row[key] not in (None, "", "[]", "{}"):
            return False
    return True


def _calls(raw: Any) -> list:
    if not raw:
        return []
    if isinstance(raw, list):
        return raw
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeStoreError("incomplete_tool_history") from exc
    if not isinstance(parsed, list):
        raise RuntimeStoreError("incomplete_tool_history")
    return parsed


def _tool_guard(prefix: list) -> int:
    pending: set[str] = set()
    seen: set[str] = set()
    count = 0
    for row in prefix:
        calls = _calls(row["tool_calls"] if "tool_calls" in row.keys() else None)
        if calls and row["role"] != "assistant":
            raise RuntimeStoreError("incomplete_tool_history")
        for call in calls:
            call_id = call.get("id") if isinstance(call, dict) else None
            if not isinstance(call_id, str) or not call_id or call_id in seen:
                raise RuntimeStoreError("incomplete_tool_history")
            seen.add(call_id)
            pending.add(call_id)
            count += 1
        result_id = row["tool_call_id"] if "tool_call_id" in row.keys() else None
        if row["role"] == "tool":
            if not isinstance(result_id, str) or result_id not in pending or calls:
                raise RuntimeStoreError("incomplete_tool_history")
            pending.remove(result_id)
        elif result_id or (row["role"] == "user" and pending):
            raise RuntimeStoreError("incomplete_tool_history")
    if pending:
        raise RuntimeStoreError("incomplete_tool_history")
    return count


def _bytes(row) -> int:
    total = 0
    for key in row.keys():
        value = row[key]
        if isinstance(value, str):
            total += len(value.encode("utf-8"))
        elif isinstance(value, bytes):
            total += len(value)
        elif value is not None:
            total += len(str(value))
    return total


def _summary(conn, db, session_id: str) -> dict[str, Any]:
    session = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
    if session is None:
        raise RuntimeStoreError("storage_unavailable", detail="edit child missing")
    rows = conn.execute(
        "SELECT role, content, timestamp FROM messages WHERE session_id=? ORDER BY id",
        (session_id,),
    ).fetchall()
    preview = next((_shown(db, row["content"]) for row in reversed(rows) if row["role"] == "user"), None)
    return {"session": {
        "id": session["id"],
        "source": session["source"],
        "title": session["title"],
        "model": session["model"],
        "started_at": session["started_at"],
        "last_active": rows[-1]["timestamp"] if rows else None,
        "message_count": len(rows),
        "preview": preview if isinstance(preview, str) else None,
        "pinned": False,
        "archived": False,
    }}


def _same_receipt(saved: dict | None, digest: str, existing, payload: dict[str, Any], child_id: str) -> bool:
    if not saved or saved.get("digest") != digest or saved.get("child_session_id") != child_id:
        return False
    if existing is None:
        raise RuntimeStoreError("storage_unavailable", detail="edit receipt has no child")
    return existing["source"] == "api_server" and existing["model"] == payload["model"]


def api_edit_branch(db, payload: Any) -> dict[str, Any]:
    """Create or replay an edit copy. The source session is not ended."""
    payload = validate_edit_payload(payload)
    child_id = "api_edit_" + payload["requestId"]
    digest = _digest(payload)
    source_id = payload["sessionId"]

    def write(conn):
        from hermes_state_errors import (
            CompressionSessionClosedError, SessionCompressionInProgressError, SessionTurnLeaseLostError)

        saved_row = conn.execute("SELECT value FROM state_meta WHERE key=?", (EDIT_PREFIX + payload["requestId"],)).fetchone()
        saved = json.loads(saved_row[0]) if saved_row is not None else None
        existing = conn.execute("SELECT id, source, model, parent_session_id FROM sessions WHERE id=?", (child_id,)).fetchone()
        if saved is not None or existing is not None:
            if not _same_receipt(saved, digest, existing, payload, child_id):
                raise RuntimeStoreError("edit_request_conflict")
            return _summary(conn, db, child_id)
        source = conn.execute("SELECT * FROM sessions WHERE id=?", (source_id,)).fetchone()
        if source is None:
            raise RuntimeStoreError("not_found")
        ended = (source["ended_at"], source["end_reason"])
        if (source["end_reason"] or "") == "compression":
            raise RuntimeStoreError("unsupported_edit_history")
        try:
            require_idle(db, conn, [source_id])
        except (SessionCompressionInProgressError, SessionTurnLeaseLostError) as exc:
            raise RuntimeStoreError("session_busy", field="compression" if isinstance(exc, SessionCompressionInProgressError) else None) from exc
        except CompressionSessionClosedError as exc:
            raise RuntimeStoreError("unsupported_edit_history") from exc
        from hermes_state_messages import _DISPLAY_INDEX_MISSING_SQL

        def _strict(loaded) -> bool:
            orders = [row["display_order"] for row in loaded]
            return all(type(order) in (int, float) and order == order for order in orders) and all(
                a < b for a, b in zip(orders, orders[1:])
            )

        rows = list(db._display_rows_from_conn(conn, source_id))
        if len(rows) > _MAX_ROWS:
            raise RuntimeStoreError("edit_prefix_too_large")
        if conn.execute(_DISPLAY_INDEX_MISSING_SQL, (source_id,)).fetchone() or not _strict(rows):
            try:
                db._reconcile_display_orders(conn, source_id)
            except Exception as exc:
                raise RuntimeStoreError("unsupported_message_order") from exc
            if conn.execute(_DISPLAY_INDEX_MISSING_SQL, (source_id,)).fetchone():
                raise RuntimeStoreError("unsupported_message_order")
            rows = list(db._display_rows_from_conn(conn, source_id))
            if len(rows) > _MAX_ROWS:
                raise RuntimeStoreError("edit_prefix_too_large")
            if not _strict(rows):
                raise RuntimeStoreError("unsupported_message_order")
        if payload["fromEnd"] > len(rows):
            raise RuntimeStoreError("edit_boundary_changed")
        boundary = rows[-payload["fromEnd"]]
        shown = _shown(db, boundary["content"])
        if shown != payload["expectedContent"] or boundary["timestamp"] != payload["expectedTimestamp"]:
            raise RuntimeStoreError("edit_boundary_changed")
        matches = sum(
            1 for row in rows
            if _shown(db, row["content"]) == payload["expectedContent"] and row["timestamp"] == payload["expectedTimestamp"]
        )
        if matches != 1:
            raise RuntimeStoreError("ambiguous_edit_boundary")
        if not _plain(db, boundary):
            raise RuntimeStoreError("unsafe_edit_boundary")
        prefix = rows[:-payload["fromEnd"]]
        if sum(_bytes(row) for row in prefix) > _MAX_BYTES:
            raise RuntimeStoreError("edit_prefix_too_large")
        tool_count = _tool_guard(prefix)
        from clawd_projects.inheritance import ProjectInheritanceError

        try:
            db._publish_child_session_row(
                conn, source, parent_session_id=source_id, child_session_id=child_id, source="api_server",
                model=payload["model"], model_config={"_branched_from": source_id}, system_prompt=None,
                cwd=source["cwd"], profile_name=source["profile_name"],
            )
        except ProjectInheritanceError as exc:
            raise RuntimeStoreError("project_inheritance_failed") from exc
        conn.execute(
            "UPDATE sessions SET system_prompt_hash=?, session_key=NULL, chat_id=NULL WHERE id=?",
            (source["system_prompt_hash"], child_id),
        )
        ids = [int(row["id"]) for row in prefix]
        if ids:
            db._clone_message_rows(conn, ids, session_id=child_id)
        conn.execute(
            "UPDATE sessions SET message_count=?, tool_call_count=? WHERE id=?",
            (len(ids), tool_count, child_id),
        )
        title = f"{source['title'] or '新对话'} · 编辑副本 {payload['requestId'][:8]}"
        db._set_session_title_in_transaction(conn, child_id, title, source=db.TITLE_SOURCE_DERIVED)
        after = conn.execute("SELECT ended_at, end_reason FROM sessions WHERE id=?", (source_id,)).fetchone()
        if after is None or (after["ended_at"], after["end_reason"]) != ended:
            raise RuntimeStoreError("storage_unavailable", detail="edit must not end the source")
        conn.execute(
            "INSERT INTO state_meta(key, value) VALUES(?, ?)",
            (EDIT_PREFIX + payload["requestId"], json.dumps({
                "request_id": payload["requestId"], "child_session_id": child_id, "digest": digest,
            }, sort_keys=True)),
        )
        return _summary(conn, db, child_id)

    return db._execute_write(write)
