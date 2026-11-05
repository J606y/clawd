"""MCP list, enable, remove, probe, and create.

No MCP CLI wrapper and no catalog install. ``create_mcp`` is the frozen export
for POST /mcp/servers. Route registration belongs to the integrator.
"""

from __future__ import annotations

import contextlib
import io
import os
import re
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from clawd_server.admin_common import NAME_RE, fail, log, panel_home, redact_text, run, write_lock
from clawd_server.admin_config import commit_set

_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PLACEHOLDER_RE = re.compile(r"^\$\{[A-Za-z_][A-Za-z0-9_]*\}$|^\$[A-Za-z_][A-Za-z0-9_]*$")
_SECRET_KEY_RE = re.compile(
    r"(?:^|_)(?:api[_-]?key|token|secret|password|passwd|credential|auth)(?:$|_)",
    re.IGNORECASE,
)
_SECRET_ASSIGN_RE = re.compile(r"(?:^|[\s;])([A-Za-z_][A-Za-z0-9_]{0,80})\s*=\s*(\S+)")
_SECRET_FLAG_RE = re.compile(
    r"--?(?:api[-_]?key|apikey|access[-_]?token|refresh[-_]?token|id[-_]?token|"
    r"client[-_]?secret|password|passwd|secret|token|bearer)(?:=|\s+)(\S+)",
    re.IGNORECASE,
)
_BEARER_RE = re.compile(r"\b(?:Bearer|Basic|Token)\s+(\S+)", re.IGNORECASE)
_KNOWN_SECRET_RE = re.compile(
    r"sk-[A-Za-z0-9]|ghp_|github_pat_|gho_|xox[baprs]-|AKIA[0-9A-Z]{16}|-----BEGIN |eyJ[A-Za-z0-9_-]{8,}\."
)
_QUERY_SECRET_RE = re.compile(
    r"(?:^|[_-])(?:api[_-]?keys?|keys?|secrets?|passw(?:or)?ds?|tokens?|credentials?|authorization|bearer)$",
    re.IGNORECASE,
)
_SHELL_META_RE = re.compile(r"[\n\r\x00;|&`<>]|\$\(")
_ALLOWED_KEYS = frozenset({"name", "transport", "command", "args", "url", "enabled", "env"})
_PROFILE_KEYS = frozenset({
    "profile", "profileName", "profile_name", "home", "hermesHome", "hermes_home", "clawdHome", "clawd_home",
})
_CATALOG_KEYS = frozenset({
    "catalog", "preset", "directory", "install", "package", "repo", "source", "marketplace",
})
_SECRET_MSG = "命令、参数或地址里不能包含密钥明文"
_MAX_ARGS = 20
_MAX_ARG = 2048
_MAX_COMMAND = 1024
_MAX_URL = 2048
_MAX_ENV = 32
_MAX_ENV_VALUE = 8192


def _servers() -> dict[str, Any]:
    from hermes_cli.mcp_config import _get_mcp_servers

    return _get_mcp_servers()


def _view(mod: Any, name: str, spec: Any) -> dict[str, Any]:
    spec = spec if isinstance(spec, dict) else {}
    tools = spec.get("tools")
    if isinstance(tools, dict):
        include = tools.get("include")
        allow = [str(item) for item in include][:200] if isinstance(include, list) else None
    elif isinstance(tools, list):
        allow = [str(item) for item in tools][:200]
    else:
        allow = None
    args = spec.get("args")
    command = spec.get("command")
    url = spec.get("url")
    return {
        "name": name,
        "transport": "http" if spec.get("url") else "stdio",
        "command": redact_text(mod, str(command)) if command else None,
        "args": [redact_text(mod, str(item)) for item in args][:20] if isinstance(args, list) else [],
        "url": redact_text(mod, str(url)) if url else None,
        "auth": spec.get("auth") if isinstance(spec.get("auth"), str) else None,
        "enabled": spec.get("enabled") is not False,
        "toolAllowlist": allow,
        "envKeys": sorted(spec["env"]) if isinstance(spec.get("env"), dict) else [],
        "headerKeys": sorted(spec["headers"]) if isinstance(spec.get("headers"), dict) else [],
        "editable": spec.get("source") != "plugin",
    }


def list_mcp(mod: Any) -> dict[str, Any]:
    def _read() -> dict[str, Any]:
        panel_home(mod)
        return {"data": [_view(mod, name, spec) for name, spec in sorted(_servers().items())]}

    return run(mod, _read)


def _name(name: Any) -> str:
    if not isinstance(name, str) or not NAME_RE.match(name):
        fail(400, "invalid_name", "MCP 服务名称不合法（只允许字母、数字、下划线、连字符）")
    if name not in _servers():
        fail(404, "mcp_not_found", "MCP 服务不存在")
    return name


def set_mcp_enabled(mod: Any, name: Any, enabled: Any) -> dict[str, Any]:
    def _write() -> dict[str, Any]:
        if not isinstance(enabled, bool):
            fail(400, "invalid_body", "需要布尔 enabled")
        with write_lock(mod):
            checked = _name(name)
            commit_set(mod, f"mcp_servers.{checked}.enabled", "true" if enabled else "false")
            after = _servers().get(checked)
        if not isinstance(after, dict) or (after.get("enabled") is not False) != enabled:
            fail(500, "mcp_toggle_unverified", "已提交但回读结果不一致，请刷新确认")
        return {"ok": True, "name": checked, "enabled": enabled, "note": "新会话或网关重启后生效"}

    return run(mod, _write)


def remove_mcp(mod: Any, name: Any) -> dict[str, Any]:
    def _write() -> dict[str, Any]:
        from hermes_cli.config import is_managed
        from hermes_cli.mcp_config import _remove_mcp_server

        if is_managed():
            fail(403, "config_managed", "当前安装由包管理器托管，面板不删除 MCP")
        with write_lock(mod):
            checked = _name(name)
            spec = _servers().get(checked)
            if isinstance(spec, dict) and spec.get("source") == "plugin":
                fail(400, "mcp_readonly", "插件提供的 MCP 不能在面板里删除")
            removed = _remove_mcp_server(checked)
            still = checked in _servers()
        if not removed or still:
            fail(500, "mcp_remove_unverified", "已提交但服务仍在配置里，请刷新确认")
        return {"ok": True, "name": checked}

    return run(mod, _write)


def test_mcp(mod: Any, name: Any) -> dict[str, Any]:
    def _probe() -> dict[str, Any]:
        from hermes_cli.mcp_config import _probe_single_server, redact_mcp_probe_text

        checked = _name(name)
        spec = _servers().get(checked)
        if not isinstance(spec, dict):
            fail(404, "mcp_not_found", "MCP 服务不存在")
        server = dict(spec)
        try:
            tools = _probe_single_server(checked, server)
        except Exception as exc:
            text = redact_mcp_probe_text(str(exc) or type(exc).__name__)
            return {"ok": False, "output": redact_text(mod, text, 3000) or "连接失败"}
        summary = f"已连接，发现 {len(tools)} 个工具"
        return {"ok": True, "output": redact_text(mod, summary, 3000) or summary}

    return run(mod, _probe)


def _reject_body_keys(body: dict[str, Any]) -> None:
    keys = set(body)
    if keys & _PROFILE_KEYS:
        fail(409, "profile_scope_unsafe", "不能用请求里的 profile 或目录猜测归属")
    if keys & _CATALOG_KEYS:
        fail(400, "invalid_body", "不提供 MCP 目录安装")
    if keys - _ALLOWED_KEYS:
        fail(400, "invalid_body", "请求包含未批准的字段")


def _optional_text(body: dict[str, Any], key: str, limit: int) -> str | None:
    if key not in body or body[key] is None:
        return None
    value = body[key]
    if not isinstance(value, str) or len(value) > limit or "\x00" in value:
        fail(400, "invalid_body", "字段类型或长度不合法")
    text = value.strip()
    return text or None


def _parse_args(body: dict[str, Any]) -> list[str]:
    if "args" not in body or body["args"] is None:
        return []
    raw = body["args"]
    if not isinstance(raw, list) or len(raw) > _MAX_ARGS:
        fail(400, "invalid_body", "args 必须是最多 20 项的字符串列表")
    args: list[str] = []
    for item in raw:
        if not isinstance(item, str) or "\x00" in item or len(item) > _MAX_ARG:
            fail(400, "invalid_body", "args 项必须是字符串")
        text = item.strip()
        if text:
            args.append(text)
    return args


def _parse_env(body: dict[str, Any]) -> dict[str, str]:
    if "env" not in body or body["env"] is None:
        return {}
    raw = body["env"]
    if not isinstance(raw, dict) or len(raw) > _MAX_ENV:
        fail(400, "invalid_body", "env 必须是最多 32 项的对象")
    env: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not _ENV_NAME_RE.match(key) or len(key) > 64:
            fail(400, "invalid_body", "环境变量名不合法")
        if not isinstance(value, str) or "\x00" in value or len(value) > _MAX_ENV_VALUE:
            fail(400, "invalid_body", "环境变量值必须是字符串")
        env[key] = value
    return env


def _parse_create(body: Any) -> dict[str, Any]:
    if not isinstance(body, dict):
        fail(400, "invalid_body", "需要对象")
    _reject_body_keys(body)
    name = body.get("name")
    if not isinstance(name, str) or not NAME_RE.match(name):
        fail(400, "invalid_body", "MCP 服务名称不合法（只允许字母、数字、下划线、连字符）")
    transport = body.get("transport")
    if transport not in {"stdio", "http"}:
        fail(400, "invalid_body", "transport 只能是 stdio 或 http")
    if "enabled" in body and body["enabled"] is not None and not isinstance(body["enabled"], bool):
        fail(400, "invalid_body", "enabled 必须是布尔值")
    enabled = True if body.get("enabled") is None else bool(body["enabled"])
    command = _optional_text(body, "command", _MAX_COMMAND)
    url = _optional_text(body, "url", _MAX_URL)
    args = _parse_args(body)
    env = _parse_env(body)
    if transport == "http":
        if not url or command or args or env:
            fail(400, "invalid_body", "HTTP 只接受地址，不接受命令、参数或环境变量")
        url = _check_http_url(url)
    else:
        if not command or url:
            fail(400, "invalid_body", "stdio 必须提供命令，不能同时提供地址")
        if _SHELL_META_RE.search(command):
            fail(400, "invalid_body", "命令必须是可执行文件，不能是一段 shell")
    return {
        "name": name,
        "transport": transport,
        "command": command,
        "args": args,
        "url": url,
        "enabled": enabled,
        "env": env,
    }


def _check_http_url(url: str) -> str:
    if any(char in url for char in "\n\r\x00 "):
        fail(400, "invalid_body", "HTTP 地址不合法")
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        fail(400, "invalid_body", "HTTP 地址必须是 http 或 https")
    if parts.username or parts.password:
        fail(400, "mcp_secret_in_command", _SECRET_MSG)
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if _QUERY_SECRET_RE.search(key) and value and not _PLACEHOLDER_RE.match(value):
            fail(400, "mcp_secret_in_command", _SECRET_MSG)
    return url


def _literal_secret(text: str) -> bool:
    if _KNOWN_SECRET_RE.search(text) or _BEARER_RE.search(text):
        return True
    for match in _SECRET_FLAG_RE.finditer(text):
        value = match.group(1)
        if value and not _PLACEHOLDER_RE.match(value):
            return True
    for match in _SECRET_ASSIGN_RE.finditer(text):
        key, value = match.group(1), match.group(2)
        if _SECRET_KEY_RE.search(key) and value and not _PLACEHOLDER_RE.match(value):
            return True
    return False


def _redactor_changed(text: str) -> bool:
    try:
        from agent.redact import redact_sensitive_text
    except Exception:
        fail(503, "redact_unavailable", "没有脱敏函数，拒绝保存可能含密钥的命令")
    if not callable(redact_sensitive_text):
        fail(503, "redact_unavailable", "没有脱敏函数，拒绝保存可能含密钥的命令")
    try:
        redacted = redact_sensitive_text(text, force=True, redact_url_credentials=True)
    except TypeError:
        try:
            redacted = redact_sensitive_text(text, force=True)
        except Exception:
            fail(503, "redact_unavailable", "脱敏函数不可用，拒绝保存可能含密钥的命令")
    except Exception:
        fail(503, "redact_unavailable", "脱敏函数不可用，拒绝保存可能含密钥的命令")
    if not isinstance(redacted, str):
        fail(503, "redact_unavailable", "脱敏函数没有返回文本，拒绝保存")
    return redacted != text


def _reject_command_secrets(parsed: dict[str, Any]) -> None:
    texts = [parsed.get("command"), parsed.get("url"), *parsed.get("args", [])]
    for text in texts:
        if not isinstance(text, str) or not text:
            continue
        if _literal_secret(text) or _redactor_changed(text):
            fail(400, "mcp_secret_in_command", _SECRET_MSG)


def _require_redact(mod: Any) -> None:
    if not callable(getattr(mod, "redact", None)):
        fail(503, "redact_unavailable", "没有脱敏函数，拒绝保存")


def _proven_home(mod: Any):
    from pathlib import Path

    from hermes_constants import get_hermes_home, get_hermes_home_override, get_process_hermes_home
    from clawd_server.home import is_production_home

    panel = getattr(mod, "HERMES_HOME", None)
    if panel is None or not str(panel).strip():
        fail(409, "profile_scope_unsafe", "面板没有已证明的数据目录，不能猜测 profile")
    hermes = os.environ.get("HERMES_HOME", "").strip()
    clawd = os.environ.get("CLAWD_HOME", "").strip()
    if not hermes and not clawd:
        fail(409, "profile_scope_unsafe", "进程没有显式数据目录，拒绝回落到默认 profile")
    try:
        process = get_process_hermes_home().resolve()
        panel_path = Path(panel).expanduser().resolve()
        explicit = Path(hermes or clawd).expanduser().resolve()
        if hermes and clawd and Path(hermes).expanduser().resolve() != Path(clawd).expanduser().resolve():
            fail(409, "profile_scope_unsafe", "HERMES_HOME 与 CLAWD_HOME 不一致，已拒绝")
    except OSError:
        fail(409, "profile_scope_unsafe", "数据目录无法解析，已拒绝")
    if panel_path != process or explicit != process:
        fail(409, "profile_scope_unsafe", "面板、进程与显式数据目录不一致，已拒绝")
    override = get_hermes_home_override()
    if override:
        try:
            other = Path(override).expanduser().resolve()
        except OSError:
            fail(409, "profile_scope_unsafe", "请求线程的 home 覆盖无法解析，已拒绝")
        if other != process:
            fail(409, "profile_scope_unsafe", "请求线程带有其他 profile 的 home 覆盖，不能靠修改进程环境切换")
    if is_production_home(process):
        fail(409, "profile_scope_unsafe", "拒绝写入生产 Hermes 数据目录")
    try:
        seen = Path(get_hermes_home()).resolve()
    except OSError:
        fail(409, "profile_scope_unsafe", "配置写入目标无法解析，已拒绝")
    if seen != process:
        fail(409, "profile_scope_unsafe", "配置写入目标与已证明的进程目录不一致")
    panel_home(mod)
    return process


def _config_in_home(home: Any):
    from hermes_cli.config import get_config_path

    path = get_config_path().resolve()
    try:
        path.relative_to(home.resolve())
    except ValueError:
        fail(409, "profile_scope_unsafe", "配置文件不在已证明的数据目录内")
    return path


def _reject_managed() -> None:
    from hermes_cli.config import get_config_backend, get_config_path, is_managed

    if is_managed():
        fail(403, "config_managed", "当前安装由包管理器托管，面板不新增 MCP")
    level = get_config_backend().locked(get_config_path().parent, "mcp_servers")
    if level:
        fail(403, "config_locked", "MCP 配置被远端锁定，未写入")


def _server_config(parsed: dict[str, Any]) -> dict[str, Any]:
    config: dict[str, Any] = {"enabled": parsed["enabled"]}
    if parsed["transport"] == "http":
        config["url"] = parsed["url"]
        return config
    config["command"] = parsed["command"]
    if parsed["args"]:
        config["args"] = list(parsed["args"])
    if parsed["env"]:
        config["env"] = dict(parsed["env"])
    return config


def _entry_matches(stored: Any, wanted: dict[str, Any]) -> bool:
    if not isinstance(stored, dict):
        return False
    for key in ("command", "url"):
        wanted_value = wanted.get(key)
        stored_value = stored.get(key)
        if wanted_value is None:
            if stored_value not in (None, ""):
                return False
        elif stored_value != wanted_value:
            return False
    wanted_args = list(wanted.get("args") or [])
    stored_args = stored.get("args")
    if stored_args is None:
        stored_args = []
    if not isinstance(stored_args, list) or [str(item) for item in stored_args] != wanted_args:
        return False
    if (stored.get("enabled") is not False) != bool(wanted.get("enabled", True)):
        return False
    wanted_env = wanted.get("env") if isinstance(wanted.get("env"), dict) else {}
    stored_env = stored.get("env") if isinstance(stored.get("env"), dict) else {}
    if set(stored_env) != set(wanted_env):
        return False
    return all(stored_env.get(key) == value for key, value in wanted_env.items())


def _copy_spec(spec: Any) -> dict[str, Any]:
    copied = dict(spec) if isinstance(spec, dict) else {}
    if isinstance(copied.get("args"), list):
        copied["args"] = list(copied["args"])
    if isinstance(copied.get("env"), dict):
        copied["env"] = dict(copied["env"])
    if isinstance(copied.get("headers"), dict):
        copied["headers"] = dict(copied["headers"])
    return copied


def _persist(mod: Any, parsed: dict[str, Any]) -> dict[str, Any]:
    from hermes_cli.mcp_config import _save_mcp_server

    home = _proven_home(mod)
    _config_in_home(home)
    _reject_managed()
    name = parsed["name"]
    before = set(_servers())
    if name in before:
        fail(409, "mcp_exists", "MCP 服务已存在")
    existing = _servers().get(name)
    if isinstance(existing, dict) and existing.get("source") == "plugin":
        fail(400, "mcp_readonly", "插件提供的 MCP 不能在面板里覆盖")
    wanted = _server_config(parsed)
    sink = io.StringIO()
    try:
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            saved = _save_mcp_server(name, wanted)
    except Exception:
        log.warning("mcp create save failed without echoing the entry")
        fail(500, "mcp_create_unverified", "保存没有完成，未当成成功")
    finally:
        sink.close()
    after = _servers()
    if not saved:
        if name in after:
            fail(500, "mcp_create_unverified", "核心拒绝保存，但回读仍看到该服务")
        fail(400, "invalid_body", "核心拒绝了该服务配置，未保存")
    if name not in after or not before <= set(after) or not _entry_matches(after.get(name), wanted):
        fail(500, "mcp_create_unverified", "已提交但回读结果不一致，请刷新确认")
    return _copy_spec(after.get(name))


def _leaks(node: Any, secrets: set[str]) -> bool:
    if isinstance(node, str):
        return node in secrets
    if isinstance(node, dict):
        return any((isinstance(key, str) and key in secrets) or _leaks(value, secrets) for key, value in node.items())
    if isinstance(node, (list, tuple)):
        return any(_leaks(value, secrets) for value in node)
    return False


def _result(mod: Any, parsed: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    view = _view(mod, parsed["name"], spec)
    if "env" in view:
        fail(500, "mcp_create_unverified", "回读包含不应返回的字段，已拒绝返回")
    payload = {
        "ok": True,
        "saved": True,
        "applied": False,
        "processEnvChanged": False,
        "hotLoaded": False,
        "effectiveNow": False,
        "loadedNow": False,
        "restartRequired": False,
        "name": parsed["name"],
        "server": view,
        "note": "已保存到当前进程已证明的数据目录。本进程未热加载，新会话或网关重启后才生效。",
    }
    public = {parsed["name"], parsed.get("command") or "", parsed.get("url") or "", *parsed.get("args", [])}
    secrets = {value for value in parsed["env"].values() if isinstance(value, str) and value and value not in public}
    if _leaks(payload, secrets):
        fail(500, "mcp_create_unverified", "回读包含不应返回的值，已拒绝返回")
    return payload


def create_mcp(mod: Any, body: Any) -> dict[str, Any]:
    """Frozen export. Integrator calls ``create_mcp(mod, body)`` with the JSON object.

    Does not register POST /mcp/servers, does not install from a catalog, and does
    not guess a profile name.
    """

    def _write() -> dict[str, Any]:
        parsed = _parse_create(body)
        _require_redact(mod)
        _reject_command_secrets(parsed)
        with write_lock(mod):
            spec = _persist(mod, parsed)
        return _result(mod, parsed, spec)

    return run(mod, _write)
