"""A bound parent's failed copy must abort the turn before any model or tool.

Uses the real ``projects.db`` and ``SessionDB`` write. The child row is rolled
back by the store; ``_ensure_db_session`` must not turn that into a warning
that lets the turn continue. A transient create error still retries.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from clawd_projects.inheritance import ProjectInheritanceError
from hermes_cli import projects_db as pdb
from hermes_state import SessionDB


def _make_project(home: Path, name: str) -> str:
    folder = home / name
    folder.mkdir()
    with pdb.connect_closing(home / "projects.db") as conn:
        return pdb.create_project(conn, name=name, primary_path=str(folder))


def _bind(home: Path, session_id: str, project_id: str) -> None:
    with pdb.connect_closing(home / "projects.db") as conn:
        pdb.bind_session_project(conn, session_id, project_id)


def _orphan_binding(home: Path, session_id: str) -> None:
    """Leave a binding whose project row is gone, without CASCADE clearing it."""
    with pdb.connect_closing(home / "projects.db") as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        with pdb.write_txn(conn):
            conn.execute(
                "UPDATE project_sessions SET project_id = ? WHERE session_id = ?",
                ("missing-project", session_id),
            )


def _agent(db: SessionDB, session_id: str, parent: str | None):
    from run_agent import AIAgent

    agent = AIAgent.__new__(AIAgent)
    agent._persist_disabled = False
    agent._session_db_created = False
    agent._session_db = db
    agent.session_id = session_id
    agent.platform = "cli"
    agent.model = "fixture-model"
    agent._session_init_model_config = {}
    agent._parent_session_id = parent
    agent._cached_system_prompt = "prompt"
    return agent


def test_missing_parent_project_rolls_back_and_blocks_the_turn(tmp_path: Path):
    home = tmp_path / "profile"
    home.mkdir()
    db = SessionDB(db_path=home / "state.db")
    ran = []
    try:
        pid = _make_project(home, "repo")
        db.create_session("parent", "cli", model="fixture-model")
        _bind(home, "parent", pid)
        _orphan_binding(home, "parent")
        agent = _agent(db, "child", "parent")

        from agent.turn_context import _ensure_session_row

        def turn():
            _ensure_session_row(agent, None)
            ran.append("model")

        with pytest.raises(ProjectInheritanceError, match="missing"):
            turn()
        assert ran == []
        assert agent._session_db_created is False
        assert db.get_session("child") is None
        assert db.get_session("parent") is not None
    finally:
        db.close()


def test_transient_create_failure_still_warns_and_retries(tmp_path: Path):
    home = tmp_path / "profile"
    home.mkdir()
    db = SessionDB(db_path=home / "state.db")
    try:
        agent = _agent(db, "plain", None)

        def locked(*_args, **_kwargs):
            raise TimeoutError("database is locked")

        agent._session_db.create_session = locked
        agent._ensure_db_session()
        assert agent._session_db_created is False
    finally:
        db.close()
