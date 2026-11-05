"""Platform enablement and masked credential status.

Writes platforms.<id>.enabled for the process profile. Credential material is reduced to valueSet. No link flow, no token write, and no gateway restart.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from clawd_server.admin_common import ConfigError, fail, panel_home, redact_text, run, write_lock

__all__ = ["list_platforms", "set_platform_enabled"]

_FORBIDDEN = {"value", "token", "secret", "password", "api_key", "apiKey", "redacted_value", "redactedValue"}


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
    fail(503, "connections_unavailable", message)


def _scrub(node: Any) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key in _FORBIDDEN and isinstance(value, str) and value:
                fail(503, "redact_unavailable", "响应里出现了令牌内容，已拒绝返回")
            _scrub(value)
    elif isinstance(node, list):
        for item in node:
            _scrub(item)


def _catalog() -> tuple[dict[str, Any], ...]:
    from hermes_cli.web_server_messaging import _messaging_platform_catalog

    try:
        raw = _messaging_platform_catalog()
    except ConfigError:
        raise
    except SystemExit:
        raise
    except Exception:
        _unavailable("无法读取连接平台目录，未返回列表")
    if not isinstance(raw, tuple) or not raw:
        _unavailable("连接平台目录不可用，未返回空列表冒充成功")
    return raw


def _config() -> dict[str, Any]:
    from hermes_cli.config import load_config

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
    return config


def _env() -> dict[str, str]:
    from hermes_cli.config import load_env

    try:
        env = load_env()
    except ConfigError:
        raise
    except SystemExit:
        raise
    except Exception:
        _unavailable("无法读取 profile 环境文件，未把令牌猜成未设置")
    if not isinstance(env, dict):
        _unavailable("环境文件不是对象，未返回令牌状态")
    return env


def _present(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _session_present(home: Path) -> bool:
    from hermes_constants import get_hermes_dir

    session = Path(get_hermes_dir("platforms/whatsapp/session", "whatsapp/session"))
    try:
        session.resolve().relative_to(home.resolve())
    except ValueError:
        fail(409, "profile_scope_unsafe", "会话路径不在当前进程 profile 内，未返回状态")
    target = session / "creds.json"
    return target.is_file() and not target.is_symlink()


def _tokens(entry: dict[str, Any], block: dict[str, Any], env: dict[str, str], home: Path) -> list[dict[str, Any]]:
    required = {str(item) for item in entry.get("required_env") or ()}
    tokens = []
    for key in entry.get("env_vars") or ():
        name = str(key)
        tokens.append({
            "key": name,
            "required": name in required,
            "valueSet": _present(env.get(name)),
        })
    platform_id = str(entry.get("id") or "")
    for attr in ("token", "api_key"):
        if attr in block or _present(block.get(attr)):
            tokens.append({
                "key": f"platforms.{platform_id}.{attr}",
                "required": False,
                "valueSet": _present(block.get(attr)),
            })
    if platform_id == "whatsapp":
        tokens.append({"key": "whatsapp.session", "required": False, "valueSet": _session_present(home)})
    return tokens


def _enabled(raw: Any, tokens: list[dict[str, Any]]) -> tuple[bool, bool | None]:
    explicit = raw if isinstance(raw, bool) else None
    if explicit is False:
        return False, explicit
    if explicit is True:
        return True, explicit
    return any(item["valueSet"] for item in tokens), explicit


def list_platforms(mod: Any) -> dict[str, Any]:
    def _read() -> dict[str, Any]:
        home = panel_home(mod)
        catalog = _catalog()
        config = _config()
        env = _env()
        platforms = config.get("platforms")
        platforms = platforms if isinstance(platforms, dict) else {}
        data = []
        for entry in catalog:
            platform_id = str(entry.get("id") or "")
            if not platform_id or "/" in platform_id or "\\" in platform_id:
                continue
            block = platforms.get(platform_id)
            block = block if isinstance(block, dict) else {}
            tokens = _tokens(entry, block, env, home)
            enabled, explicit = _enabled(block.get("enabled"), tokens)
            data.append({
                "id": platform_id,
                "name": redact_text(mod, str(entry.get("name") or platform_id), 80) or platform_id,
                "description": redact_text(mod, str(entry.get("description") or ""), 300) or "",
                "enabled": enabled,
                "enabledExplicit": explicit,
                "tokens": tokens,
            })
        if not data:
            _unavailable("连接平台目录没有可用项，未返回列表")
        payload = {
            "data": data,
            "applied": False,
            "effectiveNow": False,
            "note": "enabled 按显式开关或凭据是否存在计算。令牌只报 valueSet。这不是本进程已经连上。",
        }
        _scrub(payload)
        return payload

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
        _unavailable("无法确认配置是否被托管，未写入")
    if managed:
        fail(403, "config_managed", "当前安装由包管理器托管，面板不改连接开关")


def _conflict(platform_id: str) -> str | None:
    try:
        from hermes_cli.web_routers.messaging import _multiplex_port_binding_conflict

        reason = _multiplex_port_binding_conflict(platform_id, None)
    except ConfigError:
        raise
    except SystemExit:
        raise
    except Exception:
        _unavailable("无法核对共享监听冲突，未启用")
    if reason:
        fail(409, "profile_scope_unsafe", "当前进程所属 profile 不能启用这个共享监听平台，未写入")
    return None


def _persisted(platform_id: str) -> bool | None:
    from hermes_cli.config import read_raw_config_readonly

    raw = read_raw_config_readonly()
    platforms = raw.get("platforms") if isinstance(raw, dict) else None
    block = platforms.get(platform_id) if isinstance(platforms, dict) else None
    if isinstance(block, dict) and isinstance(block.get("enabled"), bool):
        return block["enabled"]
    return None


def set_platform_enabled(mod: Any, platform_id: Any, enabled: Any) -> dict[str, Any]:
    def _write() -> dict[str, Any]:
        if not isinstance(platform_id, str) or not platform_id or "/" in platform_id or "\\" in platform_id:
            fail(400, "invalid_body", "需要平台 id 与布尔 enabled")
        if not isinstance(enabled, bool):
            fail(400, "invalid_body", "需要平台 id 与布尔 enabled")
        panel_home(mod)
        _reject_managed()
        known = {str(entry.get("id") or "") for entry in _catalog()}
        if platform_id not in known:
            fail(404, "platform_not_found", "平台不存在")
        if enabled:
            _conflict(platform_id)
        from hermes_cli.config import write_platform_config_field
        from hermes_cli.config_backend import ConfigLockedError, ConfigWriteError

        with write_lock(mod):
            try:
                write_platform_config_field(platform_id, "enabled", enabled)
            except ConfigError:
                raise
            except SystemExit:
                raise
            except ConfigLockedError:
                fail(403, "config_locked", "配置被锁定，未写入")
            except ConfigWriteError:
                fail(500, "platform_toggle_unverified", "配置写入被拒绝，未确认保存")
            except Exception:
                fail(500, "platform_toggle_unverified", "写入后无法确认结果，未当成已生效")
            try:
                seen = _persisted(platform_id)
            except ConfigError:
                raise
            except SystemExit:
                raise
            except Exception:
                fail(500, "platform_toggle_unverified", "已提交但无法回读，未当成已生效")
        if seen is not enabled:
            fail(500, "platform_toggle_unverified", "已提交但回读结果不一致，请刷新确认")
        payload = _saved(
            "已把 platforms 启用位写入当前进程所属 profile。没有改令牌，没有配对，也没有启动或停止网关适配器。运行中的网关要等下次启动才读这个开关。",
            id=platform_id,
            enabled=enabled,
        )
        _scrub(payload)
        return payload

    return run(mod, _write)
