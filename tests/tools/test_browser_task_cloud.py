"""Unrun checks for cloud, dialog, and OOPIF ownership. Do not execute in this change."""

from __future__ import annotations

from types import SimpleNamespace
import json

import pytest

from tools.browser_supervisor_dialogs import PendingDialog
from tools.browser_supervisor_frames import FrameInfo, dialog_target_ids, oopif_session_id, walk_to_top
from tools.browser_task_cdp import CdpError, validate_cloud_browser_id, validate_cloud_ws
from tools.browser_task_registry import RegistryError, register_created
from tools.browser_use_cli import cloud_autospawn_preamble, ownership_route


def _frame(frame_id, parent=None, *, oopif=False, session=None):
    return FrameInfo(frame_id, "https://example.com", "https://example.com", parent, oopif, session)


def test_ownership_route_keeps_autospawn_and_cloud():
    assert ownership_route("", "1") == "autospawn"
    assert ownership_route("wss://browser.example/devtools?sig=1", None) == "cloud"
    assert ownership_route("http://127.0.0.1:9222", None) == "loopback"
    assert ownership_route("", None) == "missing"


def test_cloud_ws_rejects_loopback_private_and_metadata(monkeypatch):
    monkeypatch.setattr("tools.browser_url_policy.url_is_private", lambda url: "10.1.1.1" in url)
    monkeypatch.setattr("tools.browser_url_policy.is_always_blocked_url", lambda url: "169.254.169.254" in url)
    with pytest.raises(CdpError):
        validate_cloud_ws("http://127.0.0.1:9222")
    with pytest.raises(CdpError):
        validate_cloud_ws("wss://127.0.0.1/devtools")
    with pytest.raises(CdpError):
        validate_cloud_ws("wss://10.1.1.1/devtools")
    with pytest.raises(CdpError):
        validate_cloud_ws("wss://169.254.169.254/latest")
    assert validate_cloud_ws("wss://browser.example/session?sig=1") == "wss://browser.example/session?sig=1"


def test_cloud_browser_id_comes_from_backend_return():
    assert validate_cloud_browser_id("cloud:sess-1") == "cloud:sess-1"
    with pytest.raises(CdpError):
        validate_cloud_browser_id("/devtools/browser/aaa")
    with pytest.raises(CdpError):
        validate_cloud_browser_id("cloud:")


def test_register_accepts_signed_endpoint_not_vnc(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAWD_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_HOME", raising=False)
    from hermes_constants import set_hermes_home_override
    set_hermes_home_override(None)
    monkeypatch.setattr("tools.browser_url_policy.url_is_private", lambda url: False)
    monkeypatch.setattr("tools.browser_url_policy.is_always_blocked_url", lambda url: False)
    register_created(task_id="task_1", session_id="sess_1", turn_id="turn_1", target_id="page_1",
                     browser_id="cloud:sess-1", endpoint="wss://browser.example/session?sig=1")
    with pytest.raises(RegistryError):
        register_created(task_id="task_1", session_id="sess_1", turn_id="turn_1", target_id="page_2",
                         browser_id="cloud:sess-1", endpoint="vnc://browser.example")


def test_dialog_and_oopif_require_a_proven_chain():
    top = _frame("top")
    child = _frame("child", "top", oopif=True, session="sid-child")
    orphan = _frame("orphan", oopif=True, session="sid-orphan")
    frames = {"top": top, "child": child, "orphan": orphan}
    owned = {"page_1": {"session_id": "sid-page", "top_frame_id": "top"}}
    assert walk_to_top(frames, "child") == "top"
    assert dialog_target_ids("sid-page", None, owned, frames) == ["page_1"]
    assert dialog_target_ids("sid-other", "child", owned, frames) == ["page_1"]
    assert dialog_target_ids("sid-other", "orphan", owned, frames) == []
    assert oopif_session_id("child", frames, {"top"}) == "sid-child"
    assert oopif_session_id("orphan", frames, {"top"}) is None
    assert oopif_session_id("missing", frames, {"top"}) is None


def test_autospawn_preamble_uses_daemon_receipt_not_environ():
    import ast

    text = cloud_autospawn_preamble("/tmp/receipt.txt", "/tmp/spool.txt", "cloud")
    tree = ast.parse(text)
    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if name not in {"cdp", "_orig_cdp", "_guarded_cdp"} or not node.args:
            continue
        arg = node.args[0]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            calls.append(arg.value)
    assert "/proc" not in text and "BU_CDP_WS" not in text
    assert "daemon_browser_kind" in text and "identify" in text
    assert text.count("_os.environ") == 1
    assert "_os.environ.get('BU_NAME', 'default')" in text
    assert "json/list" not in text and "json/version" not in text
    assert "Target.createTarget" in calls
    assert "Target.getTargets" in text  # refusal guard, not a page scan
    assert not any("getTargets" in item or "json/list" in item or "json/version" in item for item in calls)
    assert "chr(9) + _g" in text


def test_non_loopback_http_is_not_treated_as_local():
    assert ownership_route("https://browser.example/json/version", None) == "blocked"
    assert ownership_route("http://127.0.0.1:9222", None) == "loopback"


def test_dialog_tool_refuses_unregistered_dialog(monkeypatch):
    from tools.browser_dialog_tool import browser_dialog
    dialog = PendingDialog("d-1", "alert", "hi", "", 0, "sid-user", frame_id="user-frame")
    supervisor = SimpleNamespace(
        snapshot=lambda: SimpleNamespace(pending_dialogs=(dialog,)),
        dialog_is_owned=lambda *_args: False,
        dialog_on_created_page=lambda *_args: False,
        respond_to_dialog=lambda **_kwargs: {"ok": True, "dialog": {}},
    )
    monkeypatch.setattr("tools.browser_dialog_tool.SUPERVISOR_REGISTRY.get", lambda _task: supervisor)
    monkeypatch.setattr("tools.browser_task_registry.resolve_call_identity", lambda *_args: ("task_1", "sess_1", "turn_1"))
    result = browser_dialog(action="accept", task_id="task_1", session_id="sess_1")
    assert "not on a page this task created" in result or "no single dialog" in result


def test_identityless_dialog_does_not_answer_a_created_page(monkeypatch):
    from tools.browser_dialog_tool import browser_dialog
    answered = []
    dialog = PendingDialog("d-1", "alert", "hi", "", 0, "sid-owned", frame_id="owned-frame")
    supervisor = SimpleNamespace(
        snapshot=lambda: SimpleNamespace(pending_dialogs=(dialog,)),
        dialog_on_created_page=lambda *_args: True,
        respond_to_dialog=lambda **kwargs: answered.append(kwargs) or {"ok": True, "dialog": {}},
    )
    monkeypatch.setattr("tools.browser_dialog_tool.SUPERVISOR_REGISTRY.get", lambda _task: supervisor)
    result = browser_dialog(action="accept")
    assert answered == []
    assert "session identity is required" in result


def test_install_contract_is_unchanged():
    import inspect
    from clawd_server import browser_native
    assert inspect.signature(browser_native.install).parameters.keys() == {"mod"}
    assert browser_native._REQUIRED == (
        "ApiError", "_LIVE_LOCK", "_live_task_lease", "_live", "LIVE_LEASE_S", "_live_client_id",
        "_live_task_drop", "_live_task_expire", "_live_expire", "_live_task_check_renewal",
        "_live_task_require_owner", "_live_task_timeout", "_LIVE_CAPPED_CLIENTS", "redact",
        "_LIVE_FRAME_SLOTS",
    )


def test_plugin_allows_dialog_after_ownership_check():
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[2] / "apps/panel/plugins/browser-window-guard/__init__.py"
    spec = importlib.util.spec_from_file_location("browser_window_guard_cloud", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert "browser_dialog" in module._ALLOWED
    assert "browser_action" not in module._ALLOWED


def test_signed_supervisor_start_is_refused_without_connecting():
    from tools.browser_supervisor import SUPERVISOR_REGISTRY, open_owned_page

    with pytest.raises(RuntimeError, match="second websocket"):
        SUPERVISOR_REGISTRY.get_or_start(task_id="task_1", cdp_url="wss://browser.example/session")
    with pytest.raises(RuntimeError, match="second websocket"):
        open_owned_page("wss://browser.example/session", "cloud:sess-1", "task_1", "sess_1", "turn_1")


def test_browser_close_needs_exclusive_ownership():
    from tools.browser_cdp_tool import browser_cdp

    closed = browser_cdp(method="Browser.close", task_id="task_1", session_id="sess_1")
    disposed = browser_cdp(method="Target.disposeBrowserContext", task_id="task_1", session_id="sess_1")
    assert "exclusive browser ownership" in closed
    assert "exclusive browser ownership" in disposed
    assert "wss://" not in closed and "wss://" not in disposed


def test_vault_signed_session_does_not_open_a_second_socket(monkeypatch):
    from tools import browser_vault_tool

    started = []
    monkeypatch.setattr("tools.browser_task_registry.active_task_targets", lambda _task: [])
    monkeypatch.setattr("tools.browser_session.session_for_task", lambda _task: {"cdp_url": "wss://browser.example/session"})
    monkeypatch.setattr("tools.browser_supervisor.SUPERVISOR_REGISTRY.get", lambda *_args: None)
    monkeypatch.setattr(
        "tools.browser_supervisor.SUPERVISOR_REGISTRY.get_or_start",
        lambda *_args, **_kwargs: started.append(True),
    )
    assert browser_vault_tool._ensure_supervisor("task_1") is None
    assert started == []
    refused = browser_vault_tool._eval_js_secret("task_1", "SECRET-PASSWORD-VALUE")
    assert refused["error_type"] == "supervisor_required"
    assert "SECRET-PASSWORD-VALUE" not in json.dumps(refused)
    assert started == []


def test_vault_eval_reuses_the_registered_daemon_target(monkeypatch):
    from tools import browser_vault_tool

    browser_vault_tool._HARNESS_FOCUS.clear()
    page = {
        "targetId": "page_1", "browserId": "daemon:42", "sessionId": "sess_1",
        "endpoint": "harness", "transport": "harness",
        "daemonDir": "/tmp/daemon-dir", "daemonStem": "bu-default", "daemonPid": 42,
    }
    calls = []
    started = []
    monkeypatch.setattr(browser_vault_tool, "_vault_identity", lambda *_args, **_kwargs: ("task_1", "sess_1"))
    monkeypatch.setattr("tools.browser_task_registry.active_task_targets", lambda _task: [page])
    monkeypatch.setattr("tools.browser_session.session_for_task", lambda _task: {"cdp_url": "wss://browser.example/session"})
    monkeypatch.setattr(
        "tools.browser_supervisor.SUPERVISOR_REGISTRY.get_or_start",
        lambda *_args, **_kwargs: started.append(True),
    )

    def fake_eval(directory, stem, target_id, expression, expected_pid, timeout=10.0):
        calls.append((directory, stem, target_id, expression, expected_pid))
        return {"result": {"type": "string", "value": "https://example.com/login"}}

    monkeypatch.setattr("tools.browser_task_cdp.harness_page_eval", fake_eval)
    result = browser_vault_tool._eval_js("task_1", "window.location.href", session_id="sess_1")
    assert result == {"success": True, "result": "https://example.com/login"}
    assert calls == [("/tmp/daemon-dir", "bu-default", "page_1", "window.location.href", 42)]
    assert started == []
    assert "wss://" not in json.dumps(result)


def test_harness_dialog_is_not_answered_through_a_supervisor(monkeypatch):
    from tools.browser_dialog_tool import browser_dialog

    answered = []
    supervisor = SimpleNamespace(
        snapshot=lambda: SimpleNamespace(pending_dialogs=()),
        respond_to_dialog=lambda **kwargs: answered.append(kwargs) or {"ok": True, "dialog": {}},
    )
    monkeypatch.setattr("tools.browser_dialog_tool.SUPERVISOR_REGISTRY.get", lambda _task: supervisor)
    monkeypatch.setattr("tools.browser_task_registry.resolve_call_identity", lambda *_args: ("task_1", "sess_1", "turn_1"))
    monkeypatch.setattr("tools.browser_task_registry.task_connection", lambda _task: {
        "transport": "harness", "endpoint": "harness", "daemonDir": "/tmp/daemon-dir",
        "daemonStem": "bu-default", "daemonPid": 42, "targetId": "page_1", "browserId": "daemon:42",
    })
    result = browser_dialog(action="accept", task_id="task_1", session_id="sess_1")
    assert answered == []
    assert "no target" in result
    assert "wss://" not in result
