"""Memory-provider non-secret config for the process profile.

Uses the declared config_schema store. Does not import a provider runtime to
read class schemas, does not run setup, does not write secrets, and does not
change memory.provider or project memory.
"""

from __future__ import annotations

import re
from typing import Any

from clawd_server.admin_common import ConfigError, fail, panel_home, redact_text, run, secret_name, write_lock

__all__ = ["list_providers", "set_provider_config"]

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_FIELD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")


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
    fail(503, "memory_unavailable", message)


def _active(config: dict[str, Any]) -> str | None:
    memory = config.get("memory")
    if not isinstance(memory, dict) or "provider" not in memory:
        return None
    value = memory.get("provider")
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _names() -> list[str]:
    from plugins.memory import list_memory_provider_names

    try:
        raw = list_memory_provider_names()
    except ConfigError:
        raise
    except SystemExit:
        raise
    except Exception:
        _unavailable("无法读取记忆提供方目录，未返回列表")
    if not isinstance(raw, list):
        _unavailable("记忆提供方目录不是列表，未返回列表")
    names = []
    for item in raw:
        name = str(item or "")
        if _NAME_RE.fullmatch(name):
            names.append(name)
    return sorted(set(names))


def _policy(mod: Any) -> None:
    secret_name(mod, "token")


def _secret_field(mod: Any, key: str, kind_secret: bool) -> bool:
    return kind_secret or secret_name(mod, key)


def _schema_file(name: str):
    from plugins.memory import find_provider_dir
    from plugins.memory.config_schema import get_provider_config_schema

    try:
        directory = find_provider_dir(name)
    except ConfigError:
        raise
    except SystemExit:
        raise
    except Exception:
        return None, None, "无法定位提供方目录，未读取配置"
    path = directory / "config_schema.py" if directory is not None else None
    if path is None or not path.is_file():
        return None, path, "没有 config_schema.py。面板不导入提供方运行时，类上的配置声明不能在这里编辑。"
    try:
        schema = get_provider_config_schema(name)
    except ConfigError:
        raise
    except SystemExit:
        raise
    except Exception:
        schema = None
    if schema is None:
        return None, path, "config_schema.py 无法加载，未返回字段，也未写入。"
    return schema, path, None


def _sources(schema):
    from hermes_cli.web_routers.memory_providers import (
        _honcho_resolvers,
        _read_flat_json,
        _read_json_dict,
    )
    from plugins.memory.config_schema import STORAGE_FLAT_JSON, STORAGE_HONCHO_HOST_BLOCK

    if schema.storage == STORAGE_FLAT_JSON:
        data = _read_flat_json(schema)
        return lambda field: (data,)
    if schema.storage == STORAGE_HONCHO_HOST_BLOCK:
        resolve_active_host, resolve_config_path, host_block_of = _honcho_resolvers(schema.name)
        raw = _read_json_dict(resolve_config_path(), "memory provider config")
        host_block = host_block_of(raw, resolve_active_host())
        return lambda field: (host_block, raw) if field.scope == "host" else (raw,)
    _unavailable("不认识的提供方存储，未返回字段")
    raise AssertionError(schema.storage)


def _stored(field, sources: tuple) -> Any:
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in (field.key, *field.aliases):
            if key in source and source[key] is not None:
                return source[key]
    return None


def _value_set(field, stored: Any, env: dict[str, str]) -> bool:
    if stored not in (None, ""):
        return True
    for env_key in (field.env_key, *getattr(field, "env_fallbacks", ())):
        if env_key and str(env.get(env_key) or "").strip():
            return True
    return False


def _public_fields(mod: Any, schema) -> list[dict[str, Any]]:
    from hermes_cli.config import load_env
    from hermes_cli.web_routers.memory_providers import _serialize_field_value

    try:
        env = load_env()
    except ConfigError:
        raise
    except SystemExit:
        raise
    except Exception:
        _unavailable("无法读取 profile 环境文件的设置状态，未返回字段")
    if not isinstance(env, dict):
        _unavailable("环境文件不是对象，未返回字段")
    try:
        sources_for = _sources(schema)
    except ConfigError:
        raise
    except SystemExit:
        raise
    except Exception:
        _unavailable("无法读取提供方配置存储，未返回字段")
    fields = []
    for field in schema.fields:
        key = str(field.key or "")
        if not _FIELD_RE.fullmatch(key):
            continue
        secret = _secret_field(mod, key, bool(field.is_secret))
        try:
            stored = _stored(field, sources_for(field))
        except ConfigError:
            raise
        except SystemExit:
            raise
        except Exception:
            _unavailable("无法读取提供方字段，未返回字段")
        entry: dict[str, Any] = {
            "key": key,
            "label": redact_text(mod, str(field.label or key), 120) or key,
            "kind": "secret" if secret else str(field.kind or "text"),
            "description": redact_text(mod, str(field.description or ""), 300) or "",
            "secret": secret,
            "valueSet": _value_set(field, stored, env),
        }
        if field.options:
            entry["options"] = [
                {"value": opt.value, "label": redact_text(mod, opt.label, 120) or opt.value}
                for opt in field.options
            ]
        if not secret:
            rendered = "" if stored is None else _serialize_field_value(field, stored)
            safe = redact_text(mod, rendered) or ""
            if stored is not None and safe != rendered:
                entry["secret"] = True
                entry["kind"] = "secret"
                entry["valueSet"] = True
            else:
                entry["value"] = safe
                if stored is None and field.default:
                    entry["placeholder"] = redact_text(mod, str(field.default), 200) or ""
        elif "value" in entry:
            fail(503, "redact_unavailable", "秘密字段准备回传值，已拒绝返回")
        fields.append(entry)
    return fields


def _row(mod: Any, name: str, active: str | None, *, missing: bool = False) -> dict[str, Any]:
    from agent.memory_provider import is_core_memory_provider

    builtin = is_core_memory_provider(name)
    if missing or builtin:
        return {
            "name": name,
            "active": active == name,
            "builtin": builtin,
            "missing": missing,
            "writable": False,
            "gap": "这是内置记忆哨兵或目录里不存在的名字，没有可写的提供方配置。" if builtin or missing else None,
            "fields": [],
        }
    schema, _path, gap = _schema_file(name)
    if schema is None:
        return {
            "name": name,
            "active": active == name,
            "builtin": False,
            "missing": False,
            "writable": False,
            "gap": gap,
            "fields": [],
        }
    try:
        fields = _public_fields(mod, schema)
    except ConfigError as exc:
        if exc.code in {"secret_policy_unavailable", "redact_unavailable", "profile_scope_unsafe", "profile_home_mismatch"}:
            raise
        return {
            "name": name,
            "active": active == name,
            "builtin": False,
            "missing": False,
            "writable": False,
            "gap": "无法读取该提供方的非秘密配置，未返回字段。",
            "fields": [],
        }
    except SystemExit:
        raise
    except Exception:
        return {
            "name": name,
            "active": active == name,
            "builtin": False,
            "missing": False,
            "writable": False,
            "gap": "无法读取非秘密配置，未返回字段。",
            "fields": [],
        }
    return {
        "name": name,
        "active": active == name,
        "builtin": False,
        "missing": False,
        "writable": True,
        "gap": None,
        "fields": fields,
    }


def list_providers(mod: Any) -> dict[str, Any]:
    def _read() -> dict[str, Any]:
        panel_home(mod)
        _policy(mod)
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
        active = _active(config)
        if active and not _NAME_RE.fullmatch(active):
            active = None
            active_omitted = True
        else:
            active_omitted = False
        names = _names()
        data = [_row(mod, name, active) for name in names]
        if active and active not in names and _NAME_RE.fullmatch(active):
            data.append(_row(mod, active, active, missing=True))
        return {
            "data": data,
            "active": active,
            "activeOmitted": active_omitted,
            "applied": False,
            "effectiveNow": False,
            "note": "active 是 memory.provider 的合法字面值；缺键或无法安全回显时不猜 builtin。字段值只来自配置存储，秘密只报 valueSet。",
        }

    return run(mod, _read)


def _nested_secret(mod: Any, value: Any) -> bool:
    if isinstance(value, dict):
        return any(secret_name(mod, str(key)) or _nested_secret(mod, item) for key, item in value.items())
    if isinstance(value, list):
        return any(_nested_secret(mod, item) for item in value)
    return False


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
        fail(403, "config_managed", "当前安装由包管理器托管，面板不改记忆提供方配置")


def _write_nonsecret(schema, values: dict[str, str]) -> None:
    from hermes_cli.web_routers.memory_providers import (
        _apply_field_values,
        _flat_json_path,
        _honcho_resolvers,
        _read_flat_json,
        _read_json_dict,
        _write_json_0600,
    )
    from plugins.memory.config_schema import STORAGE_FLAT_JSON, STORAGE_HONCHO_HOST_BLOCK

    if schema.storage == STORAGE_FLAT_JSON:
        existing = _read_flat_json(schema)
        _apply_field_values(schema, values, lambda _field: existing)
        _write_json_0600(_flat_json_path(schema), existing)
        return
    if schema.storage != STORAGE_HONCHO_HOST_BLOCK:
        _unavailable("不认识的提供方存储，未写入")
    from plugins.memory import import_provider_module

    oauth = import_provider_module(schema.name, "oauth")
    resolve_active_host, resolve_config_path, host_block_of = _honcho_resolvers(schema.name)
    host = resolve_active_host()
    path = resolve_config_path()
    with oauth._refresh_lock, oauth._config_refresh_lock(path):
        cfg = oauth._read_config_strict(path)
        hosts = cfg.get("hosts")
        cfg["hosts"] = hosts = hosts if isinstance(hosts, dict) else {}
        existing = host_block_of(cfg, host)
        host_key = next((key for key, value in hosts.items() if value is existing), host) if existing else host
        host_block = hosts.setdefault(host_key, existing if isinstance(existing, dict) else {})
        _apply_field_values(schema, values, lambda field: host_block if field.scope == "host" else cfg)
        _write_json_0600(path, cfg)


def _same(field, stored: Any, expected: Any) -> bool:
    from hermes_cli.web_routers.memory_providers import _UNSET

    if expected is _UNSET:
        return stored is None
    return stored == expected


def set_provider_config(mod: Any, name: Any, fields: Any) -> dict[str, Any]:
    def _write() -> dict[str, Any]:
        if not isinstance(name, str) or not _NAME_RE.fullmatch(name):
            fail(400, "invalid_body", "需要提供方名称和非秘密 fields 对象")
        if not isinstance(fields, dict) or not fields:
            fail(400, "invalid_body", "需要提供方名称和非秘密 fields 对象")
        panel_home(mod)
        _policy(mod)
        _reject_managed()
        from agent.memory_provider import is_core_memory_provider

        if is_core_memory_provider(name):
            fail(404, "provider_not_found", "内置记忆没有可写的提供方配置")
        schema, _path, gap = _schema_file(name)
        if schema is None:
            _unavailable(gap or "没有可安全写入的提供方配置")
        by_key = {field.key: field for field in schema.fields}
        from hermes_cli.web_routers.memory_providers import _coerce_field_value, _stringify_submitted

        submitted: dict[str, str] = {}
        expected: dict[str, Any] = {}
        for key, raw in fields.items():
            if not isinstance(key, str) or key not in by_key or not _FIELD_RE.fullmatch(key):
                fail(400, "unknown_key", "字段不在该提供方的声明里，未写入")
            field = by_key[key]
            if _secret_field(mod, key, bool(field.is_secret)):
                fail(403, "secret_key", "秘密字段不能通过这个接口写入，本次未写入任何字段")
            if _nested_secret(mod, raw):
                fail(403, "secret_key", "提交内容含秘密子键，本次未写入任何字段")
            text = _stringify_submitted(raw)
            try:
                coerced = _coerce_field_value(field, text)
            except ConfigError:
                raise
            except SystemExit:
                raise
            except Exception:
                fail(400, "invalid_body", f"字段 {key} 的值不合法，未写入")
            submitted[key] = text
            expected[key] = coerced
        with write_lock(mod):
            try:
                _write_nonsecret(schema, submitted)
            except ConfigError:
                raise
            except SystemExit:
                raise
            except Exception:
                fail(500, "memory_config_unverified", "写入后无法确认结果，未当成已生效")
            try:
                sources_for = _sources(schema)
            except ConfigError:
                raise
            except SystemExit:
                raise
            except Exception:
                fail(500, "memory_config_unverified", "已提交但无法回读，未当成已生效")
            for key, want in expected.items():
                if not _same(by_key[key], _stored(by_key[key], sources_for(by_key[key])), want):
                    fail(500, "memory_config_unverified", "已提交但回读结果不一致，请刷新确认")
            public = _public_fields(mod, schema)
        for field in public:
            if field.get("secret") and "value" in field:
                fail(503, "redact_unavailable", "秘密字段准备回传值，已拒绝返回")
        return _saved(
            "已写入该提供方的非秘密配置。没有切换 memory.provider，没有跑 setup，当前会话的记忆作用域没有改。",
            name=name,
            fields=[item for item in public if item["key"] in submitted],
        )

    return run(mod, _write)
