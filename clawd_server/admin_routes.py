"""Attach profile delete preview/confirm and frozen settings routes.

HTTP still enters ``Handler.do_POST`` / ``do_GET`` / ``do_PUT`` -> ``_dispatch``.
That method checks the panel key before ``_route``. The panel proxy checks
Origin and the session cookie before it reaches this process. This module does
not open a listener and does not replace ``_dispatch``.
"""

from __future__ import annotations

import importlib
from typing import Any, Callable

from clawd_server.admin_common import ConfigError, ensure_write_facts

ROUTE_SPECS = (
    "POST /profiles/delete-preview",
    "POST /profiles/delete-confirm",
    "POST /skills/enabled",
    "POST /mcp/servers",
    "POST /models/custom-endpoints",
    "GET /settings/advanced",
    "POST /settings/advanced",
    "GET /tools/toolsets",
    "POST /tools/toolsets/enabled",
    "GET /memory/providers",
    "PUT /memory/providers/config",
    "GET /connections/platforms",
    "POST /connections/platforms/enabled",
    "GET /plugins/config",
    "PUT /plugins/config",
    "GET /logs",
)

# Open objects the approved matrix allows on /settings/advanced.
# models-tab, exclude, and exclude-secret-map keys stay off this route.
ADVANCED_KEYS = frozenset({
    "credential_pool_strategies",
    "agent.reasoning_overrides",
    "terminal.docker_env",
    "web.provider_tier",
    "browser.camofox",
    "compression.model_thresholds",
    "auxiliary.vision.extra_body",
    "auxiliary.compression.extra_body",
    "auxiliary.skills_hub.extra_body",
    "auxiliary.approval.extra_body",
    "auxiliary.mcp.extra_body",
    "auxiliary.title_generation.extra_body",
    "auxiliary.memory_query_rewrite.extra_body",
    "auxiliary.tts_audio_tags.extra_body",
    "auxiliary.voice_chat.extra_body",
    "auxiliary.triage_specifier.extra_body",
    "auxiliary.kanban_decomposer.extra_body",
    "auxiliary.profile_describer.extra_body",
    "auxiliary.goal_judge.extra_body",
    "auxiliary.curator.extra_body",
    "auxiliary.monitor.extra_body",
    "auxiliary.background_review.extra_body",
    "auxiliary.moa_reference.extra_body",
    "auxiliary.moa_aggregator.extra_body",
    "display.status_phrases",
    "delegation.backends",
    "delegation.request_overrides",
    "honcho",
    "slack.channel_prompts",
    "discord.channel_prompts",
    "whatsapp",
    "telegram.channel_prompts",
    "mattermost.channel_prompts",
    "quick_commands",
    "platform_hints",
    "hooks",
    "personalities",
    "lsp.servers",
    "uploads.s3",
})

_PREVIEW = "/profiles/delete-preview"
_CONFIRM = "/profiles/delete-confirm"
_FALLTHROUGH = object()

# Python call shape is fixed here so page owners do not invent HTTP routes.
# set_skill_enabled(mod, name, enabled) is the same export admin_native binds.
# HTTP calls that export directly. It does not call the sidecar CLI writer.
# Plugin PUT ``fields`` is a changed-value object. GET/PUT responses return a field array.


def load_export(module: str, name: str) -> Callable[..., Any] | None:
    """Return a frozen export, or None when that module has not been added.

    A broken import is not treated as a missing module: the caller must refuse
    the request instead of falling through to an older writer.
    """
    full = f"clawd_server.{module}"
    try:
        loaded = importlib.import_module(full)
    except ModuleNotFoundError as exc:
        if exc.name == full:
            return None
        raise
    fn = getattr(loaded, name, None)
    if not callable(fn):
        return None
    return fn


def install(mod: Any) -> bool:
    """Wrap ``Handler._route`` once. Returns whether the live dispatch path is attached."""
    handler = getattr(mod, "Handler", None)
    mod._clawd_admin_route_specs = ROUTE_SPECS
    route = getattr(handler, "_route", None) if handler is not None else None
    dispatch = getattr(handler, "_dispatch", None) if handler is not None else None
    if handler is None or not callable(route) or not callable(dispatch):
        mod._clawd_admin_routes_attached = False
        return False
    handler._clawd_admin_mod = mod
    if not getattr(handler, "_clawd_admin_put_attached", False):
        def do_PUT(self: Any) -> None:
            self._dispatch("PUT")

        handler.do_PUT = do_PUT
        handler._clawd_admin_put_attached = True
    if not getattr(handler, "_clawd_admin_route_wrapped", False):
        original = route

        def _route(self: Any, method: str, path: str, query: dict[str, str]) -> Any:
            current = type(self)._clawd_admin_mod
            if method == "POST" and path in {_PREVIEW, _CONFIRM}:
                return handle(current, self, path)
            result = dispatch_frozen(current, self, method, path, query)
            if result is _FALLTHROUGH:
                return original(self, method, path, query)
            return result

        handler._route = _route
        handler._clawd_admin_route_wrapped = True
    if handler._dispatch is not dispatch:
        mod._clawd_admin_routes_attached = False
        return False
    mod._clawd_admin_routes_attached = True
    return True


def handle(mod: Any, handler: Any, path: str) -> Any:
    """Read the body with the handler's own parser. Never call the name-only delete."""
    reader = getattr(handler, "_json_body", None)
    if not callable(reader):
        raise mod.ApiError(500, "admin_route_unbound", "删除确认没有接到原请求解析，已拒绝")
    body = reader()
    if not isinstance(body, dict):
        raise mod.ApiError(400, "invalid_body", "需要对象")
    if path == _PREVIEW:
        fn = getattr(mod, "preview_delete_profile", None)
        if not callable(fn):
            raise mod.ApiError(503, "admin_route_unbound", "删除预览没有接到管理函数，已拒绝")
        return fn(body.get("name"))
    if path == _CONFIRM:
        fn = getattr(mod, "confirm_delete_profile", None)
        if not callable(fn):
            raise mod.ApiError(503, "admin_route_unbound", "删除确认没有接到管理函数，已拒绝")
        return fn(
            body.get("name"),
            body.get("confirm"),
            body.get("paths"),
            body.get("token"),
            body.get("fingerprint"),
        )
    raise mod.ApiError(404, "not_found", "not found")


def dispatch_frozen(mod: Any, handler: Any, method: str, path: str, query: dict[str, str]) -> Any:
    """Serve one frozen settings route, or ``_FALLTHROUGH`` when this wrapper does not own it."""
    if method == "POST" and path == "/skills/enabled":
        body = _body(mod, handler)
        name, enabled = body.get("name"), body.get("enabled")
        if not isinstance(name, str) or not isinstance(enabled, bool):
            raise mod.ApiError(400, "invalid_body", "需要 name 与布尔 enabled")
        return _write(mod, _need(mod, "admin_skills", "set_skill_enabled", "技能开关"), name, enabled)
    if method == "POST" and path == "/mcp/servers":
        body = _body(mod, handler)
        result = _write(mod, _need(mod, "admin_mcp", "create_mcp", "MCP 新增"), body)
        return _drop(result, ("env", "headers", "apiKey", "api_key"))
    if method == "POST" and path == "/models/custom-endpoints":
        body = _body(mod, handler)
        result = _write(mod, _need(mod, "admin_models", "create_custom_endpoint", "自定义端点"), body)
        return _drop(result, ("apiKey", "api_key", "token", "secret"))
    if method == "GET" and path == "/settings/advanced":
        key = _advanced_key(mod, query.get("key", ""))
        return _read(mod, _need(mod, "admin_settings", "get_advanced", "开放对象"), key)
    if method == "POST" and path == "/settings/advanced":
        body = _body(mod, handler)
        key = body.get("key")
        if not isinstance(key, str):
            raise mod.ApiError(400, "invalid_body", "需要 key 与 value")
        _advanced_key(mod, key)
        if "value" not in body:
            raise mod.ApiError(400, "invalid_body", "需要 key 与 value")
        return _write(mod, _need(mod, "admin_settings", "set_advanced", "开放对象"), key, body["value"])
    if method == "GET" and path == "/tools/toolsets":
        return _read(mod, _need(mod, "admin_tools", "list_toolsets", "工具集", "tools_unavailable"))
    if method == "POST" and path == "/tools/toolsets/enabled":
        body = _body(mod, handler)
        name, enabled = body.get("name"), body.get("enabled")
        if not isinstance(name, str) or not isinstance(enabled, bool):
            raise mod.ApiError(400, "invalid_body", "需要 name 与布尔 enabled")
        result = _write(mod, _need(mod, "admin_tools", "set_toolset_enabled", "工具集", "tools_unavailable"), name, enabled)
        if "effectiveNow" not in result:
            result["effectiveNow"] = False
        if "applied" not in result:
            result["applied"] = False
        return result
    if method == "GET" and path == "/memory/providers":
        return _read(mod, _need(mod, "admin_memory", "list_providers", "记忆提供方", "memory_unavailable"))
    if method == "PUT" and path == "/memory/providers/config":
        body = _body(mod, handler)
        if not isinstance(body.get("name"), str) or not isinstance(body.get("fields"), dict):
            raise mod.ApiError(400, "invalid_body", "需要 name 与 fields 对象")
        result = _write(
            mod,
            _need(mod, "admin_memory", "set_provider_config", "记忆提供方", "memory_unavailable"),
            body["name"],
            body["fields"],
        )
        return _drop(result, ("token", "password", "apiKey", "api_key", "secret"))
    if method == "GET" and path == "/connections/platforms":
        return _read(mod, _need(mod, "admin_connections", "list_platforms", "连接平台", "connections_unavailable"))
    if method == "POST" and path == "/connections/platforms/enabled":
        body = _body(mod, handler)
        platform_id, enabled = body.get("id"), body.get("enabled")
        if not isinstance(platform_id, str) or not isinstance(enabled, bool):
            raise mod.ApiError(400, "invalid_body", "需要 id 与布尔 enabled")
        result = _write(
            mod,
            _need(mod, "admin_connections", "set_platform_enabled", "连接平台", "connections_unavailable"),
            platform_id,
            enabled,
        )
        return _drop(result, ("token", "password", "apiKey", "api_key", "secret"))
    if method == "GET" and path == "/plugins/config":
        name = query.get("name", "")
        if not isinstance(name, str) or not name:
            raise mod.ApiError(400, "invalid_body", "需要插件 name")
        result = _read(mod, _need(mod, "admin_plugins", "get_plugin_config", "插件配置"), name)
        if isinstance(result, dict):
            result = _drop(result, ("token", "password", "apiKey", "api_key", "secret"))
        return result
    if method == "PUT" and path == "/plugins/config":
        body = _body(mod, handler)
        if not isinstance(body.get("name"), str) or not isinstance(body.get("fields"), dict):
            raise mod.ApiError(400, "invalid_body", "需要 name 与 fields 对象")
        result = _write(mod, _need(mod, "admin_plugins", "set_plugin_config", "插件配置"), body["name"], body["fields"])
        result = _drop(result, ("token", "password", "apiKey", "api_key", "secret"))
        if "loadedNow" not in result:
            result["loadedNow"] = False
        return result
    if method == "GET" and path == "/logs":
        return _logs(mod, query)
    return _FALLTHROUGH


def _logs(mod: Any, query: dict[str, str]) -> Any:
    source = query.get("source", "")
    journal = getattr(mod, "LOG_JOURNAL", None)
    if isinstance(journal, dict) and source in journal:
        raise mod.ApiError(501, "journal_host_boundary", "journal 源属于宿主边界，面板不读取")
    try:
        fn = load_export("admin_logs", "get_logs")
    except Exception as exc:
        raise mod.ApiError(503, "admin_route_unbound", "日志导出未能加载，已拒绝") from exc
    if fn is None:
        return _FALLTHROUGH
    try:
        lines = int(query.get("lines", "200"))
    except (TypeError, ValueError):
        lines = 200
    return _call(
        mod,
        fn,
        source,
        lines,
        query.get("level", ""),
        query.get("component", ""),
        query.get("q", ""),
        write=False,
    )


def _advanced_key(mod: Any, key: Any) -> str:
    if not isinstance(key, str) or key not in ADVANCED_KEYS:
        raise mod.ApiError(403, "advanced_key_denied", "这个开放对象不在允许编辑的名单里")
    return key


def _body(mod: Any, handler: Any) -> dict[str, Any]:
    reader = getattr(handler, "_json_body", None)
    if not callable(reader):
        raise mod.ApiError(500, "admin_route_unbound", "请求解析没有接到原处理函数，已拒绝")
    body = reader()
    if not isinstance(body, dict):
        raise mod.ApiError(400, "invalid_body", "需要对象")
    return body


def _need(mod: Any, module: str, name: str, label: str, code: str = "admin_route_unbound") -> Callable[..., Any]:
    try:
        fn = load_export(module, name)
    except Exception as exc:
        raise mod.ApiError(503, code, f"{label}导出未能加载，已拒绝，没有返回空成功") from exc
    if fn is None:
        raise mod.ApiError(503, code, f"{label}还没有进程内导出，已拒绝，没有返回空成功")
    return fn


def _read(mod: Any, fn: Callable[..., Any], *args: Any) -> Any:
    return _call(mod, fn, *args, write=False)


def _write(mod: Any, fn: Callable[..., Any], *args: Any) -> dict[str, Any]:
    payload = _call(mod, fn, *args, write=True)
    try:
        return ensure_write_facts(payload)
    except ConfigError as exc:
        raise mod.ApiError(exc.status, exc.code, exc.message) from exc


def _call(mod: Any, fn: Callable[..., Any], *args: Any, write: bool) -> Any:
    try:
        return fn(mod, *args)
    except ConfigError as exc:
        raise mod.ApiError(exc.status, exc.code, exc.message) from exc
    except Exception as exc:
        api_error = getattr(mod, "ApiError", None)
        if api_error is not None and isinstance(exc, api_error):
            raise
        code = "write_unverified" if write else "admin_route_unbound"
        raise mod.ApiError(500, code, "管理函数没有返回可核对的结果，不能当成已保存或已生效") from exc


def _drop(payload: dict[str, Any], names: tuple[str, ...]) -> dict[str, Any]:
    """Remove echoed credentials at the top level. Nested masks belong to the export."""
    cleaned = dict(payload)
    for name in names:
        cleaned.pop(name, None)
    return cleaned
