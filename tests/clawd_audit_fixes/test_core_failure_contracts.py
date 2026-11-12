"""Confirmed core failure contracts. NOT EXECUTED.

This file was added with the source change and has not been run. Do not treat
its presence as acceptance, a probe, or a passing regression.
"""
from __future__ import annotations

import asyncio
from pathlib import Path


def test_provider_auth_failure_result_is_not_completed():
    from gateway.platforms.api_server import (
        _ProviderAuthResolutionError,
        _provider_auth_failure_result,
    )
    from gateway.platforms.api_server_openai_routes import _response_status

    result = _provider_auth_failure_result(_ProviderAuthResolutionError("probe-injected"))
    assert result["failed"] is True
    assert result["error"]
    assert result["completed"] is False
    assert _response_status(result) == "failed"
    assert "probe-injected" not in str(result["error"])


def test_history_read_error_is_not_empty_and_chat_would_refuse():
    from gateway.platforms.api_server import (
        APIServerAdapter,
        _SessionHistoryUnreadable,
        _history_unreadable_response,
    )

    class DB:
        def get_messages_as_conversation(self, session_id):
            raise RuntimeError("probe-history-read")

    class Stub:
        async def _ensure_session_db_async(self):
            return DB()

    try:
        asyncio.run(APIServerAdapter._conversation_history_for_session(Stub(), "sid"))
    except _SessionHistoryUnreadable as exc:
        assert exc.reason == "read_failed"
    else:
        raise AssertionError("read failure was treated as history")

    class Missing:
        async def _ensure_session_db_async(self):
            return None

    try:
        asyncio.run(APIServerAdapter._conversation_history_for_session(Missing(), "sid"))
    except _SessionHistoryUnreadable as exc:
        assert exc.reason == "unavailable"
    else:
        raise AssertionError("missing store was treated as empty history")

    empty = type("Empty", (), {"get_messages_as_conversation": staticmethod(lambda _sid: [])})()

    class EmptyStore:
        async def _ensure_session_db_async(self):
            return empty

    assert asyncio.run(APIServerAdapter._conversation_history_for_session(EmptyStore(), "sid")) == []
    response = _history_unreadable_response()
    assert response.status == 503


def test_release_after_delete_removes_binding_and_refuses_live_row(tmp_path: Path):
    from hermes_cli import projects_db as pdb
    from hermes_state import SessionDB
    from clawd_projects.service import (
        ProjectBindingReleaseError,
        bind_session_to_project,
        release_project_bindings_after_delete,
    )

    home = tmp_path
    workspace = home / "workspace"
    workspace.mkdir()
    db = SessionDB(db_path=home / "state.db")
    db.create_session("live-row", "api_server")
    with pdb.connect_closing(home / "projects.db") as conn:
        project_id = pdb.create_project(
            conn, name="probe-project", primary_path=str(workspace), allow_duplicate_path=True)
    assert bind_session_to_project("live-row", project_id, hermes_home=home, session_db=db)
    try:
        release_project_bindings_after_delete(
            ["live-row"], state_db_path=home / "state.db", session_db=db)
    except ProjectBindingReleaseError:
        pass
    else:
        raise AssertionError("live row was unbound around the delete guard")
    with pdb.connect_closing(home / "projects.db") as conn:
        assert pdb.get_session_project_id(conn, "live-row") == project_id
    assert db.delete_session("live-row") is True
    with pdb.connect_closing(home / "projects.db") as conn:
        assert pdb.get_session_project_id(conn, "live-row") is None
        assert "live-row" not in pdb.list_project_sessions(conn, project_id)
    db.close()


def test_fork_rollback_removes_unpublished_child(tmp_path: Path):
    from hermes_state import SessionDB
    from gateway.platforms.api_server import _rollback_failed_fork_sync

    db = SessionDB(db_path=tmp_path / "state.db")
    db.create_session("child", "api_server")
    db.append_message = getattr(db, "append_message", None)

    class Snapshot:
        wrote = False
        write_token = None
        project_id = None

    assert _rollback_failed_fork_sync(db, "child", Snapshot()) is None
    assert db.get_session("child") is None
    db.close()
