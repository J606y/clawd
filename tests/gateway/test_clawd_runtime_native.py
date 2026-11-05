"""Static and pure checks for runtime native projection. Not executed by this change."""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def _source(relative: str) -> ast.AST:
    return ast.parse((ROOT / relative).read_text())


def test_api_trust_stamp_is_assigned_before_internal():
    tree = _source("gateway/session_ingress.py")
    stamps = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "event"
            and target.attr in {"internal", "_api_admitted_user_input"}
            for target in node.targets
        )
    ]
    assert stamps
    attrs = [target.attr for node in stamps for target in node.targets if isinstance(target, ast.Attribute)]
    assert attrs.index("_api_admitted_user_input") < attrs.index("internal")


def test_admission_observers_include_interim_and_reasoning():
    tree = _source("gateway/platforms/api_server_runs.py")
    execute = next(node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef) and node.name == "_execute_run")
    calls = [
        node for node in ast.walk(execute)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "observe_api_turn"
    ]
    assert len(calls) == 1
    keywords = {kw.arg for kw in calls[0].keywords}
    assert {"interim_assistant_callback", "reasoning_callback"} <= keywords


def test_display_kind_releases_only_the_private_stamp():
    from gateway.response_filters import display_kind_for_event

    ordinary = SimpleNamespace(internal=True, message_id="adm", metadata={}, _api_admitted_user_input=True)
    assert display_kind_for_event(ordinary) is None
    steer = SimpleNamespace(internal=True, message_id="adm", metadata={}, _api_admitted_user_input=True, _api_admitted_intent="steer")
    assert display_kind_for_event(steer) == "steer"
    spoof = SimpleNamespace(internal=True, message_id="fake", metadata={"_api_admitted_user_input": True})
    assert display_kind_for_event(spoof) == "internal_notification"
    heartbeat = SimpleNamespace(internal=True, message_id="adm", metadata={}, _api_admitted_user_input=True, _heartbeat_session_id="hb")
    assert display_kind_for_event(heartbeat) == "internal_notification"
    card = SimpleNamespace(
        internal=True, message_id="adm", _api_admitted_user_input=True,
        metadata={"display_kind": "process_complete", "display_text": "card"},
    )
    assert display_kind_for_event(card) == "process_complete"


def test_origin_is_not_edit_authorization_and_rejects_cross_session():
    from hermes_state_runtime import api_input_origin

    message = {"id": 7, "role": "user", "session_id": "sess", "platform_message_id": "adm"}
    admission = {
        "target_session_id": "sess", "principal_id": "api", "intent": "queue",
        "payload": {"api_turn_v1": {}},
    }
    assert api_input_origin(message, admission) == "api_queue"
    assert api_input_origin(message, {**admission, "target_session_id": "other"}) is None
    assert api_input_origin(message, {**admission, "payload": {"client": True}}) is None
    assert api_input_origin({**message, "role": "assistant"}, admission) is None


def test_unknown_lease_holder_stays_busy():
    from hermes_state_lease_read import lease_view
    from hermes_state_runtime import RuntimeStoreError

    assert lease_view("pid=1:pidns=9:turn=s:platform=api_server", 10, 20, 15, dead=False)["busy"] is True
    assert lease_view("legacy", 10, 20, 15, dead=False)["holder_state"] == "held"
    assert lease_view("legacy", 10, 20, 15, dead=True)["busy"] is False
    try:
        lease_view("legacy", 30, 10, 15, dead=False)
    except RuntimeStoreError:
        return
    raise AssertionError("corrupt lease must fail closed")


def test_edit_payload_does_not_accept_origin_as_authorization():
    from hermes_state_api_edit import validate_edit_payload
    from hermes_state_runtime import RuntimeStoreError

    payload = {
        "sessionId": "sess", "fromEnd": 1, "expectedContent": "hello",
        "expectedTimestamp": 1.5, "requestId": "12345678-1234-1234-1234-123456789abc",
        "model": "m", "provider": "p",
    }
    assert validate_edit_payload(payload)["sessionId"] == "sess"
    try:
        validate_edit_payload({**payload, "input_origin": "api_queue"})
    except RuntimeStoreError:
        return
    raise AssertionError("origin must not authorize an edit")


def test_runtime_routes_do_not_import_effort():
    tree = _source("gateway/platforms/clawd_runtime_routes.py")
    imported = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert not any("effort" in name for name in imported)
    server = _source("gateway/platforms/api_server.py")
    modules = [node.module or "" for node in ast.walk(server) if isinstance(node, ast.ImportFrom)]
    assert any(name.endswith("clawd_runtime_routes") for name in modules)
    assert not any("clawd_effort" in name for name in modules)
    assert any(name.endswith("clawd_project_routes") for name in modules)
