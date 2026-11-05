"""Replace sidecar runtime compensation with core service calls.

``install(mod)`` is the contract the parent calls from ``native.install``. It does
not open state.db with a private query and does not copy sidecar SQL.
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("clawd.runtime")

_PRODUCTION = ("/root/.hermes", "/usr/local/lib/hermes-agent")
_ID_PART = re.compile(r"[1-9][0-9]{0,18}")


_STORES_BY_HOME: dict[str, Any] = {}


def _home_key(path: Any) -> str | None:
    if not path:
        return None
    try:
        return str(Path(path).resolve().parent)
    except OSError:
        return None


def bind_run_store(store) -> None:
    """Register one profile home's run store. A later adapter for another home must not replace it."""
    home = _home_key(getattr(store, "_db_path", None))
    if home is None:
        return
    _STORES_BY_HOME[home] = store


def store_for_db(db):
    """The store bound to this session database's home, never the last adapter in the process.

    A sidecar process does not share the gateway's registry. The durable file next
    to this state.db is the same run-store protocol, not a shadow store. A missing
    file stays unbound so a keyed lookup remains unavailable instead of inventing
    a row.
    """
    home = _home_key(getattr(db, "db_path", None))
    if home is None:
        return None
    bound = _STORES_BY_HOME.get(home)
    if bound is not None:
        return bound
    path = Path(home) / "runs_idempotency.db"
    try:
        if not path.is_file():
            return None
    except OSError:
        return None
    from gateway.platforms.api_server_run_idempotency import RunIdempotencyStore

    store = RunIdempotencyStore(str(path))
    if not getattr(store, "durable", False):
        return None
    _STORES_BY_HOME[home] = store
    return store


def install(mod) -> None:
    """Replace runtime compensation entry points on an already-bound sidecar module."""
    mod.get_message_origins = lambda session_id, raw_ids: message_origins(mod, session_id, raw_ids)
    mod.get_commentary = lambda body: commentary(mod, body)
    mod.get_active_sessions = lambda: active_sessions(mod)
    mod.get_session_run = lambda session_id, idempotency_key=None: session_run(mod, session_id, idempotency_key=idempotency_key)
    mod.get_session_background = lambda session_id: session_background(mod, session_id)
    mod.get_session_execution = lambda session_id: session_execution(mod, session_id)
    mod.get_context = lambda session_id: session_context(mod, session_id)
    mod.get_subagents = lambda parent_id="", limit=12: session_subagents(mod, parent_id, limit)
    mod.get_usage = lambda days: session_usage(mod, days)
    mod.get_run_wait = lambda session_id, turn_started_at_ms=None: run_wait(mod, session_id, turn_started_at_ms)
    mod.create_edit_branch = lambda db_path, payload: edit_branch(mod, db_path, payload)
    mod.stop_session_execution = lambda body: stop_execution(mod, body)
    previous = tuple(getattr(mod, "_clawd_native", ()) or ())
    mod._clawd_native = tuple(dict.fromkeys([
        *previous,
        "message_origins",
        "commentary",
        "active_sessions",
        "session_run",
        "session_background",
        "session_execution",
        "session_context",
        "session_subagents",
        "session_usage",
        "run_wait",
        "edit_branch",
        "stop_execution",
    ]))


def _refuse_production(path: Path) -> None:
    text = str(path.resolve())
    if text.startswith(_PRODUCTION) or any(marker in text for marker in _PRODUCTION):
        raise RuntimeError(f"refusing production state: {text}")


def _db(db_path: Path | None = None):
    from hermes_constants import get_hermes_home
    from hermes_state import SessionDB

    path = Path(db_path) if db_path is not None else Path(get_hermes_home()) / "state.db"
    _refuse_production(path)
    return SessionDB(db_path=path)


def _api(mod, exc) -> Any:
    from hermes_state_runtime import RuntimeStoreError

    if isinstance(exc, RuntimeStoreError):
        status, code, message = {
            "invalid_params": (400, "invalid_request", "请求不合法"),
            "not_found": (404, "session_not_found", "会话不存在"),
            "storage_unavailable": (503, "runtime_unavailable", "运行状态暂不可用"),
            "session_busy": (409, "session_busy", "会话正在运行或压缩，请稍后重试"),
            "unknown_execution": (409, "unknown_execution", "执行器身份不明，未发送信号，也未删除租约"),
            "execution_changed": (409, "execution_changed", "执行器已变化，已停止后续信号"),
            "execution_stop_timeout": (504, "execution_stop_timeout", "执行器未结束，租约仍保留"),
            "admission_conflict": (409, "admission_conflict", "请求标识不唯一"),
            "session_mismatch": (409, "session_mismatch", "请求标识不属于当前会话"),
            "edit_boundary_changed": (409, "edit_boundary_changed", "消息内容或时间已变化，请刷新"),
            "edit_request_conflict": (409, "edit_request_conflict", "请求编号已被其他编辑占用"),
            "ambiguous_edit_boundary": (409, "ambiguous_edit_boundary", "存在同文同时间消息，不能安全定位"),
            "unsupported_message_order": (409, "unsupported_message_order", "展示顺序无法安全规范化，不能改用时间戳定位；会话未修改"),
            "unsafe_edit_boundary": (409, "unsafe_edit_boundary", "只能编辑无附件的普通用户文字消息"),
            "incomplete_tool_history": (409, "incomplete_tool_history", "工具调用记录不完整"),
            "unsupported_edit_history": (409, "unsupported_edit_history", "压缩或已关闭的会话不能编辑"),
            "edit_prefix_too_large": (413, "edit_prefix_too_large", "编辑历史超过安全上限"),
            "project_inheritance_failed": (503, "project_inheritance_failed", "项目归属未能在副本提交前写入，已放弃未绑定副本"),
        }.get(exc.reason, (503, "runtime_unavailable", "运行状态暂不可用"))
        return mod.ApiError(status, code, message)
    return mod.ApiError(503, "runtime_unavailable", "运行状态暂不可用")


def message_origins(mod, session_id: str, raw_ids: str) -> dict[str, Any]:
    if not isinstance(session_id, str) or not session_id or len(session_id) > 512:
        raise mod.ApiError(400, "invalid_session", "需要有效会话 ID")
    if not isinstance(raw_ids, str) or len(raw_ids) > 4096:
        raise mod.ApiError(400, "invalid_ids", "消息 ID 列表过长")
    parts = raw_ids.split(",") if raw_ids else []
    if len(parts) > 200 or any(_ID_PART.fullmatch(value) is None or int(value) > 9223372036854775807 for value in parts):
        raise mod.ApiError(400, "invalid_ids", "需要不超过 200 个正整数消息 ID")
    ids = list(dict.fromkeys(int(value) for value in parts))
    if not ids:
        return {"items": {}}
    db = _db()
    try:
        from hermes_state_runtime import project_api_input_origins

        rows = db.get_messages(session_id, include_inactive=True)
        wanted = set(ids)
        origins = project_api_input_origins(db, [row for row in rows if row.get("id") in wanted])
        return {"items": {str(mid): origin for mid, origin in origins.items() if mid in wanted}}
    except mod.ApiError:
        raise
    except Exception as exc:
        raise mod.ApiError(503, "message_origin_unavailable", "消息来源暂不可用") from exc
    finally:
        db.close()


def commentary(mod, body: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(body, dict) or not isinstance(body.get("session_id"), str) or not body["session_id"]:
        raise mod.ApiError(400, "commentary_requires_session", "旁白必须带会话 ID，不再按消息 id 跨会话读取")
    ids = body.get("ids")
    if not isinstance(ids, list) or len(ids) > 1000 or not all(type(i) is int and i > 0 for i in ids):
        raise mod.ApiError(400, "invalid_ids", "ids 需为不超过 1000 个的正整数")
    db = _db()
    try:
        from agent.history_commentary import project_history_commentary
        from hermes_constants import get_hermes_home

        wanted = set(ids)
        rows = [row for row in db.get_messages(body["session_id"]) if row.get("id") in wanted and row.get("session_id") == body["session_id"]]
        items = {}
        for raw, projected in zip(rows, project_history_commentary(rows, home=get_hermes_home())):
            notes = projected.get("display_commentary") if isinstance(projected, dict) else None
            if notes:
                items[str(raw["id"])] = notes
        return {"items": items}
    except Exception as exc:
        raise mod.ApiError(503, "commentary_unavailable", "旁白暂不可用") from exc
    finally:
        db.close()


def active_sessions(mod) -> dict[str, Any]:
    db = _db()
    try:
        from hermes_state_lease_read import list_busy_sessions

        return list_busy_sessions(db)
    except Exception as exc:
        raise _api(mod, exc) from exc
    finally:
        db.close()


def session_run(mod, session_id: str, *, idempotency_key: str | None = None) -> dict[str, Any]:
    if not isinstance(session_id, str) or not session_id:
        raise mod.ApiError(400, "invalid_session", "需要有效会话 ID")
    db = _db()
    try:
        from gateway.platforms.clawd_runtime_routes import session_run_view

        return session_run_view(db, store_for_db(db), session_id, idempotency_key=idempotency_key)
    except Exception as exc:
        raise _api(mod, exc) from exc
    finally:
        db.close()


def run_wait(mod, session_id: str, turn_started_at_ms: Any = None) -> dict[str, Any]:
    """Sidecar replacement. Persistent run/lease facts only; missing is unknown, not 0."""
    pattern = getattr(mod, "SESSION_ID_RE", None)
    if not isinstance(session_id, str) or not session_id or (pattern is not None and pattern.match(session_id) is None):
        raise mod.ApiError(400, "invalid_session", "会话 id 不合法")
    since = None
    if turn_started_at_ms not in (None, ""):
        try:
            since = float(turn_started_at_ms)
        except (TypeError, ValueError):
            raise mod.ApiError(400, "invalid_turn_start", "回合时间无效") from None
        now = time.time()
        if since != since or since in (float("inf"), float("-inf")) or not 0 < since <= (now + 60) * 1000:
            raise mod.ApiError(400, "invalid_turn_start", "回合时间无效")
    db = _db()
    try:
        from gateway.platforms.clawd_runtime_routes import project_run_wait

        return project_run_wait(db, store_for_db(db), session_id, since)
    except Exception as exc:
        raise _api(mod, exc) from exc
    finally:
        db.close()


def session_background(mod, session_id: str) -> dict[str, Any]:
    if not isinstance(session_id, str) or not session_id:
        raise mod.ApiError(400, "invalid_session", "会话 id 不合法")
    try:
        from tools.process_registry import process_registry

        return {"processes": process_registry.running_for_parent(session_id)}
    except Exception as exc:
        raise mod.ApiError(503, "process_registry_unavailable", "后台进程登记暂不可用") from exc


def edit_branch(mod, db_path, payload: dict[str, Any]) -> dict[str, Any]:
    path = Path(db_path)
    _refuse_production(path)
    db = _db(path)
    try:
        from hermes_state_api_edit import api_edit_branch

        return api_edit_branch(db, payload)
    except Exception as exc:
        raise _api(mod, exc) from exc
    finally:
        db.close()


def session_execution(mod, session_id: str) -> dict[str, Any]:
    if not isinstance(session_id, str) or not session_id:
        raise mod.ApiError(400, "invalid_session", "需要有效会话 ID")
    db = _db()
    try:
        from hermes_state_execution import project_session_execution

        view = project_session_execution(db, session_id)
        return {"busy": bool(view["busy"]), "execution": view.get("execution")}
    except Exception as exc:
        raise _api(mod, exc) from exc
    finally:
        db.close()


def session_context(mod, session_id: str) -> dict[str, Any]:
    if not isinstance(session_id, str) or not session_id:
        raise mod.ApiError(400, "invalid_session", "需要有效会话 ID")
    db = _db()
    try:
        from hermes_state_last_call import project_context_usage

        return project_context_usage(db, session_id)
    except Exception as exc:
        raise _api(mod, exc) from exc
    finally:
        db.close()


def session_subagents(mod, parent_id: str = "", limit: int = 12) -> dict[str, Any]:
    if not isinstance(parent_id, str):
        raise mod.ApiError(400, "invalid_session", "会话 id 不合法")
    if type(limit) is not int:
        raise mod.ApiError(400, "invalid_limit", "limit 不合法")
    db = _db()
    try:
        from hermes_state_subagents import list_subagents

        return list_subagents(db, parent_id, limit=limit)
    except Exception as exc:
        raise _api(mod, exc) from exc
    finally:
        db.close()


def stop_execution(mod, body: dict[str, Any]) -> dict[str, Any]:
    """Stop one proven CLI incarnation. Do not delete the lease. API runs use stop_run."""
    if (
        not isinstance(body, dict)
        or set(body) != {"session_id", "execution_id", "confirm"}
        or body.get("confirm") != "stop-cli"
        or not isinstance(body.get("session_id"), str)
        or not isinstance(body.get("execution_id"), str)
    ):
        raise mod.ApiError(400, "invalid_execution_stop", "停止请求不完整")
    db = _db()
    try:
        from hermes_state_execution import stop_cli_execution

        return stop_cli_execution(db, body["session_id"], body["execution_id"])
    except Exception as exc:
        raise _api(mod, exc) from exc
    finally:
        db.close()


def _attach_usage_close_failure(carrier: BaseException, close_error: BaseException, kind: str) -> None:
    """Keep the close exception beside an ApiError without changing its cause chain."""
    previous = getattr(carrier, "usage_close_failures", ())
    carrier.usage_close_failures = (*tuple(previous), close_error)
    carrier.add_note(f"usage_db_close_failed:{kind}")


def _release_usage_db(mod, db, primary: BaseException | None) -> None:
    """Close an opened usage handle. Never discard a close failure.

    No pending error: do not return the summary. Raise 503 ``usage_unavailable``
    with ``from None``, the same public chain as a summary failure, so the raw
    close text is not the displayed cause. The close exception stays on
    ``usage_close_failures`` and in an ERROR log that has only the type name.

    Pending ApiError: keep that object, its status/code/message, and its existing
    ``__cause__`` / ``__context__``. Attaching evidence must not raise here, or
    ``finally`` would mask the primary.
    """
    if db is None:
        return
    try:
        db.close()
    except Exception as close_error:
        kind = type(close_error).__name__
        if primary is None:
            failure = mod.ApiError(503, "usage_unavailable", "用量暂不可用")
            _attach_usage_close_failure(failure, close_error, kind)
            log.error("usage_db_close_failed (%s)", kind)
            raise failure from None
        _attach_usage_close_failure(primary, close_error, kind)
        log.error("usage_db_close_failed (%s)", kind)


def session_usage(mod, days: Any) -> dict[str, Any]:
    """Replace sidecar get_usage. Bound module home only; no private sqlite and no process-home guess.

    A failed close does not return the summary and does not replace an ApiError
    already raised for this call. The public error stays ``usage_unavailable``
    and does not include a path, SQL, or secret.
    """
    from hermes_state_usage_summary import USAGE_DAYS, summarize_usage

    if type(days) is bool or type(days) is not int or days not in USAGE_DAYS:
        raise mod.ApiError(400, "invalid_days", "days 只能是 7/30/90")
    home = getattr(mod, "HERMES_HOME", None)
    try:
        home_path = Path(home)
    except TypeError:
        raise mod.ApiError(503, "usage_unavailable", "用量暂不可用") from None
    if not home_path.is_absolute():
        raise mod.ApiError(503, "usage_unavailable", "用量暂不可用")
    path = home_path / "state.db"
    db = None
    primary: BaseException | None = None
    try:
        _refuse_production(path)
        from hermes_state import SessionDB

        db = SessionDB(db_path=path, read_only=True)
        payload = summarize_usage(db, days)
    except mod.ApiError as exc:
        primary = exc
        raise
    except Exception:
        primary = mod.ApiError(503, "usage_unavailable", "用量暂不可用")
        raise primary from None
    finally:
        _release_usage_db(mod, db, primary)
    return payload
