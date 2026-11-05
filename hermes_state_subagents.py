"""Subagent list and running-child count from the caller's SessionDB.

``tools.async_delegation.active_count`` remains the completion-unit count used
by drain and continuation. ``active_task_count`` counts every goal of a live
batch until that unit finalizes, so a child that has already ended in
``sessions`` is still included. This projection does not replace either
function. It counts child session rows, and a read failure raises instead of
reporting zero.
"""

from __future__ import annotations

import json
import time
from typing import Any

from hermes_state_runtime import RuntimeStoreError

STALL_S = 600
_MAX_LIMIT = 50


def _public_text(text: str) -> str:
    try:
        from agent.redact import redact_sensitive_text

        redacted = redact_sensitive_text(text)
        return redacted if isinstance(redacted, str) else ""
    except Exception:
        return ""


def _safe_args(raw: Any) -> dict[str, Any]:
    try:
        args = json.loads(raw) if isinstance(raw, str) else {}
    except json.JSONDecodeError:
        return {}
    if not isinstance(args, dict):
        return {}
    out: dict[str, Any] = {}
    for key, value in list(args.items())[:8]:
        if not isinstance(key, str):
            continue
        if isinstance(value, str):
            out[key] = _public_text(value)[:120]
        elif type(value) in (int, float, bool) or value is None:
            out[key] = value
    return out


def _counts(conn, parent_id: str) -> dict[str, int]:
    where = "s.source = 'subagent'"
    params: list[Any] = []
    if parent_id:
        where += " AND s.parent_session_id = ?"
        params.append(parent_id)
    now = time.time()
    row = conn.execute(
        f"""SELECT
              COUNT(*) AS total,
              SUM(CASE WHEN s.ended_at IS NULL AND COALESCE(
                    (SELECT MAX(m.timestamp) FROM messages m WHERE m.session_id = s.id), s.started_at, 0
                  ) >= ? THEN 1 ELSE 0 END) AS running,
              SUM(CASE WHEN s.ended_at IS NULL AND COALESCE(
                    (SELECT MAX(m.timestamp) FROM messages m WHERE m.session_id = s.id), s.started_at, 0
                  ) < ? THEN 1 ELSE 0 END) AS stalled
            FROM sessions s WHERE {where}""",
        (now - STALL_S, now - STALL_S, *params),
    ).fetchone()
    if row is None:
        raise RuntimeStoreError("storage_unavailable", detail="subagent count missing")
    return {
        "total": int(row["total"] or 0),
        "running": int(row["running"] or 0),
        "stalled": int(row["stalled"] or 0),
    }


def count_running_children(db, parent_id: str = "") -> int:
    """Full running-child count from session rows. Raises on store failure; never substitutes 0."""
    if parent_id and (not isinstance(parent_id, str) or len(parent_id) > 512):
        raise RuntimeStoreError("invalid_params")
    try:
        with db._read_ctx() as conn:
            return _counts(conn, parent_id)["running"]
    except RuntimeStoreError:
        raise
    except Exception as exc:
        raise RuntimeStoreError("storage_unavailable", detail="subagent count failed") from exc


_RECEIPT_DONE = frozenset({"completed", "success", "error", "failed", "interrupted", "cancelled"})


def _receipt_links(raw: Any) -> list[dict[str, Any]]:
    try:
        task = json.loads(raw) if isinstance(raw, str) else {}
    except json.JSONDecodeError as exc:
        raise RuntimeStoreError("storage_unavailable", detail="delegation receipt unreadable") from exc
    links = task.get("child_links") if isinstance(task, dict) else None
    if not isinstance(links, list):
        return []
    return [item for item in links if isinstance(item, dict)]


def count_live_subagents(db, parent_id: str = "") -> int:
    """Running session children plus dispatched children not yet stored.

    Identity is ``child_session_id`` from the durable receipt. A batch index is
    not a child. A finished receipt child is not live just because its unit is.
    Read failure raises; it does not become 0.
    """
    if parent_id and (not isinstance(parent_id, str) or len(parent_id) > 512):
        raise RuntimeStoreError("invalid_params")
    try:
        with db._read_ctx() as conn:
            stored_rows = conn.execute(
                "SELECT id FROM sessions s WHERE s.source = 'subagent' AND s.ended_at IS NULL"
                + (" AND s.parent_session_id = ?" if parent_id else ""),
                (parent_id,) if parent_id else (),
            ).fetchall()
            stored_ids = {row[0] for row in stored_rows if row[0]}
            where = "state IN ('running', 'finalizing')"
            params: list[Any] = []
            if parent_id:
                where += " AND parent_session_id = ?"
                params.append(parent_id)
            rows = conn.execute(
                f"SELECT task_json, result_json FROM async_delegations WHERE {where}",
                params,
            ).fetchall()
    except RuntimeStoreError:
        raise
    except Exception as exc:
        raise RuntimeStoreError("storage_unavailable", detail="subagent receipt count failed") from exc
    seen = set(stored_ids)
    extra = 0
    for row in rows:
        task_json, result_json = row[0], row[1]
        links = _receipt_links(task_json)
        finished: set[str] = set()
        if result_json:
            try:
                partial = json.loads(result_json)
            except json.JSONDecodeError as exc:
                raise RuntimeStoreError("storage_unavailable", detail="delegation result unreadable") from exc
            for entry in (partial.get("results") if isinstance(partial, dict) else None) or []:
                if isinstance(entry, dict) and str(entry.get("status") or "").lower() in _RECEIPT_DONE:
                    sid = entry.get("child_session_id")
                    if isinstance(sid, str) and sid:
                        finished.add(sid)
                        seen.discard(sid)
        for link in links:
            sid = link.get("child_session_id")
            if isinstance(sid, str) and sid and sid not in finished:
                seen.add(sid)
            elif not isinstance(sid, str) or not sid:
                extra += 1
    return len(seen) + extra


def _profile_key(db) -> str:
    """Identity of the opened SessionDB. Empty when the store cannot prove it.

    ``SessionDB._own_profile_name`` reads ``db_path`` only: the default root is
    ``default``, ``<root>/profiles/<name>`` is that name, and anything else is
    None. A missing proof is not filled with ``default``, the process home, the
    last adapter, or a URL.
    """
    owner = getattr(db, "_own_profile_name", None)
    if not callable(owner):
        return ""
    try:
        name = owner()
    except Exception:
        return ""
    if not isinstance(name, str):
        return ""
    text = name.strip()
    if not text or len(text) > 64 or any(ch in text for ch in "\n\r\0/\\"):
        return ""
    return text


def _execution_status(ended, stalled: bool, end_reason: Any) -> str:
    """Execution fact. Distinct from the panel status done/ended."""
    if ended is None:
        return "stalled" if stalled else "running"
    reason = str(end_reason or "").strip().lower()
    if reason in ("", "agent_close"):
        return "returned"
    if "cancel" in reason:
        return "cancelled"
    if reason in {"interrupted", "killed", "stopped", "kill", "stop"} or "interrupt" in reason:
        return "interrupted"
    if reason in {"error", "failed", "failure"} or "fail" in reason:
        return "failed"
    return "unknown"


def _clean_link_id(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > 256 or any(ch in text for ch in "\n\r\0"):
        return None
    return text


def _receipt_claims(conn, parent_id: str) -> dict[str, dict[str, Any]]:
    """Agree receipt links by child session id. A batch index or subagent id is not a key.

    Duplicate identical links agree. Disagreeing links withhold delegation and
    todo ids for that child. A receipt whose parent is not the row's parent is
    ignored, and the session row's own model_config is kept.
    """
    sql = "SELECT parent_session_id, task_json FROM async_delegations"
    params: tuple[Any, ...] = ()
    if parent_id:
        sql += " WHERE parent_session_id = ?"
        params = (parent_id,)
    try:
        rows = conn.execute(sql, params).fetchall()
    except Exception as exc:
        if "no such table" in str(exc).lower():
            return {}
        raise
    grouped: dict[str, list[dict[str, str | None]]] = {}
    for row in rows:
        receipt_parent = row["parent_session_id"]
        if parent_id and receipt_parent != parent_id:
            continue
        parent_text = receipt_parent if isinstance(receipt_parent, str) else ""
        for link in _receipt_links(row["task_json"]):
            child_session_id = _clean_link_id(link.get("child_session_id"))
            if child_session_id is None:
                continue
            grouped.setdefault(child_session_id, []).append({
                "parent": parent_text,
                "delegation_id": _clean_link_id(link.get("delegation_id")),
                "todo_id": _clean_link_id(link.get("todo_id")),
            })
    agreed: dict[str, dict[str, Any]] = {}
    for child_session_id, claims in grouped.items():
        signatures = {(item["parent"], item["delegation_id"], item["todo_id"]) for item in claims}
        if len(signatures) != 1:
            agreed[child_session_id] = {"conflict": True}
            continue
        parent, delegation_id, todo_id = next(iter(signatures))
        agreed[child_session_id] = {
            "conflict": False,
            "parent": parent,
            "delegation_id": delegation_id,
            "todo_id": todo_id,
        }
    return agreed


def _merged_link(stored: str | None, receipt: str | None) -> str | None:
    if stored and receipt and stored != receipt:
        return None
    return stored or receipt


def _link_ids(config: Any, receipt: dict[str, Any] | None, row_parent: str) -> tuple[str | None, str | None]:
    stored_delegation = _clean_link_id(_link_text(config, "_clawd_delegation_id"))
    stored_todo = _clean_link_id(_link_text(config, "_clawd_todo_id"))
    if not receipt:
        return stored_delegation, stored_todo
    if receipt.get("conflict") or receipt.get("parent") != row_parent:
        if receipt.get("conflict"):
            return None, None
        return stored_delegation, stored_todo
    return (
        _merged_link(stored_delegation, receipt.get("delegation_id")),
        _merged_link(stored_todo, receipt.get("todo_id")),
    )


def _link_text(config: Any, key: str) -> str | None:
    if not isinstance(config, dict):
        return None
    value = config.get(key)
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _page_row(conn, row, now: float, profile_key: str, claims: dict[str, dict[str, Any]]) -> dict[str, Any]:
    recent = conn.execute(
        "SELECT role, tool_name, tool_calls, substr(content, 1, 240) AS text, timestamp "
        "FROM messages WHERE session_id = ? ORDER BY id DESC LIMIT 8",
        (row["id"],),
    ).fetchall()
    last_at = recent[0]["timestamp"] if recent else row["startedAt"]
    tool = None
    snippet = ""
    for message in recent:
        if tool is None and message["role"] == "assistant" and message["tool_calls"] not in (None, "", "[]"):
            try:
                calls = json.loads(message["tool_calls"])
            except json.JSONDecodeError:
                calls = []
            if isinstance(calls, list) and calls and isinstance(calls[-1], dict):
                fn = calls[-1].get("function") or {}
                if isinstance(fn, dict):
                    tool = {"name": fn.get("name") or "tool", "args": _safe_args(fn.get("arguments"))}
        if not snippet and message["role"] == "assistant" and isinstance(message["text"], str) and message["text"].strip():
            snippet = _public_text(message["text"].strip())[:240]
        if tool is not None and snippet:
            break
    if row["endedAt"] is not None:
        status = "done" if (row["endReason"] or "") in ("agent_close", "") else "ended"
    else:
        status = "running" if now - (last_at or 0) < STALL_S else "stalled"
    stalled = status == "stalled"
    config = row["modelConfig"] if "modelConfig" in row.keys() else None
    if isinstance(config, str) and config.strip():
        try:
            config = json.loads(config)
        except json.JSONDecodeError:
            config = None
    item = {
        "id": row["id"],
        "parentId": row["parentId"],
        "title": row["title"],
        "model": row["model"],
        "startedAt": row["startedAt"],
        "endedAt": row["endedAt"],
        "endReason": row["endReason"],
        "messages": int(row["messages"] or 0),
        "toolCalls": int(row["toolCalls"] or 0),
        "apiCalls": int(row["apiCalls"] or 0),
        "tokens": int(row["tokens"] or 0),
        "parentTitle": row["parentTitle"],
        "status": status,
        "lastActivityAt": last_at,
        "lastTool": tool,
        "snippet": snippet,
        "executionStatus": _execution_status(row["endedAt"], stalled, row["endReason"]),
    }
    if profile_key:
        item["profileKey"] = profile_key
    delegation_id, todo_id = _link_ids(config, claims.get(row["id"]), row["parentId"] or "")
    if delegation_id is not None:
        item["delegationId"] = delegation_id
    if todo_id is not None:
        item["todoId"] = todo_id
    return item


def list_subagents(db, parent_id: str = "", *, limit: int = 12, offset: int = 0) -> dict[str, Any]:
    """Page child sessions. ``limit`` does not cap ``running`` or ``total``."""
    if parent_id and (not isinstance(parent_id, str) or len(parent_id) > 512):
        raise RuntimeStoreError("invalid_params")
    if type(limit) is not int or type(offset) is not int or not 1 <= limit <= _MAX_LIMIT or offset < 0:
        raise RuntimeStoreError("invalid_params")
    where = "s.source = 'subagent'"
    params: list[Any] = []
    if parent_id:
        where += " AND s.parent_session_id = ?"
        params.append(parent_id)
    try:
        with db._read_ctx() as conn:
            counts = _counts(conn, parent_id)
            rows = conn.execute(
                "SELECT s.id, s.parent_session_id AS parentId, s.title, s.model, s.started_at AS startedAt, "
                "s.ended_at AS endedAt, s.end_reason AS endReason, COALESCE(s.message_count,0) AS messages, "
                "COALESCE(s.tool_call_count,0) AS toolCalls, COALESCE(s.api_call_count,0) AS apiCalls, "
                "COALESCE(s.input_tokens,0)+COALESCE(s.output_tokens,0) AS tokens, p.title AS parentTitle, "
                "s.model_config AS modelConfig "
                f"FROM sessions s LEFT JOIN sessions p ON p.id = s.parent_session_id WHERE {where} "
                "ORDER BY (s.ended_at IS NULL) DESC, s.started_at DESC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
            now = time.time()
            profile_key = _profile_key(db)
            claims = _receipt_claims(conn, parent_id)
            data = [_page_row(conn, row, now, profile_key, claims) for row in rows]
            if parent_id:
                data = [item for item in data if item.get("parentId") == parent_id]
    except RuntimeStoreError:
        raise
    except Exception as exc:
        raise RuntimeStoreError("storage_unavailable", detail="subagent list failed") from exc
    payload = {
        "data": data,
        "running": counts["running"],
        "stalled": counts["stalled"],
        "total": counts["total"],
        "now": time.time(),
        "limit": limit,
        "offset": offset,
    }
    if profile_key:
        payload["profileKey"] = profile_key
    return payload
