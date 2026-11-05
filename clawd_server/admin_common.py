"""Shared guards for in-process panel admin routes.

No CLI, no stdout scraping, and no process-global profile switch.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, NoReturn

log = logging.getLogger("clawd.admin")

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,63}$")
MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/\-]{0,127}$")
PLUGIN_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,79}$")
PROFILE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,31}$")
WEBHOOK_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,63}$")
WEBHOOK_TOKEN_RE = re.compile(r"^[A-Za-z0-9_:\-]{1,40}$")
_CONFIG_KEY_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-]{0,127}$")


class ConfigError(Exception):
    """Panel-facing failure. Never means the write succeeded."""

    def __init__(self, status: int, code: str, message: str) -> None:
        self.status = status
        self.code = code
        self.message = message
        super().__init__(message)


def fail(status: int, code: str, message: str) -> NoReturn:
    raise ConfigError(status, code, message)


def run(mod: Any, fn: Callable[[], Any]) -> Any:
    """Map core exits and config errors onto the sidecar ApiError. Do not treat them as success."""
    try:
        return fn()
    except ConfigError as exc:
        raise mod.ApiError(exc.status, exc.code, exc.message) from exc
    except SystemExit as exc:
        log.warning("blocked SystemExit from a core admin call: %s", type(exc).__name__)
        raise mod.ApiError(500, "core_exit_blocked", "核心试图退出进程，已拦截，未当成成功") from exc


@contextmanager
def write_lock(mod: Any) -> Iterator[None]:
    lock = getattr(mod, "_WRITE_LOCK", None)
    if lock is None:
        yield
        return
    with lock:
        yield


def key_re(mod: Any) -> re.Pattern[str]:
    pattern = getattr(mod, "CONFIG_KEY_RE", None)
    return pattern if pattern is not None else _CONFIG_KEY_RE


# Approved panel heuristic false locks. Do not extend this set without a contract change.
# ``secrets.onepassword.env`` is an exclude-secret-map and stays locked.
_PANEL_FALSE_SECRET = frozenset({
    "security.redact_secrets",
    "secrets.bitwarden.enabled",
    "secrets.bitwarden.access_token_env",
    "secrets.bitwarden.project_id",
    "secrets.bitwarden.cache_ttl_seconds",
    "secrets.bitwarden.encrypted_cache.enabled",
    "secrets.bitwarden.encrypted_cache.max_stale_seconds",
    "secrets.bitwarden.override_existing",
    "secrets.bitwarden.auto_install",
    "secrets.bitwarden.server_url",
    "secrets.onepassword.enabled",
    "secrets.onepassword.account",
    "secrets.onepassword.service_account_token_env",
    "secrets.onepassword.binary_path",
    "secrets.onepassword.cache_ttl_seconds",
    "secrets.onepassword.override_existing",
})
# Core suffix matching misses this hash. The panel must still refuse it.
_KEEP_LOCKED = frozenset({"dashboard.basic_auth.password_hash"})
_WRITE_FACT_DEFAULTS = {
    "saved": False,
    "applied": False,
    "processEnvChanged": False,
    "hotLoaded": False,
    "effectiveNow": False,
    "loadedNow": False,
    "restartRequired": False,
    "processSwitched": False,
}


def _core_secret(key: str) -> bool:
    """Core credential rule. Unavailable policy refuses; it does not allow the write."""
    try:
        from hermes_cli.config import _is_secret_config_key
    except Exception as exc:
        fail(503, "secret_policy_unavailable", "核心密钥规则不可用，拒绝写入或返回")
        raise AssertionError(exc)
    try:
        return bool(_is_secret_config_key(key))
    except Exception as exc:
        fail(503, "secret_policy_unavailable", "核心密钥规则不可用，拒绝写入或返回")
        raise AssertionError(exc)


def secret_key(mod: Any, key: str) -> bool:
    """Panel heuristic plus core names. False locks are the approved list only."""
    check = getattr(mod, "_is_secret_key", None)
    if not callable(check):
        fail(503, "secret_policy_unavailable", "没有密钥拒绝规则，拒绝写入")
    try:
        panel = bool(check(key))
    except Exception as exc:
        fail(503, "secret_policy_unavailable", "密钥拒绝规则不可用，拒绝写入")
        raise AssertionError(exc)
    core = _core_secret(key)
    normalized = key.strip().lower()
    if core or normalized in _KEEP_LOCKED:
        return True
    if normalized in _PANEL_FALSE_SECRET:
        return False
    return panel


def secret_name(mod: Any, name: str) -> bool:
    check = getattr(mod, "_is_secret_name", None)
    if not callable(check):
        fail(503, "secret_policy_unavailable", "没有密钥拒绝规则，拒绝返回该字段")
    try:
        panel = bool(check(name))
    except Exception as exc:
        fail(503, "secret_policy_unavailable", "密钥拒绝规则不可用，拒绝返回该字段")
        raise AssertionError(exc)
    return panel or _core_secret(name)


def ensure_write_facts(payload: Any) -> dict[str, Any]:
    """Fill missing effect facts with negatives. Never invent applied or hot-loaded."""
    if not isinstance(payload, dict):
        fail(500, "write_unverified", "写入没有返回可核对的结果，不能当成已保存或已生效")
    out = dict(payload)
    for fact, value in _WRITE_FACT_DEFAULTS.items():
        if fact not in out:
            out[fact] = value
    if "ok" not in out:
        out["ok"] = False
    return out


def redact_text(mod: Any, value: str | None, limit: int | None = None) -> str | None:
    if value is None:
        return None
    fn = getattr(mod, "redact", None)
    if not callable(fn):
        fail(503, "redact_unavailable", "没有脱敏函数，拒绝返回可能含密钥的字段")
    rendered = fn(value)
    if not isinstance(rendered, str):
        fail(503, "redact_unavailable", "脱敏函数没有返回文本，拒绝返回该字段")
    if limit is not None:
        return rendered[:limit]
    return rendered


def assert_process_scope(mod: Any) -> Path:
    """Refuse a request-thread home override. Never assign os.environ to change profile."""
    from hermes_constants import get_hermes_home_override, get_process_hermes_home

    process = get_process_hermes_home().resolve()
    override = get_hermes_home_override()
    if override:
        other = Path(override).expanduser().resolve()
        if other != process:
            fail(409, "profile_scope_unsafe", "请求线程带有其他 profile 的 home 覆盖，不能靠修改进程环境切换")
    panel = getattr(mod, "HERMES_HOME", None)
    if panel is not None and Path(panel).resolve() != process:
        fail(409, "profile_home_mismatch", "面板数据目录与进程 HERMES_HOME 不一致，已拒绝")
    return process


def panel_home(mod: Any) -> Path:
    home = assert_process_scope(mod)
    panel = getattr(mod, "HERMES_HOME", None)
    return Path(panel) if panel is not None else home
