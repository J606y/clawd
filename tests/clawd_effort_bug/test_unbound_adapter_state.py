"""面板适配器没有 SessionAuthority 时的档位状态契约。

编写时未执行本文件。未知不是空闲。无适配器证明时不把缺席的 authority 当成 idle。
HTTP 门在 gateway/platforms/clawd_effort_routes.py，本文件不改那条路由。
"""

from __future__ import annotations

import json
import time

import pytest

from clawd_server.effort_service import (
    PENDING_KEY,
    EffortServiceError,
    profile_execution_state,
    read_session_effort,
    resolve_execution_state,
    write_session_effort,
)

SID = "sess-effort-unbound"
OTHER = "sess-effort-other"


class _Agent:
    def __init__(self, session_id):
        self.session_id = session_id


class _Task:
    def __init__(self, done=False):
        self._done = done

    def done(self):
        return self._done


class _Adapter:
    def __init__(self, db):
        self.db = db
        self.gateway_runner = None
        self._run_statuses = {}
        self._shutdown_interruptible_agents = {}
        self._active_run_agents = {}
        self._active_run_tasks = {}
        self._inflight_agent_runs = 0
        self._pending_agent_requests = 0

    def _ensure_session_db(self):
        return self.db


@pytest.fixture(autouse=True)
def _quiet_proofs(monkeypatch):
    monkeypatch.setattr("agent.secret_scope.is_multiplex_active", lambda: False)
    monkeypatch.setattr("tools.async_delegation.list_async_delegations", lambda: [])


def _db(tmp_path):
    from hermes_state import SessionDB

    db = SessionDB(db_path=tmp_path / "state.db")
    db.create_session(SID, source="api_server", model="grok-4.7", profile_name="fixture", cwd="/work/proj-a")
    db.update_session_runtime_lock(
        SID, model="grok-4.7", provider="sub2api",
        model_options={"temperature": 0.3, "reasoning_effort": "low"},
        route_source="raw_request", confirmed=True,
    )
    return db


def _config(session):
    raw = session.get("model_config")
    if isinstance(raw, str) and raw.strip():
        return json.loads(raw)
    return dict(raw or {})


def _raw(db):
    return db.get_session(SID)["model_config"]


def test_read_does_not_use_the_write_refusal(tmp_path):
    db = _db(tmp_path)
    try:
        view = read_session_effort(db, SID, running=None)
    finally:
        db.close()
    assert view["effort"] == "low"
    assert view["running"] is None
    assert view["source"] == "lock"
    assert "未写入" not in json.dumps(view, ensure_ascii=False)


def test_missing_authority_is_unknown_not_idle(tmp_path):
    db = _db(tmp_path)
    try:
        assert profile_execution_state(db, SID, None) == "unknown"
        assert resolve_execution_state(db, SID, None) == "unknown"
        assert resolve_execution_state(db, SID, None, adapter=None) == "unknown"
    finally:
        db.close()


def test_running_agent_stores_pending_and_keeps_the_lock(tmp_path):
    db = _db(tmp_path)
    adapter = _Adapter(db)
    adapter._shutdown_interruptible_agents[1] = _Agent(SID)
    adapter._inflight_agent_runs = 1
    try:
        assert resolve_execution_state(db, SID, None, adapter) == "running"
        view = write_session_effort(db, SID, "high", running=False, adapter=adapter)
        config = _config(db.get_session(SID))
    finally:
        db.close()
    assert view["applied"] == "next_turn"
    assert view["effort"] == "high"
    assert view["running"] is True
    assert config[PENDING_KEY] == "high"
    assert config["browser_model_lock"]["model_options"]["reasoning_effort"] == "low"
    assert config["browser_model_lock"]["model"] == "grok-4.7"
    assert config["browser_model_lock"]["provider"] == "sub2api"


def test_idle_adapter_commits_without_treating_a_foreign_turn_as_this_session(tmp_path):
    db = _db(tmp_path)
    db.create_session(OTHER, source="api_server", model="grok-4.7", profile_name="fixture", cwd="/work/proj-a")
    adapter = _Adapter(db)
    adapter._shutdown_interruptible_agents[1] = _Agent(OTHER)
    adapter._inflight_agent_runs = 1
    try:
        assert resolve_execution_state(db, SID, None, adapter) == "idle"
        view = write_session_effort(db, SID, "medium", running=False, adapter=adapter)
        config = _config(db.get_session(SID))
    finally:
        db.close()
    assert view["applied"] == "stored"
    assert view["running"] is False
    assert config["reasoning_effort"] == "medium"
    assert config["browser_model_lock"]["model_options"]["reasoning_effort"] == "medium"
    assert config["browser_model_lock"]["model_options"]["temperature"] == 0.3
    assert PENDING_KEY not in config


def test_unattributed_inflight_does_not_write(tmp_path):
    db = _db(tmp_path)
    adapter = _Adapter(db)
    adapter._inflight_agent_runs = 1
    try:
        before = _raw(db)
        assert resolve_execution_state(db, SID, None, adapter) == "unknown"
        with pytest.raises(EffortServiceError) as caught:
            write_session_effort(db, SID, "high", running=False, adapter=adapter)
        assert _raw(db) == before
    finally:
        db.close()
    assert caught.value.code == "effort_run_state_unavailable"
    assert caught.value.status == 503


def test_pending_admission_and_missing_maps_stay_unknown(tmp_path):
    db = _db(tmp_path)
    adapter = _Adapter(db)
    adapter._pending_agent_requests = 1
    try:
        assert resolve_execution_state(db, SID, None, adapter) == "unknown"
        adapter._pending_agent_requests = 0
        adapter._run_statuses = None
        assert resolve_execution_state(db, SID, None, adapter) == "unknown"
    finally:
        db.close()


def test_store_mismatch_and_multiplex_are_not_idle(tmp_path, monkeypatch):
    db = _db(tmp_path)
    other_home = tmp_path / "other"
    other_home.mkdir()
    other = _db(other_home)
    adapter = _Adapter(other)
    try:
        assert resolve_execution_state(db, SID, None, adapter) == "unknown"
        same = _Adapter(db)
        monkeypatch.setattr("agent.secret_scope.is_multiplex_active", lambda: True)
        assert resolve_execution_state(db, SID, None, same) == "unknown"
        same.gateway_runner = object()
        monkeypatch.setattr("agent.secret_scope.is_multiplex_active", lambda: False)
        assert resolve_execution_state(db, SID, None, same) == "unknown"
    finally:
        db.close()
        other.close()


def test_live_lease_is_running_even_when_adapter_maps_are_empty(tmp_path):
    db = _db(tmp_path)
    now = time.time()

    def _insert(conn):
        key = db._session_turn_lease_key_on_conn(conn, SID)
        conn.execute(
            "INSERT INTO session_turn_leases (conversation_id, holder, acquired_at, expires_at) VALUES (?,?,?,?)",
            (key, "panel-effort", now, now + 3600),
        )

    db._execute_write(_insert)
    adapter = _Adapter(db)
    try:
        assert resolve_execution_state(db, SID, None, adapter) == "running"
        view = write_session_effort(db, SID, "xhigh", running=False, adapter=adapter)
        config = _config(db.get_session(SID))
    finally:
        db.close()
    assert view["applied"] == "next_turn"
    assert config[PENDING_KEY] == "xhigh"
    assert config["browser_model_lock"]["model_options"]["reasoning_effort"] == "low"


def test_active_compression_lock_is_unknown_and_does_not_write(tmp_path):
    db = _db(tmp_path)
    now = time.time()

    def _insert(conn):
        conn.execute(
            "INSERT INTO compression_locks (session_id, holder, acquired_at, expires_at) VALUES (?,?,?,?)",
            (SID, "panel-effort", now, now + 3600),
        )

    db._execute_write(_insert)
    adapter = _Adapter(db)
    try:
        before = _raw(db)
        assert resolve_execution_state(db, SID, None, adapter) == "unknown"
        with pytest.raises(EffortServiceError) as caught:
            write_session_effort(db, SID, "max", running=False, adapter=adapter)
        assert _raw(db) == before
    finally:
        db.close()
    assert caught.value.code == "effort_run_state_unavailable"


def test_live_delegation_blocks_commit(tmp_path, monkeypatch):
    db = _db(tmp_path)
    monkeypatch.setattr(
        "tools.async_delegation.list_async_delegations",
        lambda: [{"status": "running", "parent_session_id": SID}],
    )
    adapter = _Adapter(db)
    try:
        assert resolve_execution_state(db, SID, None, adapter) == "running"
        view = write_session_effort(db, SID, "ultra", running=False, adapter=adapter)
        config = _config(db.get_session(SID))
    finally:
        db.close()
    assert view["applied"] == "next_turn"
    assert config["browser_model_lock"]["model_options"]["reasoning_effort"] == "low"


def test_authority_idle_plus_adapter_running_does_not_commit(tmp_path):
    db = _db(tmp_path)
    adapter = _Adapter(db)
    adapter._active_run_tasks["run-1"] = _Task(done=False)
    adapter._run_statuses["run-1"] = {"status": "running", "session_id": SID}

    class _Authority:
        def __init__(self):
            self.db = db
            self.executing = {}

        def logical_owner(self, session_id):
            return session_id

    try:
        assert resolve_execution_state(db, SID, _Authority(), adapter) == "running"
        view = write_session_effort(
            db, SID, "high", running=False, authority=_Authority(), adapter=adapter,
        )
        config = _config(db.get_session(SID))
    finally:
        db.close()
    assert view["applied"] == "next_turn"
    assert config[PENDING_KEY] == "high"
    assert config.get("reasoning_effort") != "high"
    assert config["browser_model_lock"]["model_options"]["reasoning_effort"] == "low"
    assert config["browser_model_lock"]["model"] == "grok-4.7"
