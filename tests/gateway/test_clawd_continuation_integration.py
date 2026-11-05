"""Continuation wired through SessionDB, the delivery hook, and run admission."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from gateway.clawd_continuation_decision import CONTINUATION_TEXT
from gateway.wake import persist_delegation_delivery

SECRET = "SECRET-PROMPT-DO-NOT-LEAK"
SID = "api-session-cont"


class _Adapter:
    def __init__(self, db, store):
        self._session_db = db
        self._run_statuses = {}
        self._run_idempotency_ids = set()
        self._run_idempotency_store = store
        self._run_owner_pid = os.getpid()
        self._run_owner_started = 1
        self._active_run_tasks = {}
        self.calls = []
        self.name = "api_server"
        self._host = "127.0.0.1"
        self._port = 9
        self._model_name = "hermes-agent"
        self.closed = False
        self.tasks = []

    def _ensure_session_db(self):
        return self._session_db

    def _set_run_status(self, run_id, status, **fields):
        from gateway.platforms.api_server_runs import _set_run_status
        return _set_run_status(self, run_id, status, **fields)

    def _concurrency_limited_response(self):
        return None

    def _draining_response(self):
        return None

    def _track_background_task(self, task):
        self.tasks.append(task)

    def _check_auth(self, _request):
        return None

    def _close_cached_session_dbs(self):
        self.closed = True

    async def _heartbeat_loop(self):
        return None

    async def _run_agent(self, **kwargs):
        self.calls.append(kwargs)
        return {"final_response": "continued"}, {}


def _db(tmp_path):
    from hermes_state import SessionDB
    db = SessionDB(db_path=tmp_path / "state.db")
    db.create_session(SID, source="api_server", model="grok-4.7")
    db.update_session_runtime_lock(
        SID, model="grok-4.7", provider="sub2api",
        model_options={"reasoning_effort": "high"}, confirmed=True,
    )
    db.update_session_cwd(SID, "/work/proj-a")
    db.append_message(SID, "user", content=f"请处理 {SECRET}")
    db.append_message(SID, "assistant", content="已派出")
    return db


def _store(tmp_path):
    from gateway.platforms.api_server_run_idempotency import RunIdempotencyStore
    return RunIdempotencyStore(str(tmp_path / "runs_idempotency.db"))


@pytest.mark.asyncio
async def test_delivery_hook_continues_without_a_panel(tmp_path):
    from gateway import clawd_continuation as continuation
    db = _db(tmp_path)
    adapter = _Adapter(db, _store(tmp_path))
    continuation.arm_adapter(adapter)
    service = continuation.service_for(adapter)
    assert service is not None and service.running
    try:
        await persist_delegation_delivery(
            adapter, text="[ASYNC DELEGATION BATCH COMPLETE — deleg_live]\n结果",
            session_id=SID, evt={"delegation_id": "deleg_live", "results": [{"status": "completed"}]},
        )
        for _ in range(20):
            if adapter.calls:
                break
            await asyncio.sleep(0)
        assert adapter.calls, "continuation did not admit after the delivery row"
        call = adapter.calls[0]
        assert call["requested_model"] == "grok-4.7"
        assert call["requested_provider"] == "sub2api"
        assert call["model_options"] == {"reasoning_effort": "high"}
        assert call["confirmed_runtime_lock"] is True
        assert call["user_message"] == CONTINUATION_TEXT
        assert call["session_id"] == SID
        assert call["gateway_session_key"] == SID
        assert call["cwd"] == "/work/proj-a"
        assert SECRET not in call["user_message"]
        assert service.status(SID)["last_event"]["kind"] == "admitted"
        await persist_delegation_delivery(
            adapter, text="[ASYNC DELEGATION BATCH COMPLETE — deleg_live]\n结果",
            session_id=SID, evt={"delegation_id": "deleg_live", "results": [{"status": "completed"}]},
        )
        await asyncio.sleep(0)
        assert len(adapter.calls) == 1
    finally:
        continuation.disarm_adapter(adapter)
        db.close()


@pytest.mark.asyncio
async def test_get_status_does_not_admit(tmp_path):
    from gateway.platforms import api_server_runs
    db = _db(tmp_path)
    adapter = _Adapter(db, _store(tmp_path))
    handler = api_server_runs._continuation_status_handler(adapter)

    class Request:
        match_info = {"session_id": SID}

    before = dict(adapter._run_statuses)
    response = await handler(Request())
    assert response.status == 200
    assert adapter._run_statuses == before
    assert adapter.calls == []
    listed = api_server_runs._http_routes(type("Routes", (), {
        "_handle_runs": None, "_handle_get_run": None, "_handle_run_events": None,
        "_handle_run_approval": None, "_handle_run_clarify": None, "_handle_steer_run": None,
        "_handle_resolve_unknown_run": None, "_handle_stop_run": None,
    })())
    assert any(path.endswith("/continuation") and method == "GET" for method, path, _handler in listed)
    db.close()


@pytest.mark.asyncio
async def test_connect_helper_arms_and_shutdown_stops(tmp_path):
    from gateway import clawd_continuation as continuation
    from gateway.platforms.api_server_runs import finish_api_connect, shutdown_api_runtime
    db = _db(tmp_path)
    adapter = _Adapter(db, _store(tmp_path))
    assert await finish_api_connect(adapter) is True
    assert continuation.service_for(adapter) is not None
    await shutdown_api_runtime(adapter)
    assert continuation.service_for(adapter) is None
    assert adapter.closed is True
    db.close()


@pytest.mark.asyncio
async def test_explicit_cancel_blocks_and_busy_retries_after_lease(tmp_path):
    from gateway import clawd_continuation as continuation
    db = _db(tmp_path)
    db.append_message(
        SID, "user", content="[ASYNC DELEGATION BATCH COMPLETE — deleg_busy]\n结果",
        display_kind="async_delegation_complete", display_metadata={"delegation_id": "deleg_busy"},
    )
    adapter = _Adapter(db, _store(tmp_path))
    adapter._run_statuses["run_cancel"] = {
        "status": "cancelled", "session_id": SID, "updated_at": 10**10,
    }
    continuation.arm_adapter(adapter)
    service = continuation.service_for(adapter)
    try:
        decision = await service.evaluate(SID, db, profile="default")
        assert decision.reason == "cancelled"
        assert adapter.calls == []
        assert service.status(SID)["last_event"]["detail"] == "cancelled"
    finally:
        continuation.disarm_adapter(adapter)
        db.close()
