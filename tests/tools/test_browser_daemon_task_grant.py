"""Prepared checks for daemon task grants. Not executed in this change."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from tools.browser_task_cdp import (
    CdpError, close_harness_target, create_harness_target, ensure_harness_task_grants,
    harness_call, harness_pending_dialog,
)
from tools.browser_task_registry import (
    RegistryError, active_task_targets, adopt_spool, close_owned, harness_task_grant,
    register_created, registry_path, session_view, task_connection,
)

GRANT = "a" * 43
OTHER = "b" * 43
DIR = "/tmp/daemon-dir"
STEM = "bu-default"


def _home(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAWD_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_HOME", raising=False)
    from hermes_constants import set_hermes_home_override
    set_hermes_home_override(None)


def _register(grant: str = GRANT, target_id: str = "page_1", pid: int = 42) -> None:
    register_created(task_id="task_1", session_id="sess_1", turn_id="turn_1", target_id=target_id,
                     browser_id=f"daemon:{pid}", endpoint="harness", transport="harness",
                     daemon_dir=DIR, daemon_stem=STEM, daemon_pid=pid, task_grant=grant)


def _daemon(monkeypatch, replies: list[dict], pid: int = 42):
    calls: list[dict] = []

    def ping(directory, stem):
        assert directory == DIR and stem == STEM
        return {"pid": pid, "browser_kind": "cloud"}

    def exchange(directory, stem, payload, timeout=8.0):
        calls.append(payload)
        if not replies:
            raise AssertionError("unexpected daemon command")
        return replies.pop(0)

    monkeypatch.setattr("tools.browser_task_cdp.harness_ping", ping)
    monkeypatch.setattr("tools.browser_task_cdp.harness_exchange", exchange)
    return calls


def _capable() -> dict:
    return {"task_grants": True, "dialog_identity": True, "task_grant_version": 1}


def test_harness_registration_requires_a_daemon_grant(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    with pytest.raises(RegistryError) as missing:
        _register("")
    assert missing.value.code == "task_grant_required"
    assert GRANT not in str(missing.value)
    assert not registry_path().exists()
    with pytest.raises(RegistryError) as shaped:
        _register(GRANT + " leaked")
    assert shaped.value.code == "task_grant_required"
    assert "leaked" not in str(shaped.value)


def test_public_views_omit_the_stored_grant(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    _register()
    view = session_view("sess_1", "task_1")
    connection = task_connection("task_1")
    targets = active_task_targets("task_1")
    assert view is not None and connection is not None
    for payload in (view, connection, {"targets": targets}):
        text = json.dumps(payload)
        assert "task_grant" not in payload if isinstance(payload, dict) and "targets" not in payload else True
        assert "taskGrant" not in text
        assert GRANT not in text
    assert "task_grant" not in view and "task_grant" not in connection
    assert harness_task_grant("page_1", 42) == GRANT


def test_grant_lookup_rejects_old_pid_and_unknown_target(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    _register()
    with pytest.raises(RegistryError) as replaced:
        harness_task_grant("page_1", 41)
    assert replaced.value.code == "browser_replaced"
    assert GRANT not in str(replaced.value)
    with pytest.raises(RegistryError) as missing:
        harness_task_grant("page_other", 42)
    assert missing.value.code == "task_grant_required"
    with pytest.raises(RegistryError) as boolean_pid:
        harness_task_grant("page_1", True)  # type: ignore[arg-type]
    assert boolean_pid.value.code == "browser_replaced"


def test_loopback_registration_does_not_store_a_client_grant(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    register_created(task_id="task_1", session_id="sess_1", turn_id="turn_1", target_id="page_1",
                     browser_id="/devtools/browser/aaa", endpoint="http://127.0.0.1:9222",
                     task_grant=GRANT)
    view = session_view("sess_1")
    assert view is not None and view["targetId"] == "page_1"
    assert GRANT not in json.dumps(view)
    conn = sqlite3.connect(registry_path())
    try:
        stored = conn.execute("SELECT task_grant FROM created_targets").fetchone()[0]
    finally:
        conn.close()
    assert stored == ""


def test_cleanup_error_cannot_carry_the_grant(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    _register()
    monkeypatch.setattr("tools.browser_task_cdp.close_harness_target", lambda *args: GRANT)
    close_owned("task_1")
    ended = session_view("sess_1", "task_1")
    assert ended is not None and ended["status"] == "ended"
    assert GRANT not in ended["cleanupError"]
    assert GRANT not in json.dumps(ended)


def test_spool_does_not_stamp_one_grant_on_many_targets(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    path = tmp_path / "spool.txt"
    path.write_text("page_1\npage_2\n", encoding="utf-8")
    with pytest.raises(RegistryError) as exc:
        adopt_spool(path, task_id="task_1", session_id="sess_1", turn_id="turn_1",
                    browser_id="daemon:42", endpoint="harness", transport="harness",
                    daemon_dir=DIR, daemon_stem=STEM, daemon_pid=42, task_grant=GRANT)
    assert exc.value.code == "task_grant_required"
    assert GRANT not in str(exc.value)
    assert path.read_text(encoding="utf-8") == "page_1\npage_2\n"
    assert not registry_path().exists()


def test_spool_registers_each_page_grant_and_does_not_copy_one(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    path = tmp_path / "spool.txt"
    path.write_text(f"page_1\t{GRANT}\npage_2\t{OTHER}\n", encoding="utf-8")
    adopt_spool(path, task_id="task_1", session_id="sess_1", turn_id="turn_1",
                browser_id="daemon:42", endpoint="harness", transport="harness",
                daemon_dir=DIR, daemon_stem=STEM, daemon_pid=42, task_grant="c" * 43)
    assert path.read_text(encoding="utf-8") == ""
    conn = sqlite3.connect(registry_path())
    try:
        rows = {row[0]: row[1] for row in conn.execute("SELECT target_id, task_grant FROM created_targets")}
    finally:
        conn.close()
    assert rows == {"page_1": GRANT, "page_2": OTHER}
    view = session_view("sess_1", "task_1")
    assert view is not None and GRANT not in json.dumps(view) and OTHER not in json.dumps(view)


def test_capability_probe_is_exact_and_precedes_create(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    calls = _daemon(monkeypatch, [_capable()])
    ensure_harness_task_grants(DIR, STEM, 42)
    assert calls == [{"meta": "capabilities"}]
    calls = _daemon(monkeypatch, [{"error": "'method'"}])
    with pytest.raises(CdpError) as unsupported:
        create_harness_target(DIR, STEM, 42)
    assert unsupported.value.code == "daemon_grant_unsupported"
    assert calls == [{"meta": "capabilities"}]
    calls = _daemon(monkeypatch, [{"task_grants": True, "dialog_identity": True, "task_grant_version": True}])
    with pytest.raises(CdpError) as boolean_version:
        ensure_harness_task_grants(DIR, STEM, 42)
    assert boolean_version.value.code == "daemon_grant_unsupported"
    calls = _daemon(monkeypatch, [], pid=7)
    with pytest.raises(CdpError) as replaced:
        ensure_harness_task_grants(DIR, STEM, 42)
    assert replaced.value.code == "browser_replaced"
    assert calls == []


def test_create_returns_outer_grant_and_closes_only_an_unauthorized_target(monkeypatch):
    created = {"result": {"targetId": "page_new", "task_grant": OTHER}, "task_grant": GRANT}
    calls = _daemon(monkeypatch, [_capable(), created])
    assert create_harness_target(DIR, STEM, 42) == ("page_new", GRANT)
    assert calls[0] == {"meta": "capabilities"}
    assert calls[1]["method"] == "Target.createTarget"
    assert "task_grant" not in calls[1]
    assert calls[1]["params"] == {"url": "about:blank"}

    calls = _daemon(monkeypatch, [_capable(), {"result": {"targetId": "page_new", "task_grant": OTHER}},
                                   {"result": {"success": False}}])
    with pytest.raises(CdpError) as unauthorized:
        create_harness_target(DIR, STEM, 42, url="https://example.test/path")
    assert unauthorized.value.code == "task_grant_required"
    assert "close rejected" in str(unauthorized.value)
    assert OTHER not in str(unauthorized.value)
    assert calls[1]["params"] == {"url": "https://example.test/path"}
    assert calls[2] == {"method": "Target.closeTarget", "params": {"targetId": "page_new"}}
    assert not any(item.get("method") == "Browser.close" for item in calls)


def test_harness_call_attaches_registry_grant_and_rejects_create(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    _register()
    calls = _daemon(monkeypatch, [
        {"result": {"sessionId": "sid-1"}},
        {"result": {"data": "frame"}},
    ])
    result = harness_call(DIR, STEM, 42, "page_1", "Page.captureScreenshot", {"format": "jpeg"})
    assert result == {"data": "frame"}
    assert calls[0]["method"] == "Target.attachToTarget"
    assert calls[0]["task_grant"] == GRANT
    assert calls[0]["params"] == {"targetId": "page_1", "flatten": True}
    assert calls[1]["task_grant"] == GRANT
    assert "task_grant" not in calls[1]["params"]
    assert calls[1]["session_id"] == "sid-1"

    calls = _daemon(monkeypatch, [{"result": {}}])
    harness_call(DIR, STEM, "page_1", "Page.handleJavaScriptDialog", {"accept": True},
                 expected_pid=42, session_id="sid-daemon")
    assert calls == [{
        "method": "Page.handleJavaScriptDialog",
        "params": {"accept": True},
        "task_grant": GRANT,
        "session_id": "sid-daemon",
    }]
    calls = _daemon(monkeypatch, [])
    with pytest.raises(CdpError) as empty_session:
        harness_call(DIR, STEM, "page_1", "Page.handleJavaScriptDialog", {},
                     expected_pid=42, session_id="")
    assert empty_session.value.code == "target_invalid"
    assert calls == []
    with pytest.raises(CdpError) as created:
        harness_call(DIR, STEM, "", "Target.createTarget", {"url": "about:blank"})
    assert created.value.code == "task_grant_required"
    assert calls == []
    with pytest.raises(CdpError) as missing:
        harness_call(DIR, STEM, "page_other", "Input.dispatchMouseEvent", {"type": "mousePressed"},
                     expected_pid=42)
    assert missing.value.code == "task_grant_required"
    assert GRANT not in str(missing.value)


def test_close_uses_the_registry_grant_and_does_not_close_without_one(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    _register()
    calls = _daemon(monkeypatch, [{"result": {"success": True}}, {"error": "not found"}])
    assert close_harness_target(DIR, STEM, "page_1", 42) == ""
    assert calls[0]["method"] == "Target.closeTarget"
    assert calls[0]["task_grant"] == GRANT
    assert "task_grant" not in calls[0]["params"]
    assert calls[1] == {
        "method": "Target.getTargetInfo",
        "params": {"targetId": "page_1"},
        "task_grant": GRANT,
    }
    calls = _daemon(monkeypatch, [{"result": {"success": True}}, {"error": "task_grant_required"}])
    refused = close_harness_target(DIR, STEM, "page_1", 42)
    assert "close rejected" in refused
    assert GRANT not in refused
    assert calls[1]["task_grant"] == GRANT
    calls = _daemon(monkeypatch, [])
    assert "close rejected" in close_harness_target(DIR, STEM, "page_other", 42)
    assert calls == []
    assert "identity changed" in close_harness_target(DIR, STEM, "page_1", 7)
    assert calls == []


def test_pending_dialog_keeps_daemon_identity_and_drops_grant(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    _register()
    dialog = {"type": "alert", "message": "hi", "targetId": "page_1", "sessionId": "sid-1",
              "task_grant": GRANT}
    calls = _daemon(monkeypatch, [_capable(), {"dialog": dialog}])
    got = harness_pending_dialog(DIR, STEM, 42)
    assert got == {"type": "alert", "message": "hi", "targetId": "page_1", "sessionId": "sid-1"}
    assert "frameId" not in got
    assert calls[1] == {"meta": "pending_dialog"}
    assert "task_grant" not in calls[1]
    calls = _daemon(monkeypatch, [_capable(), {"dialog": None}])
    assert harness_pending_dialog(DIR, STEM, 42) is None
    calls = _daemon(monkeypatch, [_capable(), {"dialog": {"type": "alert", "targetId": "page_other"}}])
    with pytest.raises(CdpError) as unowned:
        harness_pending_dialog(DIR, STEM, 42)
    assert unowned.value.code == "task_grant_required"
    assert GRANT not in str(unowned.value)


def test_client_source_does_not_mint_a_grant_or_a_second_transport():
    root = Path(__file__).resolve().parents[2]
    cdp = (root / "tools" / "browser_task_cdp.py").read_text(encoding="utf-8")
    registry = (root / "tools" / "browser_task_registry.py").read_text(encoding="utf-8")
    assert "def create_harness_target(" in cdp
    assert "def harness_pending_dialog(" in cdp
    assert "def ensure_harness_task_grants(" in cdp
    assert "def harness_task_grant(" in registry
    assert "token_urlsafe" not in cdp and "token_urlsafe" not in registry
    assert "Browser.close" not in cdp
    assert "LIVE_LEASE_S" not in cdp and "_HARD_LEASE_S" not in cdp
    assert "LIVE_LEASE_S" not in registry and "_HARD_LEASE_S" not in registry
