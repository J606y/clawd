"""Live continuation lifecycle. No panel, no model round-trip.

The executor is marked ``test-not-llm``. A completed run here is scheduling
evidence only. It is not an external-model acceptance.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from gateway.clawd_continuation_admit import TEST_EXECUTOR_MARKER
from gateway.clawd_continuation_decision import CONTINUATION_TEXT

REPO = Path(__file__).resolve().parents[2]
PY = REPO / ".venv" / "bin" / "python"
SECRET = "SECRET-PROMPT-DO-NOT-LEAK"
PRODUCTION_HOME = Path("/root/.hermes")


class MarkedExecutor:
    clawd_executor_marker = TEST_EXECUTOR_MARKER

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return {"final_response": "test-executor-not-llm"}, {"continuation_executor": TEST_EXECUTOR_MARKER}


def _isolate(monkeypatch, home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "tmp").mkdir(exist_ok=True)
    (home / "config.yaml").write_text("# isolated continuation probe; no secrets\nagent: {}\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CLAWD_HOME", str(home))
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("TMPDIR", str(home / "tmp"))
    monkeypatch.delenv("API_SERVER_KEY", raising=False)
    assert not str(home).startswith(str(PRODUCTION_HOME))


def _seed(db, session_id: str) -> None:
    db.create_session(session_id, source="api_server", model="grok-4.7")
    db.update_session_runtime_lock(
        session_id, model="grok-4.7", provider="sub2api",
        model_options={"reasoning_effort": "high"}, confirmed=True,
    )
    db.update_session_cwd(session_id, "/work/proj-a")
    db.append_message(session_id, "user", content=f"请处理 {SECRET}")
    db.append_message(session_id, "assistant", content="已派出")


async def _refuse_model(**_kwargs):
    raise AssertionError("continuation reached _run_agent; marked executor was required")


async def _boot(monkeypatch, home: Path):
    _isolate(monkeypatch, home)
    from gateway.config import PlatformConfig
    from gateway.platforms.api_server import APIServerAdapter

    adapter = APIServerAdapter(PlatformConfig(
        enabled=True, extra={"key": "isolated-continuation-probe-key", "host": "127.0.0.1", "port": 0},
    ))
    executor = MarkedExecutor()
    adapter.clawd_continuation_executor = executor
    adapter._run_agent = _refuse_model
    assert await adapter.connect(), "API server did not start in the isolated home"
    db = adapter._ensure_session_db()
    assert db is not None
    assert str(Path(db.db_path).resolve()).startswith(str(home.resolve()))
    assert PRODUCTION_HOME not in Path(db.db_path).resolve().parents
    return adapter, db, executor


async def _idle(adapter, timeout: float = 8) -> None:
    from gateway.clawd_continuation import service_for

    service = service_for(adapter)
    assert service is not None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pending = [task for task in service._tasks if not task.done()]
        runs = [task for task in adapter._active_run_tasks.values() if not task.done()]
        if not pending and not runs:
            return
        await asyncio.sleep(0.02)
    raise AssertionError("continuation tasks still running")


def _event(adapter, session_id: str) -> dict | None:
    from gateway.clawd_continuation import service_for

    service = service_for(adapter)
    assert service is not None
    event = service.status(session_id).get("last_event")
    return event if isinstance(event, dict) else None


@pytest.mark.asyncio
async def test_module_presence_is_not_a_start():
    from gateway.clawd_continuation import ready_fields

    fields = ready_fields(object())
    assert fields["continuation"] == "not-started"
    assert fields["continuationRunning"] is False


@pytest.mark.asyncio
async def test_live_adapter_admits_once_and_honors_gates(tmp_path, monkeypatch):
    import gateway.wake as wake

    self_posts = []

    async def _self_post(*_args, **_kwargs):
        self_posts.append(1)
        raise AssertionError("delegation completion must not self-POST a user turn")

    monkeypatch.setattr(wake, "_self_post_chat_completion", _self_post)
    adapter, db, executor = await _boot(monkeypatch, tmp_path / "live")
    from gateway.clawd_continuation import ready_fields, service_for
    from gateway.wake import persist_delegation_delivery

    try:
        live = ready_fields(adapter)
        assert live["continuation"] == "started"
        assert live["continuationRunning"] is True
        sid = "live-complete"
        _seed(db, sid)
        await persist_delegation_delivery(
            adapter, text="[ASYNC DELEGATION BATCH COMPLETE — deleg_live]\n结果",
            session_id=sid, evt={"delegation_id": "deleg_live", "results": [{"status": "completed"}]},
        )
        await _idle(adapter)
        assert len(executor.calls) == 1
        call = executor.calls[0]
        assert call["requested_model"] == "grok-4.7"
        assert call["requested_provider"] == "sub2api"
        assert call["confirmed_runtime_lock"] is True
        assert call["user_message"] == CONTINUATION_TEXT
        assert call["cwd"] == "/work/proj-a"
        assert SECRET not in call["user_message"]
        assert self_posts == []
        statuses = [item for item in adapter._run_statuses.values() if item.get("session_id") == sid]
        assert statuses
        assert statuses[-1]["status"] == "completed"
        assert statuses[-1]["continuation_executor"] == TEST_EXECUTOR_MARKER
        assert statuses[-1]["continuation_transport"] == "in_process"
        assert statuses[-1]["output"] == "test-executor-not-llm"
        assert _event(adapter, sid)["kind"] == "admitted"

        await persist_delegation_delivery(
            adapter, text="[ASYNC DELEGATION BATCH COMPLETE — deleg_live]\n结果",
            session_id=sid, evt={"delegation_id": "deleg_live", "results": [{"status": "completed"}]},
        )
        await _idle(adapter)
        assert len(executor.calls) == 1

        human = "human-after"
        _seed(db, human)
        db.append_delegation_delivery(
            human, "[ASYNC DELEGATION BATCH COMPLETE — deleg_human]\n结果",
            {"delegation_id": "deleg_human"},
        )
        db.append_message(human, "user", content="我来插话")
        from gateway.clawd_continuation import note_persisted_delivery
        note_persisted_delivery(adapter, human, {"delegation_id": "deleg_human"})
        await _idle(adapter)
        assert len(executor.calls) == 1
        assert (_event(adapter, human) or {}).get("kind") != "admitted"

        cancelled = "cancelled-session"
        _seed(db, cancelled)
        db.append_delegation_delivery(
            cancelled, "[ASYNC DELEGATION BATCH COMPLETE — deleg_cancel]\n结果",
            {"delegation_id": "deleg_cancel"},
        )
        adapter._run_statuses["run_cancel"] = {
            "status": "cancelled", "session_id": cancelled, "updated_at": time.time(),
        }
        note_persisted_delivery(adapter, cancelled, {"delegation_id": "deleg_cancel"})
        await _idle(adapter)
        assert len(executor.calls) == 1
        assert _event(adapter, cancelled)["detail"] == "cancelled"

        waiting = "approval-session"
        _seed(db, waiting)
        db.append_delegation_delivery(
            waiting, "[ASYNC DELEGATION BATCH COMPLETE — deleg_approval]\n结果",
            {"delegation_id": "deleg_approval"},
        )
        adapter._run_statuses["run_wait"] = {
            "status": "waiting_for_approval", "session_id": waiting, "updated_at": time.time(),
        }
        note_persisted_delivery(adapter, waiting, {"delegation_id": "deleg_approval"})
        await _idle(adapter)
        assert len(executor.calls) == 1
        assert _event(adapter, waiting)["detail"] == "approval_pending"
        assert self_posts == []
    finally:
        await adapter.disconnect()
        assert service_for(adapter) is None


@pytest.mark.asyncio
async def test_restart_recovers_unprocessed_notice_once(tmp_path, monkeypatch):
    home = tmp_path / "restart"
    _isolate(monkeypatch, home)
    from hermes_state import SessionDB

    db = SessionDB(db_path=home / "state.db")
    _seed(db, "restart-session")
    db.append_delegation_delivery(
        "restart-session", "[ASYNC DELEGATION BATCH COMPLETE — deleg_restart]\n结果",
        {"delegation_id": "deleg_restart"},
    )
    db.close()

    from gateway.clawd_continuation import service_for

    first, _db, executor = await _boot(monkeypatch, home)
    try:
        await _idle(first)
        assert len(executor.calls) == 1
        assert executor.calls[0]["requested_model"] == "grok-4.7"
        assert executor.calls[0]["user_message"] == CONTINUATION_TEXT
        assert SECRET not in executor.calls[0]["user_message"]
    finally:
        await first.disconnect()
        assert service_for(first) is None

    second, _db2, again = await _boot(monkeypatch, home)
    try:
        await _idle(second)
        assert again.calls == []
    finally:
        await second.disconnect()
        assert service_for(second) is None


@pytest.mark.asyncio
async def test_unmarked_executor_is_refused(tmp_path, monkeypatch):
    home = tmp_path / "unmarked"
    _isolate(monkeypatch, home)
    from hermes_state import SessionDB
    from gateway.platforms.api_server_run_idempotency import RunIdempotencyStore
    from gateway import clawd_continuation as continuation

    db = SessionDB(db_path=home / "state.db")
    _seed(db, "unmarked-session")
    ran = []

    class Adapter:
        def __init__(self):
            self._session_db = db
            self._run_statuses = {}
            self._run_idempotency_ids = set()
            self._run_idempotency_store = RunIdempotencyStore(str(home / "runs_idempotency.db"))
            self._run_owner_pid = os.getpid()
            self._run_owner_started = 1
            self._active_run_tasks = {}
            self.clawd_continuation_executor = lambda **_kwargs: ran.append(1)

        def _ensure_session_db(self):
            return db

        def _set_run_status(self, run_id, status, **fields):
            from gateway.platforms.api_server_runs import _set_run_status
            return _set_run_status(self, run_id, status, **fields)

        def _concurrency_limited_response(self):
            return None

        def _draining_response(self):
            return None

    adapter = Adapter()
    continuation.arm_adapter(adapter)
    try:
        db.append_delegation_delivery(
            "unmarked-session", "[ASYNC DELEGATION BATCH COMPLETE — deleg_unmarked]\n结果",
            {"delegation_id": "deleg_unmarked"},
        )
        decision = await continuation.service_for(adapter).evaluate("unmarked-session", db, profile="default")
        assert decision.action == "admit"
        assert ran == []
        assert continuation.service_for(adapter).status("unmarked-session")["last_event"]["detail"] == "unmarked_executor"
    finally:
        continuation.disarm_adapter(adapter)
        db.close()


def _request(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=5) as resp:
        return json.loads(resp.read().decode())


def test_panel_ready_reports_started_and_stop_leaves_no_listener(tmp_path):
    home = tmp_path / "panel-home"
    tmp = tmp_path / "panel-tmp"
    home.mkdir()
    tmp.mkdir()
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin"),
        "HOME": str(home),
        "CLAWD_HOME": str(home),
        "HERMES_HOME": str(home),
        "TMPDIR": str(tmp),
        "PYTHONPATH": str(REPO),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONHASHSEED": "0",
    }
    runtime = REPO / ".dev-home" / "tools"
    if runtime.is_dir():
        env["HERMES_RUNTIME_DIR"] = str(runtime)
    log_path = tmp_path / "panel.log"
    log = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [str(PY if PY.exists() else Path(sys.executable)), "-m", "clawd_server", "serve",
         "--home", str(home), "--host", "127.0.0.1", "--port", "0"],
        env=env, cwd=str(REPO), stdout=log, stderr=subprocess.STDOUT,
    )
    url = ""
    try:
        deadline = time.time() + 90
        while time.time() < deadline:
            if proc.poll() is not None:
                raise AssertionError(f"panel exited {proc.returncode}; see {log_path}")
            text = log_path.read_text(encoding="utf-8", errors="replace")
            for line in text.splitlines():
                if line.startswith("CLAWD_PANEL_READY"):
                    url = line.split("url=", 1)[1].strip()
                    break
            if url:
                break
            time.sleep(0.2)
        assert url, f"panel did not become ready; see {log_path}"
        ready = _request(url + "/clawd/ready")
        assert ready["legacyWatcher"] == "disabled"
        assert ready["continuation"] == "started", ready
        assert ready["continuationRunning"] is True
        assert ready["continuationModule"] == "present-not-started"
        text = log_path.read_text(encoding="utf-8", errors="replace")
        assert "legacy panel auto-continue watcher disabled" in text
        assert "auto-continue session" not in text
        assert str(PRODUCTION_HOME) not in text
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)
        log.close()
    assert proc.poll() is not None
    if url:
        port = int(url.rsplit(":", 1)[1])
        probe = socket.socket()
        probe.settimeout(1)
        try:
            assert probe.connect_ex(("127.0.0.1", port)) != 0
        finally:
            probe.close()
