"""Unrun checks for vendor PYTHONPATH and task-grant receipt wiring. Do not execute in this change."""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

from tools.browser_task_registry import RegistryError
from tools.browser_use_cli import (
    VendorPathError,
    _DAEMON_MODULE,
    _DEFAULT_TIMEOUT_S,
    _IPC_SHA256,
    _LICENSE_COPYRIGHT,
    _UNPATCHED_DAEMON_SHA256,
    _subprocess_pythonpath,
    cloud_autospawn_preamble,
    verify_vendor_tree,
)
import tools.browser_use_cli as bu


def test_pythonpath_prefixes_vendor_and_never_falls_back_to_site():
    path = _subprocess_pythonpath("/vendor/src", "/site/packages")
    assert path.split(os.pathsep) == ["/vendor/src", "/site/packages"]
    assert _subprocess_pythonpath("/vendor/src", None) == "/vendor/src"
    with pytest.raises(VendorPathError, match="refusing the unpatched package"):
        _subprocess_pythonpath("", "/site/packages")
    with pytest.raises(VendorPathError, match="refusing the unpatched package"):
        _subprocess_pythonpath("/same", "/same")


def test_vendor_failure_does_not_return_a_site_only_env(monkeypatch):
    def boom():
        raise VendorPathError("vendor tree failed; refusing the unpatched package")

    monkeypatch.setattr(bu, "verify_vendor_tree", boom)
    monkeypatch.setattr(bu, "_harness_credentials", lambda: {})
    monkeypatch.setattr("tools.browser_session.build_browser_env", lambda: {})
    with pytest.raises(VendorPathError, match="refusing the unpatched package"):
        bu._base_subprocess_env()


def test_incomplete_vendor_tree_refuses_without_a_site_path(tmp_path):
    root = tmp_path / "browser_harness_0_1_13"
    (root / "src" / "browser_harness").mkdir(parents=True)
    (root / "LICENSE").write_text("not the upstream copyright\n", encoding="utf-8")
    with pytest.raises(VendorPathError, match="refusing the unpatched package"):
        verify_vendor_tree(root)
    assert not any(part == "site-packages" for part in Path(str(root)).parts)


def test_locked_hashes_match_the_plan():
    assert _IPC_SHA256 == "b46ec6e55ddae9919ada069053dfa12a2555d33a832de57ef5eb3a457cec80b3"
    assert _UNPATCHED_DAEMON_SHA256 == "7f05f904e62af8c07153c34a1fd3d334acf5c5aded8d0ddab974596bb50a8c97"
    assert _LICENSE_COPYRIGHT == "Copyright (c) 2026 Browser Use"
    assert _DAEMON_MODULE == "browser_harness.daemon"
    assert _DEFAULT_TIMEOUT_S == 300


def test_checked_in_vendor_tree_passes_startup_file_checks():
    src = verify_vendor_tree()
    assert (src / "browser_harness" / "_ipc.py").is_file()
    assert (src / "browser_harness" / "daemon.py").is_file()


def test_preamble_reads_grant_and_compiles():
    text = cloud_autospawn_preamble("/tmp/receipt.txt", "/tmp/spool.txt", "cloud")
    ast.parse(text)
    assert "task_grant_for" in text
    assert "{'meta': 'capabilities'}" in text
    assert "Target.closeTarget" in text
    assert "Browser.close" not in text
    assert "vnc" not in text.lower()
    assert "_grant + chr(10)" in text
    assert "getTargets" in text  # pre-existing guard; must stay a refusal, not a scan


def test_missing_grant_closes_only_the_new_target(monkeypatch, tmp_path):
    receipt = tmp_path / "receipt.txt"
    spool = tmp_path / "spool.txt"
    receipt.write_text("cloud\n42\npage_1\n", encoding="utf-8")
    spool.write_text("", encoding="utf-8")
    closed = []
    registered = []
    monkeypatch.setattr("tools.browser_task_cdp.harness_exchange", lambda *_a, **_k: closed.append(_a[-1]) or {})
    monkeypatch.setattr("tools.browser_task_registry.register_created", lambda **kw: registered.append(kw))
    monkeypatch.setattr("tools.browser_task_registry.adopt_spool", lambda *_a, **_k: None)
    error = bu._register_autospawn_receipt({
        "receipt": receipt, "spool": spool, "expected_kind": "cloud",
        "task_id": "task_1", "session_id": "sess_1", "turn_id": "turn_1",
        "endpoint": "harness", "browser_id": "",
        "daemon_dir": "/tmp/daemon-dir", "daemon_stem": "bu-default",
    })
    assert "task grant" in error
    assert registered == []
    assert closed == [{"method": "Target.closeTarget", "params": {"targetId": "page_1"}}]
    assert "Browser.close" not in str(closed)
    assert receipt.read_text(encoding="utf-8") == ""


def test_spool_grants_register_per_page_and_leave_no_file(monkeypatch, tmp_path):
    grant = "a" * 32
    other = "b" * 32
    receipt = tmp_path / "receipt.txt"
    spool = tmp_path / "spool.txt"
    receipt.write_text(f"cloud\n42\npage_1\n{grant}\n", encoding="utf-8")
    spool.write_text(f"page_2\t{other}\n", encoding="utf-8")
    registered = []
    monkeypatch.setattr("tools.browser_task_cdp.harness_exchange", lambda *_a, **_k: {})
    monkeypatch.setattr("tools.browser_task_registry.register_created", lambda **kw: registered.append(kw))
    assert bu._register_autospawn_receipt({
        "receipt": receipt, "spool": spool, "expected_kind": "cloud",
        "task_id": "task_1", "session_id": "sess_1", "turn_id": "turn_1",
        "endpoint": "harness", "browser_id": "",
        "daemon_dir": "/tmp/daemon-dir", "daemon_stem": "bu-default",
    }) is None
    assert {item["target_id"]: item["task_grant"] for item in registered} == {
        "page_1": grant, "page_2": other,
    }
    assert spool.read_text(encoding="utf-8") == ""
    assert grant not in spool.read_text(encoding="utf-8")


def test_fourth_line_is_registered_as_task_grant(monkeypatch, tmp_path):
    grant = "a" * 32
    receipt = tmp_path / "receipt.txt"
    spool = tmp_path / "spool.txt"
    receipt.write_text(f"cloud\n42\npage_1\n{grant}\n", encoding="utf-8")
    spool.write_text("", encoding="utf-8")
    registered = []
    monkeypatch.setattr("tools.browser_task_cdp.harness_exchange", lambda *_a, **_k: {})
    monkeypatch.setattr("tools.browser_task_registry.register_created", lambda **kw: registered.append(kw))
    monkeypatch.setattr("tools.browser_task_registry.adopt_spool", lambda *_a, **_k: None)
    assert bu._register_autospawn_receipt({
        "receipt": receipt, "spool": spool, "expected_kind": "cloud",
        "task_id": "task_1", "session_id": "sess_1", "turn_id": "turn_1",
        "endpoint": "wss://browser.example/session", "browser_id": "cloud:sess-1",
        "daemon_dir": "/tmp/daemon-dir", "daemon_stem": "bu-default",
    }) is None
    assert registered[0]["task_grant"] == grant
    assert registered[0]["target_id"] == "page_1"


def test_unsupported_daemon_is_not_killed_unless_owned(monkeypatch):
    reloaded = []
    monkeypatch.setattr(bu, "_probe_grant_capabilities", lambda *_a: "unsupported")
    monkeypatch.setattr(bu, "_own_harness_daemon", lambda _env: False)
    monkeypatch.setattr(bu, "_reload_daemons", lambda daemons: reloaded.append(daemons))
    with pytest.raises(RegistryError) as exc:
        bu._require_task_grant_daemon({}, "/tmp/daemon-dir", "bu-default")
    assert exc.value.code == "daemon_grant_unsupported"
    assert reloaded == []

    monkeypatch.setattr(bu, "_own_harness_daemon", lambda _env: True)
    with pytest.raises(RegistryError):
        bu._require_task_grant_daemon({}, "/tmp/daemon-dir", "bu-default")
    assert reloaded  # existing --reload only; this call still did not create a page


def test_down_daemon_does_not_reload(monkeypatch):
    reloaded = []
    monkeypatch.setattr(bu, "_probe_grant_capabilities", lambda *_a: "down")
    monkeypatch.setattr(bu, "_reload_daemons", lambda daemons: reloaded.append(daemons))
    assert bu._require_task_grant_daemon({}, "/tmp/daemon-dir", "bu-default") is None
    assert reloaded == []


def test_leases_were_not_retuned():
    root = Path(__file__).resolve().parents[2]
    sidecar = (root / "clawd_server/panel_core.py").read_text(encoding="utf-8")
    native = (root / "clawd_server/browser_native.py").read_text(encoding="utf-8")
    assert "LIVE_LEASE_S = 45" in sidecar
    assert "_HARD_LEASE_S = 300.0" in native
