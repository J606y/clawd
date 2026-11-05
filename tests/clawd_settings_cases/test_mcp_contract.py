"""MCP create contract. Not executed in the implementation pass.

These cases prepare the frozen ``create_mcp(mod, body)`` export. They do not
register routes, read production homes, or install from a catalog.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from clawd_server.admin_mcp import create_mcp
from hermes_constants import reset_hermes_home_override, set_hermes_home_override

ROOT = Path(__file__).resolve().parents[2]
SECRET = "env-secret-value-not-shown"


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _mod(home: Path | None):
    mod = type("Mod", (), {})()
    mod.ApiError = ApiError
    mod.HERMES_HOME = home
    mod._WRITE_LOCK = threading.Lock()
    mod.redact = lambda value: value
    return mod


def _expect(mod, body, status: int, code: str) -> ApiError:
    with pytest.raises(ApiError) as caught:
        create_mcp(mod, body)
    assert caught.value.status == status
    assert caught.value.code == code
    assert SECRET not in str(caught.value)
    return caught.value


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    home = tmp_path / "clawd-home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("CLAWD_HOME", str(home))
    monkeypatch.delenv("HERMES_MANAGED", raising=False)
    token = set_hermes_home_override(None)
    try:
        yield home
    finally:
        reset_hermes_home_override(token)


def test_source_exports_create_and_calls_real_save():
    text = (ROOT / "clawd_server" / "admin_mcp.py").read_text(encoding="utf-8")
    assert "def create_mcp(" in text
    assert "_save_mcp_server(name, wanted)" in text
    assert "record_mcp_install" not in text
    assert "mcp_catalog" not in text
    routes = (ROOT / "clawd_server" / "admin_routes.py").read_text(encoding="utf-8")
    assert "POST /mcp/servers" in routes
    assert "create_mcp" in routes
    ui = (ROOT / "apps" / "panel" / "src" / "react" / "pages" / "settings" / "mcp-tab.tsx").read_text(encoding="utf-8")
    assert "createMcp" in ui
    assert "McpCreateInput" in ui
    assert "mcp/servers" not in ui
    assert 'type="password"' in ui
    assert "clawd mcp add" not in ui
    assert "目录安装" in ui
    assert "测试连接" in ui
    assert "确认删除" in ui
    assert "data-off" in ui


def test_signature_is_mod_and_body():
    assert list(create_mcp.__code__.co_varnames[:2]) == ["mod", "body"]


def test_profile_field_is_not_used_to_guess(monkeypatch):
    called = []
    monkeypatch.setattr("hermes_cli.mcp_config._save_mcp_server", lambda *args, **kwargs: called.append(args))
    _expect(_mod(Path("/tmp/unused-clawd-home")), {
        "name": "demo", "transport": "stdio", "command": "server-bin", "profile": "default",
    }, 409, "profile_scope_unsafe")
    assert called == []


def test_catalog_install_is_rejected(monkeypatch):
    called = []
    monkeypatch.setattr("hermes_cli.mcp_config._save_mcp_server", lambda *args, **kwargs: called.append(args))
    _expect(_mod(None), {
        "name": "demo", "transport": "http", "url": "https://example.com/mcp", "catalog": "github",
    }, 400, "invalid_body")
    assert called == []


def test_secret_in_command_is_not_saved(monkeypatch):
    called = []
    monkeypatch.setattr("hermes_cli.mcp_config._save_mcp_server", lambda *args, **kwargs: called.append(args))
    _expect(_mod(Path("/tmp/unused-clawd-home")), {
        "name": "demo",
        "transport": "stdio",
        "command": "server-bin",
        "args": ["API_KEY=supersecretvalue"],
    }, 400, "mcp_secret_in_command")
    assert called == []
    assert "supersecretvalue" not in "命令、参数或地址里不能包含密钥明文"


def test_url_userinfo_is_secret(monkeypatch):
    called = []
    monkeypatch.setattr("hermes_cli.mcp_config._save_mcp_server", lambda *args, **kwargs: called.append(args))
    _expect(_mod(Path("/tmp/unused-clawd-home")), {
        "name": "demo",
        "transport": "http",
        "url": "https://user:supersecretvalue@example.com/mcp",
    }, 400, "mcp_secret_in_command")
    assert called == []


def test_missing_panel_home_does_not_guess_default(monkeypatch):
    called = []
    monkeypatch.setattr("hermes_cli.mcp_config._save_mcp_server", lambda *args, **kwargs: called.append(args))
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.delenv("CLAWD_HOME", raising=False)
    _expect(_mod(None), {"name": "demo", "transport": "stdio", "command": "server-bin"}, 409, "profile_scope_unsafe")
    assert called == []


def test_production_home_is_refused(monkeypatch):
    called = []
    monkeypatch.setattr("hermes_cli.mcp_config._save_mcp_server", lambda *args, **kwargs: called.append(args))
    home = Path("/root/.hermes")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("CLAWD_HOME", str(home))
    token = set_hermes_home_override(None)
    try:
        _expect(_mod(home), {"name": "demo", "transport": "stdio", "command": "server-bin"}, 409, "profile_scope_unsafe")
    finally:
        reset_hermes_home_override(token)
    assert called == []


def test_duplicate_does_not_overwrite(isolated, monkeypatch):
    store = {"demo": {"command": "old", "enabled": True}}
    called = []

    def get_servers(config=None):
        del config
        return dict(store)

    def save(name, cfg):
        called.append((name, cfg))
        return True

    monkeypatch.setattr("hermes_cli.mcp_config._get_mcp_servers", get_servers)
    monkeypatch.setattr("hermes_cli.mcp_config._save_mcp_server", save)
    _expect(_mod(isolated), {"name": "demo", "transport": "stdio", "command": "server-bin"}, 409, "mcp_exists")
    assert called == []
    assert store["demo"]["command"] == "old"


def test_create_writes_env_without_returning_it(isolated, monkeypatch):
    store: dict[str, dict] = {}

    def get_servers(config=None):
        del config
        return {name: dict(spec) for name, spec in store.items()}

    def save(name, cfg):
        store[name] = dict(cfg)
        return True

    monkeypatch.setattr("hermes_cli.mcp_config._get_mcp_servers", get_servers)
    monkeypatch.setattr("hermes_cli.mcp_config._save_mcp_server", save)
    result = create_mcp(_mod(isolated), {
        "name": "localbin",
        "transport": "stdio",
        "command": "server-bin",
        "args": ["serve"],
        "enabled": False,
        "env": {"GITHUB_TOKEN": SECRET},
    })
    dumped = json.dumps(result)
    assert result["ok"] is True
    assert result["saved"] is True
    assert result["applied"] is False
    assert result["processEnvChanged"] is False
    assert result["hotLoaded"] is False
    assert result["effectiveNow"] is False
    assert result["loadedNow"] is False
    assert result["restartRequired"] is False
    assert result["server"]["envKeys"] == ["GITHUB_TOKEN"]
    assert "env" not in result["server"]
    assert SECRET not in dumped
    assert store["localbin"]["env"]["GITHUB_TOKEN"] == SECRET
    assert store["localbin"]["enabled"] is False
    assert "profile" not in result


def test_core_rejection_is_not_success(isolated, monkeypatch):
    monkeypatch.setattr("hermes_cli.mcp_config._get_mcp_servers", lambda config=None: {})
    monkeypatch.setattr("hermes_cli.mcp_config._save_mcp_server", lambda name, cfg: False)
    _expect(_mod(isolated), {
        "name": "localbin", "transport": "stdio", "command": "server-bin",
    }, 400, "invalid_body")


def test_real_save_symbol_persists_under_proven_home(isolated, monkeypatch):
    monkeypatch.setattr(
        "hermes_cli.observability.shared_metrics_disabled.record_config_saved",
        lambda *args, **kwargs: None,
    )
    result = create_mcp(_mod(isolated), {
        "name": "localbin",
        "transport": "stdio",
        "command": "server-bin",
        "args": ["serve"],
        "env": {"GITHUB_TOKEN": SECRET},
    })
    config_path = isolated / "config.yaml"
    raw = config_path.read_text(encoding="utf-8")
    assert config_path.resolve().is_relative_to(isolated.resolve())
    assert "localbin" in raw
    assert SECRET in raw
    assert SECRET not in json.dumps(result)
    assert result["applied"] is False
    assert result["server"]["envKeys"] == ["GITHUB_TOKEN"]
