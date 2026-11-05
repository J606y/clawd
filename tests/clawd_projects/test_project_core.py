"""Tests for clawd projects domain service, database layer, memory, and initialization."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest

from clawd_projects.project_memory import ProjectMemoryStore
from clawd_projects.service import (
    bind_session_to_project,
    get_clawd_projects_dir,
    get_project_by_id,
    get_project_memory_dir,
    get_project_memory_store,
    is_session_busy,
    resolve_session_project,
)
from hermes_cli import projects_db as pdb
from hermes_constants import set_hermes_home_override, reset_hermes_home_override
from tools.memory_tool import MemoryStore


@pytest.fixture
def temp_home(tmp_path):
    token = set_hermes_home_override(str(tmp_path))
    try:
        yield tmp_path
    finally:
        reset_hermes_home_override(token)


def test_project_crud_and_primary_path(temp_home):
    """Test project creation requires valid primary_path, dedup, and archive (non-destructive)."""
    with pdb.connect_closing(temp_home / "projects.db") as conn:
        proj_dir = temp_home / "my_codebase"
        proj_dir.mkdir(parents=True)

        pid = pdb.create_project(conn, name="Alpha", primary_path=str(proj_dir))
        p = pdb.get_project(conn, pid)
        assert p is not None
        assert p.name == "Alpha"
        assert p.primary_path == str(proj_dir)
        assert p.archived is False

        # Duplicate primary_path refused by default
        with pytest.raises(ValueError, match="already belongs to project"):
            pdb.create_project(conn, name="Beta", primary_path=str(proj_dir))

        # Archiving does NOT delete project or directory
        assert pdb.archive_project(conn, pid) is True
        assert proj_dir.exists()
        p_arch = pdb.get_project(conn, pid)
        assert p_arch is not None
        assert p_arch.archived is True


def test_session_project_binding_persistence(temp_home):
    """Test binding session to project and querying bound sessions."""
    from hermes_state import SessionDB

    proj_dir = temp_home / "proj_x"
    proj_dir.mkdir(parents=True)
    db = SessionDB(db_path=temp_home / "state.db")
    db.create_session("sess_p1", "cli")

    try:
        with pdb.connect_closing(temp_home / "projects.db") as conn:
            pid = pdb.create_project(conn, name="Project X", primary_path=str(proj_dir))

        assert resolve_session_project("sess_general", hermes_home=temp_home) is None
        assert bind_session_to_project("sess_p1", pid, hermes_home=temp_home, session_db=db) is True

        resolved = resolve_session_project("sess_p1", hermes_home=temp_home)
        assert resolved is not None
        assert resolved.id == pid
        assert resolved.name == "Project X"

        with pdb.connect_closing(temp_home / "projects.db") as conn:
            sessions = pdb.list_project_sessions(conn, pid)
            assert "sess_p1" in sessions

        assert bind_session_to_project("sess_p1", None, hermes_home=temp_home, session_db=db) is True
        assert resolve_session_project("sess_p1", hermes_home=temp_home) is None
    finally:
        db.close()


def test_project_memory_isolation_and_scoping(temp_home):
    """Test project-specific memory isolation and ProjectMemoryStore delegating."""
    global_store_dir = temp_home / "memories"
    global_store = MemoryStore(memory_dir=global_store_dir)
    global_store.load_from_disk()

    # Add global memory and user profile
    global_store.add("memory", "Global fact: standard Python project")
    global_store.add("user", "User fact: likes concise answers")

    # Project store
    proj_mem_store = get_project_memory_store("p_alpha", hermes_home=temp_home)
    proj_mem_store.add("memory", "Project Alpha fact: uses pytest-asyncio")

    # Create composite store
    composite = ProjectMemoryStore(
        global_store=global_store,
        project_store=proj_mem_store,
        project_id="p_alpha",
        project_name="Alpha",
    )
    composite.load_from_disk()

    # USER fact writes must go to global store
    composite.add("user", "User fact: works in UTC+8")
    assert "User fact: works in UTC+8" in global_store.user_entries
    assert "User fact: works in UTC+8" not in proj_mem_store.memory_entries

    # Default 'memory' writes in project session must go to PROJECT store, not global
    composite.add("memory", "Project Alpha fact: database is SQLite WAL")
    assert "Project Alpha fact: database is SQLite WAL" in proj_mem_store.memory_entries
    assert "Project Alpha fact: database is SQLite WAL" not in global_store.memory_entries

    # System prompt format must include global facts, project facts, and user profile
    formatted_memory = composite.format_for_system_prompt("memory")
    assert formatted_memory is not None
    assert "Global fact: standard Python project" in formatted_memory
    assert "Project Alpha fact: uses pytest-asyncio" in formatted_memory
    assert "PROJECT MEMORY (notes for project 'Alpha')" in formatted_memory

    formatted_user = composite.format_for_system_prompt("user")
    assert formatted_user is not None
    assert "User fact: likes concise answers" in formatted_user


def test_agent_init_with_project_session(temp_home):
    """Test real AIAgent initialization with project binding and prompt freezing."""
    proj_dir = temp_home / "demo_repo"
    proj_dir.mkdir(parents=True)
    (proj_dir / "AGENTS.md").write_text("# Project Custom Rules\nAlways run lint.", encoding="utf-8")

    with pdb.connect_closing(temp_home / "projects.db") as conn:
        pid = pdb.create_project(conn, name="Demo Repo", primary_path=str(proj_dir))
        pdb.bind_session_project(conn, "sess_demo", pid)

    # Seed project memory
    pm = get_project_memory_store(pid, hermes_home=temp_home)
    pm.add("memory", "Architecture note: service worker pattern")

    from run_agent import AIAgent

    agent = AIAgent(
        model="test-model",
        provider="custom",
        base_url="http://127.0.0.1:9999/v1",
        api_key="sk-test",
        session_id="sess_demo",
        platform="cli",
        skip_context_files=False,
    )

    assert getattr(agent, "project_id", None) == pid
    assert getattr(agent, "session_cwd", None) == str(proj_dir)
    assert isinstance(agent._memory_store, ProjectMemoryStore)

    # Inspect frozen system prompt
    prompt = agent._build_system_prompt()
    assert "PROJECT MEMORY (notes for project 'Demo Repo')" in prompt
    assert "Architecture note: service worker pattern" in prompt
    assert "Project Custom Rules" in prompt


def test_child_subagent_inherits_project(temp_home, request):
    """Test inheritance using the parent's explicit, persistent state store."""
    from hermes_state import SessionDB

    db = SessionDB(db_path=temp_home / "state.db")
    request.addfinalizer(db.close)
    db.create_session("sess_parent", "cli")
    proj_dir = temp_home / "parent_repo"
    proj_dir.mkdir(parents=True)

    with pdb.connect_closing(temp_home / "projects.db") as conn:
        pid = pdb.create_project(conn, name="Parent Repo", primary_path=str(proj_dir))
        pdb.bind_session_project(conn, "sess_parent", pid)

    from clawd_projects.service import get_project_memory_store
    get_project_memory_store(pid, hermes_home=temp_home).add("memory", "child must see this project fact")

    from run_agent import AIAgent
    from tools.delegate_tool import _build_child_agent

    parent = AIAgent(
        model="test-model",
        provider="custom",
        base_url="http://127.0.0.1:9999/v1",
        api_key="sk-test",
        session_id="sess_parent",
        session_db=db,
        platform="cli",
    )
    request.addfinalizer(parent.close)
    assert getattr(parent, "project_id", None) == pid

    child = _build_child_agent(
        task_index=0,
        goal="Run tests",
        context="test context",
        toolsets=None,
        model="test-model",
        max_iterations=10,
        task_count=1,
        parent_agent=parent,
    )

    assert getattr(child, "project_id", None) == pid
    assert getattr(child, "session_cwd", None) == str(proj_dir)
    assert isinstance(child._memory_store, ProjectMemoryStore)
    frozen = child._memory_store.format_for_system_prompt("memory")
    assert frozen and "child must see this project fact" in frozen

    # Verify child session is persistently bound to the same project
    resolved_child_proj = resolve_session_project(child.session_id, hermes_home=temp_home)
    assert resolved_child_proj is not None
    assert resolved_child_proj.id == pid
    try:
        child.close()
    except Exception:
        pass
