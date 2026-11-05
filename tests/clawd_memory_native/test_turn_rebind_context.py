"""Idle rebind of a reused agent. Not executed in this change.

The next facade turn must refresh project memory before ``_current_turn_id``
and before the stored prompt can be restored. An entered turn is left alone.
Ordinary mode is global only and is not blocked when session identity cannot
be proven. A project scope without a proven session db path, a failed resolve,
or an unfrozen composite without ``projects.db`` refuses the turn. Explicit
unbind strips inside ``_attach_project_context`` and does not copy an ancestor.
A child stays read-only. Profile comes from the session db, not process env.
"""

from __future__ import annotations

import pytest

from tests.clawd_memory_scope_cases.support import (
    AGENT_KW,
    close_agent,
    drop_home,
    make_agent,
    memory_targets,
    open_session_db,
    seed_bound_project,
    use_home,
)


def _beta(home, name: str, fact: str) -> str:
    from hermes_cli import projects_db as pdb
    from clawd_projects.service import get_project_memory_store

    workspace = home / "workspace" / name
    workspace.mkdir(parents=True)
    with pdb.connect_closing(home / "projects.db") as conn:
        pid = pdb.create_project(conn, name=name, primary_path=str(workspace))
    get_project_memory_store(pid, hermes_home=home).add("memory", fact)
    return pid


def _retarget(agent) -> None:
    from agent.turn_facade import _idle_project_memory_retarget

    _idle_project_memory_retarget(agent)


def test_next_turn_refreshes_before_turn_id_and_prompt_restore(tmp_path, monkeypatch):
    home = tmp_path / "next-turn"
    pid_a = seed_bound_project(home, "Alpha", "sess", "ALPHA-TURN-FACT")
    pid_b = _beta(home, "Beta", "BETA-TURN-FACT")
    db = open_session_db(home)
    db.create_session("sess", "cli")
    token = use_home(home)
    agent = None
    try:
        from clawd_projects.service import bind_session_to_project

        agent = make_agent(home, "sess", session_db=db)
        added = agent._memory_store.global_store.add("memory", "GLOBAL-STAYS-FACT")
        assert added.get("success") is True
        agent._current_turn_id = "leftover-turn"
        agent._cached_system_prompt = "STALE-ALPHA-PROMPT"
        assert bind_session_to_project("sess", pid_b, hermes_home=home, session_db=db) is True
        observed: dict = {}

        def fake_run(bound, *_args, **_kwargs):
            observed["turn_id"] = bound._current_turn_id
            observed["entered"] = bound._turn_facade_entered
            observed["project_id"] = bound._memory_store.project_id
            observed["prompt"] = bound._cached_system_prompt
            observed["nested"] = getattr(bound._memory_store.global_store, "project_id", None)
            return {"final_response": "ok", "messages": [], "failed": False}

        monkeypatch.setattr("agent.conversation_loop.run_conversation", fake_run)
        from run_agent import AIAgent

        result = AIAgent.run_conversation(agent, "next")
        assert result["final_response"] == "ok"
        assert observed["turn_id"] == ""
        assert observed["entered"] is True
        assert observed["project_id"] == pid_b
        assert observed["nested"] in (None, "")
        prompt = observed["prompt"]
        assert "BETA-TURN-FACT" in prompt
        assert "GLOBAL-STAYS-FACT" in prompt
        assert "ALPHA-TURN-FACT" not in prompt
        assert prompt != "STALE-ALPHA-PROMPT"
        assert db.get_session("sess").get("system_prompt") == prompt
        assert pid_a != pid_b
    finally:
        if agent is not None:
            close_agent(agent)
        drop_home(token)
        db.close()


def test_entered_turn_does_not_retarget(tmp_path):
    home = tmp_path / "live"
    pid_a = seed_bound_project(home, "Alpha", "sess", "LIVE-ALPHA")
    pid_b = _beta(home, "Beta", "LIVE-BETA")
    db = open_session_db(home)
    db.create_session("sess", "cli")
    token = use_home(home)
    agent = None
    try:
        from clawd_projects.service import bind_session_to_project

        agent = make_agent(home, "sess", session_db=db)
        agent._turn_facade_entered = True
        agent._current_turn_id = "live-turn"
        agent._cached_system_prompt = "LIVE-PROMPT"
        before = agent._memory_store
        assert bind_session_to_project("sess", pid_b, hermes_home=home, session_db=db) is True
        _retarget(agent)
        assert agent._current_turn_id == "live-turn"
        assert agent._memory_store is before
        assert agent._memory_store.project_id == pid_a
        assert agent._cached_system_prompt == "LIVE-PROMPT"
        assert "LIVE-BETA" not in (agent._memory_store.format_for_system_prompt("memory") or "")
    finally:
        if agent is not None:
            close_agent(agent)
        drop_home(token)
        db.close()


def test_unchanged_binding_keeps_prompt_cache(tmp_path):
    home = tmp_path / "same"
    seed_bound_project(home, "Alpha", "sess", "SAME-FACT")
    db = open_session_db(home)
    db.create_session("sess", "cli")
    token = use_home(home)
    agent = None
    try:
        agent = make_agent(home, "sess", session_db=db)
        agent._current_turn_id = "leftover"
        agent._cached_system_prompt = "KEEP-THIS-PROMPT"
        _retarget(agent)
        assert agent._current_turn_id == ""
        assert agent._cached_system_prompt == "KEEP-THIS-PROMPT"
        assert "project" in memory_targets(agent)
    finally:
        if agent is not None:
            close_agent(agent)
        drop_home(token)
        db.close()


def test_ordinary_retarget_stays_global(tmp_path):
    home = tmp_path / "ordinary"
    home.mkdir()
    db = open_session_db(home)
    db.create_session("plain", "cli")
    token = use_home(home)
    agent = None
    try:
        agent = make_agent(home, "plain", session_db=db)
        agent._cached_system_prompt = "PLAIN-PROMPT"
        _retarget(agent)
        assert getattr(agent, "project_id", None) in (None, "")
        assert "project" not in memory_targets(agent)
        assert "global_memory" not in memory_targets(agent)
        assert agent._cached_system_prompt == "PLAIN-PROMPT"
        assert not hasattr(agent._memory_store, "project_store")
    finally:
        if agent is not None:
            close_agent(agent)
        drop_home(token)
        db.close()


def test_unbind_does_not_copy_ancestor(tmp_path):
    home = tmp_path / "ancestor"
    pid_a = seed_bound_project(home, "Alpha", "parent", "ANCESTOR-ALPHA")
    pid_b = _beta(home, "Beta", "ANCESTOR-BETA")
    db = open_session_db(home)
    db.create_session("parent", "cli")
    db.create_session("child", "cli", parent_session_id="parent")
    token = use_home(home)
    agent = None
    try:
        from clawd_projects.service import bind_session_to_project, resolve_session_project

        agent = make_agent(home, "child", session_db=db)
        assert agent.project_id == pid_a
        assert bind_session_to_project("parent", pid_b, hermes_home=home, session_db=db) is True
        agent._cached_system_prompt = "CHILD-STILL-ALPHA"
        _retarget(agent)
        assert agent.project_id == pid_a
        assert agent._cached_system_prompt == "CHILD-STILL-ALPHA"
        assert "ANCESTOR-BETA" not in (agent._memory_store.format_for_system_prompt("memory") or "")

        assert bind_session_to_project("child", None, hermes_home=home, session_db=db) is True
        _retarget(agent)
        assert resolve_session_project("child", hermes_home=home) is None
        assert resolve_session_project("parent", hermes_home=home).id == pid_b
        assert getattr(agent, "project_id", None) in (None, "")
        assert agent.project is None
        assert not hasattr(agent._memory_store, "project_store")
        assert "project" not in memory_targets(agent)
        text = agent._cached_system_prompt or ""
        assert "ANCESTOR-ALPHA" not in text
        assert "ANCESTOR-BETA" not in text
        assert agent._memory_scope_freeze.project_id is None
    finally:
        if agent is not None:
            close_agent(agent)
        drop_home(token)
        db.close()


def test_child_rebind_stays_readonly(tmp_path):
    home = tmp_path / "child"
    pid_a = seed_bound_project(home, "Alpha", "child", "CHILD-ALPHA")
    pid_b = _beta(home, "Beta", "CHILD-BETA")
    db = open_session_db(home)
    db.create_session("child", "subagent")
    token = use_home(home)
    agent = None
    try:
        from run_agent import AIAgent
        from clawd_projects.project_memory import MemoryWriteDenied
        from clawd_projects.service import bind_session_to_project

        agent = AIAgent(
            **{**AGENT_KW, "platform": "subagent"},
            session_id="child",
            session_db=db,
            skip_context_files=True,
        )
        assert agent._memory_store.memory_writes_allowed is False
        assert agent._memory_scope_freeze.writable is False
        tools_before = list(agent.valid_tool_names or [])
        assert bind_session_to_project("child", pid_b, hermes_home=home, session_db=db) is True
        _retarget(agent)
        assert agent.project_id == pid_b
        assert "CHILD-BETA" in (agent._memory_store.format_for_system_prompt("memory") or "")
        assert "CHILD-ALPHA" not in (agent._memory_store.format_for_system_prompt("memory") or "")
        assert agent._memory_store.memory_writes_allowed is False
        assert agent._memory_scope_freeze.writable is False
        assert "memory" not in set(agent.valid_tool_names or []) - set(tools_before)
        with pytest.raises(MemoryWriteDenied):
            agent._memory_store.add("memory", "CHILD-MUST-NOT-WRITE")
        assert pid_a != pid_b
    finally:
        if agent is not None:
            close_agent(agent)
        drop_home(token)
        db.close()


def test_session_db_home_beats_process_env(tmp_path, monkeypatch):
    home = tmp_path / "profile"
    decoy = tmp_path / "decoy"
    seed_bound_project(home, "Alpha", "sess", "PROFILE-ALPHA")
    pid_b = _beta(home, "Beta", "PROFILE-BETA")
    seed_bound_project(decoy, "Decoy", "sess", "DECOY-FACT")
    db = open_session_db(home)
    db.create_session("sess", "cli")
    token = use_home(home)
    agent = None
    try:
        from clawd_projects.service import bind_session_to_project
        from hermes_constants import get_hermes_home, hermes_home_key

        agent = make_agent(home, "sess", session_db=db)
        drop_home(token)
        token = None
        monkeypatch.setenv("HERMES_HOME", str(decoy))
        monkeypatch.setenv("CLAWD_HOME", str(decoy))
        assert bind_session_to_project("sess", pid_b, hermes_home=home, session_db=db) is True
        _retarget(agent)
        assert agent.project_id == pid_b
        text = agent._cached_system_prompt or ""
        assert "PROFILE-BETA" in text
        assert "DECOY-FACT" not in text
        assert "PROFILE-ALPHA" not in text
        assert hermes_home_key(get_hermes_home()) == hermes_home_key(decoy)
    finally:
        if agent is not None:
            close_agent(agent)
        if token is not None:
            drop_home(token)
        db.close()


class _PathlessSessionDB:
    """Session handle with no provable path. Lease methods are absent on purpose."""

    def __init__(self):
        self.db_path = None


def _hide_projects_db(home):
    source = home / "projects.db"
    assert source.is_file()
    withheld = home / "projects.db.withheld"
    source.rename(withheld)
    assert not source.exists()
    return withheld


def _forbid_process_home(monkeypatch) -> None:
    def boom():
        raise AssertionError("guessed process home")

    monkeypatch.setattr("hermes_constants.get_hermes_home", boom)


def test_missing_db_path_refuses_project_scope_without_guessing_home(tmp_path, monkeypatch):
    home = tmp_path / "proven"
    decoy = tmp_path / "decoy"
    pid = seed_bound_project(home, "Alpha", "sess", "PROVEN-ALPHA")
    seed_bound_project(decoy, "Decoy", "sess", "DECOY-MUST-NOT-ATTACH")
    db = open_session_db(home)
    db.create_session("sess", "cli")
    token = use_home(home)
    agent = None
    try:
        agent = make_agent(home, "sess", session_db=db)
        drop_home(token)
        token = None
        agent._session_db.db_path = None
        agent._cached_system_prompt = "KEEP-PROVEN-PROMPT"
        before = agent._memory_store
        monkeypatch.setenv("HERMES_HOME", str(decoy))
        monkeypatch.setenv("CLAWD_HOME", str(decoy))
        _forbid_process_home(monkeypatch)
        with pytest.raises(RuntimeError, match="session identity is not proven"):
            _retarget(agent)
        assert agent._memory_store is before
        assert agent.project_id == pid
        assert agent._cached_system_prompt == "KEEP-PROVEN-PROMPT"
        assert "DECOY-MUST-NOT-ATTACH" not in (agent._cached_system_prompt or "")
    finally:
        if agent is not None:
            close_agent(agent)
        if token is not None:
            drop_home(token)
        db.close()


def test_missing_db_path_does_not_block_ordinary_global(tmp_path, monkeypatch):
    home = tmp_path / "plain-unproven"
    decoy = tmp_path / "decoy-plain"
    home.mkdir()
    seed_bound_project(decoy, "Decoy", "plain", "DECOY-PLAIN-FACT")
    db = open_session_db(home)
    db.create_session("plain", "cli")
    token = use_home(home)
    agent = None
    try:
        agent = make_agent(home, "plain", session_db=db)
        drop_home(token)
        token = None
        agent._session_db.db_path = None
        agent._cached_system_prompt = "PLAIN-STAYS"
        monkeypatch.setenv("HERMES_HOME", str(decoy))
        monkeypatch.setenv("CLAWD_HOME", str(decoy))
        _forbid_process_home(monkeypatch)
        _retarget(agent)
        assert getattr(agent, "project_id", None) in (None, "")
        assert agent.project is None
        assert not hasattr(agent._memory_store, "project_store")
        assert agent._cached_system_prompt == "PLAIN-STAYS"
        assert "DECOY-PLAIN-FACT" not in (agent._cached_system_prompt or "")
    finally:
        if agent is not None:
            close_agent(agent)
        if token is not None:
            drop_home(token)
        db.close()


def test_missing_session_id_refuses_project_scope(tmp_path, monkeypatch):
    home = tmp_path / "no-session"
    pid = seed_bound_project(home, "Alpha", "sess", "NO-SESSION-ALPHA")
    db = open_session_db(home)
    db.create_session("sess", "cli")
    token = use_home(home)
    agent = None
    try:
        agent = make_agent(home, "sess", session_db=db)
        agent.session_id = None
        agent._cached_system_prompt = "KEEP-NO-SESSION"
        before = agent._memory_store
        _forbid_process_home(monkeypatch)
        with pytest.raises(RuntimeError, match="session id is missing"):
            _retarget(agent)
        assert agent._memory_store is before
        assert agent.project_id == pid
        assert agent._cached_system_prompt == "KEEP-NO-SESSION"
    finally:
        if agent is not None:
            close_agent(agent)
        drop_home(token)
        db.close()


def test_missing_session_id_does_not_block_ordinary_global(tmp_path, monkeypatch):
    home = tmp_path / "plain-no-session"
    home.mkdir()
    db = open_session_db(home)
    db.create_session("plain", "cli")
    token = use_home(home)
    agent = None
    try:
        agent = make_agent(home, "plain", session_db=db)
        agent.session_id = ""
        agent._cached_system_prompt = "PLAIN-NO-SESSION"
        _forbid_process_home(monkeypatch)
        _retarget(agent)
        assert getattr(agent, "project_id", None) in (None, "")
        assert not hasattr(agent._memory_store, "project_store")
        assert agent._cached_system_prompt == "PLAIN-NO-SESSION"
    finally:
        if agent is not None:
            close_agent(agent)
        drop_home(token)
        db.close()


def test_resolve_failure_refuses_previous_project(tmp_path, monkeypatch):
    home = tmp_path / "resolve-fail"
    pid = seed_bound_project(home, "Alpha", "sess", "RESOLVE-ALPHA")
    db = open_session_db(home)
    db.create_session("sess", "cli")
    token = use_home(home)
    agent = None
    try:
        agent = make_agent(home, "sess", session_db=db)
        agent._cached_system_prompt = "KEEP-RESOLVE-PROMPT"
        before = agent._memory_store

        def explode(*_args, **_kwargs):
            raise OSError("projects.db unreadable")

        monkeypatch.setattr("clawd_projects.service.resolve_session_project", explode)
        with pytest.raises(RuntimeError, match="idle project resolve failed"):
            _retarget(agent)
        assert agent._memory_store is before
        assert agent.project_id == pid
        assert agent._cached_system_prompt == "KEEP-RESOLVE-PROMPT"
    finally:
        if agent is not None:
            close_agent(agent)
        drop_home(token)
        db.close()


def test_resolve_failure_does_not_enter_conversation_loop(tmp_path, monkeypatch):
    home = tmp_path / "resolve-loop"
    seed_bound_project(home, "Alpha", "sess", "LOOP-ALPHA")
    db = open_session_db(home)
    db.create_session("sess", "cli")
    token = use_home(home)
    agent = None
    try:
        from run_agent import AIAgent

        agent = make_agent(home, "sess", session_db=db)
        agent._cached_system_prompt = "KEEP-LOOP-PROMPT"
        before = agent._memory_store
        called = {"n": 0}

        def fake_run(*_args, **_kwargs):
            called["n"] += 1
            return {"final_response": "nope", "messages": [], "failed": False}

        def explode(*_args, **_kwargs):
            raise OSError("projects.db unreadable")

        monkeypatch.setattr("agent.conversation_loop.run_conversation", fake_run)
        monkeypatch.setattr("clawd_projects.service.resolve_session_project", explode)
        with pytest.raises(RuntimeError, match="idle project resolve failed"):
            AIAgent.run_conversation(agent, "next")
        assert called["n"] == 0
        assert agent._memory_store is before
        assert agent._cached_system_prompt == "KEEP-LOOP-PROMPT"
    finally:
        if agent is not None:
            close_agent(agent)
        drop_home(token)
        db.close()


def test_unproven_identity_does_not_enter_conversation_loop(tmp_path, monkeypatch):
    home = tmp_path / "identity-loop"
    pid = seed_bound_project(home, "Alpha", "sess", "IDENTITY-ALPHA")
    db = open_session_db(home)
    db.create_session("sess", "cli")
    token = use_home(home)
    agent = None
    try:
        from run_agent import AIAgent

        agent = make_agent(home, "sess", session_db=db)
        agent._session_db = _PathlessSessionDB()
        agent._cached_system_prompt = "KEEP-IDENTITY-PROMPT"
        before = agent._memory_store
        called = {"n": 0}

        def fake_run(*_args, **_kwargs):
            called["n"] += 1
            return {"final_response": "nope", "messages": [], "failed": False}

        monkeypatch.setattr("agent.conversation_loop.run_conversation", fake_run)
        with pytest.raises(RuntimeError, match="session identity is not proven"):
            AIAgent.run_conversation(agent, "next")
        assert called["n"] == 0
        assert agent._memory_store is before
        assert agent.project_id == pid
        assert agent._cached_system_prompt == "KEEP-IDENTITY-PROMPT"
    finally:
        if agent is not None:
            close_agent(agent)
        drop_home(token)
        db.close()


def test_missing_projects_db_refuses_unfrozen_composite(tmp_path):
    home = tmp_path / "unfrozen-missing"
    pid = seed_bound_project(home, "Alpha", "sess", "UNFROZEN-ALPHA")
    db = open_session_db(home)
    db.create_session("sess", "cli")
    token = use_home(home)
    agent = None
    try:
        agent = make_agent(home, "sess", session_db=db)
        agent._memory_scope_freeze = None
        agent._cached_system_prompt = "KEEP-UNFROZEN-PROMPT"
        before = agent._memory_store
        _hide_projects_db(home)
        with pytest.raises(RuntimeError, match="projects.db is missing"):
            _retarget(agent)
        assert agent._memory_store is before
        assert agent.project_id == pid
        assert hasattr(agent._memory_store, "project_store")
        assert agent._cached_system_prompt == "KEEP-UNFROZEN-PROMPT"
    finally:
        if agent is not None:
            close_agent(agent)
        drop_home(token)
        db.close()


def test_missing_projects_db_keeps_sealed_freeze(tmp_path):
    home = tmp_path / "sealed-missing"
    pid = seed_bound_project(home, "Alpha", "sess", "SEALED-ALPHA")
    db = open_session_db(home)
    db.create_session("sess", "cli")
    token = use_home(home)
    agent = None
    try:
        agent = make_agent(home, "sess", session_db=db)
        agent._cached_system_prompt = "KEEP-SEALED-PROMPT"
        before = agent._memory_store
        assert agent._memory_scope_freeze.project_id == pid
        _hide_projects_db(home)
        _retarget(agent)
        assert agent._memory_store is before
        assert agent.project_id == pid
        assert agent._memory_scope_freeze.project_id == pid
        assert agent._cached_system_prompt == "KEEP-SEALED-PROMPT"
    finally:
        if agent is not None:
            close_agent(agent)
        drop_home(token)
        db.close()


def test_missing_projects_db_does_not_block_ordinary_global(tmp_path):
    home = tmp_path / "plain-missing-db"
    home.mkdir()
    db = open_session_db(home)
    db.create_session("plain", "cli")
    token = use_home(home)
    agent = None
    try:
        agent = make_agent(home, "plain", session_db=db)
        agent._cached_system_prompt = "PLAIN-NO-DB"
        projects = home / "projects.db"
        if projects.is_file():
            projects.rename(home / "projects.db.withheld")
        assert not projects.exists()
        _retarget(agent)
        assert getattr(agent, "project_id", None) in (None, "")
        assert not hasattr(agent._memory_store, "project_store")
        assert agent._cached_system_prompt == "PLAIN-NO-DB"
    finally:
        if agent is not None:
            close_agent(agent)
        drop_home(token)
        db.close()


def test_attach_unbind_strips_store_without_facade(tmp_path):
    home = tmp_path / "attach-unbind"
    pid = seed_bound_project(home, "Alpha", "sess", "ATTACH-ALPHA")
    db = open_session_db(home)
    db.create_session("sess", "cli")
    token = use_home(home)
    agent = None
    try:
        from agent.agent_init import _attach_project_context
        from clawd_projects.service import bind_session_to_project

        agent = make_agent(home, "sess", session_db=db)
        assert agent.project_id == pid
        assert hasattr(agent._memory_store, "project_store")
        assert bind_session_to_project("sess", None, hermes_home=home, session_db=db) is True
        agent._current_turn_id = ""
        agent._cached_system_prompt = "STALE-UNTIL-FACADE"
        _attach_project_context(agent, {}, memory_required=False)
        assert getattr(agent, "project_id", None) in (None, "")
        assert agent.project is None
        assert not hasattr(agent._memory_store, "project_store")
        assert agent._memory_scope_freeze.project_id is None
        assert agent._cached_system_prompt == "STALE-UNTIL-FACADE"
        text = agent._memory_store.format_for_system_prompt("memory") or ""
        assert "ATTACH-ALPHA" not in text
    finally:
        if agent is not None:
            close_agent(agent)
        drop_home(token)
        db.close()


def test_attach_unbind_keeps_child_readonly(tmp_path):
    home = tmp_path / "attach-child"
    pid = seed_bound_project(home, "Alpha", "child", "ATTACH-CHILD-ALPHA")
    db = open_session_db(home)
    db.create_session("child", "subagent")
    token = use_home(home)
    agent = None
    try:
        from run_agent import AIAgent
        from agent.agent_init import _attach_project_context
        from clawd_projects.service import bind_session_to_project

        agent = AIAgent(
            **{**AGENT_KW, "platform": "subagent"},
            session_id="child",
            session_db=db,
            skip_context_files=True,
        )
        assert agent.project_id == pid
        assert agent._memory_store.memory_writes_allowed is False
        assert bind_session_to_project("child", None, hermes_home=home, session_db=db) is True
        agent._current_turn_id = ""
        _attach_project_context(agent, {}, memory_required=False)
        assert getattr(agent, "project_id", None) in (None, "")
        assert agent.project is None
        assert not hasattr(agent._memory_store, "project_store")
        assert agent._memory_store.memory_writes_allowed is False
        assert agent._memory_scope_freeze.project_id is None
        assert agent._memory_scope_freeze.writable is False
    finally:
        if agent is not None:
            close_agent(agent)
        drop_home(token)
        db.close()


def test_attach_live_turn_does_not_unbind(tmp_path):
    home = tmp_path / "attach-live"
    pid = seed_bound_project(home, "Alpha", "sess", "LIVE-ATTACH-ALPHA")
    db = open_session_db(home)
    db.create_session("sess", "cli")
    token = use_home(home)
    agent = None
    try:
        from agent.agent_init import _attach_project_context
        from clawd_projects.service import bind_session_to_project

        agent = make_agent(home, "sess", session_db=db)
        before = agent._memory_store
        agent._current_turn_id = "live-turn"
        agent._cached_system_prompt = "LIVE-ATTACH-PROMPT"
        assert bind_session_to_project("sess", None, hermes_home=home, session_db=db) is True
        _attach_project_context(agent, {}, memory_required=False)
        assert agent._memory_store is before
        assert agent.project_id == pid
        assert agent._current_turn_id == "live-turn"
        assert agent._cached_system_prompt == "LIVE-ATTACH-PROMPT"
    finally:
        if agent is not None:
            close_agent(agent)
        drop_home(token)
        db.close()


def test_entered_turn_is_not_refused_for_unproven_identity(tmp_path, monkeypatch):
    home = tmp_path / "entered-unproven"
    pid = seed_bound_project(home, "Alpha", "sess", "ENTERED-ALPHA")
    db = open_session_db(home)
    db.create_session("sess", "cli")
    token = use_home(home)
    agent = None
    try:
        agent = make_agent(home, "sess", session_db=db)
        agent._turn_facade_entered = True
        agent._current_turn_id = "live-turn"
        agent._cached_system_prompt = "ENTERED-PROMPT"
        before = agent._memory_store
        agent._session_db.db_path = None
        _forbid_process_home(monkeypatch)
        _retarget(agent)
        assert agent._memory_store is before
        assert agent.project_id == pid
        assert agent._current_turn_id == "live-turn"
        assert agent._cached_system_prompt == "ENTERED-PROMPT"
    finally:
        if agent is not None:
            close_agent(agent)
        drop_home(token)
        db.close()


def test_attach_missing_projects_db_does_not_create_or_strip(tmp_path):
    home = tmp_path / "attach-missing"
    pid = seed_bound_project(home, "Alpha", "sess", "ATTACH-MISSING-ALPHA")
    db = open_session_db(home)
    db.create_session("sess", "cli")
    token = use_home(home)
    agent = None
    try:
        from agent.agent_init import _attach_project_context

        agent = make_agent(home, "sess", session_db=db)
        before = agent._memory_store
        agent._current_turn_id = ""
        agent._cached_system_prompt = "KEEP-ATTACH-MISSING"
        _hide_projects_db(home)
        with pytest.raises(RuntimeError, match="projects.db is missing"):
            _attach_project_context(agent, {}, memory_required=False)
        assert agent._memory_store is before
        assert agent.project_id == pid
        assert hasattr(agent._memory_store, "project_store")
        assert agent._cached_system_prompt == "KEEP-ATTACH-MISSING"
        assert not (home / "projects.db").exists()
    finally:
        if agent is not None:
            close_agent(agent)
        drop_home(token)
        db.close()
