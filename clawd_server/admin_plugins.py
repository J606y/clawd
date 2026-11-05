"""Plugin list, enable/disable, and non-secret settings.

Enable/disable goes through admission, not the plugins CLI. Settings go through
``hermes_cli.plugins_settings.save_plugin_settings``, the same writer as
``PluginContext.set_config``. This module does not grant capabilities, does not
call ``activate_plugin_now``, does not broadcast settings events, does not
restart MCP servers, and does not install or hot-load plugins.
``platform_toolsets`` is left unchanged.
"""

from __future__ import annotations

import json
import re
from typing import Any, Mapping

from clawd_server.admin_common import fail, redact_text, run, secret_key, secret_name, write_lock

# Canonical registry keys are ``fal`` or ``image_gen/fal``. No traversal.
_PLUGIN_ID_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}(?:/[A-Za-z0-9][A-Za-z0-9_.-]{0,79}){0,3}$"
)
_ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_MAX_FIELDS = 64
_MAX_BODY = 32768
_MAX_STRING = 8000
_MISSING = object()
_WITHHELD = object()


def _plugin_id(name: str) -> bool:
    return bool(_PLUGIN_ID_RE.fullmatch(name)) and ".." not in name and "\\" not in name


def _rows() -> list[dict[str, Any]]:
    from hermes_cli.plugins_cmd import _discover_all_plugins, _get_disabled_set, _get_enabled_set, _plugin_status
    from hermes_cli.plugins_cmd_catalog import installed_plugin_removal

    enabled = _get_enabled_set()
    disabled = _get_disabled_set()
    active = _category_active()
    rows = []
    for name, version, description, source, directory, key in _discover_all_plugins():
        try:
            removed = installed_plugin_removal(name, directory)
        except Exception as exc:
            fail(503, "plugin_removal_check_failed", "无法核对插件下架名单，未返回列表")
            raise AssertionError(exc)
        if removed is not None:
            continue
        status = _plugin_status(name, enabled, disabled, key, source=source, dir_path=directory, active=active)
        rows.append({
            "name": str(name),
            "key": str(key or name),
            "enabled": status == "enabled",
            "status": str(status),
            "version": str(version or ""),
            "description": str(description or ""),
            "source": str(source or ""),
            "dir": directory,
        })
    return rows


def _category_active() -> set[str]:
    from hermes_cli.plugins_cmd import _category_active_names

    return set(_category_active_names())


def list_plugins(mod: Any, force: bool = False) -> dict[str, Any]:
    del force  # no CLI cache; discovery reads the current config

    def _read() -> dict[str, Any]:
        from clawd_server.admin_common import panel_home

        panel_home(mod)
        data = []
        for row in _rows():
            data.append({
                "name": row["name"],
                "key": row["key"],
                "enabled": row["enabled"],
                "status": row["status"],
                "version": row["version"],
                "description": redact_text(mod, row["description"], 300) or "",
                "source": row["source"],
            })
        return {"data": data, "loadedNow": False, "restartRequired": True, "note": "enabled 只表示准入名单，不表示本进程已加载插件。"}

    return run(mod, _read)


def _refuse_legacy(name: str) -> None:
    try:
        from hermes_cli.relay_plugin_cutover import LEGACY_RELAY_PLUGIN_KEYS
    except Exception as exc:
        fail(503, "plugin_policy_unavailable", "无法核对已下线插件名单，未切换")
        raise AssertionError(exc)
    if name in LEGACY_RELAY_PLUGIN_KEYS:
        fail(400, "plugin_removed", f"插件 {name} 已由核心接管，面板不启用旧插件")


def _discover() -> list[tuple]:
    from hermes_cli.plugins_cmd import _discover_all_plugins

    try:
        found = _discover_all_plugins()
    except Exception as exc:
        fail(503, "plugin_config_unavailable", "无法发现已安装插件，未读写配置")
        raise AssertionError(exc)
    return list(found)


def _resolve_installed(name: str) -> tuple:
    """One discovered plugin. Never pick the first of several same display names."""
    if not isinstance(name, str) or not _plugin_id(name):
        fail(400, "invalid_body", "需要已安装插件的名称或完整键")
    matches = [item for item in _discover() if name in (str(item[0]), str(item[5] or ""))]
    if not matches:
        fail(404, "plugin_not_found", "插件不存在")
    keys = [str(item[5] or item[0]) for item in matches]
    if len(matches) > 1:
        chosen = [item for item in matches if str(item[5] or "") == name]
        if len(chosen) != 1:
            shown = "、".join(keys)
            fail(409, "plugin_name_ambiguous", f"有多个同名插件：{shown}。请用完整键，未继续")
        return chosen[0]
    return matches[0]


def _removed(name: str, directory: Any) -> bool:
    from hermes_cli.plugins_cmd_catalog import installed_plugin_removal

    try:
        return installed_plugin_removal(name, directory) is not None
    except Exception as exc:
        fail(503, "plugin_removal_check_failed", "无法核对插件下架名单，未读写配置")
        raise AssertionError(exc)


def _not_loaded() -> dict[str, bool]:
    return {
        "applied": False,
        "processEnvChanged": False,
        "hotLoaded": False,
        "effectiveNow": False,
        "loadedNow": False,
        "restartRequired": False,
    }


def _plugin_dir(directory: Any) -> Any:
    from pathlib import Path

    if not directory:
        return None
    return Path(str(directory))


def _consumed(plugin_id: str, key: str) -> tuple[bool, Any]:
    """Value ``ctx.get_config`` would read. ``stored`` is false when both subtrees miss."""
    from hermes_cli.config import load_config_readonly
    from hermes_cli.plugins_state import _nested_plugin_value, _plugin_relative_segments, _plugin_settings_entry

    try:
        segments = _plugin_relative_segments(key)
    except ValueError:
        fail(400, "unknown_key", "配置键不是插件相对路径，未继续")
    try:
        config = load_config_readonly()
    except Exception as exc:
        fail(503, "plugin_config_unavailable", "无法读取当前配置档，未返回字段")
        raise AssertionError(exc)
    entry = _plugin_settings_entry(config or {}, plugin_id)
    if not isinstance(entry, Mapping):
        return False, _MISSING
    value = _nested_plugin_value(entry.get("settings"), segments, _MISSING)
    if value is not _MISSING:
        return True, value
    legacy = _nested_plugin_value(entry.get("config"), segments, _MISSING)
    if legacy is not _MISSING:
        return True, legacy
    return False, _MISSING


def _persisted(plugin_id: str, key: str) -> tuple[bool, Any]:
    """Raw ``config.yaml`` value under settings, for save readback. Not the expanded view."""
    from hermes_cli.config import read_user_config_raw
    from hermes_cli.plugins_state import _nested_plugin_value, _plugin_relative_segments, _plugin_settings_entry

    try:
        segments = _plugin_relative_segments(key)
    except ValueError:
        fail(400, "unknown_key", "配置键不是插件相对路径，未继续")
    try:
        raw = read_user_config_raw()
    except Exception as exc:
        fail(500, "plugin_config_unverified", "已提交但无法回读原始配置，请刷新确认")
        raise AssertionError(exc)
    entry = _plugin_settings_entry(raw or {}, plugin_id)
    if not isinstance(entry, Mapping):
        return False, _MISSING
    value = _nested_plugin_value(entry.get("settings"), segments, _MISSING)
    if value is _MISSING:
        return False, _MISSING
    return True, value


def _same(expected: Any, actual: Any) -> bool:
    if isinstance(expected, bool) or isinstance(actual, bool):
        return type(expected) is bool and type(actual) is bool and expected == actual
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return expected == actual
    return expected == actual


def _walk_limit(value: Any) -> None:
    if isinstance(value, str):
        if len(value) > _MAX_STRING:
            fail(400, "invalid_body", "配置文本过长，未写入")
        return
    if isinstance(value, bool) or isinstance(value, (int, float)) or value is None:
        return
    if isinstance(value, list):
        for item in value:
            _walk_limit(item)
        return
    if isinstance(value, dict):
        for item in value.values():
            _walk_limit(item)
        return
    fail(400, "invalid_body", "配置值含不支持的类型，未写入")


def _public_value(mod: Any, value: Any) -> Any:
    if value is None or isinstance(value, bool) or isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        rendered = redact_text(mod, value)
        if rendered != value:
            return _WITHHELD
        return rendered
    if isinstance(value, list):
        items = [_public_value(mod, item) for item in value]
        if any(item is _WITHHELD for item in items):
            return _WITHHELD
        return items
    if isinstance(value, dict):
        copied = {str(key): _public_value(mod, item) for key, item in value.items()}
        if any(item is _WITHHELD for item in copied.values()):
            return _WITHHELD
        return copied
    return _WITHHELD


def _schema(plugin_dir: Any) -> Mapping[str, Mapping[str, Any]]:
    from hermes_cli.plugins_settings import _manifest_config_schema

    try:
        raw = _manifest_config_schema(plugin_dir)
    except Exception as exc:
        fail(503, "plugin_config_unavailable", "无法读取插件清单，未返回配置")
        raise AssertionError(exc)
    if not isinstance(raw, Mapping):
        return {}
    return raw


def _secret_field(mod: Any, key: str, spec: Mapping[str, Any]) -> bool:
    from hermes_cli.plugins_settings import _field_type

    if _field_type(spec) == "secret" or spec.get("secret") is True:
        return True
    return secret_key(mod, key) or secret_name(mod, key)


def _public_fields(mod: Any, plugin_id: str, plugin_dir: Any, schema: Mapping[str, Any]) -> list[dict[str, Any]]:
    from hermes_cli.plugins_settings import _field_type, plugin_settings_fields
    from hermes_cli.plugins_state import _plugin_relative_segments

    if len(schema) > _MAX_FIELDS:
        fail(400, "invalid_body", "插件配置项过多，面板不返回不完整清单")
    try:
        rendered = plugin_settings_fields(plugin_id, plugin_dir)
    except Exception as exc:
        fail(503, "plugin_config_unavailable", "无法读取插件配置清单，未返回字段")
        raise AssertionError(exc)
    by_key = {str(item.get("key")): item for item in rendered if isinstance(item, dict)}
    fields: list[dict[str, Any]] = []
    for key, spec in schema.items():
        if not isinstance(spec, Mapping):
            continue
        text = str(key)
        try:
            _plugin_relative_segments(text)
        except ValueError:
            continue
        kind = _field_type(spec)
        secret = _secret_field(mod, text, spec)
        shown = by_key.get(text, {})
        label = redact_text(mod, str(spec.get("label") or spec.get("title") or text)) or text
        description = redact_text(mod, str(spec.get("description") or "")) or ""
        field: dict[str, Any] = {
            "key": text,
            "type": "secret" if secret else kind,
            "label": label,
            "description": description,
            "required": bool(spec.get("required")),
            "secret": secret,
        }
        if secret:
            field["valueSet"] = bool(shown.get("has_value"))
            env = shown.get("env")
            if isinstance(env, str) and _ENV_NAME_RE.fullmatch(env):
                field["env"] = env
        else:
            stored, value = _consumed(plugin_id, text)
            field["stored"] = stored
            if not stored and "default" in spec:
                value = spec.get("default")
            if kind == "enum":
                choices = spec.get("choices", spec.get("enum"))
                field["choices"] = [str(item) for item in choices] if isinstance(choices, list) else []
            if "default" in spec:
                default = _public_value(mod, spec.get("default"))
                if default is not _WITHHELD:
                    field["default"] = default
            if value is not _MISSING:
                safe = _public_value(mod, value)
                if safe is _WITHHELD:
                    field["valueWithheld"] = True
                else:
                    field["value"] = safe
        fields.append(field)
    return fields


def _managed() -> bool:
    try:
        from hermes_cli.config import is_managed
    except Exception as exc:
        fail(503, "plugin_policy_unavailable", "无法核对安装是否托管，未读写配置")
        raise AssertionError(exc)
    return bool(is_managed())


def get_plugin_config(mod: Any, name: Any) -> dict[str, Any]:
    """Non-secret schema fields for one installed plugin. Does not load the plugin."""

    def _read() -> dict[str, Any]:
        from clawd_server.admin_common import panel_home

        panel_home(mod)
        manifest_name, _version, _description, _source, directory, key = _resolve_installed(name)
        if not isinstance(key, str) or not key:
            fail(404, "plugin_not_found", "插件不存在")
        _refuse_legacy(str(name))
        _refuse_legacy(key)
        if _removed(manifest_name, directory):
            fail(400, "plugin_removed", "该插件已从目录下架，不返回配置")
        plugin_dir = _plugin_dir(directory)
        schema = _schema(plugin_dir)
        note = "字段来自插件清单 config_schema，值按 ctx.get_config 读取 plugins.entries 的 settings，缺失时才看旧 config。没有加载插件。秘密字段只有是否已设置，没有明文。"
        if not schema:
            note = "该插件清单没有可读的 config_schema，或清单无法读取。没有改配置，也没有加载插件。环境变量不在这里返回。"
        return {
            "name": manifest_name,
            "key": key,
            "fields": _public_fields(mod, key, plugin_dir, schema),
            "managed": _managed(),
            "saved": False,
            **_not_loaded(),
            "note": note,
        }

    return run(mod, _read)


def _validate_fields(mod: Any, plugin_id: str, schema: Mapping[str, Any], fields: Any) -> dict[str, Any]:
    from hermes_cli.plugins_settings import _VALUE_TYPES, _field_type
    from hermes_cli.plugins_state import _plugin_relative_segments

    if not isinstance(fields, dict) or not fields:
        fail(400, "invalid_body", "需要非空 fields 对象")
    if len(fields) > _MAX_FIELDS:
        fail(400, "invalid_body", "配置项过多，未写入")
    try:
        encoded = json.dumps(fields)
    except (TypeError, ValueError):
        fail(400, "invalid_body", "配置值无法编码，未写入")
    if len(encoded) > _MAX_BODY:
        fail(400, "invalid_body", "配置过大，未写入")
    clean: dict[str, Any] = {}
    for raw_key, value in fields.items():
        if not isinstance(raw_key, str) or not raw_key:
            fail(400, "unknown_key", "配置键无效，未写入")
        try:
            _plugin_relative_segments(raw_key)
        except ValueError:
            fail(400, "unknown_key", "配置键不是插件相对路径，未写入")
        spec = schema.get(raw_key)
        if not isinstance(spec, Mapping):
            fail(400, "unknown_key", f"{raw_key} 不在插件清单中，未写入")
        if _secret_field(mod, raw_key, spec) or secret_key(mod, f"plugins.entries.{plugin_id}.settings.{raw_key}"):
            fail(403, "secret_key", f"{raw_key} 是秘密字段，已全部拒绝，没有写入")
        kind = _field_type(spec)
        expected = _VALUE_TYPES.get(kind)
        if expected is None or not isinstance(value, expected) or (isinstance(value, bool) and bool not in expected):
            fail(400, "invalid_body", f"{raw_key} 的类型应为 {kind}，未写入")
        if kind == "enum" and value not in [str(item) for item in spec.get("choices", spec.get("enum"))]:
            fail(400, "invalid_body", f"{raw_key} 不在允许的选项中，未写入")
        _walk_limit(value)
        clean[raw_key] = value
    return clean


def _locked(plugin_id: str, keys: Mapping[str, Any]) -> None:
    try:
        from hermes_cli import managed_scope
    except Exception as exc:
        fail(503, "plugin_policy_unavailable", "无法核对锁定配置，未写入")
        raise AssertionError(exc)
    for key in keys:
        dotted = f"plugins.entries.{plugin_id}.settings.{key}"
        try:
            locked = managed_scope.is_key_managed(dotted)
        except Exception as exc:
            fail(503, "plugin_policy_unavailable", "无法核对锁定配置，未写入")
            raise AssertionError(exc)
        if locked:
            fail(403, "config_locked", "该配置由管理员锁定，未写入")


def set_plugin_config(mod: Any, name: Any, fields: Any) -> dict[str, Any]:
    """Persist non-secret settings. ``loadedNow`` stays false; the plugin is not loaded."""

    def _write() -> dict[str, Any]:
        from clawd_server.admin_common import panel_home
        from hermes_cli.plugins_settings import save_plugin_settings

        panel_home(mod)
        manifest_name, _version, _description, _source, directory, key = _resolve_installed(name)
        if not isinstance(key, str) or not key:
            fail(404, "plugin_not_found", "插件不存在")
        _refuse_legacy(str(name))
        _refuse_legacy(key)
        if _removed(manifest_name, directory):
            fail(400, "plugin_removed", "该插件已从目录下架，未写入")
        if _managed():
            fail(403, "config_managed", "当前安装由包管理器托管，面板不改插件配置")
        plugin_dir = _plugin_dir(directory)
        schema = _schema(plugin_dir)
        clean = _validate_fields(mod, key, schema, fields)
        with write_lock(mod):
            if _managed():
                fail(403, "config_managed", "当前安装由包管理器托管，面板不改插件配置")
            _locked(key, clean)
            try:
                written = save_plugin_settings(key, plugin_dir, clean)
            except ValueError:
                fail(400, "invalid_body", "配置值与清单类型或选项不符，未确认写入")
            except PermissionError:
                fail(403, "config_locked", "该配置被锁定或安装托管，未确认写入")
            except Exception as exc:
                fail(503, "plugin_config_unavailable", "写入插件配置失败，未确认")
                raise AssertionError(exc)
            if sorted(written) != sorted(clean):
                fail(500, "plugin_config_unverified", "已提交但写入键与请求不一致，请刷新确认")
            for field, expected in clean.items():
                stored, actual = _persisted(key, field)
                if not stored or not _same(expected, actual):
                    fail(500, "plugin_config_unverified", "已提交但回读结果不一致，请刷新确认")
            public = _public_fields(mod, key, plugin_dir, schema)
        note = (
            f"已写入当前进程配置档的 plugins.entries.{key}.settings，并回读了非秘密字段。"
            "没有在本进程加载或重载插件，没有改 .env，没有热加载。"
            "已加载的插件只有在下次调用 ctx.get_config 时才会读到新值；"
            "若它在注册时缓存了配置，这次保存不会更新那份缓存。"
        )
        return {
            "ok": True,
            "saved": True,
            "name": manifest_name,
            "key": key,
            "written": list(written),
            "fields": public,
            **_not_loaded(),
            "note": note,
        }

    return run(mod, _write)


def set_plugin_enabled(mod: Any, name: Any, enabled: Any) -> dict[str, Any]:
    def _write() -> dict[str, Any]:
        if not isinstance(enabled, bool):
            fail(400, "invalid_body", "需要插件名与布尔 enabled")
        from hermes_cli.plugins_admission import AdmissionRefused
        from hermes_cli.plugins_cmd import PluginOperationError, _activate_key
        from hermes_cli.plugins_cmd_catalog import refuse_if_installed_removed

        if _managed():
            fail(403, "config_managed", "当前安装由包管理器托管，面板不改插件启用名单")
        from clawd_server.admin_common import panel_home

        panel_home(mod)
        manifest_name, _version, _description, source, directory, key = _resolve_installed(name)
        if not isinstance(key, str) or not key:
            fail(404, "plugin_not_found", "插件不存在")
        _refuse_legacy(str(name))
        _refuse_legacy(key)
        with write_lock(mod):
            if enabled and source != "bundled":
                try:
                    refuse_if_installed_removed(key, directory)
                except PluginOperationError:
                    fail(400, "plugin_removed", "该插件已从目录下架，未启用")
            try:
                _activate_key(key, enable=enabled, console=None)
            except AdmissionRefused:
                fail(409, "plugin_admission_refused", "插件依赖或准入拒绝了这次切换，配置未改")
            except Exception:
                fail(503, "plugin_toggle_failed", "插件切换失败，未确认写入")
            from hermes_cli.plugins_cmd import _get_disabled_set, _get_enabled_set, _plugin_status

            status = _plugin_status(
                manifest_name, _get_enabled_set(), _get_disabled_set(), key,
                source=source, dir_path=directory, active=_category_active(),
            )
        if (status == "enabled") != enabled:
            fail(500, "plugin_toggle_unverified", "已提交但回读结果不一致，请刷新确认")
        note = "只改了启用名单，没有在本进程加载或卸载插件，运行中的网关也不会热加载。新名单要等网关进程重启后才会生效。平台工具集名单未改。"
        return {
            "ok": True,
            "name": manifest_name,
            "enabled": enabled,
            "loadedNow": False,
            "restartRequired": True,
            "effectiveNow": False,
            "note": note,
        }

    return run(mod, _write)
