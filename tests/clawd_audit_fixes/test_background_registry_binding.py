"""Prepared regression cases; not executed while tests are forbidden."""
from types import SimpleNamespace

import pytest

from gateway.platforms.clawd_runtime_routes import background_view
from tools.process_registry import ProcessRegistryUnavailable


def test_standalone_requires_explicit_registry_binding():
    with pytest.raises(ProcessRegistryUnavailable):
        background_view("isolated-session", gateway_bound=False)


def test_bound_registry_uses_selected_parent():
    seen = []
    def read(session_id):
        seen.append(session_id)
        return [{"session_id": session_id, "id": "isolated-process"}]
    result = background_view("isolated-session", gateway_bound=False,
                             registry=SimpleNamespace(running_for_parent=read))
    assert seen == ["isolated-session"]
    assert result["processes"][0]["id"] == "isolated-process"


@pytest.mark.parametrize("registry", [object(), SimpleNamespace(running_for_parent=None),
                                    SimpleNamespace(running_for_parent=lambda _sid: None)])
def test_invalid_binding_is_not_an_empty_success(registry):
    with pytest.raises(ProcessRegistryUnavailable):
        background_view("isolated-session", gateway_bound=False, registry=registry)


def test_registry_read_error_is_preserved():
    def read(_session_id):
        raise OSError("isolated read failure")
    with pytest.raises(OSError):
        background_view("isolated-session", gateway_bound=False,
                        registry=SimpleNamespace(running_for_parent=read))
