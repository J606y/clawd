"""P2-01 userSet follows the persisted path. Prepared only; do not treat import as acceptance."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SETTINGS = ROOT / "clawd_server" / "admin_settings.py"


def _source() -> str:
    return SETTINGS.read_text(encoding="utf-8")


def test_dump_wires_user_set_to_persisted_path():
    text = _source()
    assert text.count('"userSet": _user_set_keys(fields, raw, getattr(mod, "SETTINGS_KEY_MAP", _KEY_MAP)),') == 1
    assert "_dig(raw, key)" not in text
    assert "_KEY_MAP = {\"model_context_length\": \"model.context_length\"}" in text
    tree = ast.parse(text)
    helper = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_user_set_keys")
    dumped = ast.dump(helper)
    helper_src = ast.get_source_segment(text, helper) or ""
    assert "commit_set" not in dumped
    assert "commit_unset" not in dumped
    assert "bool(" not in helper_src
    assert "if _value" not in helper_src
    assert "if value" not in helper_src
    assert "if found:" in helper_src
    names = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    assert "_persisted_setting_path" in names
    assert "_user_set_keys" in names


def test_user_set_presence_without_writing():
    import sys

    sys.path.insert(0, str(ROOT))
    from clawd_server.admin_settings import _KEY_MAP, _user_set_keys

    assert _KEY_MAP == {"model_context_length": "model.context_length"}
    fields = {
        "model_context_length": {"type": "number"},
        "display.compact": {"type": "boolean"},
    }
    raw = {"model": {"context_length": 4096}, "display": {"compact": True}}
    before = {"model": {"context_length": 4096}, "display": {"compact": True}}
    assert _user_set_keys(fields, raw, _KEY_MAP) == ["model_context_length", "display.compact"]
    assert raw == before
    assert "context_length" not in raw

    absent = {"model_context_length": {"type": "number"}}
    assert _user_set_keys(absent, {}, _KEY_MAP) == []
    assert _user_set_keys(absent, {"model_context_length": 8192}, _KEY_MAP) == []
    assert _user_set_keys(absent, {"model": {}}, _KEY_MAP) == []
    assert _user_set_keys(absent, {"model": "name-only"}, _KEY_MAP) == []
    for value in (0, None, "", "unknown", 1.5, False):
        stored = {"model": {"context_length": value}}
        assert _user_set_keys(absent, stored, _KEY_MAP) == ["model_context_length"]
        assert stored["model"]["context_length"] == value
    compact = {"display.compact": {"type": "boolean"}}
    assert _user_set_keys(compact, {}, _KEY_MAP) == []
    assert _user_set_keys(compact, {"display": {}}, _KEY_MAP) == []
    for value in (False, 0, "", None):
        stored = {"display": {"compact": value}}
        assert _user_set_keys(compact, stored, _KEY_MAP) == ["display.compact"]
        assert stored["display"]["compact"] == value
    both = {"model": {"context_length": 4096}, "model_context_length": 1}
    assert _user_set_keys(absent, both, {"model_context_length": 12}) == []
    assert _user_set_keys(absent, both, {"model_context_length": "/tmp/context_length"}) == []
    assert _user_set_keys(absent, both, {"model_context_length": ""}) == []
    assert _user_set_keys(absent, both, None) == ["model_context_length"]
    assert _user_set_keys(absent, both, {}) == ["model_context_length"]
    assert "context_length" not in both
