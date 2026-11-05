"""Project inheritance for fork, edit-branch, and compression.

Bindings live in ``<profile>/projects.db``, not in the session row. These tests
use that store and the real publication functions. They do not infer a project
from the process cwd or from ``HERMES_HOME``.
"""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from clawd_projects.inheritance import (
    ProjectInheritanceError,
    inherit_session_project,
)
from gateway.config import PlatformConfig
from gateway.platforms.api_server import APIServerAdapter
from hermes_cli import projects_db as pdb
from hermes_constants import reset_hermes_home_override, set_hermes_home_override
from hermes_state import SessionDB

_SIDECAR = (
    Path(__file__).resolve().parents[2]
    / "apps"
    / "panel"
    / "sidecar"
    / "hermes_panel_sidecar.py"
)


def _sidecar():
    spec = importlib.util.spec_from_file_location("edit_branch_inheritance_sidecar", _SIDECAR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_project(home: Path, name: str) -> str:
    folder = home / name
    folder.mkdir()
    with pdb.connect_closing(home / "projects.db") as conn:
        return pdb.create_project(conn, name=name, primary_path=str(folder))


def _bind(home: Path, session_id: str, project_id: str | None) -> None:
    with pdb.connect_closing(home / "projects.db") as conn:
        pdb.bind_session_project(conn, session_id, project_id)


def _project_of(home: Path, session_id: str) -> str | None:
    path = home / "projects.db"
    if not path.is_file():
        return None
    with pdb.connect_closing(path) as conn:
        return pdb.get_session_project_id(conn, session_id)


def _session_ids(db: SessionDB) -> set[str]:
    return {row["id"] for row in db.list_sessions_rich(limit=100) or []}


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


def _app(db: SessionDB) -> web.Application:
    adapter = APIServerAdapter(PlatformConfig(enabled=True))
    adapter._session_db = db
    app = web.Application()
    app.router.add_post("/api/sessions/{session_id}/fork", adapter._handle_fork_session)
    app.router.add_post("/api/sessions/{session_id}/chat", adapter._handle_session_chat)
    return app


async def _fork(db: SessionDB, source_id: str, fork_id: str) -> tuple[int, object]:
    async with TestClient(TestServer(_app(db))) as cli:
        resp = await cli.post(f"/api/sessions/{source_id}/fork", json={"id": fork_id})
        try:
            body = await resp.json()
        except Exception:
            body = await resp.text()
        return resp.status, body


def _break_bind(monkeypatch) -> None:
    def _boom(*_args, **_kwargs):
        raise RuntimeError("projects.db write failed")

    monkeypatch.setattr(pdb, "bind_session_project", _boom)
    monkeypatch.setattr("clawd_projects.inheritance.pdb.bind_session_project", _boom)


def test_missing_store_is_ordinary_and_does_not_create_projects_db(tmp_path: Path) -> None:
    assert inherit_session_project("parent", "child", hermes_home=tmp_path) is None
    assert not (tmp_path / "projects.db").exists()


def test_cross_profile_refuses_bound_parent_and_writes_nothing(tmp_path: Path) -> None:
    parent_home = tmp_path / "parent"
    child_home = tmp_path / "child"
    parent_home.mkdir()
    child_home.mkdir()
    pid = _make_project(parent_home, "foreign")
    _bind(parent_home, "src", pid)

    with pytest.raises(ProjectInheritanceError, match="cross-profile"):
        inherit_session_project(
            "src", "copy", hermes_home=child_home, parent_home=parent_home,
        )

    assert not (child_home / "projects.db").exists()
    assert _project_of(parent_home, "src") == pid
    assert _project_of(parent_home, "copy") is None


def test_cross_profile_ordinary_parent_is_not_an_error(tmp_path: Path) -> None:
    parent_home = tmp_path / "parent"
    child_home = tmp_path / "child"
    parent_home.mkdir()
    child_home.mkdir()
    _make_project(parent_home, "unused")

    assert inherit_session_project(
        "src", "copy", hermes_home=child_home, parent_home=parent_home,
    ) is None
    assert not (child_home / "projects.db").exists()


def test_unreadable_store_fails_closed(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "projects.db").write_bytes(b"not a database")

    def _boom(*_args, **_kwargs):
        raise OSError("unreadable")

    monkeypatch.setattr("clawd_projects.inheritance.pdb.connect_closing", _boom)
    with pytest.raises(ProjectInheritanceError, match="could not read"):
        inherit_session_project("parent", "child", hermes_home=tmp_path)


def test_helper_keeps_existing_child_snapshot(home: Path) -> None:
    first = _make_project(home, "first")
    second = _make_project(home, "second")
    _bind(home, "parent", first)
    _bind(home, "child", first)
    _bind(home, "parent", second)

    assert inherit_session_project("parent", "child", hermes_home=home) == first
    assert _project_of(home, "child") == first


@pytest.mark.asyncio
async def test_api_fork_snapshots_project_and_ignores_process_home(
    home: Path, session_db: SessionDB, tmp_path: Path,
) -> None:
    other = tmp_path / "other-profile"
    other.mkdir()
    pid = _make_project(home, "repo")
    foreign = _make_project(other, "foreign")
    session_db.create_session("src", "api_server", model="m")
    session_db.append_message("src", "user", content="hello", timestamp=1)
    _bind(home, "src", pid)
    _bind(other, "src", foreign)
    token = set_hermes_home_override(str(other))
    try:
        status, body = await _fork(session_db, "src", "fork-1")
        assert status == 201
        assert body["session"]["id"] == "fork-1"
        # Durable before any later chat can target the copy.
        assert _project_of(home, "fork-1") == pid
        assert _project_of(other, "fork-1") is None
    finally:
        reset_hermes_home_override(token)

    _bind(home, "src", _make_project(home, "later"))
    assert _project_of(home, "fork-1") == pid
    status2, body2 = await _fork(session_db, "src", "fork-2")
    assert status2 == 201
    assert body2["session"]["id"] == "fork-2"
    assert _project_of(home, "fork-2") != pid
    assert _project_of(home, "fork-1") == pid


async def _client_chat(db: SessionDB, session_id: str):
    async with TestClient(TestServer(_app(db))) as cli:
        return await cli.post(
            f"/api/sessions/{session_id}/chat",
            json={"message": "should not be required for a missing session"},
        )


@pytest.mark.asyncio
async def test_ordinary_fork_does_not_inherit_from_cwd_or_ancestor(
    home: Path, session_db: SessionDB,
) -> None:
    folder = home / "repo"
    folder.mkdir()
    pid = _make_project(home, "repo-project")
    session_db.create_session("grand", "api_server", cwd=str(folder))
    _bind(home, "grand", pid)
    session_db.create_session("plain", "api_server", cwd=str(folder), parent_session_id="grand")
    # The create above copies the grandparent binding because it is a new child.
    # Explicit unbind must not be restored by a later fork walking the ancestor.
    _bind(home, "plain", None)
    assert _project_of(home, "plain") is None

    resp = await _fork(session_db, "plain", "plain-fork")
    assert resp[0] == 201
    assert _project_of(home, "plain-fork") is None

    session_db.create_session("orphan-parent", "api_server", cwd=str(folder))
    session_db._execute_write(
        lambda conn: conn.execute(
            "UPDATE sessions SET parent_session_id=? WHERE id=?",
            ("grand", "orphan-parent"),
        )
    )
    resp2 = await _fork(session_db, "orphan-parent", "no-walk")
    assert resp2[0] == 201
    assert _project_of(home, "no-walk") is None


@pytest.mark.asyncio
async def test_fork_bind_failure_does_not_release_a_runnable_copy(
    home: Path, session_db: SessionDB, monkeypatch,
) -> None:
    pid = _make_project(home, "repo")
    session_db.create_session("src", "api_server")
    _bind(home, "src", pid)
    _break_bind(monkeypatch)

    try:
        status, _body = await _fork(session_db, "src", "blocked-fork")
    except ProjectInheritanceError:
        status = 500
    else:
        assert status != 201

    assert session_db.get_session("blocked-fork") is None
    parent = session_db.get_session("src")
    assert parent is not None
    assert parent["ended_at"] is None
    assert _project_of(home, "blocked-fork") is None
    chat = await _client_chat(session_db, "blocked-fork")
    assert chat.status == 404


def test_edit_branch_snapshots_and_retry_does_not_rebind(home: Path) -> None:
    pid = _make_project(home, "repo")
    later = _make_project(home, "later")
    db = SessionDB(db_path=home / "state.db")
    db.create_session("original", "api_server", model="old-model")
    db.append_message("original", "user", content="keep", timestamp=10)
    db.append_message("original", "user", content="edit me", timestamp=20)
    _bind(home, "original", pid)
    db.close()

    module = _sidecar()
    request_id = str(uuid.uuid4())
    payload = {
        "sessionId": "original",
        "fromEnd": 1,
        "expectedContent": "edit me",
        "expectedTimestamp": 20,
        "requestId": request_id,
        "model": "new-model",
        "provider": "custom",
    }
    result = module.create_edit_branch(home / "state.db", payload)
    child = result["session"]["id"]
    assert child == "api_edit_" + request_id
    assert _project_of(home, child) == pid
    import sqlite3
    with sqlite3.connect(home / "state.db") as conn:
        parent_link = conn.execute(
            "SELECT parent_session_id FROM sessions WHERE id=?", (child,),
        ).fetchone()[0]
    assert parent_link in (None, "")

    _bind(home, "original", later)
    _bind(home, child, None)
    again = module.create_edit_branch(home / "state.db", payload)
    assert again["session"]["id"] == child
    assert _project_of(home, child) is None
    assert _project_of(home, "original") == later


def test_edit_branch_keeps_crash_snapshot_and_blocks_failed_bind(
    home: Path, monkeypatch,
) -> None:
    pid = _make_project(home, "repo")
    later = _make_project(home, "later")
    db = SessionDB(db_path=home / "state.db")
    db.create_session("original", "api_server", model="old-model")
    db.append_message("original", "user", content="edit me", timestamp=20)
    _bind(home, "original", pid)
    request_id = str(uuid.uuid4())
    child = "api_edit_" + request_id
    _bind(home, child, pid)
    _bind(home, "original", later)
    db.close()

    module = _sidecar()
    payload = {
        "sessionId": "original",
        "fromEnd": 1,
        "expectedContent": "edit me",
        "expectedTimestamp": 20,
        "requestId": request_id,
        "model": "new-model",
        "provider": "custom",
    }
    result = module.create_edit_branch(home / "state.db", payload)
    assert result["session"]["id"] == child
    assert _project_of(home, child) == pid

    fail_id = str(uuid.uuid4())
    fail_payload = dict(payload, requestId=fail_id)
    _bind(home, "original", pid)
    _break_bind(monkeypatch)
    with pytest.raises(module.ApiError) as raised:
        module.create_edit_branch(home / "state.db", fail_payload)
    assert raised.value.status == 503
    assert raised.value.code == "project_inheritance_failed"
    db = SessionDB(db_path=home / "state.db")
    try:
        assert db.get_session("api_edit_" + fail_id) is None
        assert db.get_session("original") is not None
    finally:
        db.close()


def test_compression_and_branch_publisher_snapshot_before_release(
    home: Path, session_db: SessionDB, tmp_path: Path,
) -> None:
    other = tmp_path / "other"
    other.mkdir()
    pid = _make_project(home, "repo")
    foreign = _make_project(other, "foreign")
    session_db.create_session("src", "api_server")
    session_db.append_message("src", "user", content="keep", timestamp=1)
    _bind(home, "src", pid)
    _bind(other, "src", foreign)
    token = set_hermes_home_override(str(other))
    holder = "inherit-test"
    try:
        assert session_db.try_acquire_compression_lock("src", holder)
        session_db.publish_compression_child(
            parent_session_id="src",
            child_session_id="compressed",
            source="api_server",
            messages=[{"role": "user", "content": "handoff"}],
            compression_lock_holder=holder,
        )
        assert _project_of(home, "compressed") == pid
        assert _project_of(other, "compressed") is None
        parent = session_db.get_session("src")
        child = session_db.get_session("compressed")
        assert parent is not None and child is not None
        assert parent["end_reason"] == "compression"
    finally:
        reset_hermes_home_override(token)

    _bind(home, "src", _make_project(home, "later"))
    assert _project_of(home, "compressed") == pid

    def _branch(conn):
        parent = conn.execute("SELECT * FROM sessions WHERE id=?", ("compressed",)).fetchone()
        session_db._publish_child_session_row(
            conn, parent, parent_session_id="compressed", child_session_id="branch-child",
            source="cli", model="m", model_config={"_branched_from": "compressed"},
            system_prompt=None, cwd=None, profile_name=None,
        )

    session_db._execute_write(_branch)
    assert _project_of(home, "branch-child") == pid
    _bind(home, "compressed", _project_of(home, "src"))
    assert _project_of(home, "branch-child") == pid


def test_compression_bind_failure_keeps_parent_and_does_not_switch(
    home: Path, session_db: SessionDB, monkeypatch,
) -> None:
    pid = _make_project(home, "repo")
    session_db.create_session("src", "api_server")
    session_db.append_message("src", "user", content="keep", timestamp=1)
    _bind(home, "src", pid)
    holder = "inherit-fail"
    assert session_db.try_acquire_compression_lock("src", holder)
    _break_bind(monkeypatch)

    switched = False
    active = "src"
    with pytest.raises(ProjectInheritanceError):
        session_db.publish_compression_child(
            parent_session_id="src",
            child_session_id="blocked-child",
            source="api_server",
            messages=[{"role": "user", "content": "handoff"}],
            compression_lock_holder=holder,
        )
        switched = True
        active = "blocked-child"

    assert switched is False
    assert active == "src"
    assert session_db.get_session("blocked-child") is None
    parent = session_db.get_session("src")
    assert parent is not None
    assert parent["ended_at"] is None
    assert parent["end_reason"] is None
    assert _project_of(home, "blocked-child") is None


def test_archive_does_not_delete_sessions_and_fork_still_copies_id(
    home: Path, session_db: SessionDB,
) -> None:
    pid = _make_project(home, "repo")
    session_db.create_session("src", "api_server")
    _bind(home, "src", pid)
    with pdb.connect_closing(home / "projects.db") as conn:
        assert pdb.archive_project(conn, pid) is True
    assert session_db.get_session("src") is not None
    assert (home / "repo").is_dir()

    import asyncio
    status, _body = asyncio.run(_fork(session_db, "src", "archived-fork"))
    assert status == 201
    source = session_db.get_session("src")
    child = session_db.get_session("archived-fork")
    assert source is not None and child is not None
    assert _project_of(home, "archived-fork") == pid
    assert _project_of(home, "src") == pid
