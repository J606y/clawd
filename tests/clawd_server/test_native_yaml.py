"""Core parser must preserve the panel's fail-closed redaction contract."""
import pytest

from clawd_server.bind import load_sidecar
from clawd_server import yaml_adapter
from hermes_yaml import safe_load


@pytest.fixture
def panel(monkeypatch):
    mod = load_sidecar()
    monkeypatch.setattr(mod, "yaml", yaml_adapter)
    return mod


def test_nested_and_shared_secret_values_are_redacted(panel):
    text = "api_key: &secret private-value\ncopy: *secret\nmodel: safe-model\nnested:\n  password: private-password\n"
    result = panel._redact_yaml(text)
    assert "private-value" not in result
    assert "private-password" not in result
    assert safe_load(result)["model"] == "safe-model"


@pytest.mark.parametrize("text", [
    "x: 1\nx: 2\n", "x: &loop [*loop]\n", "x: !!python/object:builtins.object {}\n",
    "x: 1\n---\ny: 2\n", "? [a, b]\n: value\n", "x: [unterminated\n",
    "x: " + "[" * 40 + "0" + "]" * 40,
])
def test_unsafe_yaml_is_rejected_without_returning_source(panel, text):
    with pytest.raises(panel.ApiError) as exc:
        panel._redact_yaml(text)
    assert exc.value.code == "config_parse_failed"


def test_unicode_empty_and_size_limit(panel):
    assert panel._redact_yaml("") == ""
    assert safe_load(panel._redact_yaml("name: 工作\n"))["name"] == "工作"
    with pytest.raises(panel.ApiError):
        panel._redact_yaml("x" * 1_048_577)
