"""Static contract for custom model endpoints. Not executed in this change.

Reads only the two owned source files. Does not import clawd, open config,
or read any .env.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODELS = ROOT / "clawd_server" / "admin_models.py"
TAB = ROOT / "apps" / "panel" / "src" / "react" / "pages" / "settings" / "models-tab.tsx"


def _function(path: Path, name: str) -> str:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            segment = ast.get_source_segment(source, node)
            assert segment
            return segment
    raise AssertionError(name)


def test_create_custom_endpoint_is_frozen_and_does_not_publish_or_default() -> None:
    source = MODELS.read_text(encoding="utf-8")
    tree = ast.parse(source)
    fn = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "create_custom_endpoint")
    assert [arg.arg for arg in fn.args.args] == ["mod", "body"]
    body = _function(MODELS, "create_custom_endpoint")
    assert "model.default" not in body
    assert "model.provider" not in body
    assert "os.environ" not in body
    public = _function(MODELS, "_public_result")
    assert '"hotLoaded": False' in public
    assert '"applied": False' in public
    assert '"effectiveNow": False' in public
    assert '"processEnvChanged": False' in public
    assert '"hasKey"' in public
    assert "apiKey" not in public
    assert "api_key" not in public
    assert "publish=False" in source
    assert "endpoint_exists" in source


def test_models_tab_does_not_refill_key_or_add_default_entry() -> None:
    text = TAB.read_text(encoding="utf-8")
    assert "setDefaultModel" not in text
    assert "canonicalModel" not in text
    assert "ultra" not in text
    assert "models/custom-endpoints" in text
    assert 'autoComplete="new-password"' in text
    assert "value={secret}" in text
    assert "provider.apiKey" not in text
    assert "provider.key" not in text
    assert "hasKey" in text
    assert "不设为默认模型" in text
