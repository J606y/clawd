"""Process-local config read and write.

Uses hermes_cli.config helpers that return or raise. Does not call the CLI
printers, and does not read their printed text. Secret and unknown keys are
refused before any write. File writes do not assign os.environ.
"""

from __future__ import annotations

import logging
from typing import Any

from clawd_server.admin_common import (
    ConfigError,
    assert_process_scope,
    fail,
    key_re,
    run,
    secret_key,
    write_lock,
)

log = logging.getLogger("clawd.admin")


def _missing():
    from hermes_cli.config import _MISSING

    return _MISSING


def lookup(key: str) -> Any:
    """Resolved value, or the core missing sentinel. Does not print."""
    from hermes_cli.config import (
        _get_nested,
        _is_env_config_key,
        _legacy_gateway_platforms_key,
        _redirect_platform_display_key,
        load_config,
        load_env,
        read_raw_config_readonly,
    )

    from hermes_cli.config_env_routing import is_env_setting_key

    if _is_env_config_key(key) or is_env_setting_key(key):
        # Profile file, not the process environment. A panel read must match a panel write.
        env_value = load_env().get(key.upper())
        if env_value is None and is_env_setting_key(key):
            env_value = read_raw_config_readonly().get(key)
        return _missing() if env_value is None else env_value
    legacy = _legacy_gateway_platforms_key(key)
    canonical, _note = _redirect_platform_display_key(key)
    config = load_config()
    value = _get_nested(config, canonical)
    if value is _missing() and legacy:
        value = _get_nested(config, legacy)
    return value


def optional(key: str) -> Any:
    value = lookup(key)
    return None if value is _missing() else value


def _display(key: str, value: Any) -> str:
    from agent.redact import _redact_enabled, mask_secret
    from hermes_cli.config import _format_config_get_value, _is_secret_config_key, redact_config_value

    shown = value
    if _redact_enabled():
        if isinstance(shown, str) and _is_secret_config_key(key):
            shown = mask_secret(shown)
        elif not isinstance(shown, str):
            shown = redact_config_value(shown)
    return _format_config_get_value(shown, as_json=False)


def _check_key(mod: Any, key: Any) -> str:
    if not isinstance(key, str) or not key_re(mod).match(key):
        fail(400, "invalid_key", "配置项名称不合法")
    return key


def config_get(mod: Any, key: str) -> str | None:
    def _read() -> str | None:
        checked = _check_key(mod, key)
        assert_process_scope(mod)
        value = lookup(checked)
        if value is _missing():
            return None
        return _display(checked, value)

    return run(mod, _read)


def config_get_json(mod: Any, key: str) -> Any:
    def _read() -> Any:
        checked = _check_key(mod, key)
        assert_process_scope(mod)
        value = lookup(checked)
        if value is _missing():
            return None
        from hermes_cli.config import redact_config_value

        return redact_config_value(value)

    return run(mod, _read)


def _reject_managed(key: str, action: str) -> None:
    from hermes_cli.config import get_config_backend, get_config_path, is_managed
    from hermes_cli import config as config_mod

    if is_managed():
        fail(403, "config_managed", "当前安装由包管理器托管，面板不改配置")
    managed = getattr(config_mod, "managed_scope", None)
    if managed is not None and managed.is_key_managed(key):
        fail(403, "config_managed", f"配置项 {key} 由管理员托管，不能{action}")
    level = get_config_backend().locked(get_config_path().parent, key)
    if level:
        fail(403, "config_locked", f"配置项 {key} 被远端配置 {level} 锁定，未写入")


def _refuse_unknown(key: str) -> None:
    from hermes_cli.config import _is_wrong_prefix_suggestion, _validate_config_key

    known, suggestion = _validate_config_key(key)
    if known:
        return
    hint = f"，是否指 {suggestion}" if suggestion else ""
    if suggestion and _is_wrong_prefix_suggestion(key, suggestion):
        fail(400, "unknown_key", f"核心不认识配置项 {key}{hint}，未写入")
    fail(400, "unknown_key", f"核心不认识配置项 {key}{hint}，未写入")


def _yaml_set(key: str, value: str) -> tuple[str, Any]:
    from hermes_cli.config import (
        _coerce_config_set_value,
        _get_nested,
        _legacy_gateway_platforms_key,
        _normalize_root_model_keys,
        _redirect_platform_display_key,
        _refuse_container_type_mismatch,
        _set_nested,
        _terminal_env_value,
        _touch_skin_file,
        _unset_nested,
        _write_user_config,
        get_config_path,
        require_readable_config_before_write,
        save_env_value,
        terminal_config_env_var_for_key,
    )
    from hermes_cli.config_backend import pinning_keys
    from hermes_cli.config_section_guard import _guard_section_overwrite

    legacy = _legacy_gateway_platforms_key(key)
    canonical, _note = _redirect_platform_display_key(key)
    _refuse_unknown(canonical)
    config_path = get_config_path()
    user_config = require_readable_config_before_write(config_path)
    coerced = _coerce_config_set_value(canonical, value)
    model_val = user_config.get("model")
    if canonical.startswith("model.") and isinstance(model_val, str) and model_val:
        user_config["model"] = {"default": model_val}
        model_val = user_config["model"]
    try:
        canonical = _guard_section_overwrite(canonical, coerced, user_config, False)
        coerced = _refuse_container_type_mismatch(canonical, coerced, user_config, False)
        _set_nested(user_config, canonical, coerced)
    except ValueError as exc:
        fail(400, "config_set_failed", "写入配置失败")
        raise AssertionError(exc)  # unreachable; fail always raises
    if legacy:
        _unset_nested(user_config, legacy)
    old_provider = model_val.get("provider") if isinstance(model_val, dict) else None
    if canonical == "model.provider" and str(old_provider or "").strip().lower() != str(coerced).strip().lower():
        from hermes_cli.route_identity import drop_stale_model_route

        drop_stale_model_route(user_config.get("model"), coerced, user_config)
    if canonical.strip().lower() in {"model.api_base", "api_base"}:
        user_config = _normalize_root_model_keys(user_config)
        canonical = "model.base_url"
    with pinning_keys(canonical):
        _write_user_config(config_path, user_config)
    env_var = terminal_config_env_var_for_key(canonical)
    if env_var and canonical != "terminal.cwd":
        save_env_value(env_var, _terminal_env_value(coerced), publish=False)
    try:
        _touch_skin_file(canonical, coerced)
    except Exception:
        log.warning("skin touch failed after config write", exc_info=True)
    if canonical == "display.language":
        try:
            from agent.i18n import reset_language_cache

            reset_language_cache()
        except Exception:
            log.warning("language cache reset failed after config write", exc_info=True)
    stored = _get_nested(user_config, canonical)
    return canonical, coerced if stored is _missing() else stored


def _credential_key(key: str) -> bool:
    from hermes_cli.config import _is_secret_config_key

    return _is_secret_config_key(key)


def _registered_env(key: str) -> bool:
    from hermes_cli.config_env_routing import is_registered_env_name

    return is_registered_env_name(key.upper())


def _stored(key: str) -> Any:
    """Value in this profile's files. Does not read os.environ."""
    from hermes_cli.config import (
        _get_nested,
        _is_env_config_key,
        _redirect_platform_display_key,
        load_env,
        read_raw_config_readonly,
    )
    from hermes_cli.config_env_routing import is_env_setting_key

    if _is_env_config_key(key) or is_env_setting_key(key):
        return load_env().get(key.upper())
    canonical, _note = _redirect_platform_display_key(key)
    value = _get_nested(read_raw_config_readonly(), canonical)
    return None if value is _missing() else value


def _require_readback(key: str, expected: Any) -> Any:
    seen = _stored(key)
    if seen != expected:
        fail(500, "config_readback_mismatch", "已提交但回读与写入不一致，请刷新确认")
    return seen


def commit_set(mod: Any, key: str, value: str) -> Any:
    """Write one legal, non-secret, known key. Returns the read-back value."""
    checked = _check_key(mod, key)
    if secret_key(mod, checked) or _credential_key(checked):
        fail(403, "secret_key", "密钥类配置不允许在面板里修改")
    assert_process_scope(mod)
    if not isinstance(value, str):
        fail(400, "invalid_value", "配置值必须是字符串")
    _reject_managed(checked, "修改")
    from hermes_cli.config import _is_env_config_key, require_env_writable, save_env_value
    from hermes_cli.config_env_routing import is_env_setting_key, save_env_setting

    try:
        if _is_env_config_key(checked):
            require_env_writable(checked.upper(), "set")
            expected = value.replace("\n", "").replace("\r", "")
            save_env_value(checked.upper(), expected, publish=False)
            written, stored = checked, expected
        elif is_env_setting_key(checked):
            if not _registered_env(checked):
                _refuse_unknown(checked)
            require_env_writable(checked.upper(), "set")
            expected = value.replace("\n", "").replace("\r", "")
            save_env_setting(checked, expected, publish=False)
            written, stored = checked, expected
        else:
            written, stored = _yaml_set(checked, value)
    except ConfigError:
        raise
    except SystemExit as exc:
        raise ConfigError(400, "config_rejected", "核心拒绝了这次配置修改，未继续") from exc
    except ValueError as exc:
        log.warning("config set refused: %s", type(exc).__name__)
        fail(400, "config_set_failed", "写入配置失败")
    except Exception as exc:
        log.warning("config set failed: %s", type(exc).__name__)
        fail(500, "config_set_failed", "写入配置失败")
    return _require_readback(written, stored)


def commit_unset(mod: Any, key: str, *, missing_ok: bool) -> None:
    checked = _check_key(mod, key)
    if secret_key(mod, checked) or _credential_key(checked):
        fail(403, "secret_key", "密钥类配置不允许在面板里修改")
    assert_process_scope(mod)
    _reject_managed(checked, "清除")
    from hermes_cli.config import (
        _is_env_config_key,
        _legacy_gateway_platforms_key,
        _redirect_platform_display_key,
        _unset_nested,
        _write_user_config,
        get_config_path,
        remove_env_value,
        require_readable_config_before_write,
        terminal_config_env_var_for_key,
    )
    from hermes_cli.config_env_routing import is_env_setting_key, remove_env_setting
    from hermes_cli.left_core_migration import LEFT_CORE_TERMINAL_KEYS

    try:
        if _is_env_config_key(checked):
            from hermes_cli.config import remove_env_value, require_env_writable

            require_env_writable(checked.upper(), "remove")
            found = remove_env_value(checked.upper(), publish=False)
        elif is_env_setting_key(checked):
            if not _registered_env(checked):
                _refuse_unknown(checked)
            found = bool(remove_env_setting(checked, publish=False))
        else:
            legacy = _legacy_gateway_platforms_key(checked)
            canonical, _note = _redirect_platform_display_key(checked)
            config_path = get_config_path()
            user_config = require_readable_config_before_write(config_path)
            found = _unset_nested(user_config, canonical)
            if legacy:
                found = _unset_nested(user_config, legacy) or found
            env_var = terminal_config_env_var_for_key(canonical) or LEFT_CORE_TERMINAL_KEYS.get(canonical)
            if env_var and canonical != "terminal.cwd":
                found = remove_env_value(env_var, publish=False) or found
            if found:
                _write_user_config(config_path, user_config)
    except ConfigError:
        raise
    except SystemExit as exc:
        raise ConfigError(400, "config_rejected", "核心拒绝了这次配置清除，未继续") from exc
    except ValueError as exc:
        log.warning("config unset refused: %s", type(exc).__name__)
        fail(400, "config_unset_failed", "清除配置失败")
    except Exception as exc:
        log.warning("config unset failed: %s", type(exc).__name__)
        fail(500, "config_unset_failed", "清除配置失败")
    if not found and not missing_ok:
        fail(400, "config_unset_failed", "配置项未设置，无法清除")


def effect_fields(key: str) -> dict[str, Any]:
    """Facts a settings form can show. Never claims the running process hot-loaded the write."""
    from hermes_cli.config import _is_env_config_key
    from hermes_cli.config_env_routing import is_env_setting_key

    restart = bool(_is_env_config_key(key) or is_env_setting_key(key))
    return {
        "saved": True,
        "applied": False,
        "processEnvChanged": False,
        "restartRequired": restart,
        "hotLoaded": False,
    }


def effect_text(fields: dict[str, Any]) -> str:
    if fields.get("restartRequired") is True:
        return "applied 为 false，本进程环境未改。待重启后新进程从文件读取才生效。"
    return "applied 为 false，本进程不会热加载，不能当成当前进程已经生效。"


def config_set(mod: Any, key: str, value: str) -> dict[str, Any]:
    """Panel /config/set. Validates, writes, and reads back. No stdout capture."""

    def _write() -> dict[str, Any]:
        with write_lock(mod):
            seen = commit_set(mod, key, value)
        shown = None if seen is None else _display(key, seen)
        fields = effect_fields(key)
        return {
            "ok": True,
            "key": key,
            "value": shown,
            "output": "已按核心校验写入并回读一致。未捕获命令行输出。" + effect_text(fields),
            **fields,
        }

    return run(mod, _write)


def set_key(mod: Any, key: str, value: str) -> Any:
    def _write() -> Any:
        with write_lock(mod):
            return commit_set(mod, key, value)

    return run(mod, _write)


def unset_key(mod: Any, key: str, *, missing_ok: bool) -> None:
    def _write() -> None:
        with write_lock(mod):
            commit_unset(mod, key, missing_ok=missing_ok)

    run(mod, _write)
