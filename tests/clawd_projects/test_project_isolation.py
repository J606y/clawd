"""Runtime isolation: path safety, fail-closed busy, project memory, profile boundary."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from clawd_projects.project_memory import ProjectMemoryStore
from clawd_projects.service import (
    SessionBusyError,
    SessionStoreCheckError,
    get_project_memory_dir,
    get_project_memory_store,
    is_session_busy,
    prepare_project_directory,
)
from gateway.platforms.clawd_project_routes import register_project_routes
from hermes_cli import projects_db as pdb
from hermes_constants import set_hermes_home_override, reset_hermes_home_override
from tools.memory_tool import MemoryStore, memory_tool
from tools.write_approval import GateDecision


@pytest.fixture
def temp_home(tmp_path):
    token = set_hermes_home_override(str(tmp_path))
    try:
        yield tmp_path
    finally:
        reset_hermes_home_override(token)


def _memory_enum(agent) -> list[str]:
    for tool in agent.tools or []:
        fn = (tool or {}).get("function") or {}
        if fn.get("name") == "memory":
            return list(fn["parameters"]["properties"]["target"]["enum"])
    return []


def test_memory_dir_rejects_path_escape(temp_home):
    with pytest.raises(ValueError):
        get_project_memory_dir("../etc", hermes_home=temp_home)
    with pytest.raises(ValueError):
        get_project_memory_dir("p_ok/../../tmp", hermes_home=temp_home)
    safe = get_project_memory_dir("p_alpha", hermes_home=temp_home)
    assert safe.is_relative_to(temp_home / "projects")


def test_directory_requires_explicit_create_and_rejects_relative(temp_home, monkeypatch):
    monkeypatch.chdir(temp_home)
    relative = "sneaky/workspace"
    with pytest.raises(ValueError, match="absolute"):
        prepare_project_directory(relative, create_directory=True)
    assert not (temp_home / "sneaky").exists()

    missing = temp_home / "missing-proj"
    with pytest.raises(ValueError, match="create_directory"):
        prepare_project_directory(str(missing), create_directory=False)
    assert not missing.exists()

    created = prepare_project_directory(str(missing), create_directory=True)
    assert Path(created).is_dir()

    a_file = temp_home / "not-dir"
    a_file.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="directory"):
        prepare_project_directory(str(a_file), create_directory=True)


def test_unknown_busy_error_fails_closed():
    class Boom:
        def _execute_write(self, fn, patience_s=None):
            raise RuntimeError("disk exploded")

    with pytest.raises(SessionStoreCheckError):
        is_session_busy(Boom(), "sess")
    from clawd_projects.service import bind_session_to_project
    with pytest.raises(SessionStoreCheckError):
        bind_session_to_project("sess", "p_x", session_db=Boom())


def test_real_admission_is_busy(temp_home):
    from hermes_state import SessionDB

    db = SessionDB(db_path=temp_home / "state.db")
    try:
        db.create_session("s_busy", "cli")

        def _mark(conn):
            conn.execute(
                "INSERT INTO session_admissions (admission_id, request_id, principal_id, target_session_id, "
                "lineage_json, payload_json, payload_digest, intent, status, owner_epoch, generation) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                ("adm1", "req1", "prin", "s_busy", "{}", "{}", "d", "queue", "started", 1, 1),
            )

        db._execute_write(_mark)
        assert is_session_busy(db, "s_busy") is True
        with pytest.raises(SessionBusyError):
            from clawd_projects.service import bind_session_to_project
            bind_session_to_project("s_busy", "p_missing", session_db=db, hermes_home=temp_home)
    finally:
        db.close()


def test_normal_memory_rejects_project_targets(temp_home, monkeypatch):
    monkeypatch.setattr("tools.memory_tool.get_memory_dir", lambda: temp_home / "memories")
    store = MemoryStore(memory_dir=temp_home / "memories")
    store.load_from_disk()
    refused = json.loads(memory_tool(action="add", target="project", content="should not land", store=store))
    assert refused["success"] is False
    assert store.memory_entries == []
    refused_global = json.loads(memory_tool(
        action="add", target="global_memory", content="also no", store=store,
    ))
    assert refused_global["success"] is False
    assert "project" not in store.accepted_targets()


def test_project_memory_tool_keeps_approval_and_freeze(temp_home, monkeypatch):
    global_dir = temp_home / "memories"
    monkeypatch.setattr("tools.memory_tool.get_memory_dir", lambda: global_dir)
    global_store = MemoryStore(memory_dir=global_dir)
    global_store.load_from_disk()
    global_store.add("memory", "global stays global")
    project_store = get_project_memory_store("p_alpha", hermes_home=temp_home)
    project_store.add("memory", "alpha only")
    composite = ProjectMemoryStore(global_store, project_store, "p_alpha", "Alpha")
    composite.load_from_disk()
    frozen = composite.format_for_system_prompt("memory")
    assert frozen and "alpha only" in frozen and "global stays global" in frozen

    other = get_project_memory_store("p_beta", hermes_home=temp_home)
    composite.add("memory", "alpha two")
    assert "alpha two" in project_store.memory_entries
    assert "alpha two" not in global_store.memory_entries
    assert other.memory_entries == []
    assert composite.format_for_system_prompt("memory") == frozen

    staged = GateDecision(stage=True, message="staged for approval")
    monkeypatch.setattr("tools.write_approval.evaluate_gate", lambda *a, **k: staged)
    monkeypatch.setattr("tools.write_approval.stage_write", lambda *a, **k: {"id": "pend-1"})
    result = json.loads(memory_tool(action="add", target="memory", content="needs approval", store=composite))
    assert result.get("staged") is True
    assert "needs approval" not in project_store.memory_entries
    assert "needs approval" not in global_store.memory_entries

    global_write = json.loads(memory_tool(
        action="add", target="global_memory", content="explicit global", store=composite,
    ))
    assert global_write.get("staged") is True
    assert "explicit global" not in global_store.memory_entries


def test_project_init_does_not_degrade_when_project_memory_fails(temp_home, monkeypatch):
    proj_dir = temp_home / "demo_repo"
    proj_dir.mkdir()
    with pdb.connect_closing(temp_home / "projects.db") as conn:
        pid = pdb.create_project(conn, name="Demo", primary_path=str(proj_dir))
        pdb.bind_session_project(conn, "sess_fail", pid)

    def _boom(*_a, **_k):
        raise RuntimeError("project memory unreadable")

    monkeypatch.setattr("clawd_projects.service.get_project_memory_store", _boom)
    from run_agent import AIAgent
    with pytest.raises(RuntimeError, match="project memory unreadable"):
        AIAgent(
            model="test-model", provider="custom", base_url="http://127.0.0.1:9999/v1",
            api_key="sk-test", session_id="sess_fail", platform="cli", quiet_mode=True,
        )


def test_child_does_not_copy_project_across_profiles(temp_home, tmp_path, request):
    from hermes_state import SessionDB

    db = SessionDB(db_path=temp_home / "state.db")
    request.addfinalizer(db.close)
    db.create_session("sess_parent", "cli")
    proj_dir = temp_home / "parent_repo"
    proj_dir.mkdir()
    with pdb.connect_closing(temp_home / "projects.db") as conn:
        pid = pdb.create_project(conn, name="Parent", primary_path=str(proj_dir))
        pdb.bind_session_project(conn, "sess_parent", pid)

    from run_agent import AIAgent
    from tools.delegate_tool import _build_child_agent

    parent = AIAgent(
        model="test-model", provider="custom", base_url="http://127.0.0.1:9999/v1",
        api_key="sk-test", session_id="sess_parent", session_db=db, platform="cli", quiet_mode=True,
    )
    other = tmp_path / "other-profile"
    other.mkdir()
    child = _build_child_agent(
        task_index=0, goal="isolated", context=None, toolsets=None, model="test-model",
        max_iterations=2, task_count=1, parent_agent=parent, profile_home=other,
    )
    try:
        assert getattr(child, "project_id", None) in (None, "")
        from clawd_projects.service import resolve_session_project
        assert resolve_session_project(child.session_id, hermes_home=other) is None
        assert resolve_session_project("sess_parent", hermes_home=temp_home).id == pid
        # Parent id must not have been inserted as a project row in the other profile.
        other_db = other / "projects.db"
        if other_db.exists():
            with pdb.connect_closing(other_db) as conn:
                assert pdb.get_project(conn, pid) is None
                assert pdb.get_session_project_id(conn, child.session_id) is None
    finally:
        for agent in (child, parent):
            try:
                agent.close()
            except Exception:
                pass


def test_project_schema_matches_store_and_normal_schema_does_not(temp_home):
    proj_dir = temp_home / "schema_repo"
    proj_dir.mkdir()
    with pdb.connect_closing(temp_home / "projects.db") as conn:
        pid = pdb.create_project(conn, name="Schema", primary_path=str(proj_dir))
        pdb.bind_session_project(conn, "sess_schema", pid)
    from run_agent import AIAgent
    bound = AIAgent(
        model="test-model", provider="custom", base_url="http://127.0.0.1:9999/v1",
        api_key="sk-test", session_id="sess_schema", platform="cli", quiet_mode=True,
    )
    plain = AIAgent(
        model="test-model", provider="custom", base_url="http://127.0.0.1:9999/v1",
        api_key="sk-test", session_id="sess_plain", platform="cli", quiet_mode=True,
    )
    try:
        bound_enum = _memory_enum(bound)
        plain_enum = _memory_enum(plain)
        assert "project" in bound_enum and "global_memory" in bound_enum
        assert "project" not in plain_enum and "global_memory" not in plain_enum
        assert isinstance(bound._memory_store, ProjectMemoryStore)
    finally:
        for agent in (bound, plain):
            try:
                agent.close()
            except Exception:
                pass


@pytest.mark.asyncio
async def test_api_rejects_missing_auth_non_object_and_relative_path(temp_home):
    class NoAuth:
        pass

    app = web.Application()
    register_project_routes(app, NoAuth())
    async with TestClient(TestServer(app)) as client:
        res = await client.get("/api/projects")
        assert res.status == 401

    class OpenAuth:
        def _check_auth(self, request):
            return None

    app2 = web.Application()
    register_project_routes(app2, OpenAuth())
    async with TestClient(TestServer(app2)) as client:
        res = await client.post("/api/projects", json=["not", "object"])
        assert res.status == 400
        missing = temp_home / "no-implicit"
        res = await client.post("/api/projects", json={
            "name": "Rel", "primary_path": "relative/proj", "create_directory": True,
        })
        assert res.status == 400
        assert not missing.exists()
        assert not Path("relative/proj").exists()


@pytest.mark.asyncio
async def test_api_unknown_busy_error_is_not_success(temp_home):
    class Boom:
        def _check_auth(self, request):
            return None

        def _ensure_session_db(self):
            return self

        def _execute_write(self, fn, patience_s=None):
            raise RuntimeError("disk exploded")

    proj_dir = temp_home / "repo"
    proj_dir.mkdir()
    with pdb.connect_closing(temp_home / "projects.db") as conn:
        pid = pdb.create_project(conn, name="P", primary_path=str(proj_dir))
    app = web.Application()
    register_project_routes(app, Boom())
    async with TestClient(TestServer(app)) as client:
        res = await client.post(f"/api/sessions/s1/project", json={"project_id": pid})
        assert res.status == 503
        assert (await res.json())["error"] == "session_check_failed"
