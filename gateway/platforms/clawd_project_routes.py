"""HTTP route handlers for clawd projects and project memory.

Exported as `register_project_routes(app, adapter)` for attachment by the API server
or external integration layer.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from aiohttp import web

from clawd_projects.migration import (
    MigrationError,
    execute_folder_migration,
    get_migration_receipt,
    plan_folder_migration,
    preview_folder_migration,
    rollback_folder_migration,
)
from clawd_projects.memory_authorization import (
    MemoryAuthorizationError,
    apply_authorized_project_memory,
    authorization_error_body,
    authorize_user_project_memory_read,
    authorize_user_project_memory_write,
    load_authorized_project_memory,
)
from clawd_projects.service import (
    SessionBusyError,
    SessionStoreCheckError,
    bind_session_to_project,
    get_project_by_id,
    prepare_project_directory,
)
from hermes_cli import projects_db as pdb
from hermes_constants import get_hermes_home

logger = logging.getLogger("gateway.clawd_projects")


def _json_error(message: str, status: int = 400, code: Optional[str] = None) -> web.Response:
    payload = {"error": code or "bad_request", "message": message}
    return web.json_response(payload, status=status)


def _migration_http(payload: dict[str, Any]) -> web.Response:
    status = int(payload.get("http_status") or (200 if payload.get("success") is True else 409))
    if payload.get("success") is not True:
        payload = {**payload, "success": False}
        if status < 400:
            status = 409
    return web.json_response(payload, status=status)


def _migration_error(exc: MigrationError) -> web.Response:
    body: dict[str, Any] = {
        "success": False,
        "error": exc.code,
        "message": exc.message,
        "cross_db_atomic": False,
        "applied": False,
    }
    if exc.details:
        body["details"] = exc.details
    if exc.receipt:
        body["receipt"] = exc.receipt
    return web.json_response(body, status=exc.status)


def _check_adapter_auth(adapter: Any, request: web.Request) -> Optional[web.Response]:
    """Require a real auth check. Missing checker is a refusal, not an open route."""
    checker = getattr(adapter, "_check_auth", None)
    if not callable(checker):
        return _json_error("authentication is not configured", 401, "unauthorized")
    res = checker(request)
    if isinstance(res, web.Response):
        return res
    return None


async def _json_object(request: web.Request) -> tuple[Optional[dict[str, Any]], Optional[web.Response]]:
    try:
        body = await request.json()
    except Exception:
        return None, _json_error("Invalid JSON body", 400, "invalid_json")
    if not isinstance(body, dict):
        return None, _json_error("JSON body must be an object", 400, "invalid_json")
    return body, None


def _explicit_true(value: Any) -> bool:
    return value is True or (isinstance(value, str) and value.strip().lower() in {"1", "true", "yes"})


def _memory_generation(value: Any, *, query: bool = False) -> Any:
    """JSON ints pass through. Only a query string of digits is parsed; a JSON string is not coerced."""
    if value is None or value == "":
        return None
    if type(value) is int:
        return value
    if query and isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            return int(text)
    return value


def _memory_principal(adapter: Any) -> tuple[Any, Any]:
    """API key holder as the profile's existing session principal. Not a new token."""
    from gateway.session_contract import Principal
    from hermes_state_runtime import RuntimeStoreError

    runner = getattr(adapter, "gateway_runner", None)
    if runner is None:
        raise MemoryAuthorizationError("missing_identity", "session authority is required", status=401)
    try:
        from gateway.session_authorities import require_active_authority
        authority = require_active_authority(runner)
    except RuntimeStoreError as exc:
        reason = str(getattr(exc, "reason", "") or "unauthorized")
        if reason == "profile_mismatch":
            raise MemoryAuthorizationError("profile_mismatch", "profile authority is not active", status=403) from exc
        raise MemoryAuthorizationError(
            "missing_identity", f"session authority unavailable ({reason})", status=401
        ) from exc
    actor = Principal(
        "api",
        authority.profile_id,
        frozenset({"session:read", "session:control"}),
        "api-project-memory",
    )
    return actor, authority


def _authz_error(exc: MemoryAuthorizationError) -> web.Response:
    return web.json_response(authorization_error_body(exc), status=exc.status)


class ClawdProjectRoutes:
    """Encapsulates project and project-memory HTTP endpoint implementations."""

    def __init__(self, adapter: Any):
        self.adapter = adapter

    def _auth(self, request: web.Request) -> Optional[web.Response]:
        return _check_adapter_auth(self.adapter, request)

    def _get_db_conn(self) -> pdb.sqlite3.Connection:
        # Note: in multiplex mode, get_hermes_home() points to the scoped profile home
        home = get_hermes_home()
        return pdb.connect(home / "projects.db")

    async def handle_list_projects(self, request: web.Request) -> web.Response:
        """GET /api/projects — list projects with session counts."""
        if auth_err := self._auth(request):
            return auth_err

        include_archived = request.query.get("include_archived", "").lower() in ("true", "1", "yes")

        def _query() -> list[dict[str, Any]]:
            with pdb.connect_closing(get_hermes_home() / "projects.db") as conn:
                projects = pdb.list_projects(conn, include_archived=include_archived)
                results = []
                for p in projects:
                    d = p.to_dict()
                    sessions = pdb.list_project_sessions(conn, p.id)
                    d["session_count"] = len(sessions)
                    results.append(d)
                return results

        data = await asyncio.to_thread(_query)
        return web.json_response({"projects": data})

    async def handle_create_project(self, request: web.Request) -> web.Response:
        """POST /api/projects — create a project with required primary_path directory."""
        if auth_err := self._auth(request):
            return auth_err

        body, err = await _json_object(request)
        if err is not None or body is None:
            return err or _json_error("JSON body must be an object", 400, "invalid_json")

        name = str(body.get("name") or "").strip()
        if not name:
            return _json_error("Project name is required", 400, "missing_name")

        primary_path = str(body.get("primary_path") or "").strip()
        if not primary_path:
            return _json_error("primary_path is required for project mode", 400, "missing_primary_path")

        try:
            resolved_path = prepare_project_directory(
                primary_path, create_directory=_explicit_true(body.get("create_directory"))
            )
        except ValueError as exc:
            return _json_error(str(exc), 400, "invalid_directory")

        slug = body.get("slug")
        description = body.get("description")
        icon = body.get("icon")
        color = body.get("color")
        board_slug = body.get("board_slug")
        allow_duplicate = bool(body.get("allow_duplicate_path", False))

        def _create() -> dict[str, Any]:
            with pdb.connect_closing(get_hermes_home() / "projects.db") as conn:
                pid = pdb.create_project(
                    conn,
                    name=name,
                    slug=slug,
                    primary_path=str(resolved_path),
                    description=description,
                    icon=icon,
                    color=color,
                    board_slug=board_slug,
                    allow_duplicate_path=allow_duplicate,
                )
                proj = pdb.get_project(conn, pid)
                assert proj is not None
                res = proj.to_dict()
                res["session_count"] = 0
                return res

        try:
            res = await asyncio.to_thread(_create)
            return web.json_response({"project": res}, status=201)
        except ValueError as exc:
            return _json_error(str(exc), 400, "duplicate_or_invalid_project")

    async def handle_get_project(self, request: web.Request) -> web.Response:
        """GET /api/projects/{id} — get project details."""
        if auth_err := self._auth(request):
            return auth_err

        pid = request.match_info["id"]

        def _get() -> Optional[dict[str, Any]]:
            with pdb.connect_closing(get_hermes_home() / "projects.db") as conn:
                p = pdb.get_project(conn, pid)
                if not p:
                    return None
                d = p.to_dict()
                d["session_count"] = len(pdb.list_project_sessions(conn, p.id))
                return d

        data = await asyncio.to_thread(_get)
        if data is None:
            return _json_error(f"Project not found: {pid}", 404, "project_not_found")
        return web.json_response({"project": data})

    async def handle_patch_project(self, request: web.Request) -> web.Response:
        """PATCH /api/projects/{id} — update metadata or archive/unarchive."""
        if auth_err := self._auth(request):
            return auth_err

        pid = request.match_info["id"]
        body, err = await _json_object(request)
        if err is not None or body is None:
            return err or _json_error("JSON body must be an object", 400, "invalid_json")

        def _update() -> Optional[dict[str, Any]]:
            with pdb.connect_closing(get_hermes_home() / "projects.db") as conn:
                p = pdb.get_project(conn, pid)
                if not p:
                    return None

                # Handle archive state change
                if "archived" in body:
                    if body["archived"]:
                        pdb.archive_project(conn, p.id)
                    else:
                        pdb.restore_project(conn, p.id)

                # Top-level patchable fields
                name = body.get("name")
                description = body.get("description")
                icon = body.get("icon")
                color = body.get("color")
                board_slug = body.get("board_slug")
                if any(v is not None for v in (name, description, icon, color, board_slug)):
                    pdb.update_project(
                        conn,
                        p.id,
                        name=name,
                        description=description,
                        icon=icon,
                        color=color,
                        board_slug=board_slug,
                    )
                updated = pdb.get_project(conn, p.id)
                assert updated is not None
                res = updated.to_dict()
                res["session_count"] = len(pdb.list_project_sessions(conn, updated.id))
                return res

        try:
            res = await asyncio.to_thread(_update)
            if res is None:
                return _json_error(f"Project not found: {pid}", 404, "project_not_found")
            return web.json_response({"project": res})
        except ValueError as exc:
            return _json_error(str(exc), 400, "invalid_update")

    async def handle_project_sessions(self, request: web.Request) -> web.Response:
        """GET /api/projects/{id}/sessions — list sessions bound to project."""
        if auth_err := self._auth(request):
            return auth_err

        pid = request.match_info["id"]

        def _list() -> Optional[list[str]]:
            with pdb.connect_closing(get_hermes_home() / "projects.db") as conn:
                p = pdb.get_project(conn, pid)
                if not p:
                    return None
                return pdb.list_project_sessions(conn, p.id)

        session_ids = await asyncio.to_thread(_list)
        if session_ids is None:
            return _json_error(f"Project not found: {pid}", 404, "project_not_found")

        # Hydrate session summary details from session_db if available
        sdb = None
        if hasattr(self.adapter, "_ensure_session_db_async"):
            sdb = await self.adapter._ensure_session_db_async()
        elif hasattr(self.adapter, "_ensure_session_db"):
            sdb = self.adapter._ensure_session_db()

        session_items = []
        for sid in session_ids:
            item: dict[str, Any] = {"session_id": sid}
            if sdb is not None:
                try:
                    s_row = await asyncio.to_thread(sdb.get_session, sid)
                    if isinstance(s_row, dict):
                        item["title"] = s_row.get("title")
                        item["created_at"] = s_row.get("started_at")
                        item["last_activity_at"] = s_row.get("last_activity_at")
                        item["message_count"] = s_row.get("message_count", 0)
                except Exception:
                    pass
            session_items.append(item)

        return web.json_response({"project_id": pid, "sessions": session_items})

    async def handle_get_session_project(self, request: web.Request) -> web.Response:
        """GET /api/sessions/{session_id}/project — get bound project for a session."""
        if auth_err := self._auth(request):
            return auth_err

        sid = request.match_info["session_id"]

        def _get() -> tuple[Optional[str], Optional[dict[str, Any]]]:
            with pdb.connect_closing(get_hermes_home() / "projects.db") as conn:
                pid = pdb.get_session_project_id(conn, sid)
                if not pid:
                    return None, None
                proj = pdb.get_project(conn, pid)
                return pid, (proj.to_dict() if proj else None)

        pid, proj = await asyncio.to_thread(_get)
        return web.json_response({
            "session_id": sid,
            "project_id": pid,
            "project": proj,
        })

    async def handle_bind_session_project(self, request: web.Request) -> web.Response:
        """POST /api/sessions/{session_id}/project — bind or move session to a project."""
        if auth_err := self._auth(request):
            return auth_err

        sid = request.match_info["session_id"]
        body, err = await _json_object(request)
        if err is not None or body is None:
            return err or _json_error("JSON body must be an object", 400, "invalid_json")

        project_id = body.get("project_id")
        if project_id is not None:
            project_id = str(project_id).strip() or None

        sdb = None
        if hasattr(self.adapter, "_ensure_session_db_async"):
            sdb = await self.adapter._ensure_session_db_async()
        elif hasattr(self.adapter, "_ensure_session_db"):
            sdb = self.adapter._ensure_session_db()

        def _bind() -> bool:
            # Idle check and the projects write share the state write callback.
            # That is a lock fence, not a cross-database atomic commit.
            return bind_session_to_project(sid, project_id, session_db=sdb)

        try:
            ok = await asyncio.to_thread(_bind)
            return web.json_response({
                "success": bool(ok),
                "session_id": sid,
                "project_id": project_id,
            })
        except LookupError:
            return _json_error(f"Session not found: {sid}", 404, "session_not_found")
        except ValueError as exc:
            return _json_error(str(exc), 404, "project_not_found")
        except SessionBusyError:
            return _json_error("Cannot change project of an active session", 409, "session_busy")
        except SessionStoreCheckError as exc:
            return _json_error(str(exc), 503, "session_check_failed")
        except Exception:
            logger.exception("session project bind failed")
            return _json_error("session project bind failed", 500, "bind_failed")

    async def handle_get_project_memory(self, request: web.Request) -> web.Response:
        """GET /api/projects/{id}/memory — read project memory for a bound session.

        Bearer auth is not enough. ``session_id`` must belong to the active
        profile and be bound to this project. This does not call evaluate_gate.
        """
        if auth_err := self._auth(request):
            return auth_err

        pid = request.match_info["id"]
        session_id = str(request.query.get("session_id") or "").strip()
        generation = _memory_generation(request.query.get("execution_generation"), query=True)

        def _read() -> dict[str, Any]:
            actor, authority = _memory_principal(self.adapter)
            edit = authorize_user_project_memory_read(
                actor,
                session_id=session_id,
                project_id=pid,
                generation=generation,
                authority=authority,
            )
            return load_authorized_project_memory(edit, actor=actor, authority=authority)

        try:
            data = await asyncio.to_thread(_read)
        except MemoryAuthorizationError as exc:
            return _authz_error(exc)
        return web.json_response({"project_id": pid, "memory": data})

    async def handle_update_project_memory(self, request: web.Request) -> web.Response:
        """POST /api/projects/{id}/memory — user edit of project memory.

        Requires the active profile's session authority and a session bound to
        this project. Does not call evaluate_gate: the caller is the editor.
        Agent tool writes stay on memory_tool's existing approval gate.
        ``execution_generation``, when present, must be the running claim.
        """
        if auth_err := self._auth(request):
            return auth_err

        pid = request.match_info["id"]
        body, err = await _json_object(request)
        if err is not None or body is None:
            return err or _json_error("JSON body must be an object", 400, "invalid_json")

        operations = body.get("operations")
        if not isinstance(operations, list):
            return _json_error("operations array is required", 400, "missing_operations")
        session_id = str(body.get("session_id") or "").strip()
        generation = _memory_generation(body.get("execution_generation"))

        def _apply() -> dict[str, Any]:
            actor, authority = _memory_principal(self.adapter)
            edit = authorize_user_project_memory_write(
                actor,
                session_id=session_id,
                project_id=pid,
                generation=generation,
                authority=authority,
            )
            return apply_authorized_project_memory(edit, operations, actor=actor, authority=authority)

        try:
            res = await asyncio.to_thread(_apply)
        except MemoryAuthorizationError as exc:
            return _authz_error(exc)

        status = 200 if res.get("success") else 400
        return web.json_response(res, status=status)


    async def _session_db(self) -> Any:
        if hasattr(self.adapter, "_ensure_session_db_async"):
            return await self.adapter._ensure_session_db_async()
        if hasattr(self.adapter, "_ensure_session_db"):
            return self.adapter._ensure_session_db()
        return None

    async def handle_migration_preview(self, request: web.Request) -> web.Response:
        """GET /api/projects/migrations/preview — read-only folder inventory. Does not migrate."""
        if auth_err := self._auth(request):
            return auth_err
        home = get_hermes_home()
        try:
            payload = await asyncio.to_thread(preview_folder_migration, home)
        except MigrationError as exc:
            return _migration_error(exc)
        except Exception:
            logger.exception("folder migration preview failed")
            return _json_error("旧文件夹预览失败，未当作空列表", 500, "migration_preview_failed")
        return _migration_http(payload)

    async def handle_migration_plan(self, request: web.Request) -> web.Response:
        """POST /api/projects/migrations/preview — dry-run an explicit mapping. Never writes."""
        if auth_err := self._auth(request):
            return auth_err
        body, err = await _json_object(request)
        if err is not None or body is None:
            return err or _json_error("JSON body must be an object", 400, "invalid_json")
        home = get_hermes_home()
        session_db = await self._session_db()
        try:
            payload = await asyncio.to_thread(
                plan_folder_migration,
                body.get("mappings"),
                session_db,
                home,
                reuse_migration_id=body.get("reuse_migration_id"),
                confirm_reuse=body.get("confirm_reuse") is True,
            )
        except MigrationError as exc:
            return _migration_error(exc)
        except Exception:
            logger.exception("folder migration plan failed")
            return _json_error("迁移核对失败，未写入", 500, "migration_plan_failed")
        return _migration_http(payload)

    async def handle_migration_execute(self, request: web.Request) -> web.Response:
        """POST /api/projects/migrations — explicit confirmed mapping. Not a preview."""
        if auth_err := self._auth(request):
            return auth_err
        body, err = await _json_object(request)
        if err is not None or body is None:
            return err or _json_error("JSON body must be an object", 400, "invalid_json")
        home = get_hermes_home()
        session_db = await self._session_db()
        try:
            payload = await asyncio.to_thread(
                execute_folder_migration,
                body.get("mappings"),
                session_db,
                confirm=body.get("confirm") is True,
                idempotency_key=body.get("idempotency_key"),
                hermes_home=home,
                reuse_migration_id=body.get("reuse_migration_id"),
                confirm_reuse=body.get("confirm_reuse") is True,
            )
        except MigrationError as exc:
            return _migration_error(exc)
        except Exception:
            logger.exception("folder migration execute failed")
            return _json_error("迁移失败，未报告成功", 500, "migration_failed")
        return _migration_http(payload)

    async def handle_migration_get(self, request: web.Request) -> web.Response:
        """GET /api/projects/migrations/{migration_id} — read one receipt."""
        if auth_err := self._auth(request):
            return auth_err
        home = get_hermes_home()
        try:
            payload = await asyncio.to_thread(get_migration_receipt, request.match_info["migration_id"], home)
        except MigrationError as exc:
            return _migration_error(exc)
        except Exception:
            logger.exception("folder migration receipt read failed")
            return _json_error("迁移回执读取失败", 500, "migration_receipt_failed")
        return _migration_http(payload)

    async def handle_migration_rollback(self, request: web.Request) -> web.Response:
        """POST /api/projects/migrations/{migration_id}/rollback — restore this receipt's unchanged bindings."""
        if auth_err := self._auth(request):
            return auth_err
        body, err = await _json_object(request)
        if err is not None or body is None:
            return err or _json_error("JSON body must be an object", 400, "invalid_json")
        home = get_hermes_home()
        session_db = await self._session_db()
        try:
            payload = await asyncio.to_thread(
                rollback_folder_migration,
                request.match_info["migration_id"],
                session_db,
                confirm=body.get("confirm") is True,
                hermes_home=home,
            )
        except MigrationError as exc:
            return _migration_error(exc)
        except Exception:
            logger.exception("folder migration rollback failed")
            return _json_error("回滚失败，未报告成功", 500, "migration_rollback_failed")
        return _migration_http(payload)


def register_project_routes(app: web.Application, adapter: Any) -> None:
    """Register clawd project routes onto the given aiohttp web.Application.

    Supports both base routes and multiplexed /p/{profile}/ routes.
    """
    handlers = ClawdProjectRoutes(adapter)

    route_definitions = [
        ("GET", "/api/projects/migrations/preview", handlers.handle_migration_preview),
        ("POST", "/api/projects/migrations/preview", handlers.handle_migration_plan),
        ("POST", "/api/projects/migrations", handlers.handle_migration_execute),
        ("GET", "/api/projects/migrations/{migration_id}", handlers.handle_migration_get),
        ("POST", "/api/projects/migrations/{migration_id}/rollback", handlers.handle_migration_rollback),
        ("GET", "/api/projects", handlers.handle_list_projects),
        ("POST", "/api/projects", handlers.handle_create_project),
        ("GET", "/api/projects/{id}", handlers.handle_get_project),
        ("PATCH", "/api/projects/{id}", handlers.handle_patch_project),
        ("GET", "/api/projects/{id}/sessions", handlers.handle_project_sessions),
        ("GET", "/api/sessions/{session_id}/project", handlers.handle_get_session_project),
        ("POST", "/api/sessions/{session_id}/project", handlers.handle_bind_session_project),
        ("GET", "/api/projects/{id}/memory", handlers.handle_get_project_memory),
        ("POST", "/api/projects/{id}/memory", handlers.handle_update_project_memory),
    ]

    for method, path, handler in route_definitions:
        app.router.add_route(method, path, handler)
        # Register multiplexed mirror
        app.router.add_route(method, f"/p/{{profile}}{path}", handler)
    logger.info("clawd project routes registered (%d endpoints)", len(route_definitions))
