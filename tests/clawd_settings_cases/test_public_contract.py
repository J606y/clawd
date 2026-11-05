"""Static contract checks for frozen settings routes and secret policy.

Not a product probe. Do not treat a passing import of this file as acceptance.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]

# Same panel heuristic the sidecar uses. Copied here so the test does not import the sidecar.
_SECRET_NAME_RE = re.compile(
    r"(?:^|[_-])(?:api[_-]?keys?|keys?|secrets?|passw(?:or)?ds?|private[_-]?keys?|cookies?|authorization|token|hash(?:es)?|salts?|credentials?)$",
    re.I,
)


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        self.status = status
        self.code = code
        self.message = message
        super().__init__(message)


def _panel_secret_name(name: str) -> bool:
    return bool(_SECRET_NAME_RE.search(name))


def _panel_secret_key(key: str) -> bool:
    return any(_panel_secret_name(part) for part in key.split("."))


def _mod() -> SimpleNamespace:
    return SimpleNamespace(
        ApiError=ApiError,
        _is_secret_key=_panel_secret_key,
        _is_secret_name=_panel_secret_name,
        LOG_JOURNAL={"j-gateway": "hermes-gateway", "j-sidecar": "hermes-panel-sidecar"},
    )


def test_frozen_routes_are_registered_without_replacing_dispatch():
    from clawd_server.admin_routes import ROUTE_SPECS, install

    required = {
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
    }
    assert required <= set(ROUTE_SPECS)
    text = (ROOT / "clawd_server" / "admin_routes.py").read_text(encoding="utf-8")
    assert "Handler._dispatch =" not in text
    assert "subprocess" not in text

    calls = {}

    class Handler:
        def _dispatch(self, method: str) -> None:
            calls["dispatch"] = method

        def _route(self, method: str, path: str, query: dict[str, str]):
            calls["original"] = (method, path)
            return {"original": True}

        def _json_body(self):
            return {"name": "demo", "enabled": True}

    mod = _mod()
    mod.Handler = Handler
    assert install(mod) is True
    assert Handler.do_PUT.__name__ == "do_PUT"
    Handler().do_PUT()
    assert calls["dispatch"] == "PUT"
    assert Handler._dispatch is not Handler._route


def test_skill_route_calls_export_and_does_not_emit_501(monkeypatch):
    from clawd_server import admin_routes

    seen = {}

    def fake_export(module: str, name: str):
        assert (module, name) == ("admin_skills", "set_skill_enabled")

        def set_skill_enabled(mod, skill_name, enabled):
            del mod
            seen["args"] = (skill_name, enabled)
            return {"ok": True, "saved": True, "applied": False, "name": skill_name, "enabled": enabled}

        return set_skill_enabled

    monkeypatch.setattr(admin_routes, "load_export", fake_export)

    class Handler:
        def _dispatch(self, method: str) -> None:
            del method

        def _route(self, method: str, path: str, query: dict[str, str]):
            del method, path, query
            return {"original": True}

        def _json_body(self):
            return {"name": "demo", "enabled": False}

    mod = _mod()
    mod.Handler = Handler
    admin_routes.install(mod)
    result = Handler()._route("POST", "/skills/enabled", {})
    assert seen["args"] == ("demo", False)
    assert result["saved"] is True
    assert result["applied"] is False
    assert result.get("code") != "501"


def test_missing_advanced_export_is_not_an_empty_success(monkeypatch):
    from clawd_server import admin_routes

    monkeypatch.setattr(admin_routes, "load_export", lambda module, name: None)

    class Handler:
        def _dispatch(self, method: str) -> None:
            del method

        def _route(self, method: str, path: str, query: dict[str, str]):
            del method, path, query
            return {"original": True, "data": []}

        def _json_body(self):
            return {"key": "hooks", "value": {"a": 1}}

    mod = _mod()
    mod.Handler = Handler
    admin_routes.install(mod)
    handler = Handler()
    try:
        handler._route("POST", "/settings/advanced", {})
    except ApiError as exc:
        assert exc.status == 503
        assert exc.code == "admin_route_unbound"
        assert "空成功" in exc.message
    else:
        raise AssertionError("missing advanced export was treated as success")
    try:
        handler._route("GET", "/settings/advanced", {"key": "providers"})
    except ApiError as exc:
        assert exc.status == 403
        assert exc.code == "advanced_key_denied"
    else:
        raise AssertionError("models-tab key was accepted on the advanced route")


def test_plugin_config_passes_name_and_fields(monkeypatch):
    from clawd_server import admin_routes

    seen = {}

    def fake_export(module: str, name: str):
        assert module == "admin_plugins" and name == "set_plugin_config"

        def set_plugin_config(mod, plugin_name, fields):
            del mod
            seen["args"] = (plugin_name, fields)
            return {"ok": True, "saved": True, "applied": False, "loadedNow": False, "name": plugin_name}

        return set_plugin_config

    monkeypatch.setattr(admin_routes, "load_export", fake_export)

    class Handler:
        def _dispatch(self, method: str) -> None:
            del method

        def _route(self, method: str, path: str, query: dict[str, str]):
            del method, path, query
            return {"original": True}

        def _json_body(self):
            return {"name": "demo", "fields": {"enabled": True}, "token": "must-not-echo"}

    mod = _mod()
    mod.Handler = Handler
    admin_routes.install(mod)
    result = Handler()._route("PUT", "/plugins/config", {})
    assert seen["args"] == ("demo", {"enabled": True})
    assert "token" not in result
    assert result["loadedNow"] is False
    assert result["applied"] is False


def test_journal_source_is_501_and_not_an_empty_log():
    from clawd_server.admin_routes import dispatch_frozen

    try:
        dispatch_frozen(_mod(), SimpleNamespace(), "GET", "/logs", {"source": "j-gateway"})
    except ApiError as exc:
        assert exc.status == 501
        assert exc.code == "journal_host_boundary"
    else:
        raise AssertionError("journal source was not refused")


def test_secret_policy_unlocks_approved_false_locks_and_keeps_real_secrets():
    from clawd_server.admin_common import ConfigError, ensure_write_facts, secret_key

    mod = _mod()
    assert secret_key(mod, "security.redact_secrets") is False
    assert secret_key(mod, "secrets.bitwarden.enabled") is False
    assert secret_key(mod, "secrets.bitwarden.access_token_env") is False
    assert secret_key(mod, "secrets.onepassword.env") is True
    assert secret_key(mod, "dashboard.basic_auth.password_hash") is True
    assert secret_key(mod, "providers.demo.token") is True
    assert secret_key(mod, "auxiliary.voice_chat.api_key") is True
    bare = SimpleNamespace()
    try:
        secret_key(bare, "security.redact_secrets")
    except ConfigError as exc:
        assert exc.status == 503
        assert exc.code == "secret_policy_unavailable"
    else:
        raise AssertionError("missing secret policy was allowed")
    facts = ensure_write_facts({"ok": True})
    assert facts["saved"] is False
    assert facts["applied"] is False
    assert facts["hotLoaded"] is False
    kept = ensure_write_facts({"ok": True, "saved": True, "applied": False})
    assert kept["saved"] is True
    assert kept["applied"] is False


def test_plugin_write_rejects_the_get_field_array():
    from clawd_server.admin_routes import dispatch_frozen

    try:
        dispatch_frozen(
            _mod(),
            SimpleNamespace(_json_body=lambda: {"name": "demo", "fields": [{"key": "enabled", "type": "boolean"}]}),
            "PUT",
            "/plugins/config",
            {},
        )
    except ApiError as exc:
        assert exc.status == 400
        assert exc.code == "invalid_body"
    else:
        raise AssertionError("plugin config accepted the GET field array as a write")


def test_client_types_match_field_array_and_key_status():
    text = (ROOT / "apps" / "panel" / "src" / "react" / "lib" / "admin-api.ts").read_text(encoding="utf-8")
    assert "fields?: PluginConfigField[]" in text
    assert "setPluginConfig(name: string, fields: Record<string, unknown>)" in text
    assert "hasKey?: boolean | null" in text
    assert "valueSet: boolean" in text
    assert "apiKey?: string" not in text.split("export interface ConnectionTokenStatus", 1)[1].split("export interface ConnectionPlatformInfo", 1)[0]
