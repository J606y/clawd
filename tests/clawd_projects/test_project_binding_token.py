"""DB binding-token transaction tests. Not executed by this change."""

from __future__ import annotations

import inspect
import sqlite3

import pytest

from hermes_cli import projects_db as pdb


def _project(db, path) -> str:
    path.mkdir()
    with pdb.connect_closing(db) as conn:
        return pdb.create_project(conn, name="P", primary_path=str(path))


def test_signature_matches_service_forward_and_reader():
    params = inspect.signature(pdb.bind_session_project).parameters
    assert params["write_token"].default is None
    assert params["write_token"].kind is inspect.Parameter.KEYWORD_ONLY
    assert params["expected_write_token"].default is None
    assert params["expected_write_token"].kind is inspect.Parameter.KEYWORD_ONLY
    reader = inspect.signature(pdb.get_session_write_token)
    assert list(reader.parameters) == ["conn", "session_id"]


def test_legacy_schema_adds_nullable_token_and_cannot_infer_rollback(tmp_path):
    db = tmp_path / "old.db"
    raw = sqlite3.connect(db)
    raw.executescript(
        """
        CREATE TABLE projects (
            id TEXT PRIMARY KEY,
            slug TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            archived INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE project_sessions (
            session_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            bound_at INTEGER NOT NULL
        );
        INSERT INTO projects (id, slug, name, created_at, archived)
        VALUES ('p1', 'p1', 'P', 1, 0);
        INSERT INTO project_sessions (session_id, project_id, bound_at)
        VALUES ('s', 'p1', 1);
        """
    )
    raw.commit()
    raw.close()
    with pdb.connect_closing(db) as conn:
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(project_sessions)")}
        assert "write_token" in cols
        assert pdb.get_session_write_token(conn, "s") is None
        missed = pdb.bind_session_project(conn, "s", "p1", expected_write_token=pdb.new_write_token())
        assert missed.applied is False
        assert pdb.get_session_project_id(conn, "s") == "p1"


def test_rebind_mints_token_and_same_second_aba_does_not_release(tmp_path):
    db = tmp_path / "projects.db"
    pid = _project(db, tmp_path / "repo")
    with pdb.connect_closing(db) as conn:
        first = pdb.bind_session_project(conn, "s", pid)
        bound_at = pdb.get_session_binding(conn, "s").bound_at
        second = pdb.bind_session_project(conn, "s", pid)
        assert second.write_token != first.write_token
        conn.execute("UPDATE project_sessions SET bound_at = ? WHERE session_id = ?", (bound_at, "s"))
        missed = pdb.bind_session_project(conn, "s", pid, expected_write_token=first.write_token)
        assert missed.applied is False
        assert pdb.get_session_write_token(conn, "s") == second.write_token
        released = pdb.bind_session_project(conn, "s", pid, expected_write_token=second.write_token)
        assert released.applied is True
        assert pdb.get_session_write_token(conn, "s") is None
        assert pdb.get_session_project_id(conn, "s") is None


def test_supplied_token_is_idempotent_and_not_reusable(tmp_path):
    db = tmp_path / "projects.db"
    pid = _project(db, tmp_path / "repo")
    other = tmp_path / "other"
    other.mkdir()
    token = pdb.new_write_token()
    with pdb.connect_closing(db) as conn:
        other_id = pdb.create_project(conn, name="Other", primary_path=str(other))
        first = pdb.bind_session_project(conn, "s1", pid, write_token=token)
        bound_at = pdb.get_session_binding(conn, "s1").bound_at
        again = pdb.bind_session_project(conn, "s1", pid, write_token=token)
        assert again.applied is True
        assert again.write_token == token
        assert pdb.get_session_binding(conn, "s1").bound_at == bound_at
        assert conn.execute("SELECT COUNT(*) FROM project_sessions").fetchone()[0] == 1
        with pytest.raises(ValueError, match="not reusable"):
            pdb.bind_session_project(conn, "s2", pid, write_token=token)
        with pytest.raises(ValueError, match="not reusable"):
            pdb.bind_session_project(conn, "s1", other_id, write_token=token)
        assert pdb.get_session_project_id(conn, "s1") == pid
        pdb.bind_session_project(conn, "s1", None)
        with pytest.raises(ValueError, match="not reusable"):
            pdb.bind_session_project(conn, "s1", pid, write_token=token)
        assert pdb.get_session_project_id(conn, "s1") is None


def test_conditional_release_compares_project_and_refuses_timestamp(tmp_path):
    db = tmp_path / "projects.db"
    pid = _project(db, tmp_path / "repo")
    other = tmp_path / "other"
    other.mkdir()
    with pdb.connect_closing(db) as conn:
        other_id = pdb.create_project(conn, name="Other", primary_path=str(other))
        token = pdb.new_write_token()
        pdb.bind_session_project(conn, "s", pid, write_token=token)
        wrong_project = pdb.bind_session_project(conn, "s", other_id, expected_write_token=token)
        assert wrong_project.applied is False
        assert pdb.get_session_project_id(conn, "s") == pid
        with pytest.raises(ValueError):
            pdb.bind_session_project(conn, "s", None, expected_write_token=token)
        with pytest.raises(ValueError):
            pdb.bind_session_project(conn, "s", pid, write_token="1710000000")
        with pytest.raises(ValueError):
            pdb.bind_session_project(
                conn,
                "s",
                pid,
                write_token=pdb.new_write_token(),
                expected_write_token=token,
            )
        assert pdb.get_session_write_token(conn, "s") == token
        assert pdb.get_session_project_id(conn, "s") == pid
