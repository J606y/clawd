"""Boundary cases for child reservation project identity.

未运行。本文件只准备隔离用例，不构成 pass、验收或上线许可。
不要把这些断言拿到生产 state.db / projects.db 上执行。
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from clawd_projects.inheritance import (
    ProjectInheritanceError,
    copy_parent_project_before_release,
    release_owned_child_binding,
)
from gateway.session_worker_child import _reservation_state_db, reserve_child_execution
from hermes_cli import projects_db as pdb
from hermes_constants import reset_hermes_home_override, set_hermes_home_override
from hermes_state import SessionDB
from hermes_state_runtime import RuntimeStoreError, apply_worker_receipt, begin_runtime_epoch, register_worker_execution
from tools.delegate_tool import _child_session_id_for_project


def _make_project(home: Path, name: str) -> str:
    folder = home / name
    folder.mkdir()
    with pdb.connect_closing(home / "projects.db") as conn:
        return pdb.create_project(conn, name=name, primary_path=str(folder))


def _bind(home: Path, session_id: str, project_id: str | None, *, write_token: str | None = None) -> str | None:
    with pdb.connect_closing(home / "projects.db") as conn:
        result = pdb.bind_session_project(conn, session_id, project_id, write_token=write_token)
    return getattr(result, "write_token", None)


def _project_of(home: Path, session_id: str) -> str | None:
    path = home / "projects.db"
    if not path.is_file():
        return None
    with pdb.connect_closing(path) as conn:
        return pdb.get_session_project_id(conn, session_id)


def _token_of(home: Path, session_id: str) -> str | None:
    path = home / "projects.db"
    if not path.is_file():
        return None
    with pdb.connect_closing(path) as conn:
        return pdb.get_session_write_token(conn, session_id)


def _reserve(db: SessionDB, epoch: int, sequence: int = 1):
    from functools import partial

    handler = partial(
        reserve_child_execution,
        epoch=epoch,
        execution_id="worker",
        identity={"profile_id": "home", "pid": 1, "birth": 1},
        principal="local",
        limits=(2, 1),
        refused=None,
    )
    return apply_worker_receipt(
        db,
        epoch=epoch,
        execution_id="worker",
        session_id="parent",
        generation=0,
        sequence=sequence,
        operation="child.reserve",
        payload={"secret": f"child-secret-{sequence:08d}"},
        handlers={"child.reserve": handler},
    )


@pytest.fixture
def home(tmp_path: Path) -> Path:
    root = tmp_path / "profile"
    root.mkdir()
    return root


@pytest.fixture
def session_db(home: Path):
    db = SessionDB(db_path=home / "state.db")
    try:
        yield db
    finally:
        db.close()


def _ledger(home: Path) -> tuple[SessionDB, int]:
    db = SessionDB(db_path=home / "state.db")
    db.create_session("parent", source="kanban", profile_name="coder")
    db.create_session("grand", source="kanban", profile_name="coder")
    epoch = begin_runtime_epoch(db, instance_id="boot")
    register_worker_execution(
        db,
        epoch=epoch,
        execution_id="worker",
        session_id="parent",
        generation=0,
        kind="compute",
        adoption_secret="private-fixture",
    )
    return db, epoch


def test_prebind_source_does_not_force_rebind() -> None:
    source = inspect.getsource(_child_session_id_for_project)
    assert "force=True" not in source
    assert "bind_session_to_project" not in source
    assert "get_hermes_home" not in source


def test_missing_state_path_does_not_publish_or_guess(home: Path, monkeypatch) -> None:
    called = []
    monkeypatch.setattr(
        "clawd_projects.service.bind_session_to_project",
        lambda *args, **kwargs: called.append((args, kwargs)) or True,
    )
    with pytest.raises(ProjectInheritanceError, match="trusted state.db"):
        _child_session_id_for_project("parent", object(), home)
    assert called == []
    assert not (home / "projects.db").exists()


def test_reservation_without_state_db_is_a_store_error_not_a_child(tmp_path: Path) -> None:
    other = tmp_path / "notes.db"
    other.write_bytes(b"not-state")
    with pytest.raises(RuntimeStoreError, match="project_inheritance_failed"):
        _reservation_state_db(SimpleNamespace(db_path=other))
    with pytest.raises(RuntimeStoreError, match="project_inheritance_failed"):
        _reservation_state_db(SimpleNamespace(db_path=None))


def test_reserved_id_keeps_snapshot_and_explicit_unbind(home: Path, session_db: SessionDB) -> None:
    first = _make_project(home, "first")
    second = _make_project(home, "second")
    _bind(home, "parent", first)
    _bind(home, "child-bound", first)
    _bind(home, "parent", second)
    session_db.create_session("child-bound", "subagent", parent_session_id="parent")
    session_db.create_session("child-unbound", "subagent", parent_session_id="parent")

    class Reserved:
        def __init__(self, session_id: str) -> None:
            self.reserved_session_id = session_id
            self.db_path = home / "state.db"

    assert _child_session_id_for_project("parent", Reserved("child-bound"), Path("/tmp/process-home")) == "child-bound"
    assert _project_of(home, "child-bound") == first
    assert _child_session_id_for_project("parent", Reserved("child-unbound"), None) == "child-unbound"
    assert _project_of(home, "child-unbound") is None


def test_in_process_insert_snapshots_state_db_profile_not_process_home(
    home: Path, session_db: SessionDB, tmp_path: Path,
) -> None:
    pid = _make_project(home, "repo")
    session_db.create_session("parent", "cli")
    _bind(home, "parent", pid)
    other = tmp_path / "process-home"
    other.mkdir()
    foreign = _make_project(other, "foreign")
    _bind(other, "parent", foreign)
    token = set_hermes_home_override(str(other))
    try:
        child = _child_session_id_for_project("parent", session_db, other)
    finally:
        reset_hermes_home_override(token)
    assert child
    assert session_db.get_session(child)["parent_session_id"] == "parent"
    assert _project_of(home, child) == pid
    assert _project_of(other, child) is None


def test_existing_session_id_is_not_adopted(home: Path, session_db: SessionDB, monkeypatch) -> None:
    session_db.create_session("taken", "cli")
    monkeypatch.setattr("hermes_state_ids.new_session_id", lambda: "taken")
    with pytest.raises(ProjectInheritanceError, match="existing session id"):
        _child_session_id_for_project("parent", session_db, None)
    assert session_db.get_session("taken")["source"] == "cli"
    assert _project_of(home, "taken") is None


def test_worker_reserve_snapshots_parent_project_and_keeps_delegate_marker(home: Path) -> None:
    db, epoch = _ledger(home)
    try:
        pid = _make_project(home, "repo")
        _bind(home, "parent", pid)
        other = home.parent / "process-home"
        other.mkdir()
        foreign = _make_project(other, "foreign")
        _bind(other, "parent", foreign)
        token = set_hermes_home_override(str(other))
        try:
            child = _reserve(db, epoch)["child"]
        finally:
            reset_hermes_home_override(token)
        row = db.get_session(child["session_id"])
        assert row["parent_session_id"] == "parent"
        assert row["source"] == "subagent"
        assert json.loads(row["model_config"])["_delegate_from"] == "parent"
        assert _project_of(home, child["session_id"]) == pid
        assert _token_of(home, child["session_id"])
        assert _project_of(other, child["session_id"]) is None
    finally:
        db.close()


def test_worker_does_not_walk_ancestors_or_rebind_a_published_child(home: Path) -> None:
    db, epoch = _ledger(home)
    try:
        grand = _make_project(home, "grand")
        _bind(home, "grand", grand)
        db._execute_write(lambda conn: conn.execute(
            "UPDATE sessions SET parent_session_id=? WHERE id=?", ("grand", "parent"),
        ))
        child = _reserve(db, epoch)["child"]
        assert _project_of(home, child["session_id"]) is None

        class Reserved:
            reserved_session_id = child["session_id"]

        later = _make_project(home, "later")
        _bind(home, "parent", later)
        assert _child_session_id_for_project("parent", Reserved(), None) == child["session_id"]
        assert _project_of(home, child["session_id"]) is None
    finally:
        db.close()


def test_bind_failure_rolls_back_the_child_row_and_is_not_a_refusal(home: Path, monkeypatch) -> None:
    db, epoch = _ledger(home)
    try:
        pid = _make_project(home, "repo")
        _bind(home, "parent", pid)

        def _boom(*_args, **_kwargs):
            raise RuntimeError("projects.db write failed")

        monkeypatch.setattr("clawd_projects.inheritance.pdb.bind_session_project", _boom)
        with pytest.raises(RuntimeStoreError, match="project_inheritance_failed") as raised:
            _reserve(db, epoch)
        assert getattr(raised.value, "reason", "") == "project_inheritance_failed"
        with db._read_ctx() as conn:
            assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 2
            assert conn.execute("SELECT COUNT(*) FROM worker_executions WHERE kind='child'").fetchone()[0] == 0
        assert _project_of(home, "parent") == pid
    finally:
        db.close()


def test_release_deletes_only_the_proved_token(home: Path, session_db: SessionDB) -> None:
    pid = _make_project(home, "repo")
    other = _make_project(home, "other")
    owned = _bind(home, "child", pid)
    kept = _bind(home, "sibling", other)
    assert owned and kept
    assert release_owned_child_binding(
        "child", state_db_path=home / "state.db", project_id=pid, write_token="wt_" + "ab" * 16,
    ) is False
    assert _project_of(home, "child") == pid
    assert _project_of(home, "sibling") == other
    with pytest.raises(ProjectInheritanceError):
        release_owned_child_binding(
            "child", state_db_path=home / "state.db", project_id="", write_token=owned,
        )
    assert _project_of(home, "child") == pid
    assert release_owned_child_binding(
        "child", state_db_path=home / "state.db", project_id=pid, write_token=owned,
    ) is True
    assert _project_of(home, "child") is None
    assert _project_of(home, "sibling") == other
    assert _token_of(home, "sibling") == kept


def test_visibility_mismatch_releases_only_this_call_token(home: Path, session_db: SessionDB, monkeypatch) -> None:
    pid = _make_project(home, "repo")
    _bind(home, "parent", pid)
    _bind(home, "sibling", pid)
    seen = {"token": None}

    real_bind = pdb.bind_session_project
    real_get = pdb.get_session_project_id

    def bind(conn, session_id, project_id=None, **kwargs):
        result = real_bind(conn, session_id, project_id, **kwargs)
        if session_id == "child":
            seen["token"] = result.write_token
        return result

    def get_id(conn, session_id):
        value = real_get(conn, session_id)
        if seen["token"] and session_id == "child":
            return "not-the-written-project"
        return value

    monkeypatch.setattr("clawd_projects.inheritance.pdb.bind_session_project", bind)
    monkeypatch.setattr("clawd_projects.inheritance.pdb.get_session_project_id", get_id)
    with pytest.raises(ProjectInheritanceError, match="not visible"):
        copy_parent_project_before_release("parent", "child", state_db_path=home / "state.db")
    assert seen["token"]
    assert _project_of(home, "child") is None
    assert _project_of(home, "parent") == pid
    assert _project_of(home, "sibling") == pid


def test_projects_write_is_not_atomic_with_a_later_state_failure(home: Path, session_db: SessionDB) -> None:
    """The copy can commit projects.db while state.db has no child session row.

    This is the known window. Conditional release is only for the token this
    call returned. It is not a cross-database transaction and not a success.
    """
    pid = _make_project(home, "repo")
    _bind(home, "parent", pid)
    proof = copy_parent_project_before_release("parent", "child", state_db_path=home / "state.db")
    assert proof.wrote is True
    assert proof.write_token
    assert proof.project_id == pid
    assert session_db.get_session("child") is None
    assert _project_of(home, "child") == pid
    assert release_owned_child_binding(
        "child",
        state_db_path=home / "state.db",
        project_id=proof.project_id,
        write_token=proof.write_token,
    ) is True
    assert _project_of(home, "child") is None
    assert _project_of(home, "parent") == pid
