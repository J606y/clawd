"""Last API-call context snapshot written beside cumulative token deltas.

The panel ring needs the last call's prompt size, not the session sum and not
an ``agent.log`` scrape. The snapshot is stored in ``state_meta`` by the same
``update_token_counts`` transaction that applies the cumulative delta.
"""

from __future__ import annotations

import json
import math
import time
from typing import Any

LAST_CALL_PREFIX = "session_last_api_call.v1."
_FIELDS = ("prompt_tokens", "output_tokens", "cache_read_tokens", "model", "provider", "base_url", "api_call", "at")


def last_call_key(session_id: str) -> str:
    return LAST_CALL_PREFIX + session_id


def normalize_last_call(raw: Any) -> dict[str, Any] | None:
    """Return a snapshot dict, or None when the caller did not supply one."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("last_api_call must be an object")
    prompt = raw.get("prompt_tokens")
    if type(prompt) is not int or prompt < 0:
        raise ValueError("last_api_call.prompt_tokens must be a non-negative int")
    output = raw.get("output_tokens", 0)
    cached = raw.get("cache_read_tokens", 0)
    call = raw.get("api_call", 0)
    at = raw.get("at", time.time())
    for name, value in (("output_tokens", output), ("cache_read_tokens", cached), ("api_call", call)):
        if type(value) is not int or value < 0:
            raise ValueError(f"last_api_call.{name} must be a non-negative int")
    if type(at) not in (int, float) or not math.isfinite(at) or at < 0:
        raise ValueError("last_api_call.at must be a finite timestamp")
    model = raw.get("model") or ""
    provider = raw.get("provider") or ""
    base_url = raw.get("base_url") or ""
    if not all(isinstance(value, str) and len(value) <= 512 for value in (model, provider, base_url)):
        raise ValueError("last_api_call route fields must be short strings")
    return {
        "prompt_tokens": prompt,
        "output_tokens": output,
        "cache_read_tokens": cached,
        "model": model,
        "provider": provider,
        "base_url": base_url,
        "api_call": call,
        "at": float(at),
    }


def write_last_call(conn, session_id: str, raw: Any) -> None:
    """Upsert the snapshot on the caller's open write transaction."""
    snapshot = normalize_last_call(raw)
    if snapshot is None:
        return
    conn.execute(
        "INSERT INTO state_meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (last_call_key(session_id), json.dumps({key: snapshot[key] for key in _FIELDS}, sort_keys=True)),
    )


def read_last_call(db, session_id: str) -> dict[str, Any] | None:
    with db._read_ctx() as conn:
        row = conn.execute("SELECT value FROM state_meta WHERE key=?", (last_call_key(session_id),)).fetchone()
    if row is None or not isinstance(row[0], str):
        return None
    try:
        return normalize_last_call(json.loads(row[0]))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        from hermes_state_runtime import RuntimeStoreError

        raise RuntimeStoreError("storage_unavailable", detail="last api call snapshot unreadable") from exc


def _window(snapshot: dict[str, Any]) -> tuple[int | None, str]:
    try:
        from hermes_cli.config import load_config_readonly

        configured = (load_config_readonly() or {}).get("model") or {}
        length = configured.get("context_length") if isinstance(configured, dict) else None
        if type(length) is int and length > 0:
            return length, "config"
    except Exception:
        pass
    model = snapshot.get("model") or ""
    base_url = snapshot.get("base_url") or ""
    if model:
        try:
            from agent.model_metadata import get_cached_context_length

            cached = get_cached_context_length(model, base_url)
            if type(cached) is int and cached > 0:
                return cached, "cached"
        except Exception:
            pass
    return None, "unknown"


def project_context_usage(db, session_id: str) -> dict[str, Any]:
    """Last-call prompt size plus a non-probing window. No log scrape."""
    if not isinstance(session_id, str) or not session_id:
        from hermes_state_runtime import RuntimeStoreError

        raise RuntimeStoreError("invalid_params")
    if db.get_session(session_id) is None:
        from hermes_state_runtime import RuntimeStoreError

        raise RuntimeStoreError("not_found")
    snapshot = read_last_call(db, session_id)
    if snapshot is None:
        return {"used": None, "window": None, "source": "no-data", "session": _totals(db, session_id)}
    window, source = _window(snapshot)
    return {
        "used": snapshot["prompt_tokens"],
        "window": window,
        "model": snapshot["model"] or None,
        "provider": snapshot["provider"] or None,
        "calls": snapshot["api_call"],
        "at": snapshot["at"],
        "out": snapshot["output_tokens"],
        "cached": snapshot["cache_read_tokens"],
        "source": source,
        "session": _totals(db, session_id),
    }


def _totals(db, session_id: str) -> dict[str, int] | None:
    session = db.get_session(session_id) or {}
    try:
        return {
            "input": int(session.get("input_tokens") or 0),
            "output": int(session.get("output_tokens") or 0),
            "cacheRead": int(session.get("cache_read_tokens") or 0),
            "apiCalls": int(session.get("api_call_count") or 0),
            "messages": int(session.get("message_count") or 0),
        }
    except (TypeError, ValueError):
        return None
