"""Toolset list and next-session enablement.

Reads and writes the same catalog and platform list the core checklist uses.
Does not refresh a running session, does not install providers, and does not
pick a Hermes profile. ``cli`` from the configuration-platform helper is a
tool-platform key, not a profile name.
"""

from __future__ import annotations

from typing import Any

from clawd_server.admin_common import ConfigError, fail, panel_home, redact_text, run, write_lock

__all__ = ["list_toolsets", "set_toolset_enabled"]


def _saved(note: str, **extra: Any) -> dict[str, Any]:
    return {
        "ok": True,
        "saved": True,
        "applied": False,
        "processEnvChanged": False,
        "hotLoaded": False,
        "effectiveNow": False,
        "loadedNow": False,
        "restartRequired": False,
        "note": note,
        **extra,
    }


def _unavailable(message: str) -> None:
    fail(503, "tools_unavailable", message)


def _rows() -> list[tuple[str, str, str]]:
    from hermes_cli.tools_config import _get_effective_configurable_toolsets

    try:
        raw = _get_effective_configurable_toolsets()
    except ConfigError:
        raise
    except SystemExit:
        raise
    except Exception:
        _unavailable("无法读取工具集目录，未返回列表")
    if not isinstance(raw, list) or not raw:
        _unavailable("工具集目录不可用，未返回空列表冒充成功")
    rows: list[tuple[str, str, str]] = []
    for item in raw:
        if not isinstance(item, tuple) or len(item) < 3:
            _unavailable("工具集目录形状不对，未返回列表")
        name = str(item[0] or "")
        if not name or "/" in name or "\\" in name or ".." in name:
            continue
        rows.append((name, str(item[1] or ""), str(item[2] or "")))
    if not rows:
        _unavailable("工具集目录没有可用名称，未返回列表")
    return rows


def _flags(config: dict[str, Any], rows: list[tuple[str, str, str]]) -> dict[str, tuple[bool, str, bool]]:
    from hermes_cli.tools_config import (
        _CONFIG_ONLY_TOOLSETS,
        _get_platform_tools,
        _toolset_configuration_platform,
    )
    from utils import is_truthy_value

    flags: dict[str, tuple[bool, str, bool]] = {}
    cache: dict[str, set[str]] = {}
    for name, _label, _desc in rows:
        platform = _toolset_configuration_platform(name)
        if not isinstance(platform, str) or not platform:
            _unavailable("工具集没有配置平台，未返回列表")
        config_only = name in _CONFIG_ONLY_TOOLSETS
        if config_only:
            section = config.get(name)
            section = section if isinstance(section, dict) else {}
            enabled = is_truthy_value(section.get("enabled", True), default=True)
        else:
            if platform not in cache:
                try:
                    resolved = _get_platform_tools(config, platform, include_default_mcp_servers=False)
                except ConfigError:
                    raise
                except SystemExit:
                    raise
                except Exception:
                    _unavailable("无法解析平台工具集，未返回列表")
                cache[platform] = {str(item) for item in resolved}
            enabled = name in cache[platform]
        flags[name] = (bool(enabled), platform, config_only)
    return flags


def _view(mod: Any, rows: list[tuple[str, str, str]], flags: dict[str, tuple[bool, str, bool]]) -> list[dict[str, Any]]:
    data = []
    for name, label, desc in rows:
        enabled, platform, config_only = flags[name]
        text = desc or label or name
        data.append({
            "name": name,
            "enabled": enabled,
            "description": redact_text(mod, text, 300) or "",
            "platform": platform,
            "configOnly": config_only,
        })
    return data


def list_toolsets(mod: Any) -> dict[str, Any]:
    def _read() -> dict[str, Any]:
        panel_home(mod)
        from hermes_cli.config import load_config

        rows = _rows()
        try:
            config = load_config()
        except ConfigError:
            raise
        except SystemExit:
            raise
        except Exception:
            _unavailable("无法读取当前进程 profile 的配置，未返回列表")
        if not isinstance(config, dict):
            _unavailable("配置不是对象，未返回列表")
        return {
            "data": _view(mod, rows, _flags(config, rows)),
            "applied": False,
            "effectiveNow": False,
            "note": "名单来自当前进程所属 profile。enabled 是已保存配置，不是本会话已经加载的工具集。",
        }

    return run(mod, _read)


def _reject_managed() -> None:
    from hermes_cli.config import is_managed

    try:
        managed = is_managed()
    except ConfigError:
        raise
    except SystemExit:
        raise
    except Exception:
        fail(503, "tools_unavailable", "无法确认配置是否被托管，未写入")
    if managed:
        fail(403, "config_managed", "当前安装由包管理器托管，面板不改工具集")


def _map_save(fn) -> None:
    from hermes_cli.config_backend import ConfigLockedError, ConfigWriteError

    try:
        fn()
    except ConfigError:
        raise
    except SystemExit:
        raise
    except ConfigLockedError:
        fail(403, "config_locked", "配置被锁定，未写入")
    except ConfigWriteError:
        fail(500, "toolset_toggle_unverified", "配置写入被拒绝，未确认保存")
    except Exception:
        fail(500, "toolset_toggle_unverified", "写入后无法确认结果，未当成已生效")


def set_toolset_enabled(mod: Any, name: Any, enabled: Any) -> dict[str, Any]:
    def _write() -> dict[str, Any]:
        if not isinstance(name, str) or not name or "/" in name or "\\" in name or ".." in name:
            fail(400, "invalid_body", "需要工具集名称与布尔 enabled")
        if not isinstance(enabled, bool):
            fail(400, "invalid_body", "需要工具集名称与布尔 enabled")
        panel_home(mod)
        _reject_managed()
        from hermes_cli.config import load_config, save_config
        from hermes_cli.tools_config import _get_platform_tools, _save_platform_tools

        with write_lock(mod):
            rows = _rows()
            known = {item[0] for item in rows}
            if name not in known:
                fail(404, "toolset_not_found", "工具集不存在")
            try:
                config = load_config()
            except ConfigError:
                raise
            except SystemExit:
                raise
            except Exception:
                _unavailable("无法读取当前进程 profile 的配置，未写入")
            if not isinstance(config, dict):
                _unavailable("配置不是对象，未写入")
            before = _flags(config, rows)
            _enabled, platform, config_only = before[name]

            def _persist() -> None:
                if config_only:
                    section = config.get(name)
                    if not isinstance(section, dict):
                        section = {}
                        config[name] = section
                    section["enabled"] = enabled
                    save_config(config)
                    return
                current = set(_get_platform_tools(config, platform, include_default_mcp_servers=False))
                if enabled:
                    current.add(name)
                else:
                    current.discard(name)
                # Core save. It may also drop this name from agent.disabled_toolsets
                # so the next read is not forced off. It does not touch a live session.
                _save_platform_tools(config, platform, current)

            _map_save(_persist)
            try:
                after_config = load_config()
            except ConfigError:
                raise
            except SystemExit:
                raise
            except Exception:
                fail(500, "toolset_toggle_unverified", "已提交但无法回读，未当成已生效")
            if not isinstance(after_config, dict):
                fail(500, "toolset_toggle_unverified", "回读配置不是对象，未当成已生效")
            after = _flags(after_config, rows)
        if after.get(name, (None,))[0] is not enabled:
            fail(500, "toolset_toggle_unverified", "已提交但回读结果不一致，请刷新确认")
        note = (
            "已保存到当前进程所属 profile。没有刷新正在运行的会话，也没有安装依赖。"
            "网关会话的工具集在启动策略里冻结；这次写入不调用回合间刷新。下一会话才会读取新名单。"
        )
        if not config_only:
            note += "启用时核心写入可能从 agent.disabled_toolsets 去掉这个名字，否则下一次回读仍会显示关闭。"
        else:
            note += "这项只写自己的 enabled，不进入 platform_toolsets。"
        return _saved(note, name=name, enabled=enabled, platform=platform, configOnly=config_only)

    return run(mod, _write)
