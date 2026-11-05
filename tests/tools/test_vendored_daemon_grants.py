"""Vendored daemon grant checks. Prepared only; this change does not run them."""

from __future__ import annotations

import ast
import asyncio
import hashlib
import os
import sys
import types
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
VENDOR = ROOT / "tools" / "vendor" / "browser_harness_0_1_13"
PKG = VENDOR / "src" / "browser_harness"
SDIST = ROOT / "artifacts" / "browser-daemon-source" / "browser_harness-0.1.13.tar.gz"

UNPATCHED = {
    "daemon.py": "7f05f904e62af8c07153c34a1fd3d334acf5c5aded8d0ddab974596bb50a8c97",
    "helpers.py": "5ea8560a01b74281833923720ffd23ca8e4f6b873c2419c821f7695a04091cf3",
}
PATCHED = {
    "daemon.py": "781609813fa0f0d20dda3c5a634d3bc6a01cc7403d8d7b303874903b15434d45",
    "helpers.py": "171744c2ff9c86321d1487c80eb975b433ee4aca9a02b26857fe11be40bd0a4a",
}
UNCHANGED = {
    "__init__.py": "b159e84a2701448a4905041f48b27bf6d26cc420b597b82e4fdeabbd7fe26c2f",
    "_ipc.py": "b46ec6e55ddae9919ada069053dfa12a2555d33a832de57ef5eb3a457cec80b3",
    "admin.py": "d2d21c753f06e9275ce32291ca7e2c20acb83fab50486fffdbbfbb0fb1a7da94",
    "auth.py": "e71acf31c28524f4b26f87770333d154c2182b70a9412be5276d982fa6c2cf9d",
    "macos.py": "e7fb08ba92af6976a58c6abc91e36eb625b978102ba03fee8f9f5911c88f5a74",
    "mcp_cli.py": "11d66e3168436bf9bf49c2ddd301c19ac7bed9e717fc907fbb5ff61c7c160ece",
    "paths.py": "ba3c6e4c91a8e5f8add837eedd51a1aa391f47a7d11c79db47ac8b8146267ad7",
    "recorder.py": "ccf987870ab88cff7dbefe586165eaac122bba53dc73813e8202d65c840814e8",
    "run.py": "cf2f5e52596c45686f0b4d606b89bce10a153293d2205523bfb246d161dc07b7",
    "SKILL.md": "2155ce650490e214734b2ea2f3b23f9dd12addedd0382e048a8e9d805ab507a7",
    "telemetry.py": "8d48b95afe4b149c2be115e0df18dd9f62496148a194277702d74829455cfa94",
    "video.py": "6112a7582cfc71f7a8db9d8c0f703cbbf6ec47b5c5464564725154ffc3e41142",
    "video_render.py": "96a2a69d3653d47f8a2de1b4b51d1b7deca4a12d163e03d7f4afcc262b2e4e04",
    "video-template.html": "e237f67c2636e6946fd853c286fba0df0e7b10cfa2c95d4a9bb08fdd9d24e933",
}
LICENSE_SHA = "5afa3d97bf1e6f998d587fa101e3fe0d0c416840b7fe77bcd6ae97690a334631"
SDIST_SHA = "284dc547a042c309feafd9a9f4a74b2a8651b7963ea3ac6cb2f2d64889f6a8f3"

SESSION_METHODS = (
    "Page.navigate",
    "Page.captureScreenshot",
    "Page.handleJavaScriptDialog",
    "Runtime.evaluate",
    "Input.dispatchMouseEvent",
    "DOM.getDocument",
    "DOM.getBoxModel",
    "Network.getCookies",
    "Network.getResponseBody",
    "Fetch.enable",
    "CSS.getComputedStyleForNode",
    "Debugger.enable",
    "Emulation.setDeviceMetricsOverride",
    "Overlay.enable",
    "Accessibility.getFullAXTree",
    "Storage.clearDataForOrigin",
    "IO.read",
    "Log.enable",
    "HeapProfiler.takeHeapSnapshot",
    "NotARealDomain.touch",
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fn_source(path: Path, name: str) -> str:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    nodes = list(tree.body)
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            nodes.extend(node.body)
    for node in nodes:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            segment = ast.get_source_segment(text, node)
            if segment:
                return segment
    raise AssertionError(f"{name} not found")


def test_vendor_tree_keeps_sdist_bytes_except_two_patches():
    assert _sha(SDIST) == SDIST_SHA
    license_text = (VENDOR / "LICENSE").read_bytes()
    assert _sha(VENDOR / "LICENSE") == LICENSE_SHA
    assert b"Copyright (c) 2026 Browser Use" in license_text
    assert b"Nous Research" not in license_text
    notice = (VENDOR / "NOTICE").read_text(encoding="utf-8")
    assert "Copyright (c) 2026 Browser Use" in notice
    assert "MIT" in notice
    assert SDIST_SHA in notice
    assert "daemon.py" in notice and "helpers.py" in notice
    for name, digest in UNCHANGED.items():
        assert _sha(PKG / name) == digest
    for name, digest in PATCHED.items():
        assert _sha(PKG / name) == digest
        assert digest != UNPATCHED[name]
    daemon_src = (PKG / "daemon.py").read_text(encoding="utf-8")
    helpers_src = (PKG / "helpers.py").read_text(encoding="utf-8")
    assert "hmac.compare_digest" in daemon_src
    assert "task_grant_required" in daemon_src
    assert "task_grant_mismatch" in daemon_src
    assert "browser_close_refused" in daemon_src
    assert "task_grants" in daemon_src and "dialog_identity" in daemon_src
    assert "task_grant_version" in daemon_src
    for domain in ("DOM", "Network", "Fetch", "CSS", "Debugger", "Emulation"):
        assert domain in daemon_src
    assert "Target.sendMessageToTarget" in daemon_src
    assert "Browser.getWindowForTarget" in daemon_src
    attach = _fn_source(PKG / "daemon.py", "attach_first_page")
    assert "token_urlsafe" not in attach
    assert "_issued_targets" not in attach
    assert "def task_grant_for(" in helpers_src
    assert '.get("result", {})' in helpers_src
    new_tab = _fn_source(PKG / "helpers.py", "new_tab")
    assert "about:blank" in new_tab
    assert "cur_url" in new_tab


class _FakeCDP:
    def __init__(self):
        self.calls = []
        self.raise_session = False

    async def send_raw(self, method, params=None, session_id=None):
        self.calls.append((method, params, session_id))
        if self.raise_session:
            raise RuntimeError("Session with given id not found")
        if method == "Target.createTarget":
            return {"targetId": "task-1"}
        if method == "Target.attachToTarget":
            return {"sessionId": "sess-1"}
        if method == "Target.getTargets":
            return {"targetInfos": []}
        if method == "Target.getTargetInfo":
            tid = (params or {}).get("targetId")
            return {"targetInfo": {"targetId": tid, "url": "https://example.test/", "title": "t", "type": "page"}}
        if method == "Target.closeTarget":
            return {"success": True}
        return {}


def _install_cdp_stub():
    client = types.ModuleType("cdp_use.client")

    class CDPClient:
        def __init__(self, *args, **kwargs):
            pass

    client.CDPClient = CDPClient
    package = types.ModuleType("cdp_use")
    package.client = client
    sys.modules["cdp_use"] = package
    sys.modules["cdp_use.client"] = client


@pytest.fixture(scope="module")
def vendored(tmp_path_factory):
    home = tmp_path_factory.mktemp("bh-home")
    keys = (
        "BH_HOME", "BH_RUNTIME_DIR", "BH_TMP_DIR", "BH_AGENT_WORKSPACE",
        "BH_CONFIG_DIR", "BU_NAME", "BU_BROWSER_ID", "BU_CDP_URL", "BU_CDP_WS",
    )
    saved_env = {key: os.environ.get(key) for key in keys}
    saved_modules = {
        name: sys.modules[name]
        for name in list(sys.modules)
        if name == "browser_harness" or name.startswith("browser_harness.")
        or name == "cdp_use" or name.startswith("cdp_use.")
    }
    saved_path = list(sys.path)
    os.environ["BH_HOME"] = str(home)
    os.environ["BH_RUNTIME_DIR"] = str(home / "runtime")
    os.environ["BH_TMP_DIR"] = str(home / "tmp")
    os.environ["BH_AGENT_WORKSPACE"] = str(home / "workspace")
    os.environ["BH_CONFIG_DIR"] = str(home / "config")
    os.environ["BU_NAME"] = "default"
    for key in ("BU_BROWSER_ID", "BU_CDP_URL", "BU_CDP_WS"):
        os.environ.pop(key, None)
    for name in list(sys.modules):
        if name == "browser_harness" or name.startswith("browser_harness.") or name == "cdp_use" or name.startswith("cdp_use."):
            del sys.modules[name]
    sys.path.insert(0, str(VENDOR / "src"))
    _install_cdp_stub()
    try:
        import browser_harness.daemon as daemon
        import browser_harness.helpers as helpers
        yield daemon, helpers
    finally:
        sys.path[:] = saved_path
        for name in list(sys.modules):
            if name == "browser_harness" or name.startswith("browser_harness.") or name == "cdp_use" or name.startswith("cdp_use."):
                del sys.modules[name]
        sys.modules.update(saved_modules)
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _daemon(daemon_mod):
    daemon = daemon_mod.Daemon()
    daemon.cdp = _FakeCDP()
    daemon.session = "user-sess"
    daemon.target_id = "user-target"
    return daemon


def _issue(daemon):
    created = asyncio.run(daemon.handle({
        "method": "Target.createTarget",
        "params": {"url": "about:blank"},
    }))
    grant = created["task_grant"]
    attached = asyncio.run(daemon.handle({
        "method": "Target.attachToTarget",
        "params": {"targetId": "task-1", "flatten": True},
        "task_grant": grant,
    }))
    attached_calls = list(daemon.cdp.calls)
    daemon.cdp.calls.clear()
    assert any(call[0] == "Page.enable" and call[2] == "sess-1" for call in attached_calls)
    assert not any(call[0] in {"DOM.enable", "Runtime.enable", "Network.enable"} for call in attached_calls)
    return grant, attached["result"]["sessionId"]


def test_capabilities_does_not_touch_cdp_or_secrets(vendored):
    daemon_mod, _helpers = vendored
    daemon = _daemon(daemon_mod)
    daemon.cdp = None
    response = asyncio.run(daemon.handle({
        "meta": "capabilities",
        "token": "secret-token",
        "task_grant": "secret-grant",
    }))
    assert response == {
        "task_grants": True,
        "dialog_identity": True,
        "task_grant_version": 1,
    }
    assert "secret" not in str(response)


def test_create_target_issues_sibling_grant_without_moving_default_page(vendored):
    daemon_mod, _helpers = vendored
    daemon = _daemon(daemon_mod)
    response = asyncio.run(daemon.handle({
        "method": "Target.createTarget",
        "params": {"url": "about:blank"},
        "task_id": "client-task",
        "owner": "client",
    }))
    assert response["result"] == {"targetId": "task-1"}
    assert "task_grant" not in response["result"]
    assert isinstance(response["task_grant"], str) and response["task_grant"]
    assert daemon.session == "user-sess"
    assert daemon.target_id == "user-target"
    assert daemon._issued_targets == {"task-1"}
    assert daemon._task_grants["task-1"] == response["task_grant"]
    assert "client-task" not in daemon._task_grants.values()


def test_attach_first_page_does_not_issue_grant(vendored):
    daemon_mod, _helpers = vendored
    daemon = _daemon(daemon_mod)
    asyncio.run(daemon.attach_first_page())
    assert daemon._task_grants == {}
    assert daemon._issued_targets == set()
    assert any(call[0] == "Target.createTarget" for call in daemon.cdp.calls)


@pytest.mark.parametrize("method", SESSION_METHODS)
def test_task_session_without_grant_sends_no_cdp(vendored, method):
    daemon_mod, _helpers = vendored
    daemon = _daemon(daemon_mod)
    grant, session_id = _issue(daemon)
    denied = asyncio.run(daemon.handle({
        "method": method,
        "params": {},
        "session_id": session_id,
    }))
    assert denied == {"error": "task_grant_required"}
    assert daemon.cdp.calls == []
    mismatch = asyncio.run(daemon.handle({
        "method": method,
        "params": {},
        "session_id": session_id,
        "task_grant": grant + "x",
    }))
    assert mismatch == {"error": "task_grant_mismatch"}
    assert daemon.cdp.calls == []
    allowed = asyncio.run(daemon.handle({
        "method": method,
        "params": {},
        "session_id": session_id,
        "task_grant": grant,
    }))
    assert allowed == {"result": {}}
    assert daemon.cdp.calls[-1][0] == method


def test_unknown_user_page_stays_compatible(vendored):
    daemon_mod, _helpers = vendored
    daemon = _daemon(daemon_mod)
    _issue(daemon)
    response = asyncio.run(daemon.handle({
        "method": "DOM.getDocument",
        "params": {"depth": 0},
        "session_id": "user-sess",
    }))
    assert response == {"result": {}}
    listed = asyncio.run(daemon.handle({"method": "Target.getTargets", "params": {}}))
    assert listed["result"]["targetInfos"] == []
    closed = asyncio.run(daemon.handle({
        "method": "Target.closeTarget",
        "params": {"targetId": "user-target"},
    }))
    assert closed == {"result": {"success": True}}


def test_target_and_session_params_are_gated(vendored):
    daemon_mod, _helpers = vendored
    daemon = _daemon(daemon_mod)
    grant, session_id = _issue(daemon)
    cases = (
        {"method": "Target.closeTarget", "params": {"targetId": "task-1"}},
        {"method": "Target.attachToTarget", "params": {"targetId": "task-1", "flatten": True}},
        {"method": "Target.activateTarget", "params": {"targetId": "task-1"}},
        {"method": "Target.sendMessageToTarget", "params": {"sessionId": session_id, "message": "{}"}},
        {"method": "Target.detachFromTarget", "params": {"sessionId": session_id}},
        {"method": "Browser.getWindowForTarget", "params": {"targetId": "task-1"}},
        {"method": "Target.getTargetInfo", "params": {"targetId": "task-1"}},
    )
    for req in cases:
        daemon.cdp.calls.clear()
        denied = asyncio.run(daemon.handle(req))
        assert denied == {"error": "task_grant_required"}
        assert daemon.cdp.calls == []
        req = dict(req)
        req["task_grant"] = "wrong"
        mismatch = asyncio.run(daemon.handle(req))
        assert mismatch == {"error": "task_grant_mismatch"}
        assert daemon.cdp.calls == []
    allowed = asyncio.run(daemon.handle({
        "method": "Target.sendMessageToTarget",
        "params": {"sessionId": session_id, "message": "{}"},
        "task_grant": grant,
    }))
    assert allowed["result"] == {}


def test_close_browser_and_page_never_send_cdp(vendored):
    daemon_mod, _helpers = vendored
    daemon = _daemon(daemon_mod)
    grant, _session_id = _issue(daemon)
    for method in ("Browser.close", "Target.disposeBrowserContext", "Page.close"):
        daemon.cdp.calls.clear()
        refused = asyncio.run(daemon.handle({
            "method": method,
            "params": {},
            "task_grant": grant,
        }))
        assert refused == {"error": "browser_close_refused"}
        assert daemon.cdp.calls == []


def test_set_session_to_task_page_requires_grant_and_keeps_default(vendored):
    daemon_mod, _helpers = vendored
    daemon = _daemon(daemon_mod)
    grant, session_id = _issue(daemon)
    denied = asyncio.run(daemon.handle({
        "meta": "set_session",
        "session_id": session_id,
        "target_id": "task-1",
    }))
    assert denied == {"error": "task_grant_required"}
    assert daemon.session == "user-sess"
    assert daemon.target_id == "user-target"
    assert daemon.cdp.calls == []
    switched = asyncio.run(daemon.handle({
        "meta": "set_session",
        "session_id": session_id,
        "target_id": "task-1",
        "task_grant": grant,
    }))
    assert switched == {"session_id": session_id}
    assert daemon.session == session_id
    assert daemon.target_id == "task-1"
    daemon.cdp.calls.clear()
    missing = asyncio.run(daemon.handle({"method": "Network.enable", "params": {}}))
    assert missing == {"error": "task_grant_required"}
    assert daemon.cdp.calls == []


def test_grant_or_lost_task_session_does_not_reattach_first_page(vendored):
    daemon_mod, _helpers = vendored
    daemon = _daemon(daemon_mod)
    grant, session_id = _issue(daemon)
    daemon._session_targets[session_id] = "task-1"
    daemon.session = session_id
    daemon.cdp.raise_session = True
    called = {"n": 0}

    async def boom(*_args, **_kwargs):
        called["n"] += 1
        raise AssertionError("attach_first_page")

    daemon.attach_first_page = boom
    lost = asyncio.run(daemon.handle({
        "method": "Page.navigate",
        "params": {"url": "https://example.test/"},
        "task_grant": grant,
    }))
    assert lost == {"error": "Session with given id not found"}
    assert called["n"] == 0
    daemon.session = "user-sess"
    daemon.target_id = "user-target"
    user_lost = asyncio.run(daemon.handle({
        "method": "Page.navigate",
        "params": {"url": "https://example.test/"},
        "session_id": "user-sess",
        "task_grant": grant,
    }))
    assert user_lost == {"error": "Session with given id not found"}
    assert called["n"] == 0


def test_user_default_session_loss_still_recovers(vendored):
    daemon_mod, _helpers = vendored
    daemon = _daemon(daemon_mod)
    daemon.cdp.raise_session = True
    called = {"n": 0}

    async def recover(*_args, **_kwargs):
        called["n"] += 1
        return None

    daemon.attach_first_page = recover
    lost = asyncio.run(daemon.handle({
        "method": "Page.navigate",
        "params": {"url": "https://example.test/"},
    }))
    assert lost == {"error": "Session with given id not found"}
    assert called["n"] == 1


def test_named_task_params_do_not_run_on_the_user_page(vendored):
    daemon_mod, _helpers = vendored
    daemon = _daemon(daemon_mod)
    grant, session_id = _issue(daemon)
    daemon.cdp.calls.clear()
    refused = asyncio.run(daemon.handle({
        "method": "Page.navigate",
        "params": {"url": "https://example.test/", "targetId": "task-1"},
        "session_id": "user-sess",
        "task_grant": grant,
    }))
    assert refused == {"error": "task_grant_mismatch"}
    assert daemon.cdp.calls == []
    assert daemon.session == "user-sess"
    assert daemon.target_id == "user-target"
    window = asyncio.run(daemon.handle({
        "method": "Browser.getWindowForTarget",
        "params": {"targetId": "task-1"},
        "task_grant": grant,
    }))
    assert window == {"error": "task_grant_mismatch"}
    assert daemon.cdp.calls == []
    allowed = asyncio.run(daemon.handle({
        "method": "Page.navigate",
        "params": {"url": "https://example.test/", "targetId": "task-1"},
        "session_id": session_id,
        "task_grant": grant,
    }))
    assert allowed == {"result": {}}
    assert daemon.cdp.calls[-1][0] == "Page.navigate"
    assert daemon.cdp.calls[-1][2] == session_id


def test_dialog_identity_uses_only_known_fields(vendored):
    daemon_mod, _helpers = vendored
    daemon = _daemon(daemon_mod)
    _issue(daemon)
    daemon._record_event("Page.javascriptDialogOpening", {
        "url": "https://example.test/task",
        "frameId": "frame-1",
        "message": "leave?",
        "type": "beforeunload",
        "hasBrowserHandler": False,
        "defaultPrompt": "",
    }, session_id="sess-1")
    dialog = daemon.dialog
    assert dialog["sessionId"] == "sess-1"
    assert dialog["targetId"] == "task-1"
    assert dialog["frameId"] == "frame-1"
    assert dialog["type"] == "beforeunload"
    assert dialog["message"] == "leave?"
    assert isinstance(dialog["dialogId"], str) and dialog["dialogId"]
    assert "task_grant" not in dialog
    daemon._record_event("Page.javascriptDialogClosed", {}, session_id="other-sess")
    assert daemon.dialog is dialog
    daemon._record_event("Page.javascriptDialogClosed", {}, session_id="sess-1")
    assert daemon.dialog is None
    daemon._record_event("Page.javascriptDialogOpening", {
        "message": "hi",
        "type": "alert",
    }, session_id=None)
    assert "sessionId" not in daemon.dialog
    assert "targetId" not in daemon.dialog
    assert "frameId" not in daemon.dialog
    daemon._record_event("Page.javascriptDialogClosed", {}, session_id="sess-1")
    assert daemon.dialog is not None
    daemon._record_event("Page.javascriptDialogClosed", {}, session_id=None)
    assert daemon.dialog is None
    pending = asyncio.run(daemon.handle({"meta": "pending_dialog"}))
    assert pending == {"dialog": None}


def test_auto_attach_maps_only_issued_targets(vendored):
    daemon_mod, _helpers = vendored
    daemon = _daemon(daemon_mod)
    grant, _session_id = _issue(daemon)
    daemon._record_event("Target.attachedToTarget", {
        "sessionId": "auto-sess",
        "targetInfo": {"targetId": "task-1", "type": "page"},
    }, session_id=None)
    denied = asyncio.run(daemon.handle({
        "method": "DOM.describeNode",
        "params": {},
        "session_id": "auto-sess",
    }))
    assert denied == {"error": "task_grant_required"}
    daemon._record_event("Target.attachedToTarget", {
        "sessionId": "user-auto",
        "targetInfo": {"targetId": "user-target", "type": "page"},
    }, session_id=None)
    allowed = asyncio.run(daemon.handle({
        "method": "DOM.describeNode",
        "params": {},
        "session_id": "user-auto",
    }))
    assert allowed == {"result": {}}
    assert "user-target" not in daemon._task_grants
    assert grant


def test_shutdown_closes_only_issued_targets(vendored):
    daemon_mod, _helpers = vendored
    daemon = _daemon(daemon_mod)
    _issue(daemon)
    daemon._issued_targets.add("task-1")
    asyncio.run(daemon._close_issued_targets())
    closed = [call for call in daemon.cdp.calls if call[0] == "Target.closeTarget"]
    assert closed == [("Target.closeTarget", {"targetId": "task-1"}, None)]
    assert not any(call[0] == "Target.getTargets" for call in daemon.cdp.calls)
    assert "user-target" not in {call[1].get("targetId") for call in closed}


def test_address_classes_do_not_stop_at_three_prefixes(vendored):
    daemon_mod, _helpers = vendored
    assert "DOM" in daemon_mod._SESSION_SCOPED_DOMAINS
    assert "Network" in daemon_mod._SESSION_SCOPED_DOMAINS
    assert "Fetch" in daemon_mod._SESSION_SCOPED_DOMAINS
    for method in ("DOM.getDocument", "Network.getCookies", "Fetch.enable", "NotARealDomain.touch"):
        assert daemon_mod.command_address_classes(method) == ("session", "target_param", "session_param")
    assert "target_param" in daemon_mod.command_address_classes("Target.sendMessageToTarget")
    assert "session_param" in daemon_mod.command_address_classes("Target.detachFromTarget")
    assert daemon_mod.command_address_classes("Browser.getWindowForTarget")[0] == "session"
    assert daemon_mod.command_address_classes("Page.close") == ("refused_close",)
    assert "Target.sendMessageToTarget" in daemon_mod._KNOWN_TARGET_PARAM_METHODS


def test_helpers_keep_grant_outside_cdp_result(vendored):
    _daemon_mod, helpers = vendored
    helpers._TASK_GRANTS.clear()
    helpers._SESSION_TARGETS.clear()
    helpers._DEFAULT_TASK_TARGET = None
    helpers._remember_task_grant(
        {"method": "Target.createTarget", "params": {"url": "about:blank"}},
        {"result": {"targetId": "task-1"}, "task_grant": "grant-1"},
    )
    assert helpers.task_grant_for("task-1") == "grant-1"
    assert helpers.task_grant_for("missing") is None
    helpers._remember_task_grant(
        {"method": "Target.attachToTarget", "params": {"targetId": "task-1", "flatten": True}},
        {"result": {"sessionId": "sess-1"}},
    )
    attached = helpers._attach_task_grant({
        "method": "DOM.getDocument",
        "params": {"depth": 0},
        "session_id": "sess-1",
    })
    assert attached["task_grant"] == "grant-1"
    plain = helpers._attach_task_grant({"method": "Target.getTargets", "params": {}})
    assert "task_grant" not in plain
    seen = {}
    original_send = helpers._send

    def fake_send(req, response_timeout=None):
        seen["req"] = req
        return {"result": {"targetId": "task-1"}, "task_grant": "grant-1"}

    helpers._send = fake_send
    try:
        assert helpers.cdp("Target.createTarget", url="about:blank") == {"targetId": "task-1"}
        assert "task_grant" not in seen["req"]
        switched = helpers._attach_task_grant({
            "meta": "set_session",
            "session_id": "sess-1",
            "target_id": "task-1",
        })
        assert switched["task_grant"] == "grant-1"
    finally:
        helpers._send = original_send
        helpers._TASK_GRANTS.clear()
        helpers._SESSION_TARGETS.clear()
        helpers._DEFAULT_TASK_TARGET = None
