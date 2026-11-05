"""Static contract for installed-plugin settings. Not executed in this change.

Does not import clawd_server, does not read a home, and does not call the writer.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_plugin_config_exports_use_the_real_writer_and_refuse_load():
    source = _text("clawd_server/admin_plugins.py")
    assert "def get_plugin_config(" in source
    assert "def set_plugin_config(" in source
    assert "save_plugin_settings(" in source
    assert "read_user_config_raw(" in source
    assert "load_config_readonly(" in source
    assert "panel_home(mod)" in source
    assert 'fail(403, "secret_key"' in source
    assert '"loadedNow": False' in source
    assert "activate_plugin_now(" not in source
    assert "broadcast_plugin_event(" not in source
    assert "restart_mcp_servers" not in source
    assert "os.environ" not in source


def test_frozen_route_calls_the_exports_and_does_not_claim_load():
    routes = _text("clawd_server/admin_routes.py")
    assert 'path == "/plugins/config"' in routes
    assert 'load_export' in routes or "_need(mod, \"admin_plugins\", \"get_plugin_config\"" in routes
    assert '"admin_plugins", "get_plugin_config"' in routes
    assert '"admin_plugins", "set_plugin_config"' in routes
    assert 'result["loadedNow"] = False' in routes
    panel = _text("apps/panel/src/react/pages/settings/plugins-tab.tsx")
    assert "getPluginConfig(" in panel
    assert "setPluginConfig(" in panel
    assert "已在本进程加载成功" not in panel
    assert 'className="skeleton"' in panel
    assert "clawd plugins" in panel


def test_bundled_plugin_manifests_in_tree_do_not_declare_config_schema():
    plugins = ROOT / "plugins"
    hits = []
    for path in plugins.rglob("plugin.yaml"):
        text = path.read_text(encoding="utf-8", errors="replace")
        if "config_schema" in text:
            hits.append(str(path.relative_to(ROOT)))
    for path in plugins.rglob("plugin.json"):
        text = path.read_text(encoding="utf-8", errors="replace")
        if "config_schema" in text:
            hits.append(str(path.relative_to(ROOT)))
    assert hits == []
