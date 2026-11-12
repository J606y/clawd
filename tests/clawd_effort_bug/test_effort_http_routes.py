"""HTTP effort routes must call the real resolver.

Do not patch ``resolve_execution_state`` or ``profile_execution_state``.
A green result from a mocked resolver would hide the unbound 503.

Not executed in the implementation turn.
"""

from __future__ import annotations

import json
import threading
import time
from contextlib import nullcontext

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from clawd_server.effort_service import PENDING_KEY, resolve_execution_state
from gateway.platforms.clawd_effort_routes import (
    _commit_effort,
    _run_state,
    register_effort_routes,
)

SID = "sess-effort-http"
PARENT = "sess-effort-http-parent"
CHILD = "sess-effort-http-child"
MISSING = "sess-effort-http-missing"


class _Agent:
    def __init__(self, session_id):
        self.session_id = session_id


class _Scope:
    def __init__(self, adapter, profile):
        self.adapter = adapter
        self.profile = profile
        self.token = None

    def __enter__(self):
        self.adapter.active_profile = self.profile
        home = self.adapter.home_for(self.profile)
        if home is not None:
            from hermes_constants import set_hermes_home_override
            self.token = set_hermes_home_override(str(home))
        return self

    def __exit__(self, *_exc):
        self.adapter.active_profile = None
        if self.token is not None:
            from hermes_constants import reset_hermes_home_override
            reset_hermes_home_override(self.token)
        return False


class PanelAdapter:
    """The maps ``resolve_execution_state`` actually reads. Not a resolver fake."""

    def __init__(self, db):
        self.db = db
        self.dbs = {}
        self.homes = {}
        self.default_home = None
        self.active_profile = None
        self.gateway_runner = None
        self._run_statuses = {}
        self._shutdown_interruptible_agents = {}
        self._active_run_agents = {}
        self._active_run_tasks = {}
        self._inflight_agent_runs = 0
        self._pending_agent_requests = 0
        self.allow = True
        self.auth_raises = False
        self.draining = None

    def home_for(self, profile):
        if profile and profile in self.homes:
            return self.homes[profile]
        return self.default_home

    def _check_auth(self, _request):
        if self.auth_raises:
            raise RuntimeError("auth checker failed")
        if not self.allow:
            return web.json_response(
                {"error": {"message": "no", "type": "invalid_request_error", "code": "unauthorized"}},
                status=401,
            )
        return None

    def _ensure_session_db(self):
        profile = self.active_profile
        if profile and profile in self.dbs:
            return self.dbs[profile]
        return self.db

    def _profile_scope(self, profile):
        return _Scope(self, profile)

    def _draining_response(self):
        return self.draining


class _NoAuth:
    def __init__(self, db):
        self.db = db
        self.gateway_runner = None

    def _ensure_session_db(self):
        return self.db

    def _profile_scope(self, _profile):
        return nullcontext()


@pytest.fixture(autouse=True)
def _quiet_proofs(monkeypatch):
    monkeypatch.setattr("agent.secret_scope.is_multiplex_active", lambda: False)
    monkeypatch.setattr("tools.async_delegation.list_async_delegations", lambda: [])


def _db(tmp_path, name="state.db"):
    from hermes_state import SessionDB

    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    db = SessionDB(db_path=path)
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


def _raw(db, session_id=SID):
    return db.get_session(session_id)["model_config"]


def _app(adapter):
    app = web.Application()
    register_effort_routes(app, adapter)
    return app


async def _call(adapter, method, path, **kwargs):
    async with TestClient(TestServer(_app(adapter))) as client:
        response = await getattr(client, method)(path, **kwargs)
        text = await response.text()
        try:
            body = json.loads(text) if text else None
        except json.JSONDecodeError:
            body = text
        return response.status, body


def _code(body):
    return (body or {}).get("error", {}).get("code")


def test_route_binds_the_real_resolver():
    import gateway.platforms.clawd_effort_routes as routes
    import inspect

    assert routes.resolve_execution_state is resolve_execution_state
    run = inspect.getsource(_run_state)
    commit = inspect.getsource(_commit_effort)
    assert "resolve_execution_state(db, session_id, authority, adapter)" in run
    assert "adapter=adapter" in commit
    assert "or authority is None" not in commit
    assert "or authority is None" not in inspect.getsource(routes.ClawdEffortRoutes.handle_put)


@pytest.mark.asyncio
async def test_unbound_idle_get_and_put(tmp_path):
    db = _db(tmp_path)
    adapter = PanelAdapter(db)
    try:
        status, body = await _call(adapter, "get", f"/api/sessions/{SID}/effort")
        assert status == 200, body
        assert body["running"] is False
        assert body["effort"] == "low"
        assert body["source"] == "lock"
        assert "未写入" not in json.dumps(body, ensure_ascii=False)
        status, body = await _call(
            adapter, "put", f"/api/sessions/{SID}/effort", json={"effort": "medium"},
        )
        assert status == 200, body
        config = _config(db.get_session(SID))
    finally:
        db.close()
    assert body["applied"] == "stored"
    assert body["running"] is False
    assert config["reasoning_effort"] == "medium"
    assert config["browser_model_lock"]["model"] == "grok-4.7"
    assert config["browser_model_lock"]["provider"] == "sub2api"
    assert config["browser_model_lock"]["model_options"]["reasoning_effort"] == "medium"
    assert config["browser_model_lock"]["model_options"]["temperature"] == 0.3
    assert PENDING_KEY not in config


@pytest.mark.asyncio
async def test_running_agent_put_is_pending_only(tmp_path):
    db = _db(tmp_path)
    adapter = PanelAdapter(db)
    adapter._shutdown_interruptible_agents[1] = _Agent(SID)
    adapter._inflight_agent_runs = 1
    before = _raw(db)
    try:
        status, got = await _call(adapter, "get", f"/api/sessions/{SID}/effort")
        assert status == 200 and got["running"] is True
        status, body = await _call(
            adapter, "put", f"/api/sessions/{SID}/effort", json={"effort": "high"},
        )
        config = _config(db.get_session(SID))
    finally:
        db.close()
    assert status == 200, body
    assert body["applied"] == "next_turn"
    assert body["running"] is True
    assert config[PENDING_KEY] == "high"
    assert config["browser_model_lock"]["model_options"]["reasoning_effort"] == "low"
    assert config["browser_model_lock"]["model"] == "grok-4.7"
    assert config["browser_model_lock"]["provider"] == "sub2api"
    assert json.loads(before)["browser_model_lock"]["model"] == config["browser_model_lock"]["model"]


@pytest.mark.asyncio
async def test_compression_child_running_does_not_commit_parent(tmp_path):
    db = _db(tmp_path)
    db.create_session(PARENT, source="api_server", model="grok-4.7", profile_name="fixture")
    db.update_session_runtime_lock(
        PARENT, model="grok-4.7", provider="sub2api",
        model_options={"temperature": 0.3, "reasoning_effort": "low"},
        route_source="raw_request", confirmed=True,
    )
    db.end_session(PARENT, "compression")
    db.create_session(CHILD, source="api_server", model="grok-4.7", parent_session_id=PARENT)
    adapter = PanelAdapter(db)
    adapter._shutdown_interruptible_agents[1] = _Agent(CHILD)
    adapter._inflight_agent_runs = 1
    try:
        assert resolve_execution_state(db, PARENT, None, adapter) == "running"
        status, body = await _call(
            adapter, "put", f"/api/sessions/{PARENT}/effort", json={"effort": "max"},
        )
        config = _config(db.get_session(PARENT))
    finally:
        db.close()
    assert status == 200, body
    assert body["applied"] == "next_turn"
    assert config[PENDING_KEY] == "max"
    assert config["browser_model_lock"]["model_options"]["reasoning_effort"] == "low"
    assert config["browser_model_lock"]["model"] == "grok-4.7"


@pytest.mark.asyncio
async def test_live_lease_put_is_pending(tmp_path):
    db = _db(tmp_path)
    now = time.time()

    def _insert(conn):
        key = db._session_turn_lease_key_on_conn(conn, SID)
        conn.execute(
            "INSERT INTO session_turn_leases (conversation_id, holder, acquired_at, expires_at) VALUES (?,?,?,?)",
            (key, "panel-effort", now, now + 3600),
        )

    db._execute_write(_insert)
    adapter = PanelAdapter(db)
    try:
        status, body = await _call(
            adapter, "put", f"/api/sessions/{SID}/effort", json={"effort": "xhigh"},
        )
        config = _config(db.get_session(SID))
    finally:
        db.close()
    assert status == 200, body
    assert body["applied"] == "next_turn"
    assert config[PENDING_KEY] == "xhigh"
    assert config["browser_model_lock"]["model_options"]["reasoning_effort"] == "low"


@pytest.mark.asyncio
async def test_durable_worker_put_is_pending(tmp_path):
    db = _db(tmp_path)
    db._execute_write(lambda conn: conn.execute(
        "INSERT INTO worker_executions (execution_id, session_id, kind, owner_epoch, generation, status, adoption_digest) "
        "VALUES (?,?,?,?,?,?,?)",
        ("ex-effort-http", SID, "child", 1, 1, "running", "digest"),
    ))
    adapter = PanelAdapter(db)
    try:
        status, body = await _call(
            adapter, "put", f"/api/sessions/{SID}/effort", json={"effort": "ultra"},
        )
        config = _config(db.get_session(SID))
    finally:
        db.close()
    assert status == 200, body
    assert body["applied"] == "next_turn"
    assert config[PENDING_KEY] == "ultra"
    assert config["browser_model_lock"]["model_options"]["reasoning_effort"] == "low"
    assert config["browser_model_lock"]["model"] == "grok-4.7"


@pytest.mark.asyncio
async def test_queued_admission_put_is_pending(tmp_path):
    db = _db(tmp_path)
    db._execute_write(lambda conn: conn.execute(
        "INSERT INTO session_admissions (admission_id, request_id, principal_id, target_session_id, "
        "lineage_json, payload_json, payload_digest, intent, status) VALUES (?,?,?,?,?,?,?,?,?)",
        ("adm-effort-http", "req-effort-http", "api", SID, "[]", "{}", "digest", "queue", "queued"),
    ))
    adapter = PanelAdapter(db)
    try:
        status, body = await _call(
            adapter, "put", f"/api/sessions/{SID}/effort", json={"effort": "high"},
        )
        config = _config(db.get_session(SID))
    finally:
        db.close()
    assert status == 200, body
    assert config[PENDING_KEY] == "high"
    assert config["browser_model_lock"]["model_options"]["reasoning_effort"] == "low"
    assert config["browser_model_lock"]["provider"] == "sub2api"


@pytest.mark.asyncio
async def test_unknown_does_not_write(tmp_path, monkeypatch):
    db = _db(tmp_path)
    adapter = PanelAdapter(db)
    adapter._inflight_agent_runs = 1
    before = _raw(db)
    try:
        status, got = await _call(adapter, "get", f"/api/sessions/{SID}/effort")
        assert status == 200 and got["running"] is None
        status, body = await _call(
            adapter, "put", f"/api/sessions/{SID}/effort", json={"effort": "high"},
        )
        assert status == 503 and _code(body) == "effort_run_state_unavailable"
        assert _raw(db) == before
        adapter._inflight_agent_runs = 0
        now = time.time()
        db._execute_write(lambda conn: conn.execute(
            "INSERT INTO compression_locks (session_id, holder, acquired_at, expires_at) VALUES (?,?,?,?)",
            (SID, "panel-effort", now, now + 3600),
        ))
        status, body = await _call(
            adapter, "put", f"/api/sessions/{SID}/effort", json={"effort": "high"},
        )
        assert status == 503 and _code(body) == "effort_run_state_unavailable"
        assert _raw(db) == before
        db._execute_write(lambda conn: conn.execute("DELETE FROM compression_locks WHERE session_id=?", (SID,)))
        monkeypatch.setattr("agent.secret_scope.is_multiplex_active", lambda: True)
        status, body = await _call(
            adapter, "put", f"/api/sessions/{SID}/effort", json={"effort": "high"},
        )
        assert status == 503 and _raw(db) == before
        monkeypatch.setattr("agent.secret_scope.is_multiplex_active", lambda: False)
        adapter.gateway_runner = object()
        status, body = await _call(
            adapter, "put", f"/api/sessions/{SID}/effort", json={"effort": "high"},
        )
        assert status == 503 and _raw(db) == before
    finally:
        db.close()


@pytest.mark.asyncio
async def test_worker_count_gap_does_not_idle(tmp_path, monkeypatch):
    db = _db(tmp_path)
    adapter = PanelAdapter(db)
    before = _raw(db)
    monkeypatch.setattr("gateway.platforms.api_server_runs.api_worker_live_count", lambda: 1)
    try:
        assert resolve_execution_state(db, SID, None, adapter) == "unknown"
        status, body = await _call(
            adapter, "put", f"/api/sessions/{SID}/effort", json={"effort": "high"},
        )
        assert status == 503 and _raw(db) == before
        monkeypatch.setattr(
            "gateway.platforms.api_server_runs.api_worker_live_count",
            lambda: (_ for _ in ()).throw(RuntimeError("worker count unreadable")),
        )
        status, body = await _call(
            adapter, "put", f"/api/sessions/{SID}/effort", json={"effort": "high"},
        )
        assert status == 503 and _code(body) == "effort_run_state_unavailable"
        assert _raw(db) == before
    finally:
        db.close()


@pytest.mark.asyncio
async def test_missing_session_is_404_not_unknown(tmp_path):
    db = _db(tmp_path)
    adapter = PanelAdapter(db)
    before = _raw(db)
    try:
        status, body = await _call(adapter, "get", f"/api/sessions/{MISSING}/effort")
        assert status == 404 and _code(body) == "session_not_found"
        status, body = await _call(
            adapter, "put", f"/api/sessions/{MISSING}/effort", json={"effort": "high"},
        )
        assert status == 404 and _code(body) == "session_not_found"
        assert status != 503
        assert _raw(db) == before
        status, body = await _call(
            adapter, "put", f"/api/sessions/{MISSING}/effort", json={"effort": "nope"},
        )
        assert status == 400 and _code(body) == "invalid_effort"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_auth_and_input_validation_stay(tmp_path):
    db = _db(tmp_path)
    before = _raw(db)
    bare = _NoAuth(db)
    try:
        status, body = await _call(bare, "put", f"/api/sessions/{SID}/effort", json={"effort": "high"})
        assert status == 401 and _code(body) == "unauthorized"
        assert _raw(db) == before
        adapter = PanelAdapter(db)
        adapter.allow = False
        status, body = await _call(adapter, "get", f"/api/sessions/{SID}/effort")
        assert status == 401
        status, body = await _call(adapter, "put", f"/api/sessions/{SID}/effort", json={"effort": "high"})
        assert status == 401 and _raw(db) == before
        adapter.allow = True
        adapter.auth_raises = True
        status, _body = await _call(adapter, "put", f"/api/sessions/{SID}/effort", json={"effort": "high"})
        assert status == 401 and _raw(db) == before
        adapter.auth_raises = False
        status, body = await _call(adapter, "put", f"/api/sessions/{'s' * 300}/effort", json={"effort": "low"})
        assert status == 400 and _code(body) == "invalid_session_id"
        status, body = await _call(
            adapter, "put", f"/api/sessions/{SID}/effort", data=b"{", headers={"Content-Type": "application/json"},
        )
        assert status == 400 and _code(body) == "invalid_json"
        status, body = await _call(
            adapter, "put", f"/api/sessions/{SID}/effort", json={"effort": "low", "model": "x"},
        )
        assert status == 400 and _code(body) == "unexpected_fields"
        status, body = await _call(
            adapter, "put", f"/api/sessions/{SID}/effort", json={"effort": "nope"},
        )
        assert status == 400 and _code(body) == "invalid_effort"
        assert _raw(db) == before
        adapter.draining = web.Response(status=503, text="draining")
        status, _text = await _call(adapter, "put", f"/api/sessions/{SID}/effort", json={"effort": "high"})
        assert status == 503 and _raw(db) == before
    finally:
        db.close()


@pytest.mark.asyncio
async def test_cross_profile_and_cross_db_do_not_write(tmp_path):
    from hermes_state import SessionDB
    from gateway.session_authorities import authority_for_home

    home_a = tmp_path / "alpha"
    home_b = tmp_path / "beta"
    db_a = _db(home_a)
    beta_path = home_b / "state.db"
    beta_path.parent.mkdir(parents=True, exist_ok=True)
    db_b = SessionDB(db_path=beta_path)
    other = _db(tmp_path / "other-store")
    request_db = None
    adapter = PanelAdapter(db_a)
    adapter.dbs = {"alpha": db_a, "beta": db_b}
    adapter.homes = {"alpha": home_a, "beta": home_b}
    before_a = _raw(db_a)
    try:
        status, body = await _call(
            adapter, "put", f"/p/beta/api/sessions/{SID}/effort", json={"effort": "high"},
        )
        assert status == 404 and _code(body) == "session_not_found"
        assert _raw(db_a) == before_a
        assert db_b.get_session(SID) is None
        status, body = await _call(
            adapter, "put", f"/p/alpha/api/sessions/{SID}/effort", json={"effort": "medium"},
        )
        assert status == 200, body
        assert _config(db_a.get_session(SID))["reasoning_effort"] == "medium"
        assert db_b.get_session(SID) is None

        request_db = _db(tmp_path / "request-store")
        before_request = _raw(request_db)
        before_other = _raw(other)
        mismatched = PanelAdapter(request_db)
        mismatched.default_home = other.db_path.parent
        mismatched.gateway_runner = type("Runner", (), {
            "session_authority": type("Auth", (), {"db": other})(),
            "session_authorities": None,
        })()
        found = authority_for_home(mismatched.gateway_runner, other.db_path.parent)
        assert found is not None and found.db is other
        status, body = await _call(
            mismatched, "put", f"/api/sessions/{SID}/effort", json={"effort": "ultra"},
        )
        assert status == 503 and _code(body) == "effort_run_state_unavailable"
        assert _raw(request_db) == before_request
        assert _raw(other) == before_other
    finally:
        db_a.close()
        db_b.close()
        other.close()
        if request_db is not None:
            request_db.close()


def test_concurrent_writes_keep_the_model_lock(tmp_path):
    db = _db(tmp_path)
    adapter = PanelAdapter(db)
    real = db._execute_write

    def _wrapped(fn, patience_s=None):
        def _inner(conn):
            row = conn.execute("SELECT model_config FROM sessions WHERE id=?", (SID,)).fetchone()
            try:
                raw = row["model_config"]
            except Exception:
                raw = row[0]
            config = json.loads(raw)
            config["browser_model_lock"]["model"] = "raced-model"
            config["browser_model_lock"]["provider"] = "raced-provider"
            conn.execute(
                "UPDATE sessions SET model_config=? WHERE id=?",
                (json.dumps(config), SID),
            )
            return fn(conn)

        if patience_s is None:
            return real(_inner)
        return real(_inner, patience_s)

    db._execute_write = _wrapped
    try:
        from clawd_server.effort_service import write_session_effort
        view = write_session_effort(db, SID, "high", running=False, adapter=adapter)
        config = _config(db.get_session(SID))
    finally:
        db._execute_write = real
        db.close()
    assert view["applied"] == "stored"
    assert config["browser_model_lock"]["model"] == "raced-model"
    assert config["browser_model_lock"]["provider"] == "raced-provider"
    assert config["browser_model_lock"]["model_options"]["reasoning_effort"] == "high"
    assert config["browser_model_lock"]["model_options"]["temperature"] == 0.3

    db = _db(tmp_path / "threads")
    adapter = PanelAdapter(db)
    errors = []

    def _write(level):
        try:
            from clawd_server.effort_service import write_session_effort
            write_session_effort(db, SID, level, running=False, adapter=adapter)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=_write, args=(level,)) for level in ("high", "medium", "ultra", "low")]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(8)
        assert not any(thread.is_alive() for thread in threads)
        assert errors == []
        config = _config(db.get_session(SID))
    finally:
        db.close()
    assert config["browser_model_lock"]["model"] == "grok-4.7"
    assert config["browser_model_lock"]["provider"] == "sub2api"
    assert config["browser_model_lock"]["model_options"]["temperature"] == 0.3
    assert config["browser_model_lock"]["model_options"]["reasoning_effort"] in {"high", "medium", "ultra", "low"}
    assert PENDING_KEY not in config
