"""Snapshot stability, subagent read-only, and prompt-cache overlay order.

Not executed in this change. Migration routes are not called. Approval is not
forced open. A disk write after the cached prefix is stored is the store
primitive an approved replay would use, so a rebuild would see it and a
restored prefix must not.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from tests.clawd_memory_scope_cases.support import (
    INVOKE_TOOL_NOT_SUBAGENT_GATE,
    assert_rejected,
    call_memory,
    close_agent,
    drop_home,
    joined,
    make_agent,
    memory_file,
    memory_targets,
    open_session_db,
    project_memory_entries,
    scoped_write_outcome,
    seed_bound_project,
    use_home,
)


def _tool_call(name: str, arguments: str):
    return SimpleNamespace(
        id="call_subagent_memory",
        type="function",
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def test_parent_move_and_explicit_unbind_do_not_retarget_child_memory(tmp_path):
    home = tmp_path / "snapshot"
    pid_a = seed_bound_project(home, "Alpha", "parent", "ALPHA-SNAPSHOT-FACT")
    workspace_b = home / "workspace" / "Beta"
    workspace_b.mkdir(parents=True)
    from hermes_cli import projects_db as pdb

    with pdb.connect_closing(home / "projects.db") as conn:
        pid_b = pdb.create_project(conn, name="Beta", primary_path=str(workspace_b))
        pdb.bind_session_project(conn, "unused", pid_b)
    from clawd_projects.service import get_project_memory_store

    get_project_memory_store(pid_b, hermes_home=home).add("memory", "BETA-LATER-FACT")
    db = open_session_db(home)
    db.create_session("parent", "cli")
    token = use_home(home)
    try:
        from clawd_projects.service import bind_session_to_project, resolve_session_project

        db.create_session("child", "cli", parent_session_id="parent")
        assert resolve_session_project("child", hermes_home=home).id == pid_a
        child = make_agent(home, "child", session_db=db)
        try:
            assert "ALPHA-SNAPSHOT-FACT" in (child._memory_store.format_for_system_prompt("memory") or "")
            assert "BETA-LATER-FACT" not in (child._memory_store.format_for_system_prompt("memory") or "")
        finally:
            close_agent(child)

        assert bind_session_to_project("parent", pid_b, hermes_home=home, session_db=db) is True
        get_project_memory_store(pid_b, hermes_home=home).add("memory", "BETA-AFTER-MOVE")
        moved = make_agent(home, "child", session_db=db)
        try:
            text = moved._memory_store.format_for_system_prompt("memory") or ""
            assert moved.project_id == pid_a
            assert "ALPHA-SNAPSHOT-FACT" in text
            assert "BETA-AFTER-MOVE" not in text
            before = text
            outcome = scoped_write_outcome(
                call_memory(moved, target="memory", content="CHILD-STAYS-ON-ALPHA"),
                "CHILD-STAYS-ON-ALPHA",
                joined(project_memory_entries(home, pid_a)),
                joined(project_memory_entries(home, pid_b)),
            )
            assert outcome in {"applied", "staged"}
            assert moved._memory_store.format_for_system_prompt("memory") == before
        finally:
            close_agent(moved)

        db.create_session("sibling", "cli", parent_session_id="parent")
        assert resolve_session_project("sibling", hermes_home=home).id == pid_b
        assert resolve_session_project("child", hermes_home=home).id == pid_a

        assert bind_session_to_project("child", None, hermes_home=home, session_db=db) is True
        db.create_session("child", "cli", parent_session_id="parent")
        assert resolve_session_project("child", hermes_home=home) is None
        unbound = make_agent(home, "child", session_db=db)
        try:
            assert getattr(unbound, "project_id", None) in (None, "")
            assert "project" not in memory_targets(unbound)
            assert_rejected(
                call_memory(unbound, target="project", content="UNBOUND-MUST-NOT-WRITE"),
                "UNBOUND-MUST-NOT-WRITE",
                joined(project_memory_entries(home, pid_a)),
                joined(project_memory_entries(home, pid_b)),
            )
        finally:
            close_agent(unbound)
    finally:
        drop_home(token)
        db.close()


def test_subagent_reads_snapshot_and_turn_gate_does_not_write(tmp_path):
    home = tmp_path / "subagent"
    pid = seed_bound_project(home, "Parent", "sess_parent", "CHILD-MAY-READ-THIS")
    db = open_session_db(home)
    db.create_session("sess_parent", "cli")
    mem = memory_file(home, pid)
    before = mem.read_bytes()
    token = use_home(home)
    parent = None
    child = None
    try:
        from tools.delegate_tool import _build_child_agent

        parent = make_agent(home, "sess_parent", session_db=db)
        child = _build_child_agent(
            task_index=0,
            goal="read only",
            context=None,
            toolsets=None,
            model="test-model",
            max_iterations=2,
            task_count=1,
            parent_agent=parent,
        )
        assert child.project_id == pid
        assert "CHILD-MAY-READ-THIS" in (child._memory_store.format_for_system_prompt("memory") or "")
        assert "memory" not in (child.valid_tool_names or set())
        assert "memory" not in memory_targets(child)
        assert db.get_session(child.session_id) is not None

        child._invalid_tool_retries = 0
        child._invalid_json_retries = 0
        from agent.turn_tool_validation import validate_tool_calls

        messages: list[dict] = []
        verdict = validate_tool_calls(
            child,
            SimpleNamespace(
                content="",
                tool_calls=[_tool_call("memory", json.dumps({
                    "action": "add", "target": "memory", "content": "SUBAGENT-MUST-NOT-WRITE",
                }))],
            ),
            "tool_calls",
            messages=messages,
            conversation_history=[],
            api_call_count=1,
            effective_task_id="subagent-memory",
        )
        assert verdict.action == "continue"
        assert mem.read_bytes() == before
        assert "SUBAGENT-MUST-NOT-WRITE" not in joined(project_memory_entries(home, pid))
        assert any("memory" in str(item.get("content", "")) for item in messages if item.get("role") == "tool")

        from clawd_projects.service import bind_session_to_project, resolve_session_project
        from hermes_cli import projects_db as pdb

        other = home / "workspace" / "Other"
        other.mkdir(parents=True)
        with pdb.connect_closing(home / "projects.db") as conn:
            pid_b = pdb.create_project(conn, name="Other", primary_path=str(other))
        assert bind_session_to_project("sess_parent", pid_b, hermes_home=home, session_db=db) is True
        assert resolve_session_project(child.session_id, hermes_home=home).id == pid
        assert child._memory_store.project_id == pid
        assert child._memory_store.project_store.memory_dir == mem.parent

        assert bind_session_to_project(child.session_id, None, hermes_home=home, session_db=db) is True
        db.create_session(child.session_id, "subagent", parent_session_id="sess_parent")
        assert resolve_session_project(child.session_id, hermes_home=home) is None
        fresh = make_agent(home, child.session_id, session_db=db)
        try:
            assert_rejected(
                call_memory(fresh, target="project", content="REBIND-MUST-NOT-WRITE"),
                "REBIND-MUST-NOT-WRITE",
                joined(project_memory_entries(home, pid)),
                joined(project_memory_entries(home, pid_b)),
            )
        finally:
            close_agent(fresh)
        assert INVOKE_TOOL_NOT_SUBAGENT_GATE
    finally:
        if child is not None:
            close_agent(child)
        if parent is not None:
            close_agent(parent)
        drop_home(token)
        db.close()


def test_prompt_cache_reuses_bytes_and_overlay_order(tmp_path):
    home = tmp_path / "cache"
    workspace = home / "workspace" / "Demo"
    workspace.mkdir(parents=True)
    (workspace / "AGENTS.md").write_text("PROJECT-RULE-TOKEN always lint.", encoding="utf-8")
    from hermes_cli import projects_db as pdb

    with pdb.connect_closing(home / "projects.db") as conn:
        pid = pdb.create_project(conn, name="Demo", primary_path=str(workspace))
        pdb.bind_session_project(conn, "sess_cache", pid)
    from tools.memory_tool import MemoryStore
    from clawd_projects.service import get_project_memory_store

    global_dir = home / "memories"
    global_store = MemoryStore(memory_dir=global_dir)
    global_store.load_from_disk()
    global_store.add("memory", "GLOBAL-NOTE-TOKEN")
    global_store.add("user", "USER-NOTE-TOKEN")
    get_project_memory_store(pid, hermes_home=home).add("memory", "PROJECT-NOTE-TOKEN")

    db = open_session_db(home)
    db.create_session("sess_cache", "cli")
    token = use_home(home)
    first = None
    second = None
    try:
        from agent.conversation_loop import _restore_or_build_system_prompt
        from agent.system_prompt import build_system_prompt_parts

        first = make_agent(home, "sess_cache", session_db=db, skip_context_files=False)
        history = [{"role": "user", "content": "hi"}]
        _restore_or_build_system_prompt(first, None, history)
        cached = first._cached_system_prompt
        assert isinstance(cached, str) and cached
        stored = db.get_session("sess_cache")
        assert stored is not None and stored.get("system_prompt") == cached

        parts = build_system_prompt_parts(first)
        stable, context, volatile = parts["stable"], parts["context"], parts["volatile"]
        for token_text in ("GLOBAL-NOTE-TOKEN", "PROJECT-NOTE-TOKEN", "USER-NOTE-TOKEN", "PROJECT-RULE-TOKEN"):
            assert token_text not in stable
        assert "PROJECT-RULE-TOKEN" in context
        assert "MEMORY (your personal notes)" in volatile
        assert "PROJECT MEMORY (notes for project 'Demo')" in volatile
        assert "USER PROFILE (who the user is)" in volatile
        assert volatile.index("MEMORY (your personal notes)") < volatile.index("PROJECT MEMORY")
        assert volatile.index("PROJECT MEMORY") < volatile.index("USER PROFILE")
        assert cached.index(context) < cached.index("MEMORY (your personal notes)")
        assert cached.index("MEMORY (your personal notes)") < cached.index("PROJECT MEMORY")
        assert cached.index("PROJECT MEMORY") < cached.index("USER PROFILE")
        assert cached.startswith(stable)

        call_memory(first, target="memory", content="TOOL-WRITE-AFTER-CACHE")
        assert first._cached_system_prompt == cached
        assert "TOOL-WRITE-AFTER-CACHE" not in first._cached_system_prompt
        assert "TOOL-WRITE-AFTER-CACHE" not in (first._memory_store.format_for_system_prompt("memory") or "")
        get_project_memory_store(pid, hermes_home=home).add("memory", "DISK-AFTER-CACHE-TOKEN")
        global_store.add("memory", "GLOBAL-DISK-AFTER-CACHE")

        second = make_agent(home, "sess_cache", session_db=db, skip_context_files=False)
        live = second._memory_store.format_for_system_prompt("memory") or ""
        assert "DISK-AFTER-CACHE-TOKEN" in live
        assert "GLOBAL-DISK-AFTER-CACHE" in live
        _restore_or_build_system_prompt(second, None, history)
        assert second._cached_system_prompt == cached
        assert "TOOL-WRITE-AFTER-CACHE" not in second._cached_system_prompt
        assert "DISK-AFTER-CACHE-TOKEN" not in second._cached_system_prompt
        assert "GLOBAL-DISK-AFTER-CACHE" not in second._cached_system_prompt
        assert "PROJECT-RULE-TOKEN" in second._cached_system_prompt
    finally:
        if second is not None:
            close_agent(second)
        if first is not None:
            close_agent(first)
        drop_home(token)
        db.close()
