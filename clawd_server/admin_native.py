"""Bind panel admin routes to in-process core functions.

Parent installs this from clawd_server.native.install after the stdout config_set
wrapper. This module replaces that wrapper and attaches delete preview/confirm
onto Handler._route. It does not install browser or runtime hooks and does not
edit the sidecar file.
"""

from __future__ import annotations

from typing import Any

from clawd_server import admin_config, admin_mcp, admin_models, admin_plugins, admin_profiles, admin_settings, admin_webhooks

# Old sidecar function -> in-process replacement. Parent wires install() only.
REPLACED = (
    "config_get",
    "config_get_json",
    "config_set",
    "get_models",
    "set_default_model",
    "set_provider_models",
    "set_provider_discover",
    "set_excluded_providers",
    "get_cron_model",
    "set_cron_model",
    "list_mcp",
    "set_mcp_enabled",
    "remove_mcp",
    "test_mcp",
    "list_plugins",
    "set_plugin_enabled",
    "list_profiles",
    "show_profile",
    "create_profile",
    "use_profile",
    "delete_profile",
    "list_webhooks",
    "recent_deliveries",
    "create_webhook",
    "delete_webhook",
    "set_webhook_enabled",
    "test_webhook",
    "get_settings",
    "set_setting",
    "reset_setting",
)

# Kept refused, or not safe to call from a multi-threaded panel process.
BLOCKED = {
    "restart_service": "宿主重启保持禁用",
    "delete_profile": "现有请求只带名称，不能当确认；确认接口另挂，不调用会改写进程环境的核心删除",
    "recent_deliveries": "投递日志刮取没有进程内读接口",
    "use_profile_process_switch": "只写 sticky 文件，不修改 os.environ",
    "plugin_activate_now": "不在面板进程加载插件，不改 platform_toolsets",
    "journal": "日志 journal 不在本模块，仍是侧车宿主边界",
}


def _refuse_cli(mod: Any, *args: str, **kwargs: Any) -> Any:
    del kwargs
    head = " ".join(str(part) for part in args[:2])
    raise mod.ApiError(501, "cli_retired", f"不再执行子进程：{head or 'hermes'}")


def _keep_host_disabled(mod: Any) -> None:
    def restart_service(name: str, force: bool = False) -> Any:
        del name, force
        raise mod.ApiError(403, "restart_disabled", "clawd 不重启系统服务；生命周期由 clawd panel 统一管理")

    mod.restart_service = restart_service


def _bind_skill_export(mod: Any) -> None:
    """Bind the same export ``POST /skills/enabled`` calls. A missing export refuses instead of keeping an older writer."""
    from clawd_server.admin_routes import load_export

    try:
        fn = load_export("admin_skills", "set_skill_enabled")
    except Exception:
        fn = None
    if not callable(fn):
        def set_skill_enabled(name: Any, enabled: Any) -> Any:
            del name, enabled
            raise mod.ApiError(503, "admin_route_unbound", "技能开关还没有进程内导出，已拒绝，没有改用命令行或旧写入")

        mod.set_skill_enabled = set_skill_enabled
        return
    mod.set_skill_enabled = lambda name, enabled: fn(mod, name, enabled)


def _mark_journal_unavailable(mod: Any) -> None:
    """Existing logs page disables sources whose available flag is false. Do not read journal."""
    original = getattr(mod, "list_log_sources", None)
    if not callable(original):
        return

    def list_log_sources() -> dict[str, Any]:
        payload = original()
        if not isinstance(payload, dict):
            return payload
        data = payload.get("data")
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and item.get("kind") == "journal":
                    item["available"] = False
        return payload

    mod.list_log_sources = list_log_sources


def install(mod: Any) -> None:
    """Replace sidecar admin callables. Does not spawn clawd or hermes."""
    mod.config_get = lambda key: admin_config.config_get(mod, key)
    mod.config_get_json = lambda key: admin_config.config_get_json(mod, key)
    mod.config_set = lambda key, value: admin_config.config_set(mod, key, value)
    mod._clawd_config_set_owner = "admin"
    mod.get_models = lambda: admin_models.get_models(mod)
    mod.set_default_model = lambda model, provider: admin_models.set_default_model(mod, model, provider)
    mod.set_provider_models = lambda provider, models: admin_models.set_provider_models(mod, provider, models)
    mod.set_provider_discover = lambda provider, discover: admin_models.set_provider_discover(mod, provider, discover)
    mod.set_excluded_providers = lambda items: admin_models.set_excluded_providers(mod, items)
    mod.get_cron_model = lambda: admin_models.get_cron_model(mod)
    mod.set_cron_model = lambda model, provider: admin_models.set_cron_model(mod, model, provider)
    mod.list_mcp = lambda: admin_mcp.list_mcp(mod)
    mod.set_mcp_enabled = lambda name, enabled: admin_mcp.set_mcp_enabled(mod, name, enabled)
    mod.remove_mcp = lambda name: admin_mcp.remove_mcp(mod, name)
    mod.test_mcp = lambda name: admin_mcp.test_mcp(mod, name)
    mod.list_plugins = lambda force=False: admin_plugins.list_plugins(mod, force)
    mod.set_plugin_enabled = lambda name, enabled: admin_plugins.set_plugin_enabled(mod, name, enabled)
    mod.list_profiles = lambda: admin_profiles.list_profiles(mod)
    mod.show_profile = lambda name: admin_profiles.show_profile(mod, name)
    mod.create_profile = lambda name, clone_from, description: admin_profiles.create_profile(mod, name, clone_from, description)
    mod.use_profile = lambda name: admin_profiles.use_profile(mod, name)
    mod.delete_profile = lambda name: admin_profiles.delete_profile(mod, name)
    mod.preview_delete_profile = lambda name: admin_profiles.preview_delete_profile(mod, name)
    mod.confirm_delete_profile = lambda name, confirm, paths, token=None, fingerprint=None: admin_profiles.confirm_delete_profile(mod, name, confirm, paths, token, fingerprint)
    mod.list_webhooks = lambda: admin_webhooks.list_webhooks(mod)
    mod.recent_deliveries = lambda route, limit: admin_webhooks.recent_deliveries(mod, route, limit)
    mod.create_webhook = lambda body: admin_webhooks.create_webhook(mod, body)
    mod.delete_webhook = lambda name: admin_webhooks.delete_webhook(mod, name)
    mod.set_webhook_enabled = lambda name, enabled: admin_webhooks.set_webhook_enabled(mod, name, enabled)
    mod.test_webhook = lambda name: admin_webhooks.test_webhook(mod, name)
    mod.get_settings = lambda: admin_settings.get_settings(mod)
    mod.set_setting = lambda key, value: admin_settings.set_setting(mod, key, value)
    mod.reset_setting = lambda key: admin_settings.reset_setting(mod, key)
    _bind_skill_export(mod)
    _mark_journal_unavailable(mod)
    mod._hermes = lambda *args, **kwargs: _refuse_cli(mod, *args, **kwargs)
    mod._hermes_set = lambda key, value: _refuse_cli(mod, "config", "set")
    mod._hermes_unset = lambda key: _refuse_cli(mod, "config", "unset")
    _keep_host_disabled(mod)
    # Updates are clawd-native now: check our own GitHub releases and pull
    # them with a detached runner. The host hermes update stays disabled.
    from clawd_server import update_native

    update_native.install_update(mod)
    from clawd_server import kanban_native

    kanban_native.install_kanban(mod)
    from clawd_server.admin_routes import install as install_routes

    install_routes(mod)
    mod._clawd_admin_native = REPLACED
    mod._clawd_admin_blocked = tuple(BLOCKED)
