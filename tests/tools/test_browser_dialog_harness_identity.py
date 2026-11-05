"""Unrun checks for harness dialog identity and CDP grant registration. Do not execute in this change."""

from __future__ import annotations

import json
from types import SimpleNamespace

from tools.browser_cdp_tool import _cdp_on_harness, browser_cdp
from tools.browser_dialog_tool import _dialog_identity, _grants_match, browser_dialog
from tools.browser_supervisor_dialogs import PendingDialog

_GRANT = "a" * 32
_CONN = {
    "transport": "harness", "endpoint": "harness",
    "daemonDir": "/tmp/daemon-dir", "daemonStem": "bu-default", "daemonPid": 42,
    "browserId": "daemon:42",
}


def _patch_harness(monkeypatch, dialog, *, grant=_GRANT, owned=True, exchange_result=None):
    sent = []
    started = []

    def exchange(_directory, _stem, payload, timeout=8.0):
        sent.append(payload)
        return {} if exchange_result is None else exchange_result

    monkeypatch.setattr("tools.browser_task_registry.resolve_call_identity", lambda *_a: ("task_1", "sess_1", "turn_1"))
    monkeypatch.setattr("tools.browser_task_registry.task_connection", lambda _task: dict(_CONN))
    monkeypatch.setattr("tools.browser_task_registry.active_binding", lambda *_a: ("daemon:42", "harness") if owned else None)
    monkeypatch.setattr("tools.browser_task_registry.harness_task_grant", lambda *_a: grant)
    monkeypatch.setattr("tools.browser_task_cdp.ensure_harness_task_grants", lambda *_a: None)
    monkeypatch.setattr("tools.browser_task_cdp.harness_pending_dialog", lambda *_a: dialog)
    monkeypatch.setattr("tools.browser_task_cdp.harness_exchange", exchange)
    monkeypatch.setattr(
        "tools.browser_dialog_tool.SUPERVISOR_REGISTRY.get",
        lambda *_a: started.append(True) or (_ for _ in ()).throw(AssertionError("supervisor")),
    )
    return sent, started


def test_dialog_identity_requires_target_session_and_frame():
    assert _dialog_identity({"targetId": "page_1", "sessionId": "sid", "frameId": "frame"})["targetId"] == "page_1"
    assert _dialog_identity({"dialog": {"targetId": "page_1", "sessionId": "sid"}}) is None
    assert _dialog_identity(None) is None


def test_grant_match_uses_registry_when_dialog_omits_grant():
    assert _grants_match(_GRANT, "") is True
    assert _grants_match(_GRANT, _GRANT) is True
    assert _grants_match(_GRANT, "b" * 32) is False
    assert _grants_match("", "") is False


def test_harness_dialog_answers_only_with_identity_and_matching_grant(monkeypatch):
    sent, started = _patch_harness(monkeypatch, {
        "targetId": "page_1", "sessionId": "sid-1", "frameId": "frame-1",
        "dialogId": "d-1", "type": "alert", "message": "hi",
    })
    result = browser_dialog(action="accept", task_id="task_1", session_id="sess_1")
    body = json.loads(result)
    assert body["success"] is True
    assert started == []
    assert sent[0]["method"] == "Page.handleJavaScriptDialog"
    assert sent[0]["session_id"] == "sid-1"
    assert sent[0]["task_grant"] == _GRANT
    assert _GRANT not in result
    assert "wss://" not in result


def test_missing_frame_fails_closed_without_answering(monkeypatch):
    sent, started = _patch_harness(monkeypatch, {"targetId": "page_1", "sessionId": "sid-1"})
    result = browser_dialog(action="accept", task_id="task_1", session_id="sess_1")
    assert "dialog_unscoped" in result and "no target" in result
    assert sent == [] and started == []


def test_unowned_target_is_not_claimed(monkeypatch):
    sent, started = _patch_harness(monkeypatch, {
        "targetId": "user_page", "sessionId": "sid-user", "frameId": "user-frame",
    }, owned=False)
    result = browser_dialog(action="dismiss", task_id="task_1", session_id="sess_1")
    assert "dialog_unowned" in result
    assert sent == [] and started == []


def test_grant_mismatch_does_not_answer(monkeypatch):
    sent, _started = _patch_harness(monkeypatch, {
        "targetId": "page_1", "sessionId": "sid-1", "frameId": "frame-1", "task_grant": "b" * 32,
    })
    result = browser_dialog(action="accept", task_id="task_1", session_id="sess_1")
    assert "dialog_unowned" in result
    assert sent == []


def test_identityless_harness_dialog_is_not_answered(monkeypatch):
    started = []
    monkeypatch.setattr("tools.browser_task_registry.task_connection", lambda _task: dict(_CONN))
    monkeypatch.setattr(
        "tools.browser_dialog_tool.SUPERVISOR_REGISTRY.get",
        lambda *_a: started.append(True),
    )
    result = browser_dialog(action="accept", task_id="task_1")
    assert "session identity is required" in result
    assert started == []


def test_wss_without_daemon_locator_does_not_open_a_second_socket(monkeypatch):
    started = []
    monkeypatch.setattr("tools.browser_task_registry.resolve_call_identity", lambda *_a: ("task_1", "sess_1", "turn_1"))
    monkeypatch.setattr("tools.browser_task_registry.task_connection", lambda _task: {
        "endpoint": "wss://browser.example/session", "transport": "",
    })
    monkeypatch.setattr(
        "tools.browser_dialog_tool.SUPERVISOR_REGISTRY.get",
        lambda *_a: started.append(True),
    )
    result = browser_dialog(action="accept", task_id="task_1", session_id="sess_1")
    assert "no target" in result
    assert "wss://" not in result
    assert started == []


def test_non_harness_dialog_still_uses_the_supervisor(monkeypatch):
    answered = []
    dialog = PendingDialog("d-1", "alert", "hi", "", 0, "sid-owned", frame_id="owned-frame")
    supervisor = SimpleNamespace(
        snapshot=lambda: SimpleNamespace(pending_dialogs=(dialog,)),
        dialog_is_owned=lambda *_a: True,
        respond_to_dialog=lambda **kwargs: answered.append(kwargs) or {"ok": True, "dialog": {"type": "alert"}},
    )
    monkeypatch.setattr("tools.browser_task_registry.resolve_call_identity", lambda *_a: ("task_1", "sess_1", "turn_1"))
    monkeypatch.setattr("tools.browser_task_registry.task_connection", lambda _task: {
        "transport": "loopback", "endpoint": "http://127.0.0.1:9222",
    })
    monkeypatch.setattr("tools.browser_dialog_tool.SUPERVISOR_REGISTRY.get", lambda _task: supervisor)
    result = browser_dialog(action="accept", task_id="task_1", session_id="sess_1")
    assert json.loads(result)["success"] is True
    assert answered and answered[0]["action"] == "accept"


def test_cdp_create_registers_daemon_grant_and_does_not_scan(monkeypatch):
    registered = []
    monkeypatch.setattr("tools.browser_cdp_tool._harness_grant_probe", lambda *_a: "ok")
    monkeypatch.setattr("tools.browser_task_cdp.ensure_harness_task_grants", lambda *_a: None)
    monkeypatch.setattr("tools.browser_task_cdp.create_harness_target", lambda *_a, **_k: ("page_1", _GRANT))
    monkeypatch.setattr("tools.browser_task_registry.register_created", lambda **kw: registered.append(kw))
    result = _cdp_on_harness(
        _CONN, "Target.createTarget", {}, None, ("task_1", "sess_1", "turn_1"),
    )
    assert json.loads(result)["result"] == {"targetId": "page_1"}
    assert registered[0]["task_grant"] == _GRANT
    assert _GRANT not in result


def test_cdp_unsupported_daemon_does_not_create_or_kill(monkeypatch):
    created = []
    monkeypatch.setattr("tools.browser_cdp_tool._harness_grant_probe", lambda *_a: "unsupported")
    monkeypatch.setattr("tools.browser_task_cdp.create_harness_target", lambda *_a, **_k: created.append(True))
    result = _cdp_on_harness(_CONN, "Target.createTarget", {}, None, ("task_1", "sess_1", "turn_1"))
    assert "daemon_grant_unsupported" in result
    assert created == []


def test_browser_close_refusal_stays():
    closed = browser_cdp(method="Browser.close", task_id="task_1", session_id="sess_1")
    disposed = browser_cdp(method="Target.disposeBrowserContext", task_id="task_1", session_id="sess_1")
    assert "exclusive browser ownership" in closed
    assert "exclusive browser ownership" in disposed
    assert "wss://" not in closed
