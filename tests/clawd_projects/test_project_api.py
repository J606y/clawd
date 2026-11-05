"""Tests for clawd projects and project memory HTTP API routes."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gateway.platforms.base import Platform, PlatformConfig
from gateway.platforms.clawd_project_routes import register_project_routes
from hermes_cli import projects_db as pdb
from hermes_constants import set_hermes_home_override, reset_hermes_home_override


class DummyAdapter:
    """Mock APIServerAdapter providing authentication and session_db access."""

    def __init__(self, api_key: str = "sk-test-key"):
        self._api_key = api_key
        self.name = "api_server"
        self._session_db = None

    def _check_auth(self, request: web.Request):
        if not self._api_key:
            return None
        auth = request.headers.get("Authorization", "")
        if auth != f"Bearer {self._api_key}":
            return web.json_response({"error": "unauthorized"}, status=401)
        return None

    def _ensure_session_db(self):
        return self._session_db


@pytest.fixture
def temp_home(tmp_path):
    token = set_hermes_home_override(str(tmp_path))
    try:
        yield tmp_path
    finally:
        reset_hermes_home_override(token)


@pytest.fixture
def auth_headers():
    return {"Authorization": "Bearer sk-test-key"}


@pytest.fixture
def app_and_adapter(temp_home):
    adapter = DummyAdapter(api_key="sk-test-key")
    app = web.Application()
    register_project_routes(app, adapter)
    return app, adapter


@pytest.mark.asyncio
async def test_auth_rejection(app_and_adapter):
    app, adapter = app_and_adapter
    async with TestClient(TestServer(app)) as client:
        # Request without auth header
        res = await client.get("/api/projects")
        assert res.status == 401


@pytest.mark.asyncio
async def test_create_and_list_projects(app_and_adapter, auth_headers, temp_home):
    app, adapter = app_and_adapter
    proj_dir = temp_home / "repos" / "alpha"

    async with TestClient(TestServer(app)) as client:
        # Missing name
        res = await client.post("/api/projects", json={"primary_path": str(proj_dir)}, headers=auth_headers)
        assert res.status == 400

        # Missing primary_path
        res = await client.post("/api/projects", json={"name": "Alpha"}, headers=auth_headers)
        assert res.status == 400

        # Successful creation only when create_directory is explicit
        res = await client.post(
            "/api/projects",
            json={"name": "Alpha", "primary_path": str(proj_dir), "description": "Alpha project",
                  "create_directory": True},
            headers=auth_headers,
        )
        assert res.status == 201
        data = await res.json()
        assert "project" in data
        pid = data["project"]["id"]
        assert pid.startswith("p_")
        assert proj_dir.exists()

        # List projects
        res = await client.get("/api/projects", headers=auth_headers)
        assert res.status == 200
        list_data = await res.json()
        assert len(list_data["projects"]) == 1
        assert list_data["projects"][0]["id"] == pid
        assert list_data["projects"][0]["session_count"] == 0


@pytest.mark.asyncio
async def test_session_binding_and_busy_rejection(app_and_adapter, auth_headers, temp_home):
    """Bind requires a real session row; a core busy admission is 409, not a string match."""
    from hermes_state import SessionDB

    app, adapter = app_and_adapter
    proj_dir = temp_home / "repo_busy"
    proj_dir.mkdir(parents=True)
    with pdb.connect_closing(temp_home / "projects.db") as conn:
        pid = pdb.create_project(conn, name="BusyProj", primary_path=str(proj_dir))

    db = SessionDB(db_path=temp_home / "state.db")
    db.create_session("s_idle", "api_server")
    db.create_session("s_busy", "api_server")
    adapter._session_db = db

    def _mark_busy(conn):
        conn.execute(
            "INSERT INTO session_admissions (admission_id, request_id, principal_id, target_session_id, "
            "lineage_json, payload_json, payload_digest, intent, status, owner_epoch, generation) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("adm-busy", "req-busy", "prin", "s_busy", "{}", "{}", "digest", "queue", "started", 1, 1),
        )

    db._execute_write(_mark_busy)
    try:
        async with TestClient(TestServer(app)) as client:
            missing = await client.post(
                "/api/sessions/s_missing/project", json={"project_id": pid}, headers=auth_headers,
            )
            assert missing.status == 404

            res = await client.post(
                "/api/sessions/s_idle/project", json={"project_id": pid}, headers=auth_headers,
            )
            assert res.status == 200
            assert (await res.json())["success"] is True

            busy = await client.post(
                "/api/sessions/s_busy/project", json={"project_id": pid}, headers=auth_headers,
            )
            assert busy.status == 409
            assert (await busy.json())["error"] == "session_busy"
    finally:
        db.close()


def _memory_file(home: Path, project_id: str) -> Path:
    return home / "projects" / project_id / "memory" / "MEMORY.md"


def _create_project(home: Path, name: str) -> str:
    workspace = home / "workspace" / name
    workspace.mkdir(parents=True, exist_ok=True)
    with pdb.connect_closing(home / "projects.db") as conn:
        return pdb.create_project(conn, name=name, primary_path=str(workspace))


def _bind_project(home: Path, session_id: str, project_id: str) -> None:
    with pdb.connect_closing(home / "projects.db") as conn:
        assert pdb.bind_session_project(conn, session_id, project_id)


def _attach_authority(adapter, home: Path, session_id: str, *, generation: int | None = None):
    """Real SessionAuthority for this profile home. Does not mint a binding."""
    from types import SimpleNamespace

    from gateway.session_authority import LiveSession, SessionAuthority
    from hermes_state import SessionDB
    from hermes_state_runtime import begin_runtime_epoch

    db = SessionDB(db_path=home / "state.db")
    if db.get_session(session_id) is None:
        db.create_session(session_id, "api_server")
    if generation is not None:
        def _claim(conn):
            conn.execute(
                "UPDATE sessions SET runtime_generation=? WHERE id=?",
                (generation, session_id),
            )
            conn.execute(
                "INSERT INTO session_admissions (admission_id, request_id, principal_id, target_session_id, "
                "lineage_json, payload_json, payload_digest, intent, status, owner_epoch, generation) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    f"adm-{session_id}-{generation}",
                    f"req-{session_id}-{generation}",
                    "prin",
                    session_id,
                    "{}",
                    "{}",
                    "digest",
                    "queue",
                    "started",
                    1,
                    generation,
                ),
            )
        db._execute_write(_claim)
    epoch = begin_runtime_epoch(db, instance_id="memory-api")
    runner = SimpleNamespace(_draining=False, session_runtime_descriptor={})
    authority = SessionAuthority(
        runner, profile_id="default", instance_id="memory-api", db=db, epoch=epoch,
    )
    authority.sessions[session_id] = LiveSession(None, "api-memory")
    runner.session_authority = authority
    adapter.gateway_runner = runner
    adapter._session_db = db
    return db


@pytest.mark.asyncio
async def test_project_memory_api(app_and_adapter, auth_headers, temp_home):
    """Idle user edit uses the bound session, not the URL project id alone."""
    app, adapter = app_and_adapter
    pid = _create_project(temp_home, "MemProj")
    _bind_project(temp_home, "s_mem", pid)
    db = _attach_authority(adapter, temp_home, "s_mem")
    try:
        async with TestClient(TestServer(app)) as client:
            res = await client.get(
                f"/api/projects/{pid}/memory",
                params={"session_id": "s_mem"},
                headers=auth_headers,
            )
            assert res.status == 200
            mem_data = await res.json()
            assert mem_data["memory"]["entries"] == []

            ops = [
                {"action": "add", "content": "Convention: code must pass lint."},
                {"action": "add", "content": "Stack: python3.14 + aiohttp."},
            ]
            res = await client.post(
                f"/api/projects/{pid}/memory",
                json={"session_id": "s_mem", "operations": ops},
                headers=auth_headers,
            )
            assert res.status == 200
            applied = await res.json()
            assert applied["success"] is True
            assert applied.get("staged") is not True
            assert applied["entry_count"] == 2

            res = await client.get(
                f"/api/projects/{pid}/memory",
                params={"session_id": "s_mem"},
                headers=auth_headers,
            )
            assert res.status == 200
            readback = await res.json()
            assert len(readback["memory"]["entries"]) == 2
            assert "Convention: code must pass lint." in readback["memory"]["entries"]
        assert not (temp_home / "memories" / "MEMORY.md").exists()
    finally:
        db.close()


@pytest.mark.asyncio
async def test_project_memory_rejects_missing_identity(app_and_adapter, auth_headers, temp_home):
    """Bearer alone, a missing session, or a URL project id is not a credential."""
    app, adapter = app_and_adapter
    pid = _create_project(temp_home, "MemProj")
    _bind_project(temp_home, "s_mem", pid)
    db = _attach_authority(adapter, temp_home, "s_mem")
    try:
        async with TestClient(TestServer(app)) as client:
            naked = await client.get(f"/api/projects/{pid}/memory", params={"session_id": "s_mem"})
            assert naked.status == 401

            missing_session = await client.get(
                f"/api/projects/{pid}/memory", headers=auth_headers,
            )
            assert missing_session.status == 401
            assert (await missing_session.json())["error"] == "missing_identity"

            invented = await client.post(
                f"/api/projects/{pid}/memory",
                json={"session_id": "s_invented", "operations": [{"action": "add", "content": "NO"}]},
                headers=auth_headers,
            )
            assert invented.status == 404
            assert (await invented.json())["error"] == "session_not_found"
            assert db.get_session("s_invented") is None
            with pdb.connect_closing(temp_home / "projects.db") as conn:
                assert pdb.get_session_project_id(conn, "s_invented") is None

            other_pid = _create_project(temp_home, "OtherProj")
            mismatch = await client.post(
                f"/api/projects/{other_pid}/memory",
                json={"session_id": "s_mem", "operations": [{"action": "add", "content": "URL-ID"}]},
                headers=auth_headers,
            )
            assert mismatch.status == 403
            assert (await mismatch.json())["error"] == "project_mismatch"
            assert not _memory_file(temp_home, pid).exists()
            assert not _memory_file(temp_home, other_pid).exists()
    finally:
        db.close()


@pytest.mark.asyncio
async def test_project_memory_rejects_wrong_profile(app_and_adapter, auth_headers, temp_home):
    """Inactive authority and another profile home both refuse. Neither writes."""
    from types import SimpleNamespace

    app, adapter = app_and_adapter
    pid = _create_project(temp_home, "MemProj")
    _bind_project(temp_home, "s_mem", pid)
    fact = "WRONG-PROFILE"
    try:
        async with TestClient(TestServer(app)) as client:
            adapter.gateway_runner = SimpleNamespace(_draining=False, session_runtime_descriptor={})
            inactive = await client.post(
                f"/api/projects/{pid}/memory",
                json={"session_id": "s_mem", "operations": [{"action": "add", "content": fact}]},
                headers=auth_headers,
            )
            assert inactive.status == 403
            assert (await inactive.json())["error"] == "profile_mismatch"

            other = temp_home / "other-profile"
            other.mkdir()
            other_db = _attach_authority(adapter, other, "s_mem")
            try:
                crossed = await client.post(
                    f"/api/projects/{pid}/memory",
                    json={"session_id": "s_mem", "operations": [{"action": "add", "content": fact}]},
                    headers=auth_headers,
                )
                body = await crossed.json()
                assert crossed.status == 403
                assert body["error"] == "not_bound"
                assert body["success"] is False
            finally:
                other_db.close()
        assert not _memory_file(temp_home, pid).exists() or fact not in _memory_file(temp_home, pid).read_text(encoding="utf-8")
    finally:
        adapter.gateway_runner = None


@pytest.mark.asyncio
async def test_project_memory_rejects_stale_and_string_generation(app_and_adapter, auth_headers, temp_home):
    """A string generation is not coerced into the running claim."""
    app, adapter = app_and_adapter
    pid = _create_project(temp_home, "MemProj")
    _bind_project(temp_home, "s_mem", pid)
    db = _attach_authority(adapter, temp_home, "s_mem", generation=3)
    try:
        async with TestClient(TestServer(app)) as client:
            stale = await client.post(
                f"/api/projects/{pid}/memory",
                json={
                    "session_id": "s_mem",
                    "operations": [{"action": "add", "content": "STALE"}],
                    "execution_generation": 2,
                },
                headers=auth_headers,
            )
            assert stale.status == 409
            assert (await stale.json())["error"] == "stale_generation"

            coerced = await client.post(
                f"/api/projects/{pid}/memory",
                json={
                    "session_id": "s_mem",
                    "operations": [{"action": "add", "content": "STRING-GEN"}],
                    "execution_generation": "3",
                },
                headers=auth_headers,
            )
            assert coerced.status == 409
            assert (await coerced.json())["error"] == "stale_generation"
            assert not _memory_file(temp_home, pid).exists() or "STRING-GEN" not in _memory_file(temp_home, pid).read_text(encoding="utf-8")

            current = await client.post(
                f"/api/projects/{pid}/memory",
                json={
                    "session_id": "s_mem",
                    "operations": [{"action": "add", "content": "CURRENT-GEN"}],
                    "execution_generation": 3,
                },
                headers=auth_headers,
            )
            assert current.status == 200
            assert (await current.json())["success"] is True
            readback = await client.get(
                f"/api/projects/{pid}/memory",
                params={"session_id": "s_mem", "execution_generation": "3"},
                headers=auth_headers,
            )
            assert readback.status == 200
            entries = (await readback.json())["memory"]["entries"]
            assert "CURRENT-GEN" in entries
            stored = _memory_file(temp_home, pid).read_text(encoding="utf-8")
            assert "STALE" not in stored
            assert "STRING-GEN" not in stored
    finally:
        db.close()
