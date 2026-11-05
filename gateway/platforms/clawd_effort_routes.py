"""HTTP routes for session reasoning effort.

Exported as ``register_effort_routes(app, adapter)`` for the parent integration
layer. This module does not mount itself. Handlers enter the request profile
scope and never mutate process env.
"""

from __future__ import annotations

import asyncio
import logging
import re
from contextlib import AbstractContextManager
from typing import Any, Optional, cast

from aiohttp import web

from clawd_server.effort_service import (
    EffortServiceError,
    profile_execution_state,
    read_session_effort,
    write_session_effort,
)

logger = logging.getLogger("gateway.clawd_effort")

_SESSION_ID_MAX = 256


def _error(message: str, status: int, code: str) -> web.Response:
    return web.json_response(
        {"error": {"message": message, "type": "invalid_request_error", "code": code}},
        status=status,
    )


def _check_auth(adapter: Any, request: web.Request) -> Optional[web.Response]:
    """Return a rejection, or None only when the checker explicitly allowed.

    ``web.Response`` can be falsy. Callers must use ``is not None``. A missing
    checker, a raised checker, or any non-Response result fails closed.
    """
    checker = getattr(adapter, "_check_auth", None)
    if not callable(checker):
        return _error("authentication is not configured", 401, "unauthorized")
    try:
        result = checker(request)
    except Exception:
        logger.warning("effort auth check failed", exc_info=True)
        return _error("authentication failed", 401, "unauthorized")
    if result is None:
        return None
    if isinstance(result, web.Response):
        return result
    return _error("authentication failed", 401, "unauthorized")


def _valid_session_id(session_id: str) -> bool:
    if not session_id or len(session_id) > _SESSION_ID_MAX or re.search(r"[\r\n\x00]", session_id):
        return False
    from gateway.session import _is_path_unsafe
    return not _is_path_unsafe(session_id)


def _profile_name(request: web.Request) -> tuple[Optional[str], Optional[web.Response]]:
    raw = request.match_info.get("profile")
    if raw is None:
        return None, None
    try:
        from hermes_cli.profiles import normalize_profile_name
        return normalize_profile_name(raw), None
    except ValueError:
        return None, _error("无效的 profile", 400, "invalid_profile")


def _scope(adapter: Any, profile: Optional[str]) -> AbstractContextManager[Any]:
    enter = getattr(adapter, "_profile_scope", None)
    if not callable(enter):
        raise EffortServiceError("profile_scope_unavailable", "无法按请求进入 profile，档位未写入", 503)
    scope = enter(profile)
    if not hasattr(scope, "__enter__") or not hasattr(scope, "__exit__"):
        raise EffortServiceError("profile_scope_unavailable", "无法按请求进入 profile，档位未写入", 503)
    return cast(AbstractContextManager[Any], scope)


def _authority(adapter: Any) -> Any:
    runner = getattr(adapter, "gateway_runner", None)
    if runner is None:
        return None
    from hermes_constants import get_hermes_home
    from gateway.session_authorities import authority_for_home
    return authority_for_home(runner, get_hermes_home())


def _run_state(adapter: Any, session_id: str, db: Any) -> str:
    """``running``, ``idle``, or ``unknown``.

    Idle is only this profile's authority execution map plus its lease and
    admission rows. ``adapter._run_statuses`` is not profile-scoped: the same
    session id in another profile must not be treated as idle.
    """
    try:
        authority = _authority(adapter)
    except Exception:
        logger.warning("effort authority lookup failed for %s", session_id, exc_info=True)
        return "unknown"
    try:
        return profile_execution_state(db, session_id, authority)
    except Exception:
        logger.warning("effort run-state lookup failed for %s", session_id, exc_info=True)
        return "unknown"


class ClawdEffortRoutes:
    def __init__(self, adapter: Any) -> None:
        self.adapter = adapter

    async def _db(self):
        opener = getattr(self.adapter, "_ensure_session_db", None)
        if not callable(opener):
            raise EffortServiceError("session_db_unavailable", "会话库不可用，档位未写入", 503)
        db = opener()
        if db is None:
            raise EffortServiceError("session_db_unavailable", "会话库不可用，档位未写入", 503)
        return db

    async def handle_get(self, request: web.Request) -> web.Response:
        auth_err = _check_auth(self.adapter, request)
        if auth_err is not None:
            return auth_err
        session_id = request.match_info.get("session_id", "")
        if not _valid_session_id(session_id):
            return _error("无效的会话", 400, "invalid_session_id")
        profile, profile_err = _profile_name(request)
        if profile_err is not None:
            return profile_err
        try:
            with _scope(self.adapter, profile):
                db = await self._db()
                state = _run_state(self.adapter, session_id, db)
                running = None if state == "unknown" else state == "running"
                view = await asyncio.to_thread(read_session_effort, db, session_id, running=running)
        except EffortServiceError as exc:
            return _error(exc.message, exc.status, exc.code)
        except Exception:
            logger.exception("failed to read effort for %s", session_id)
            return _error("档位读取失败", 500, "effort_read_failed")
        return web.json_response(view)

    async def handle_put(self, request: web.Request) -> web.Response:
        auth_err = _check_auth(self.adapter, request)
        if auth_err is not None:
            return auth_err
        draining = getattr(self.adapter, "_draining_response", None)
        if callable(draining):
            blocked = draining()
            if blocked is not None:
                if isinstance(blocked, web.Response):
                    return blocked
                return _error("服务正在关闭，档位未写入", 503, "effort_run_state_unavailable")
        session_id = request.match_info.get("session_id", "")
        if not _valid_session_id(session_id):
            return _error("无效的会话", 400, "invalid_session_id")
        try:
            body = await request.json()
        except Exception:
            return _error("无效的请求", 400, "invalid_json")
        if not isinstance(body, dict) or set(body) != {"effort"}:
            return _error("只能提交 effort", 400, "unexpected_fields")
        profile, profile_err = _profile_name(request)
        if profile_err is not None:
            return profile_err
        try:
            with _scope(self.adapter, profile):
                db = await self._db()
                authority: Any = None
                state = "unknown"
                try:
                    authority = _authority(self.adapter)
                    state = profile_execution_state(db, session_id, authority)
                except Exception:
                    logger.warning("effort run-state lookup failed for %s", session_id, exc_info=True)
                    state = "unknown"
                    authority = None
                if state == "unknown" or authority is None:
                    raise EffortServiceError(
                        "effort_run_state_unavailable", "无法确认会话是否仍在运行，档位未写入", 503,
                    )
                view = await asyncio.to_thread(
                    write_session_effort,
                    db,
                    session_id,
                    body["effort"],
                    running=state == "running",
                    authority=authority,
                )
        except EffortServiceError as exc:
            return _error(exc.message, exc.status, exc.code)
        except Exception:
            logger.exception("failed to write effort for %s", session_id)
            return _error("档位写入失败", 500, "effort_write_failed")
        return web.json_response(view)


def register_effort_routes(app: web.Application, adapter: Any) -> None:
    """Attach effort routes. Parent mounts this; do not edit api_server.py for it.

    Not called from this module. Mounting is still required before the routes exist.
    """
    handlers = ClawdEffortRoutes(adapter)
    routes = (
        ("GET", "/api/sessions/{session_id}/effort", handlers.handle_get),
        ("PUT", "/api/sessions/{session_id}/effort", handlers.handle_put),
    )
    for method, path, handler in routes:
        app.router.add_route(method, path, handler)
        app.router.add_route(method, f"/p/{{profile}}{path}", handler)
    logger.info("clawd effort routes registered (%d endpoints)", len(routes))
