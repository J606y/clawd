"""Model, provider, and cron-model routes. Reads load_config; writes commit_set.

``create_custom_endpoint(mod, body)`` is the frozen export for
POST /models/custom-endpoints. This module does not register that route.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any
from urllib.parse import parse_qsl, urlparse

from clawd_server.admin_common import MODEL_ID_RE, NAME_RE, fail, panel_home, redact_text, run, write_lock
from clawd_server.admin_config import commit_set, commit_unset, optional

log = logging.getLogger("clawd.admin")
_ENV_REF_RE = re.compile(r"^\$\{[A-Za-z_][A-Za-z0-9_]*\}$")
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_LEGACY_MASK_RE = re.compile(r".{4}\.\.\..{4}")
_URL_SECRET_QUERY = frozenset({"key", "api_key", "apikey", "token", "access_token", "secret", "password"})
_ENDPOINT_FIELDS = frozenset({"id", "baseUrl", "apiKey", "models"})
_MISSING = object()
_NOTE = "已写入当前进程 profile 的 providers。密钥不回传。当前进程未热加载，也不改主模型；新会话重新读取配置后才会用到。"


def _providers() -> dict[str, Any]:
    raw = optional("providers")
    return raw if isinstance(raw, dict) else {}


def _current(model: Any) -> dict[str, Any]:
    block = model if isinstance(model, dict) else {"default": model} if isinstance(model, str) else {}
    return {key: block.get(key) for key in ("provider", "default", "base_url", "api_mode") if isinstance(block.get(key), (str, type(None)))}


def get_models(mod: Any) -> dict[str, Any]:
    def _read() -> dict[str, Any]:
        panel_home(mod)
        model = optional("model")
        providers_raw = optional("providers")
        catalog = optional("model_catalog")
        current = _current(model)
        if isinstance(current.get("base_url"), str):
            current["base_url"] = redact_text(mod, current["base_url"])
        try:
            raw_providers = _raw_providers()
            raw_ok = True
        except Exception as exc:
            log.warning("endpoint key status unread: %s", type(exc).__name__)
            raw_providers = {}
            raw_ok = False
        from hermes_cli.config_providers import find_provider_entry

        providers = []
        for name, spec in (providers_raw if isinstance(providers_raw, dict) else {}).items():
            if not isinstance(spec, dict):
                continue
            models = spec.get("models")
            base = spec.get("base_url")
            _stored, raw_entry = find_provider_entry(raw_providers, name)
            providers.append({
                "id": name,
                "name": spec.get("name") if isinstance(spec.get("name"), str) else name,
                "baseUrl": redact_text(mod, base) if isinstance(base, str) else None,
                "apiMode": spec.get("api_mode") if isinstance(spec.get("api_mode"), str) else None,
                "models": [item for item in models if isinstance(item, str)] if isinstance(models, list) else [],
                "discoverModels": bool(spec.get("discover_models")),
                "editable": bool(NAME_RE.match(str(name))),
                "hasKey": None if not raw_ok or not isinstance(raw_entry, dict) else _entry_has_key(raw_entry),
            })
        excluded = catalog.get("excluded_providers") if isinstance(catalog, dict) else None
        return {
            "current": current,
            "providers": providers,
            "excludedProviders": [item for item in excluded if isinstance(item, str)] if isinstance(excluded, list) else [],
        }

    return run(mod, _read)


def _provider_for_edit(name: Any) -> str:
    if not isinstance(name, str) or name not in _providers():
        fail(404, "provider_not_found", "提供方不存在")
    if not NAME_RE.match(name):
        fail(400, "provider_unsupported", "该提供方名称含特殊字符，请在服务器上修改")
    return name


def set_default_model(mod: Any, model: Any, provider: Any) -> dict[str, Any]:
    def _write() -> dict[str, Any]:
        if not isinstance(model, str) or not MODEL_ID_RE.match(model):
            fail(400, "invalid_model", "模型名不合法")
        with write_lock(mod):
            chosen = provider
            if chosen is not None:
                chosen = _provider_for_edit(chosen)
                commit_set(mod, "model.provider", chosen)
            commit_set(mod, "model.default", model)
            after = get_models(mod)["current"]
        if after.get("default") != model or (provider is not None and after.get("provider") != chosen):
            fail(500, "model_unverified", "已提交但回读结果不一致，请刷新确认")
        return {"ok": True, "current": after, "note": "只对新会话生效"}

    return run(mod, _write)


def set_provider_models(mod: Any, provider: Any, models: Any) -> dict[str, Any]:
    def _write() -> dict[str, Any]:
        if not isinstance(models, list) or not 0 < len(models) <= 100 or not all(isinstance(item, str) and MODEL_ID_RE.match(item) for item in models):
            fail(400, "invalid_models", "模型列表需为 1–100 个合法模型名")
        with write_lock(mod):
            name = _provider_for_edit(provider)
            commit_set(mod, f"providers.{name}.models", json.dumps(models))
            after = next((item for item in get_models(mod)["providers"] if item["id"] == name), None)
        if not after or after["models"] != models:
            fail(500, "models_unverified", "已提交但回读结果不一致，请刷新确认")
        return {"ok": True, "provider": after}

    return run(mod, _write)


def set_provider_discover(mod: Any, provider: Any, discover: Any) -> dict[str, Any]:
    def _write() -> dict[str, Any]:
        if not isinstance(discover, bool):
            fail(400, "invalid_body", "需要布尔 discover")
        with write_lock(mod):
            name = _provider_for_edit(provider)
            commit_set(mod, f"providers.{name}.discover_models", "true" if discover else "false")
            after = next((item for item in get_models(mod)["providers"] if item["id"] == name), None)
        if not after or after["discoverModels"] != discover:
            fail(500, "discover_unverified", "已提交但回读结果不一致，请刷新确认")
        return {"ok": True, "provider": after}

    return run(mod, _write)


def set_excluded_providers(mod: Any, items: Any) -> dict[str, Any]:
    def _write() -> dict[str, Any]:
        if not isinstance(items, list) or len(items) > 100 or not all(isinstance(item, str) and MODEL_ID_RE.match(item) and "." not in item for item in items):
            fail(400, "invalid_providers", "需要提供方名称列表")
        with write_lock(mod):
            if items:
                commit_set(mod, "model_catalog.excluded_providers", json.dumps(items))
            else:
                commit_unset(mod, "model_catalog.excluded_providers", missing_ok=False)
            after = get_models(mod)["excludedProviders"]
        if sorted(after) != sorted(items):
            fail(500, "excluded_unverified", "已提交但回读结果不一致，请刷新确认")
        return {"ok": True, "excludedProviders": after}

    return run(mod, _write)


def _cron_jobs(mod: Any) -> list[dict[str, Any]]:
    path = panel_home(mod) / "cron" / "jobs.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        fail(503, "cron_jobs_unreadable", "定时任务列表无法读取，不以空列表冒充没有任务")
        raise AssertionError(exc)
    jobs = raw.get("jobs", raw) if isinstance(raw, dict) else raw
    if isinstance(jobs, dict):
        jobs = list(jobs.values())
    out: list[dict[str, Any]] = []
    for job in jobs if isinstance(jobs, list) else []:
        if not isinstance(job, dict) or not isinstance(job.get("id"), str):
            continue
        model = job.get("model") if isinstance(job.get("model"), str) and job["model"].strip() else None
        provider = job.get("provider") if isinstance(job.get("provider"), str) and job["provider"].strip() else None
        name = redact_text(mod, str(job.get("name") or job["id"]), 80)
        out.append({
            "id": job["id"],
            "name": name,
            "enabled": job.get("enabled", True) is not False,
            "model": model,
            "provider": provider,
        })
    return out


def get_cron_model(mod: Any) -> dict[str, Any]:
    def _read() -> dict[str, Any]:
        cron = optional("cron")
        cron = cron if isinstance(cron, dict) else {}

        def text(field: str) -> str | None:
            value = cron.get(field)
            return value.strip() if isinstance(value, str) and value.strip() else None

        main = get_models(mod)["current"]
        return {
            "cronModel": text("model"),
            "cronProvider": text("model_provider"),
            "main": {"provider": main.get("provider"), "model": main.get("default")},
            "jobs": _cron_jobs(mod),
        }

    return run(mod, _read)


def set_cron_model(mod: Any, model: Any, provider: Any) -> dict[str, Any]:
    def _write() -> dict[str, Any]:
        clear = model is None or model == ""
        model_name = model if isinstance(model, str) else ""
        provider_name = ""
        if not clear:
            if not isinstance(model, str) or not MODEL_ID_RE.match(model):
                fail(400, "invalid_model", "模型名不合法")
            model_name = model
            provider_name = _provider_for_edit(provider)
        with write_lock(mod):
            if clear:
                commit_unset(mod, "cron.model", missing_ok=True)
                commit_unset(mod, "cron.model_provider", missing_ok=True)
            else:
                commit_set(mod, "cron.model_provider", provider_name)
                commit_set(mod, "cron.model", model_name)
            after = get_cron_model(mod)
        want_model = None if clear else model_name
        want_provider = None if clear else provider_name
        if after["cronModel"] != want_model or after["cronProvider"] != want_provider:
            fail(500, "cron_model_unverified", "已提交但回读结果不一致，请刷新确认")
        return {"ok": True, **after, "note": "下一次定时任务触发时生效；已固定模型的任务不受影响"}

    return run(mod, _write)


class _ParsedEndpoint:
    __slots__ = ("id", "base_url", "api_key", "models")

    def __init__(self, endpoint_id: str, base_url: str, api_key: str | None, models: list[str]) -> None:
        self.id = endpoint_id
        self.base_url = base_url
        self.api_key = api_key
        self.models = models


def _raw_providers() -> dict[str, Any]:
    raw = _exact_config()
    providers = raw.get("providers")
    return providers if isinstance(providers, dict) else {}


def _exact_config() -> dict[str, Any]:
    """On-disk config for this process profile. Does not expand secrets."""
    from hermes_cli.config import get_config_path
    from hermes_cli.config_backend import read_config_doc

    try:
        data = read_config_doc(get_config_path())
    except FileNotFoundError:
        return {}
    if data is None:
        return {}
    if not isinstance(data, dict):
        fail(400, "invalid_body", "配置文件不是映射，未写入")
    return data


def _env_nonempty(name: str) -> bool:
    if not _ENV_NAME_RE.fullmatch(name):
        return False
    from hermes_cli.config import load_env

    value = load_env().get(name)
    return isinstance(value, str) and bool(value.strip())


def _entry_has_key(entry: dict[str, Any]) -> bool | None:
    """Whether a credential is configured. Never returns the credential."""
    try:
        raw = entry.get("api_key")
        if isinstance(raw, str) and raw.strip():
            template = _ENV_REF_RE.fullmatch(raw.strip())
            if template:
                inner = template.group(0)[2:-1]
                return _env_nonempty(inner)
            return True
        for field in ("key_env", "api_key_env"):
            name = entry.get(field)
            if isinstance(name, str) and name.strip():
                return _env_nonempty(name.strip())
        return False
    except Exception as exc:
        log.warning("endpoint key status unavailable: %s", type(exc).__name__)
        return None


def _alias(value: str) -> str:
    return value.strip().casefold().replace(" ", "-")


def _endpoint_taken(endpoint_id: str) -> bool:
    raw = _exact_config()
    providers = raw.get("providers")
    if providers is not None and not isinstance(providers, dict):
        fail(400, "invalid_body", "providers 不是映射，未覆盖写入")
    want = _alias(endpoint_id)
    if isinstance(providers, dict):
        for key, spec in providers.items():
            if _alias(str(key)) == want:
                return True
            if isinstance(spec, dict):
                name = spec.get("name")
                if isinstance(name, str) and _alias(name) == want:
                    return True
    legacy = raw.get("custom_providers")
    if isinstance(legacy, list):
        for item in legacy:
            if not isinstance(item, dict):
                continue
            for field in ("name", "provider_key"):
                value = item.get(field)
                if isinstance(value, str) and _alias(value) == want:
                    return True
    return False


def _model_slot(raw: dict[str, Any]) -> tuple[Any, ...]:
    model = raw.get("model")
    if isinstance(model, dict):
        return (
            model.get("default"),
            model.get("provider"),
            model.get("base_url"),
            "api_key" in model,
            "key_env" in model,
        )
    return (model, None, None, False, False)


def _rejected_secret(value: str) -> bool:
    return value.startswith("«redacted") or value == "***" or bool(_ENV_REF_RE.fullmatch(value) or _LEGACY_MASK_RE.fullmatch(value))


def _parse_base_url(value: Any) -> str:
    if not isinstance(value, str):
        fail(400, "invalid_body", "baseUrl 必须是字符串")
    base_url = value.strip().rstrip("/")
    if not base_url or len(base_url) > 2048 or any(char.isspace() for char in base_url):
        fail(400, "invalid_body", "baseUrl 不合法")
    try:
        parsed = urlparse(base_url)
    except ValueError:
        fail(400, "invalid_body", "baseUrl 不合法")
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        fail(400, "invalid_body", "baseUrl 必须是带主机名的 http 或 https 地址")
    if parsed.username or parsed.password or "@" in parsed.netloc:
        fail(400, "invalid_body", "baseUrl 不能携带用户信息")
    names = {name.lower() for name, _query in parse_qsl(parsed.query, keep_blank_values=True)}
    if names & _URL_SECRET_QUERY:
        fail(400, "invalid_body", "baseUrl 不能携带密钥参数")
    return base_url


def _parse_api_key(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        fail(400, "invalid_body", "apiKey 必须是字符串")
    key = value.strip()
    if not key:
        return None
    if len(key) > 4096 or not key.isascii() or any(ord(char) < 32 or ord(char) == 127 for char in key) or _rejected_secret(key):
        fail(400, "invalid_body", "apiKey 不合法，未写入")
    return key


def _parse_models(value: Any) -> list[str]:
    if not isinstance(value, list) or not 0 < len(value) <= 100 or not all(isinstance(item, str) and MODEL_ID_RE.match(item) for item in value):
        fail(400, "invalid_body", "models 需为 1–100 个合法模型名")
    if len(set(value)) != len(value):
        fail(400, "invalid_body", "models 含重复模型名")
    return list(value)


def _parse_endpoint_body(body: Any) -> _ParsedEndpoint:
    if not isinstance(body, dict) or set(body) - _ENDPOINT_FIELDS:
        fail(400, "invalid_body", "请求体必须只含 id、baseUrl、models，以及可选的 apiKey")
    endpoint_id = body.get("id")
    if not isinstance(endpoint_id, str) or not NAME_RE.match(endpoint_id):
        fail(400, "invalid_body", "端点 id 只允许字母、数字、下划线和连字符，且不超过 64 个字符")
    return _ParsedEndpoint(endpoint_id, _parse_base_url(body.get("baseUrl")), _parse_api_key(body.get("apiKey")), _parse_models(body.get("models")))


def _env_snapshot(name: str) -> Any:
    from hermes_cli.config import load_env

    env = load_env()
    return _MISSING if name not in env else env.get(name)


def _env_equals(name: str, expected: str) -> bool:
    seen = _env_snapshot(name)
    return isinstance(seen, str) and seen == expected


def _write_env(name: str, value: str) -> None:
    from hermes_cli.config import require_env_writable, save_env_value

    try:
        require_env_writable(name, "set")
    except ValueError as exc:
        log.warning("endpoint env slot refused: %s", type(exc).__name__)
        fail(403, "config_locked", "当前 profile 不允许写入该端点密钥槽，未保存")
    save_env_value(name, value, publish=False)
    if not _env_equals(name, value):
        fail(500, "endpoint_unverified", "密钥已提交但回读不一致，未返回密钥")


def _write_entry(mod: Any, parsed: _ParsedEndpoint, env_name: str) -> None:
    prefix = f"providers.{parsed.id}"
    commit_set(mod, f"{prefix}.name", parsed.id)
    commit_set(mod, f"{prefix}.base_url", parsed.base_url)
    commit_set(mod, f"{prefix}.models", json.dumps(parsed.models))
    if env_name:
        commit_set(mod, f"{prefix}.key_env", env_name)


def _verify_entry(parsed: _ParsedEndpoint, env_name: str) -> None:
    entry = _raw_providers().get(parsed.id)
    if not isinstance(entry, dict):
        fail(500, "endpoint_unverified", "回读没有该端点")
    if entry.get("name") != parsed.id or entry.get("base_url") != parsed.base_url or entry.get("models") != parsed.models:
        fail(500, "endpoint_unverified", "回读与提交不一致")
    if "api_key" in entry or entry.get("api_key_env"):
        fail(500, "endpoint_unverified", "端点出现了不允许回读的密钥字段，已撤回")
    if env_name:
        if entry.get("key_env") != env_name or not parsed.api_key or not _env_equals(env_name, parsed.api_key):
            fail(500, "endpoint_unverified", "密钥槽回读不一致，未返回密钥")
    elif entry.get("key_env"):
        fail(500, "endpoint_unverified", "未提交密钥但端点出现了密钥槽")


def _rollback(mod: Any, endpoint_id: str, config_started: bool, env_name: str, previous: Any, env_written: bool) -> None:
    if config_started:
        try:
            commit_unset(mod, f"providers.{endpoint_id}", missing_ok=True)
        except Exception as exc:
            log.warning("custom endpoint config rollback failed: %s %s", endpoint_id, type(exc).__name__)
    if not env_written or not env_name:
        return
    try:
        from hermes_cli.config import remove_env_value, save_env_value

        if previous is _MISSING:
            remove_env_value(env_name, publish=False)
        elif isinstance(previous, str):
            save_env_value(env_name, previous, publish=False)
    except Exception as exc:
        log.warning("custom endpoint env rollback failed: %s %s", env_name, type(exc).__name__)


def _public_result(endpoint_id: str, has_key: bool) -> dict[str, Any]:
    return {
        "ok": True,
        "saved": True,
        "applied": False,
        "processEnvChanged": False,
        "hotLoaded": False,
        "effectiveNow": False,
        "loadedNow": False,
        "restartRequired": False,
        "id": endpoint_id,
        "hasKey": has_key,
        "note": _NOTE,
    }


def create_custom_endpoint(mod: Any, body: Any) -> dict[str, Any]:
    """Create one ``providers.<id>`` entry. Does not set the default model.

    Integrator calls ``create_custom_endpoint(mod, body)`` with the JSON object
    ``{id, baseUrl, apiKey?, models}``. The key is written only to this profile's
    ``.env`` with ``publish=False``. The response has ``hasKey`` and never the key.
    """

    def _write() -> dict[str, Any]:
        parsed = _parse_endpoint_body(body)
        with write_lock(mod):
            panel_home(mod)
            if _endpoint_taken(parsed.id):
                fail(409, "endpoint_exists", "端点已存在，未覆盖")
            before = _model_slot(_exact_config())
            env_name = ""
            previous: Any = _MISSING
            env_written = False
            config_started = False
            try:
                if parsed.api_key:
                    from hermes_cli.config import custom_endpoint_key_env

                    env_name = custom_endpoint_key_env(parsed.id)
                    previous = _env_snapshot(env_name)
                    env_written = True
                    _write_env(env_name, parsed.api_key)
                config_started = True
                _write_entry(mod, parsed, env_name)
                _verify_entry(parsed, env_name)
                if _model_slot(_exact_config()) != before:
                    fail(500, "endpoint_unverified", "写入改变了主模型槽，已撤回")
            except BaseException:
                _rollback(mod, parsed.id, config_started, env_name, previous, env_written)
                raise
        return _public_result(parsed.id, bool(parsed.api_key))

    return run(mod, _write)
