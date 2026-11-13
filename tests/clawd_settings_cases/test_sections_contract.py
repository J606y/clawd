"""Static contract for tool, memory, and connection settings.

Does not import product modules, read a profile, or contact a service.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

PY = {
    "tools": ROOT / "clawd_server" / "admin_tools.py",
    "memory": ROOT / "clawd_server" / "admin_memory.py",
    "connections": ROOT / "clawd_server" / "admin_connections.py",
}
TSX = {
    "tools": ROOT / "apps" / "panel" / "src" / "react" / "pages" / "settings" / "tools-section.tsx",
    "memory": ROOT / "apps" / "panel" / "src" / "react" / "pages" / "settings" / "memory-section.tsx",
    "connections": ROOT / "apps" / "panel" / "src" / "react" / "pages" / "settings" / "connections-section.tsx",
}
REPORT = ROOT / "docs" / "clawd" / "settings-sections-implementation.md"

FORBIDDEN = (
    "save_env_value",
    "_save_submitted_secrets",
    "_write_provider_flat(",
    "_write_provider_honcho(",
    "_update_memory_provider_config(",
    "_profile_scope",
    "get_active_profile_name",
    "os.environ",
    "post_setup",
    "post-setup",
    "onboarding",
    "pairing",
    "/api/env/reveal",
    '"applied": True',
    "applied=True",
)


def _text(path: Path) -> str:
    assert path.is_file(), path
    return path.read_text(encoding="utf-8")


def _defs(path: Path) -> set[str]:
    tree = ast.parse(_text(path))
    return {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}


def test_frozen_python_exports():
    assert {"list_toolsets", "set_toolset_enabled"} <= _defs(PY["tools"])
    assert {"list_providers", "set_provider_config"} <= _defs(PY["memory"])
    assert {"list_platforms", "set_platform_enabled"} <= _defs(PY["connections"])


def test_saved_is_not_called_applied():
    for path in PY.values():
        text = _text(path)
        assert '"applied": False' in text
        assert '"effectiveNow": False' in text
        assert "panel_home" in text
        assert '"restartRequired": False' in text
        for banned in FORBIDDEN:
            assert banned not in text, f"{path.name} contains {banned}"


def test_error_codes_and_secret_boundaries():
    tools = _text(PY["tools"])
    memory = _text(PY["memory"])
    connections = _text(PY["connections"])
    assert "tools_unavailable" in tools and "toolset_not_found" in tools
    assert "memory_unavailable" in memory and "secret_key" in memory and "valueSet" in memory
    assert "connections_unavailable" in connections and "platform_not_found" in connections
    assert "valueSet" in connections
    assert "redacted_value" not in connections
    assert "没有切换 memory.provider" in memory


def test_frozen_components_call_panel_core_paths():
    expected = {
        "tools": ("export function ToolsSection", "panel-core/", "tools/toolsets", "tools/toolsets/enabled"),
        "memory": ("export function MemorySection", "panel-core/", "memory/providers", "memory/providers/config"),
        "connections": ("export function ConnectionsSection", "panel-core/", "connections/platforms", "connections/platforms/enabled"),
    }
    for key, needles in expected.items():
        text = _text(TSX[key])
        for needle in needles:
            assert needle in text
        assert "applied === true" in text
        assert 'type="password"' not in text
        assert "开始配对" not in text
        assert "post-setup" not in text


def test_report_records_static_only():
    text = _text(REPORT)
    assert "未运行" in text
    assert "config-tab" in text
    assert str(REPORT) in text or REPORT.name in text
