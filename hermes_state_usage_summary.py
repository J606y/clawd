"""Panel usage summary from the caller's SessionDB.

This is the settings usage page contract: ``started_at`` window, local calendar
day, and the existing totals/daily/byModel/bySource/top/aux fields. It is not
``InsightsEngine``, ``/api/analytics/usage``, or ``auxiliary_usage_by_task``.

The caller passes the profile SessionDB. This module does not open a database,
does not read process home, and does not substitute an empty payload for a
failed read.
"""

from __future__ import annotations

import math
import time
from typing import Any

from hermes_state_runtime import RuntimeStoreError

USAGE_DAYS = (7, 30, 90)
_DAY = r"0123456789-"
_TOTAL_KEYS = ("sessions", "input", "output", "cacheRead", "apiCalls")
_DAILY_SQL = (
    "SELECT date(started_at,'unixepoch','localtime') AS day, COUNT(*) AS sessions, "
    "COALESCE(SUM(input_tokens),0) AS input, COALESCE(SUM(output_tokens),0) AS output, "
    "COALESCE(SUM(cache_read_tokens),0) AS cacheRead, COALESCE(SUM(api_call_count),0) AS apiCalls "
    "FROM sessions WHERE started_at > ? GROUP BY day ORDER BY day"
)
_MODEL_SQL = (
    "SELECT COALESCE(NULLIF(model,''),'(未知)') AS model, "
    "COALESCE(NULLIF(billing_provider,''),'-') AS provider, "
    "COUNT(*) AS sessions, COALESCE(SUM(input_tokens),0) AS input, "
    "COALESCE(SUM(output_tokens),0) AS output, COALESCE(SUM(api_call_count),0) AS apiCalls "
    "FROM sessions WHERE started_at > ? GROUP BY 1, 2 ORDER BY input+output DESC LIMIT 50"
)
_SOURCE_SQL = (
    "SELECT COALESCE(NULLIF(source,''),'(未知)') AS source, COUNT(*) AS sessions, "
    "COALESCE(SUM(input_tokens),0) AS input, COALESCE(SUM(output_tokens),0) AS output "
    "FROM sessions WHERE started_at > ? GROUP BY 1 ORDER BY input+output DESC"
)
_TOP_SQL = (
    "SELECT id, source, model, title, started_at AS startedAt, "
    "COALESCE(input_tokens,0)+COALESCE(output_tokens,0) AS totalTokens, "
    "COALESCE(api_call_count,0) AS apiCalls, COALESCE(message_count,0) AS messages "
    "FROM sessions WHERE started_at > ? ORDER BY totalTokens DESC LIMIT 10"
)
_AUX_SQL = (
    "SELECT u.task AS task, COUNT(DISTINCT u.session_id) AS sessions, "
    "COALESCE(SUM(u.api_call_count),0) AS apiCalls, "
    "COALESCE(SUM(u.input_tokens),0) AS input, COALESCE(SUM(u.output_tokens),0) AS output "
    "FROM session_model_usage u JOIN sessions s ON s.id = u.session_id "
    "WHERE s.started_at > ? AND u.task != '' GROUP BY u.task ORDER BY input+output DESC"
)


def _unavailable(exc: Exception | None = None) -> RuntimeStoreError:
    err = RuntimeStoreError("storage_unavailable")
    if exc is not None:
        raise err from exc
    raise err


def _count(value: Any) -> int:
    if type(value) is bool:
        _unavailable()
    if type(value) is int:
        return value
    if type(value) is float and math.isfinite(value) and value.is_integer():
        return int(value)
    _unavailable()
    raise AssertionError("unreachable")


def _text(value: Any) -> str:
    if type(value) is not str:
        _unavailable()
    return value


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    return _text(value)


def _stamp(value: Any) -> int | float:
    if type(value) is bool or type(value) not in (int, float) or not math.isfinite(value):
        _unavailable()
    return value


def _cell(row: Any, key: str) -> Any:
    try:
        return row[key]
    except (KeyError, IndexError, TypeError) as exc:
        _unavailable(exc)
    raise AssertionError("unreachable")


def _day(value: Any) -> str:
    text = _text(value)
    if len(text) != 10 or text[4] != "-" or text[7] != "-" or any(ch not in _DAY for ch in text):
        _unavailable()
    return text


def _fetch(conn: Any, sql: str, cutoff: float) -> list[Any]:
    try:
        return list(conn.execute(sql, (cutoff,)))
    except Exception as exc:
        _unavailable(exc)
    raise AssertionError("unreachable")


def _daily(conn: Any, cutoff: float) -> list[dict[str, Any]]:
    return [
        {
            "day": _day(_cell(row, "day")),
            "sessions": _count(_cell(row, "sessions")),
            "input": _count(_cell(row, "input")),
            "output": _count(_cell(row, "output")),
            "cacheRead": _count(_cell(row, "cacheRead")),
            "apiCalls": _count(_cell(row, "apiCalls")),
        }
        for row in _fetch(conn, _DAILY_SQL, cutoff)
    ]


def _models(conn: Any, cutoff: float) -> list[dict[str, Any]]:
    return [
        {
            "model": _text(_cell(row, "model")),
            "provider": _text(_cell(row, "provider")),
            "sessions": _count(_cell(row, "sessions")),
            "input": _count(_cell(row, "input")),
            "output": _count(_cell(row, "output")),
            "apiCalls": _count(_cell(row, "apiCalls")),
        }
        for row in _fetch(conn, _MODEL_SQL, cutoff)
    ]


def _sources(conn: Any, cutoff: float) -> list[dict[str, Any]]:
    return [
        {
            "source": _text(_cell(row, "source")),
            "sessions": _count(_cell(row, "sessions")),
            "input": _count(_cell(row, "input")),
            "output": _count(_cell(row, "output")),
        }
        for row in _fetch(conn, _SOURCE_SQL, cutoff)
    ]


def _top(conn: Any, cutoff: float) -> list[dict[str, Any]]:
    rows = []
    for row in _fetch(conn, _TOP_SQL, cutoff):
        session_id = _text(_cell(row, "id"))
        if not session_id:
            _unavailable()
        rows.append({
            "id": session_id,
            "source": _optional_text(_cell(row, "source")),
            "model": _optional_text(_cell(row, "model")),
            "title": _optional_text(_cell(row, "title")),
            "startedAt": _stamp(_cell(row, "startedAt")),
            "totalTokens": _count(_cell(row, "totalTokens")),
            "apiCalls": _count(_cell(row, "apiCalls")),
            "messages": _count(_cell(row, "messages")),
        })
    return rows


def _aux(conn: Any, cutoff: float) -> list[dict[str, Any]]:
    rows = []
    for row in _fetch(conn, _AUX_SQL, cutoff):
        task = _text(_cell(row, "task"))
        if not task:
            _unavailable()
        rows.append({
            "task": task,
            "sessions": _count(_cell(row, "sessions")),
            "apiCalls": _count(_cell(row, "apiCalls")),
            "input": _count(_cell(row, "input")),
            "output": _count(_cell(row, "output")),
        })
    return rows


def _payload(days: int, daily: list[dict[str, Any]], by_model: list[dict[str, Any]], by_source: list[dict[str, Any]], top: list[dict[str, Any]], aux: list[dict[str, Any]]) -> dict[str, Any]:
    totals = {key: sum(row[key] for row in daily) for key in _TOTAL_KEYS}
    payload = {
        "days": days,
        "totals": totals,
        "daily": daily,
        "byModel": by_model,
        "bySource": by_source,
        "top": top,
        "aux": aux,
    }
    return require_usage_payload(payload)


def require_usage_payload(payload: Any) -> dict[str, Any]:
    """Accept only the panel shape. A partial or drifted object is a store failure, not zeros."""
    if not isinstance(payload, dict) or set(payload) != {"days", "totals", "daily", "byModel", "bySource", "top", "aux"}:
        _unavailable()
    if type(payload["days"]) is not int or payload["days"] not in USAGE_DAYS:
        _unavailable()
    totals = payload["totals"]
    if not isinstance(totals, dict) or set(totals) != set(_TOTAL_KEYS) or any(type(totals[key]) is not int for key in _TOTAL_KEYS):
        _unavailable()
    daily = payload["daily"]
    if not isinstance(daily, list) or any(not isinstance(row, dict) or set(row) != {"day", *_TOTAL_KEYS} for row in daily):
        _unavailable()
    if not isinstance(payload["byModel"], list) or not isinstance(payload["bySource"], list) or not isinstance(payload["top"], list) or not isinstance(payload["aux"], list):
        _unavailable()
    if any(type(row.get(key)) is not int for row in daily for key in _TOTAL_KEYS):
        _unavailable()
    if any(totals[key] != sum(row[key] for row in daily) for key in _TOTAL_KEYS):
        _unavailable()
    return payload


def summarize_usage(db: Any, days: Any) -> dict[str, Any]:
    """Aggregate one already-opened SessionDB. Raises; never returns a stand-in empty object."""
    if type(days) is bool or type(days) is not int or days not in USAGE_DAYS:
        raise RuntimeStoreError("invalid_params", field="days")
    reader = getattr(db, "_read_ctx", None)
    if not callable(reader):
        _unavailable()
    flusher = getattr(db, "flush_for_read", None)
    if callable(flusher):
        try:
            flusher()
        except Exception as exc:
            _unavailable(exc)
    cutoff = time.time() - days * 86400
    # No explicit BEGIN. ``_read_ctx`` may yield a pooled connection that has to
    # go back idle; the SessionDB read lock/pool is the concurrency boundary.
    try:
        with reader() as conn:
            return _payload(days, _daily(conn, cutoff), _models(conn, cutoff), _sources(conn, cutoff), _top(conn, cutoff), _aux(conn, cutoff))
    except RuntimeStoreError:
        raise
    except Exception as exc:
        _unavailable(exc)
    raise AssertionError("unreachable")
