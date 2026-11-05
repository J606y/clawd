"""Static checks for native browser-target ownership. Not executed in this change."""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from clawd_server.browser_native import install
from tools.browser_task_cdp import CdpError, close_exact, http_base
from tools.browser_task_registry import (
    RegistryError, close_owned, profile_home, register_created, session_view,
)
from tools.browser_use_cli import _handle_browser_exec, ownership_preamble


def _home(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAWD_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_HOME", raising=False)
    from hermes_constants import set_hermes_home_override
    set_hermes_home_override(None)


def _sidecar():
    class ApiError(Exception):
        def __init__(self, status, code, message):
            self.status, self.code = status, code
            super().__init__(message)

    lease = {"until": 0.0}
    live = {"until": 0.0}

    def drop():
        lease.clear()
        lease["until"] = 0.0

    return SimpleNamespace(
        ApiError=ApiError, _LIVE_LOCK=threading.Lock(), _live_task_lease=lease, _live=live,
        LIVE_LEASE_S=45, _live_client_id=lambda value: value, _live_task_drop=drop,
        _live_task_expire=lambda: 0.0, _live_expire=lambda: 0.0,
        _live_task_check_renewal=lambda *args, **kwargs: None,
        _live_task_require_owner=lambda *args, **kwargs: None,
        _live_task_timeout=lambda *args, **kwargs: None, _LIVE_CAPPED_CLIENTS={},
        redact=lambda text: text, _LIVE_FRAME_SLOTS=threading.BoundedSemaphore(2),
    )


def test_profile_home_refuses_unset_and_production(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    monkeypatch.delenv("CLAWD_HOME", raising=False)
    with pytest.raises(RegistryError) as missing:
        profile_home()
    assert missing.value.code == "profile_unset"
    monkeypatch.setenv("CLAWD_HOME", "/root/.hermes")
    with pytest.raises(RegistryError) as banned:
        profile_home()
    assert banned.value.code == "profile_refused"


def test_registry_is_scoped_to_the_session(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    register_created(task_id="task_1", session_id="sess_1", turn_id="turn_1", target_id="page_1",
                     browser_id="/devtools/browser/aaa", endpoint="http://127.0.0.1:9222")
    view = session_view("sess_1")
    assert view is not None and view["targetId"] == "page_1"
    assert session_view("sess_other") is None


def test_close_skips_a_replaced_browser(monkeypatch):
    calls = []
    monkeypatch.setattr("tools.browser_task_cdp._json", lambda method, url: {
        "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/other"})
    monkeypatch.setattr("tools.browser_task_cdp._read", lambda method, url: calls.append(url))
    error = close_exact("http://127.0.0.1:9222", "page_1", "/devtools/browser/expected")
    assert "identity changed" in error
    assert calls == []


def test_close_owned_closes_only_the_registered_id(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    register_created(task_id="task_1", session_id="sess_1", turn_id="turn_1", target_id="page_1",
                     browser_id="/devtools/browser/aaa", endpoint="http://127.0.0.1:9222")
    seen = []
    monkeypatch.setattr("tools.browser_task_cdp.close_exact", lambda base, target_id, browser_id: seen.append(target_id) or "")
    close_owned("task_1")
    assert seen == ["page_1"]
    ended = session_view("sess_1", "task_1")
    assert ended is not None and ended["status"] == "ended"
    close_owned(None)
    close_owned("")


def test_http_base_rejects_vnc_and_missing_endpoint():
    with pytest.raises(CdpError):
        http_base("vnc://127.0.0.1:5900")
    with pytest.raises(CdpError):
        http_base("")


def test_preamble_does_not_use_the_shadow_store():
    text = ownership_preamble("target_1", "/tmp/clawd-spool.txt")
    assert "target_1" in text
    assert "live_task_store" not in text
    assert "PANEL_LIVE_TASK_DB" not in text
    assert "json/list" not in text


def test_handler_refuses_without_turn_identity():
    from tools.approval_context import _approval_session_id, _approval_turn_id
    _approval_session_id.set("")
    _approval_turn_id.set("")
    result = _handle_browser_exec({"code": "print(1)"}, task_id="task_1", session_id="sess_1")
    assert "refused" in result


def test_install_reads_registry_not_shadow_store(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    mod = _sidecar()
    install(mod)
    with pytest.raises(mod.ApiError) as retired:
        mod._live_task_store()
    assert retired.value.code == "live_registry_retired"
    frame = mod.live_task("sess_1")
    assert frame["status"] == "waiting"
    assert frame["image"] is None
    assert frame["targetId"] is None
