"""Panel runtime reads that call core services. Not a second state machine.

Registered from ``APIServerAdapter._http_route_table``. Effort routes are mounted
by the parent after every track returns; this module does not import them.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import sqlite3
from typing import Any

from aiohttp import web

logger = logging.getLogger("gateway.clawd_runtime")

_SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}$")
_EDIT_HTTP = {
    "invalid_params": (400, "invalid_edit_request", "需要完整的编辑请求字段"),
    "not_found": (404, "session_not_found", "原会话不存在"),
    "edit_boundary_changed": (409, "edit_boundary_changed", "消息内容或时间已变化，请刷新"),
    "ambiguous_edit_boundary": (409, "ambiguous_edit_boundary", "存在同文同时间消息，不能安全定位"),
    "unsupported_message_order": (409, "unsupported_message_order", "展示顺序无法安全规范化，不能改用时间戳定位；会话未修改"),
    "unsafe_edit_boundary": (409, "unsafe_edit_boundary", "只能编辑无附件的普通用户文字消息"),
    "edit_request_conflict": (409, "edit_request_conflict", "请求编号已被其他编辑占用"),
    "session_busy": (409, "session_busy", "会话正在运行或压缩，请稍后重试"),
    "unknown_execution": (409, "unknown_execution", "执行器身份不明，未发送信号，也未删除租约"),
    "execution_changed": (409, "execution_changed", "执行器已变化，已停止后续信号"),
    "execution_stop_timeout": (504, "execution_stop_timeout", "执行器未结束，租约仍保留"),
    "edit_prefix_too_large": (413, "edit_prefix_too_large", "编辑历史超过安全上限"),
    "incomplete_tool_history": (409, "incomplete_tool_history", "工具调用记录不完整"),
    "unsupported_edit_history": (409, "unsupported_edit_history", "压缩或已关闭的会话不能编辑"),
    "project_inheritance_failed": (503, "project_inheritance_failed", "项目归属未能在副本提交前写入，已放弃未绑定副本"),
    "storage_unavailable": (503, "runtime_unavailable", "运行状态暂不可用"),
    "admission_conflict": (409, "admission_conflict", "请求标识不唯一"),
    "session_mismatch": (409, "session_mismatch", "请求标识不属于当前会话"),
}


def _fail(message: str, status: int, code: str) -> web.Response:
    return web.json_response(
        {"error": {"message": message, "type": "invalid_request_error", "code": code}},
        status=status,
    )


def _auth(adapter, request: web.Request) -> web.Response | None:
    checker = getattr(adapter, "_check_auth", None)
    if not callable(checker):
        return _fail("authentication is not configured", 401, "unauthorized")
    denied = checker(request)
    return denied if isinstance(denied, web.Response) else None


def _session_id(request: web.Request) -> str | web.Response:
    session_id = request.match_info.get("session_id", "")
    if _SESSION_ID.fullmatch(session_id) is None:
        return _fail("需要有效会话 ID", 400, "invalid_session")
    return session_id


def _store_error(exc) -> web.Response:
    from hermes_state_runtime import RuntimeStoreError

    reason = exc.reason if isinstance(exc, RuntimeStoreError) else "storage_unavailable"
    status, code, message = _EDIT_HTTP.get(reason, (503, "runtime_unavailable", "运行状态暂不可用"))
    return _fail(message, status, code)


def message_projection(db, messages) -> tuple[dict[Any, list], dict[int, str]]:
    """Commentary and origin for one already-loaded page. Failure omits the field; it does not drop history."""
    commentary: dict[Any, list] = {}
    origins: dict[int, str] = {}
    try:
        from agent.history_commentary import project_history_commentary
        from hermes_constants import get_hermes_home

        projected = project_history_commentary(list(messages), home=get_hermes_home())
        for raw, item in zip(messages, projected):
            if isinstance(raw, dict) and isinstance(item, dict) and "display_commentary" in item and raw.get("id") is not None:
                commentary[raw["id"]] = item["display_commentary"]
    except Exception:
        logger.warning("history commentary projection failed", exc_info=True)
    try:
        from hermes_state_runtime import project_api_input_origins

        origins = project_api_input_origins(db, messages)
    except Exception:
        logger.warning("message origin projection failed", exc_info=True)
    return commentary, origins


def session_run_view(db, store, session_id: str, *, idempotency_key: str | None = None, scope: str | None = None) -> dict[str, Any]:
    from hermes_state_lease_read import read_session_turn_lease
    from hermes_state_runtime import RuntimeStoreError

    if db.get_session(session_id) is None:
        raise RuntimeStoreError("not_found")
    lease = read_session_turn_lease(db, session_id)
    result: dict[str, Any] = {"run": None, "busy": bool(lease["busy"])}
    if lease["busy"] and lease.get("acquired_at") is not None:
        result["acquired_at"] = lease["acquired_at"]
    if store is None:
        if idempotency_key is not None:
            raise RuntimeStoreError("storage_unavailable")
        return result
    found = store.discover_for_session(session_id, idempotency_key=idempotency_key, scope=scope)
    if idempotency_key is not None:
        result["resolution"] = {"idempotency_key": idempotency_key, "state": "found" if found else "not_found"}
    if found:
        result["run"] = found
    return result


_TERMINAL_RUN = frozenset({"completed", "failed", "cancelled", "interrupted"})
_LIVE_RUN = frozenset({"queued", "running", "waiting_for_approval", "stopping", "unknown"})


def project_run_wait(db, store, session_id: str, turn_started_at_ms: float | None = None) -> dict[str, Any]:
    """Retry/end projection from durable run and lease rows. Never reads agent.log.

    Missing facts are ``unknown``. They are not idle and not numeric zero.
    """
    from hermes_state_lease_read import read_session_turn_lease
    from hermes_state_runtime import RuntimeStoreError

    now_ms = round(time.time() * 1000)
    view: dict[str, Any] = {
        "state": "unknown",
        "source": "durable",
        "available": False,
        "observedAtMs": now_ms,
    }
    if turn_started_at_ms is not None:
        view["turnStartedAtMs"] = turn_started_at_ms
    if db.get_session(session_id) is None:
        raise RuntimeStoreError("not_found")
    try:
        lease = read_session_turn_lease(db, session_id)
    except Exception:
        return view
    if not isinstance(lease, dict) or not isinstance(lease.get("busy"), bool):
        return view
    run = None
    if store is not None:
        try:
            run = store.discover_for_session(session_id)
        except Exception:
            return view
    status = run.get("status") if isinstance(run, dict) else None
    view["busy"] = lease["busy"]
    if isinstance(status, str):
        view["runStatus"] = status
    if status in _TERMINAL_RUN and lease["busy"] is False:
        view["state"] = "idle"
        view["available"] = True
        return view
    if lease["busy"] or status in _LIVE_RUN:
        view["state"] = "unknown"
        view["available"] = False
        return view
    return view


def background_view(
    session_id: str, *, gateway_bound: bool, registry=None,
) -> dict[str, Any]:
    from tools.process_registry import ProcessRegistryUnavailable, process_registry

    if registry is None:
        if not gateway_bound:
            raise ProcessRegistryUnavailable("api adapter has no bound process registry")
        registry = process_registry
    reader = getattr(registry, "running_for_parent", None)
    if not callable(reader):
        raise ProcessRegistryUnavailable("bound process registry cannot be read")
    processes = reader(session_id)
    if not isinstance(processes, list):
        raise ProcessRegistryUnavailable("bound process registry returned an invalid view")
    return {"processes": processes}


def http_routes(adapter) -> list[tuple[str, str, Any]]:
    return [
        ("GET", "/api/runtime/sessions/active", lambda request: handle_active(adapter, request)),
        ("GET", "/api/runtime/sessions/{session_id}/lease", lambda request: handle_lease(adapter, request)),
        ("GET", "/api/runtime/sessions/{session_id}/run", lambda request: handle_run(adapter, request)),
        ("GET", "/api/runtime/sessions/{session_id}/execution", lambda request: handle_execution(adapter, request)),
        ("POST", "/api/runtime/sessions/{session_id}/execution/stop", lambda request: handle_execution_stop(adapter, request)),
        ("GET", "/api/runtime/sessions/{session_id}/context", lambda request: handle_context(adapter, request)),
        ("GET", "/api/runtime/subagents", lambda request: handle_subagents(adapter, request)),
        ("GET", "/api/runtime/usage", lambda request: handle_usage(adapter, request)),
        ("GET", "/api/runtime/sessions/{session_id}/background", lambda request: handle_background(adapter, request)),
        ("POST", "/api/runtime/sessions/{session_id}/edit-branch", lambda request: handle_edit(adapter, request)),
    ]


async def _db(adapter):
    getter = getattr(adapter, "_ensure_session_db_async", None)
    if not callable(getter):
        return None, _fail("Session database unavailable", 503, "session_db_unavailable")
    db = await getter()
    if db is None:
        return None, _fail("Session database unavailable", 503, "session_db_unavailable")
    return db, None


async def handle_active(adapter, request: web.Request) -> web.Response:
    if denied := _auth(adapter, request):
        return denied
    db, err = await _db(adapter)
    if err:
        return err
    try:
        from hermes_state_lease_read import list_busy_sessions

        payload = await asyncio.to_thread(list_busy_sessions, db)
    except Exception as exc:
        logger.warning("active session projection failed", exc_info=True)
        return _store_error(exc)
    if not isinstance(payload.get("data"), list):
        return _fail("活动会话暂不可用", 503, "session_active_unavailable")
    return web.json_response(payload)


async def handle_lease(adapter, request: web.Request) -> web.Response:
    if denied := _auth(adapter, request):
        return denied
    session_id = _session_id(request)
    if isinstance(session_id, web.Response):
        return session_id
    db, err = await _db(adapter)
    if err is not None or db is None:
        return err or _fail("Session database unavailable", 503, "session_db_unavailable")
    try:
        from hermes_state_lease_read import read_session_turn_lease
        from hermes_state_runtime import RuntimeStoreError

        if await asyncio.to_thread(db.get_session, session_id) is None:
            raise RuntimeStoreError("not_found")
        payload = await asyncio.to_thread(read_session_turn_lease, db, session_id)
    except Exception as exc:
        logger.warning("lease projection failed", exc_info=True)
        return _store_error(exc)
    return web.json_response({"object": "hermes.session.lease", **payload})


async def handle_run(adapter, request: web.Request) -> web.Response:
    if denied := _auth(adapter, request):
        return denied
    session_id = _session_id(request)
    if isinstance(session_id, web.Response):
        return session_id
    key = request.query.get("idempotency_key")
    if key is not None and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}", key) is None:
        return _fail("请求标识不合法", 400, "invalid_idempotency_key")
    db, err = await _db(adapter)
    if err is not None or db is None:
        return err or _fail("Session database unavailable", 503, "session_db_unavailable")
    store = getattr(adapter, "_run_idempotency_store", None)
    if store is None:
        return _fail("运行索引暂不可用", 503, "session_run_unavailable")
    scoper = getattr(adapter, "_run_idempotency_scope", None)
    if not callable(scoper):
        return _fail("运行索引暂不可用", 503, "session_run_unavailable")
    try:
        # The bound adapter method owns its internal api_server dependency.
        # Its public contract accepts only the request.
        scope = scoper(request)
        if not isinstance(scope, str) or not scope:
            return _fail("运行索引暂不可用", 503, "session_run_unavailable")
    except Exception:
        logger.warning("run discovery scope failed", exc_info=True)
        return _fail("运行索引暂不可用", 503, "session_run_unavailable")
    try:
        payload = await asyncio.to_thread(
            session_run_view, db, store, session_id, idempotency_key=key, scope=scope)
    except Exception as exc:
        logger.warning("session run discovery failed", exc_info=True)
        return _store_error(exc)
    return web.json_response(payload)


async def handle_background(adapter, request: web.Request) -> web.Response:
    if denied := _auth(adapter, request):
        return denied
    session_id = _session_id(request)
    if isinstance(session_id, web.Response):
        return session_id
    try:
        payload = await asyncio.to_thread(
            background_view, session_id,
            gateway_bound=getattr(adapter, "gateway_runner", None) is not None,
            registry=getattr(adapter, "_background_process_registry", None))
    except Exception as exc:
        logger.warning("background registry read failed", exc_info=True)
        return _fail("后台进程登记暂不可用", 503, "process_registry_unavailable")
    if not isinstance(payload.get("processes"), list):
        return _fail("后台进程登记暂不可用", 503, "process_registry_unavailable")
    return web.json_response(payload)


async def handle_edit(adapter, request: web.Request) -> web.Response:
    if denied := _auth(adapter, request):
        return denied
    session_id = _session_id(request)
    if isinstance(session_id, web.Response):
        return session_id
    try:
        body = await request.json()
    except Exception:
        return _fail("需要完整的编辑请求字段", 400, "invalid_edit_request")
    if not isinstance(body, dict) or body.get("sessionId") != session_id:
        return _fail("编辑请求不属于当前会话", 400, "invalid_edit_request")
    db, err = await _db(adapter)
    if err:
        return err
    try:
        from hermes_state_api_edit import api_edit_branch

        payload = await asyncio.to_thread(api_edit_branch, db, body)
    except (sqlite3.Error, OSError) as exc:
        logger.warning("edit branch store failed", exc_info=True)
        return _fail("编辑副本暂不可用", 503, "edit_unavailable")
    except Exception as exc:
        logger.warning("edit branch refused", exc_info=True)
        return _store_error(exc)
    if not isinstance(payload, dict) or not isinstance(payload.get("session"), dict):
        return _fail("编辑副本暂不可用", 503, "edit_unavailable")
    return web.json_response(payload)


async def handle_execution(adapter, request: web.Request) -> web.Response:
    if denied := _auth(adapter, request):
        return denied
    session_id = _session_id(request)
    if isinstance(session_id, web.Response):
        return session_id
    db, err = await _db(adapter)
    if err is not None or db is None:
        return err or _fail("Session database unavailable", 503, "session_db_unavailable")
    try:
        from hermes_state_execution import project_session_execution

        payload = await asyncio.to_thread(project_session_execution, db, session_id)
    except Exception as exc:
        logger.warning("session execution projection failed", exc_info=True)
        return _store_error(exc)
    if not isinstance(payload, dict) or not isinstance(payload.get("busy"), bool):
        return _fail("执行状态暂不可用", 503, "session_execution_unavailable")
    return web.json_response({"busy": payload["busy"], "execution": payload.get("execution")})


async def handle_execution_stop(adapter, request: web.Request) -> web.Response:
    if denied := _auth(adapter, request):
        return denied
    session_id = _session_id(request)
    if isinstance(session_id, web.Response):
        return session_id
    try:
        body = await request.json()
    except Exception:
        return _fail("停止请求不完整", 400, "invalid_execution_stop")
    if (
        not isinstance(body, dict)
        or set(body) != {"session_id", "execution_id", "confirm"}
        or body.get("session_id") != session_id
        or body.get("confirm") != "stop-cli"
        or not isinstance(body.get("execution_id"), str)
    ):
        return _fail("停止请求不完整", 400, "invalid_execution_stop")
    db, err = await _db(adapter)
    if err is not None or db is None:
        return err or _fail("Session database unavailable", 503, "session_db_unavailable")
    try:
        from hermes_state_execution import stop_cli_execution

        payload = await asyncio.to_thread(stop_cli_execution, db, session_id, body["execution_id"])
    except Exception as exc:
        logger.warning("cli execution stop refused", exc_info=True)
        return _store_error(exc)
    return web.json_response(payload)


async def handle_context(adapter, request: web.Request) -> web.Response:
    if denied := _auth(adapter, request):
        return denied
    session_id = _session_id(request)
    if isinstance(session_id, web.Response):
        return session_id
    db, err = await _db(adapter)
    if err is not None or db is None:
        return err or _fail("Session database unavailable", 503, "session_db_unavailable")
    try:
        from hermes_state_last_call import project_context_usage

        payload = await asyncio.to_thread(project_context_usage, db, session_id)
    except Exception as exc:
        logger.warning("context snapshot read failed", exc_info=True)
        return _store_error(exc)
    if not isinstance(payload, dict) or "used" not in payload:
        return _fail("上下文用量暂不可用", 503, "context_unavailable")
    return web.json_response(payload)


async def handle_subagents(adapter, request: web.Request) -> web.Response:
    if denied := _auth(adapter, request):
        return denied
    parent = request.query.get("parent") or ""
    if parent and _SESSION_ID.fullmatch(parent) is None:
        return _fail("需要有效会话 ID", 400, "invalid_session")
    try:
        limit = int(request.query.get("limit") or "12")
        offset = int(request.query.get("offset") or "0")
    except ValueError:
        return _fail("分页参数不合法", 400, "invalid_limit")
    db, err = await _db(adapter)
    if err is not None or db is None:
        return err or _fail("Session database unavailable", 503, "session_db_unavailable")
    try:
        from hermes_state_subagents import list_subagents

        payload = await asyncio.to_thread(list_subagents, db, parent, limit=limit, offset=offset)
    except Exception as exc:
        logger.warning("subagent projection failed", exc_info=True)
        return _store_error(exc)
    if not isinstance(payload, dict) or not isinstance(payload.get("running"), int) or not isinstance(payload.get("data"), list):
        return _fail("子代理状态暂不可用", 503, "subagents_unavailable")
    return web.json_response(payload)


def parse_usage_days(request: web.Request) -> int | web.Response:
    """Require one canonical days value. Duplicates are ambiguous; booleans are not coerced."""
    from hermes_state_usage_summary import USAGE_DAYS

    query = getattr(request, "query", None)
    getter = getattr(query, "getall", None)
    if not callable(getter):
        return _fail("days 参数不明确", 400, "ambiguous_days")
    try:
        values = getter("days", [])
    except TypeError:
        try:
            values = getter("days")
        except KeyError:
            values = []
    except KeyError:
        values = []
    if not isinstance(values, (list, tuple)):
        return _fail("days 参数不明确", 400, "ambiguous_days")
    if len(values) == 0:
        return _fail("days 只能是 7/30/90", 400, "invalid_days")
    if len(values) != 1:
        return _fail("days 参数重复", 400, "ambiguous_days")
    raw = values[0]
    allowed = {str(day): day for day in USAGE_DAYS}
    if type(raw) is bool or not isinstance(raw, str) or raw.strip().lower() in {"true", "false"} or raw not in allowed:
        return _fail("days 只能是 7/30/90", 400, "invalid_days")
    return allowed[raw]


async def handle_usage(adapter, request: web.Request) -> web.Response:
    if denied := _auth(adapter, request):
        return denied
    days = parse_usage_days(request)
    if isinstance(days, web.Response):
        return days
    db, err = await _db(adapter)
    if err is not None or db is None:
        return err or _fail("Session database unavailable", 503, "session_db_unavailable")
    try:
        from hermes_state_usage_summary import require_usage_payload, summarize_usage

        payload = require_usage_payload(await asyncio.to_thread(summarize_usage, db, days))
    except Exception as exc:
        from hermes_state_runtime import RuntimeStoreError

        if isinstance(exc, RuntimeStoreError) and exc.reason == "invalid_params":
            return _fail("days 只能是 7/30/90", 400, "invalid_days")
        reason = getattr(exc, "reason", None)
        logger.warning("usage summary failed (%s)", reason if isinstance(reason, str) else type(exc).__name__)
        return _fail("用量暂不可用", 503, "usage_unavailable")
    return web.json_response(payload)
