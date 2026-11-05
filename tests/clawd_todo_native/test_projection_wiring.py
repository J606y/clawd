"""Child row plus delegation receipt to list_subagents fields.

Prepared only. This module was not executed. It must stay on a tmp SessionDB
and must not open a production home.
"""

from __future__ import annotations

import json
import time

import pytest

from hermes_state import SessionDB
from hermes_state_subagents import list_subagents


@pytest.fixture
def db(tmp_path):
    store = SessionDB(db_path=tmp_path / "state.db")
    yield store
    store.close()


def _child(db, session_id, parent, **model_config):
    db.create_session(
        session_id,
        "subagent",
        model="m",
        parent_session_id=parent,
        model_config=model_config or None,
    )


def _receipt(db, delegation_id, parent, links):
    now = time.time()
    payload = json.dumps({"child_links": links})

    def _insert(conn):
        conn.execute(
            "INSERT OR REPLACE INTO async_delegations "
            "(delegation_id, origin_session, parent_session_id, state, dispatched_at, updated_at, task_json) "
            "VALUES (?, ?, ?, 'running', ?, ?, ?)",
            (delegation_id, parent, parent, now, now, payload),
        )

    db._execute_write(_insert)


def _one(db, parent):
    page = list_subagents(db, parent, limit=12)
    assert len(page["data"]) == 1
    return page, page["data"][0]


def test_row_id_is_child_session_not_subagent_or_index(db):
    _child(
        db,
        "child-sess",
        "parent",
        _clawd_delegation_id="deleg-1",
        _clawd_todo_id="todo-1",
        _clawd_subagent_id="sa-not-session",
    )
    _receipt(db, "deleg-1", "parent", [{
        "task_index": 0,
        "todo_id": "todo-1",
        "delegation_id": "deleg-1",
        "subagent_id": "sa-not-session",
        "child_session_id": "child-sess",
    }])
    page, row = _one(db, "parent")
    assert row["id"] == "child-sess"
    assert row["id"] != "sa-not-session"
    assert row["delegationId"] == "deleg-1"
    assert row["todoId"] == "todo-1"
    assert row["executionStatus"] == "running"
    assert "sa-not-session" not in row.values()
    assert 0 not in row.values()
    assert page["data"][0]["parentId"] == "parent"


def test_receipt_fills_todo_when_session_patch_missed_it(db):
    _child(db, "child-sess", "parent", _clawd_delegation_id="deleg-1")
    _receipt(db, "deleg-1", "parent", [{
        "task_index": 3,
        "subagent_id": "sa-batch",
        "delegation_id": "deleg-1",
        "todo_id": "todo-from-receipt",
        "child_session_id": "child-sess",
    }])
    _, row = _one(db, "parent")
    assert row["id"] == "child-sess"
    assert row["delegationId"] == "deleg-1"
    assert row["todoId"] == "todo-from-receipt"


def test_subagent_id_and_task_index_do_not_identify_a_child(db):
    _child(db, "child-sess", "parent")
    _receipt(db, "deleg-x", "parent", [{
        "task_index": 0,
        "subagent_id": "sa-only",
        "delegation_id": "deleg-x",
        "todo_id": "todo-x",
    }])
    _, row = _one(db, "parent")
    assert row["id"] == "child-sess"
    assert "delegationId" not in row
    assert "todoId" not in row


def test_disagreement_omits_only_the_contested_field(db):
    _child(db, "child-sess", "parent", _clawd_delegation_id="deleg-row", _clawd_todo_id="todo-1")
    _receipt(db, "deleg-receipt", "parent", [{
        "delegation_id": "deleg-receipt",
        "todo_id": "todo-1",
        "child_session_id": "child-sess",
    }])
    _, row = _one(db, "parent")
    assert "delegationId" not in row
    assert row["todoId"] == "todo-1"


def test_two_receipts_claiming_one_child_withhold_link_ids(db):
    _child(db, "child-sess", "parent", _clawd_delegation_id="deleg-row", _clawd_todo_id="todo-row")
    _receipt(db, "deleg-a", "parent", [{
        "delegation_id": "deleg-a",
        "todo_id": "todo-a",
        "child_session_id": "child-sess",
    }])
    _receipt(db, "deleg-b", "parent", [{
        "delegation_id": "deleg-b",
        "todo_id": "todo-b",
        "child_session_id": "child-sess",
    }])
    _, row = _one(db, "parent")
    assert row["id"] == "child-sess"
    assert "delegationId" not in row
    assert "todoId" not in row
    assert row["executionStatus"] == "running"


def test_other_parent_receipt_does_not_override_this_row(db):
    _child(db, "child-sess", "parent", _clawd_delegation_id="deleg-row", _clawd_todo_id="todo-row")
    _receipt(db, "deleg-other", "other-parent", [{
        "delegation_id": "deleg-other",
        "todo_id": "todo-other",
        "child_session_id": "child-sess",
    }])
    _, row = _one(db, "parent")
    assert row["delegationId"] == "deleg-row"
    assert row["todoId"] == "todo-row"


def test_parent_filter_drops_another_parents_child(db):
    _child(db, "mine", "parent")
    _child(db, "theirs", "other")
    page = list_subagents(db, "parent", limit=12)
    assert [row["id"] for row in page["data"]] == ["mine"]


def test_terminal_mapping_does_not_use_completed(db):
    cases = (
        ("closed", "agent_close", "returned", "done"),
        ("blank", "", "returned", "done"),
        ("bad", "failed", "failed", "ended"),
        ("stopped", "interrupted", "interrupted", "ended"),
        ("dropped", "cancelled", "cancelled", "ended"),
        ("rotated", "compression", "unknown", "ended"),
        ("unit-done", "completed", "unknown", "ended"),
    )
    for session_id, reason, execution, panel in cases:
        _child(db, session_id, "parent")
        if reason:
            db.end_session(session_id, reason)
        else:
            db._execute_write(lambda conn, sid=session_id: conn.execute(
                "UPDATE sessions SET ended_at = ?, end_reason = '' WHERE id = ?",
                (time.time(), sid),
            ))
    page = list_subagents(db, "parent", limit=20)
    by_id = {row["id"]: row for row in page["data"]}
    for session_id, _reason, execution, panel in cases:
        assert by_id[session_id]["executionStatus"] == execution
        assert by_id[session_id]["status"] == panel
        assert by_id[session_id]["executionStatus"] != "completed"


def test_unproven_home_does_not_invent_default(db, monkeypatch):
    def boom():
        raise AssertionError("process home must not name the profile")

    monkeypatch.setattr("hermes_constants.get_hermes_home", boom)
    _child(db, "child-sess", "parent", _clawd_delegation_id="deleg-1")
    page = list_subagents(db, "parent", limit=12)
    assert "profileKey" not in page
    assert "profileKey" not in page["data"][0]
    assert page.get("profileKey") != "default"


def test_store_proven_profile_is_view_and_row_identity(db):
    db._own_profile_name = lambda: "alpha"
    _child(db, "child-sess", "parent")
    page = list_subagents(db, "parent", limit=12)
    assert page["profileKey"] == "alpha"
    assert page["data"][0]["profileKey"] == "alpha"


def test_store_proven_default_is_not_a_fallback(db):
    db._own_profile_name = lambda: "default"
    _child(db, "child-sess", "parent")
    page = list_subagents(db, "parent", limit=12)
    assert page["profileKey"] == "default"
    assert page["data"][0]["profileKey"] == "default"
