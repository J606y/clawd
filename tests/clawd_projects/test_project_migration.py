"""Explicit folder migration tests. Not executed by the implementation change."""

from __future__ import annotations

import inspect
import json
import sqlite3
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from clawd_projects.migration import (
    MigrationError,
    execute_folder_migration,
    plan_folder_migration,
    preview_folder_migration,
    rollback_folder_migration,
)
from clawd_projects.service import bind_session_to_project, commit_session_binding
from gateway.platforms.clawd_project_routes import register_project_routes
from hermes_cli import projects_db as pdb
from hermes_constants import reset_hermes_home_override, set_hermes_home_override


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWD_HOME", str(tmp_path))
    token = set_hermes_home_override(str(tmp_path))
    try:
        yield tmp_path
    finally:
        reset_hermes_home_override(token)


def _folders(home: Path, rows: list[tuple[str, str]], assignments: list[tuple[str, str]]) -> bytes:
    path = home / "panel_folders.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE folders (id TEXT PRIMARY KEY, name TEXT NOT NULL, name_key TEXT NOT NULL UNIQUE, collapsed INTEGER NOT NULL DEFAULT 0)"
    )
    conn.execute(
        "CREATE TABLE assignments (session_id TEXT PRIMARY KEY, folder_id TEXT NOT NULL REFERENCES folders(id) ON DELETE CASCADE)"
    )
    for folder_id, name in rows:
        conn.execute(
            "INSERT INTO folders (id, name, name_key, collapsed) VALUES (?, ?, ?, 0)",
            (folder_id, name, name.casefold()),
        )
    for session_id, folder_id in assignments:
        conn.execute("INSERT INTO assignments (session_id, folder_id) VALUES (?, ?)", (session_id, folder_id))
    conn.commit()
    conn.close()
    return path.read_bytes()


def _sessions(home: Path, *session_ids: str):
    from hermes_state import SessionDB

    db = SessionDB(db_path=home / "state.db")
    for session_id in session_ids:
        db.create_session(session_id, "api_server")
    return db


def _mapping(folder_id: str, path: Path, create: bool = False) -> dict:
    return {"folder_id": folder_id, "primary_path": str(path), "create_directory": create}


def test_preview_does_not_guess_or_write(isolated: Path):
    (isolated / "Work").mkdir()
    before = _folders(isolated, [("fld", "Work")], [("s1", "fld")])
    payload = preview_folder_migration(isolated)
    assert payload["success"] is True
    assert payload["read_only"] is True
    assert payload["applied"] is False
    assert payload["folders"][0]["primary_path"] is None
    assert payload["folders"][0]["guessed"] is False
    assert payload["folders"][0]["status"] == "needs_explicit_path"
    assert (isolated / "panel_folders.db").read_bytes() == before
    assert not (isolated / "projects.db").exists()


def test_missing_source_is_not_an_empty_migration(isolated: Path):
    payload = preview_folder_migration(isolated)
    assert payload["source_missing"] is True
    assert payload["folders"] == []
    assert payload["applied"] is False
    assert not (isolated / "projects.db").exists()


def test_execute_requires_confirm_and_explicit_directory(isolated: Path):
    before = _folders(isolated, [("fld", "Work")], [])
    with pytest.raises(MigrationError) as missing_confirm:
        execute_folder_migration([_mapping("fld", isolated / "repo")], None, confirm=False, hermes_home=isolated)
    assert missing_confirm.value.code == "confirm_required"
    with pytest.raises(MigrationError) as relative:
        execute_folder_migration(
            [{"folder_id": "fld", "primary_path": "repo", "create_directory": False}],
            None,
            confirm=True,
            hermes_home=isolated,
        )
    assert relative.value.code == "invalid_directory"
    missing = isolated / "missing-repo"
    with pytest.raises(MigrationError) as absent:
        execute_folder_migration([_mapping("fld", missing)], None, confirm=True, hermes_home=isolated)
    assert absent.value.code == "invalid_directory"
    assert not missing.exists()
    assert (isolated / "panel_folders.db").read_bytes() == before
    assert not (isolated / "projects.db").exists()


def test_forbidden_and_foreign_homes_do_not_write(isolated: Path, tmp_path: Path, monkeypatch):
    _folders(isolated, [("fld", "Work")], [])
    with pytest.raises(MigrationError) as forbidden:
        execute_folder_migration([_mapping("fld", Path("/etc"))], None, confirm=True, hermes_home=isolated)
    assert forbidden.value.code == "forbidden_path"
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    _folders(foreign, [("fld", "Work")], [])
    monkeypatch.setenv("CLAWD_HOME", str(isolated))
    with pytest.raises(MigrationError) as scope:
        preview_folder_migration(foreign)
    assert scope.value.code == "scope_refused"
    assert not (foreign / "projects.db").exists()
    assert not (isolated / "projects.db").exists()


def test_plan_is_read_only(isolated: Path):
    repo = isolated / "repo"
    repo.mkdir()
    before = _folders(isolated, [("fld", "Work")], [])
    planned = plan_folder_migration([_mapping("fld", repo)], None, isolated)
    assert planned["success"] is True
    assert planned["read_only"] is True
    assert planned["applied"] is False
    assert planned["directories_created"] is False
    assert (isolated / "panel_folders.db").read_bytes() == before
    assert not (isolated / "projects.db").exists()
    assert not (isolated / "project_folder_migrations.db").exists()


def test_conflict_and_busy_do_not_overwrite(isolated: Path):
    repo = isolated / "repo"
    other = isolated / "other"
    repo.mkdir()
    other.mkdir()
    before = _folders(
        isolated,
        [("fld", "Work"), ("busy", "Busy")],
        [("s1", "fld"), ("s_busy", "busy")],
    )
    db = _sessions(isolated, "s1", "s_busy")
    try:
        with pdb.connect_closing(isolated / "projects.db") as conn:
            other_id = pdb.create_project(conn, name="Other", primary_path=str(other))
        assert bind_session_to_project("s1", other_id, hermes_home=isolated, session_db=db) is True
        with pytest.raises(MigrationError) as conflict:
            execute_folder_migration([_mapping("fld", repo)], db, confirm=True, hermes_home=isolated)
        assert conflict.value.status == 409
        with pdb.connect_closing(isolated / "projects.db") as conn:
            assert pdb.get_session_project_id(conn, "s1") == other_id
            assert len(pdb.list_projects(conn, include_archived=True)) == 1

        db._execute_write(
            lambda conn: conn.execute(
                "INSERT INTO session_admissions (admission_id, request_id, principal_id, target_session_id, "
                "lineage_json, payload_json, payload_digest, intent, status, owner_epoch, generation) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                ("adm-busy", "req-busy", "prin", "s_busy", "{}", "{}", "digest", "queue", "started", 1, 1),
            )
        )
        with pytest.raises(MigrationError) as busy:
            execute_folder_migration([_mapping("busy", isolated / "busy-repo", True)], db, confirm=True, hermes_home=isolated)
        assert busy.value.code == "session_busy"
        assert not (isolated / "busy-repo").exists()
        with pdb.connect_closing(isolated / "projects.db") as conn:
            assert len(pdb.list_projects(conn, include_archived=True)) == 1
        assert (isolated / "panel_folders.db").read_bytes() == before
    finally:
        db.close()


def test_missing_session_writes_nothing(isolated: Path):
    repo = isolated / "repo"
    repo.mkdir()
    before = _folders(isolated, [("fld", "Work")], [("missing", "fld")])
    db = _sessions(isolated, "other")
    try:
        with pytest.raises(MigrationError) as missing:
            execute_folder_migration([_mapping("fld", repo)], db, confirm=True, hermes_home=isolated)
        assert missing.value.status == 404
        assert not (isolated / "projects.db").exists()
        assert (isolated / "panel_folders.db").read_bytes() == before
    finally:
        db.close()


def test_apply_is_idempotent_and_rollback_keeps_source(isolated: Path):
    repo = isolated / "repo"
    repo.mkdir()
    before = _folders(isolated, [("fld", "Work")], [("s1", "fld")])
    db = _sessions(isolated, "s1")
    try:
        first = execute_folder_migration(
            [_mapping("fld", repo)],
            db,
            confirm=True,
            idempotency_key="same-key",
            hermes_home=isolated,
        )
        assert first["success"] is True
        assert first["status"] == "applied"
        assert first["cross_db_atomic"] is False
        assert first["source_write_attempted"] is False
        project_id = first["folders"][0]["project_id"]
        token = first["folders"][0]["sessions"][0]["write_token"]
        assert token
        second = execute_folder_migration(
            [_mapping("fld", repo)],
            db,
            confirm=True,
            idempotency_key="same-key",
            hermes_home=isolated,
        )
        assert second["success"] is True
        assert second["idempotent_replay"] is True
        assert second["migration_id"] == first["migration_id"]
        assert second["folders"][0]["sessions"][0]["write_token"] == token
        with pdb.connect_closing(isolated / "projects.db") as conn:
            assert pdb.get_session_project_id(conn, "s1") == project_id
            assert pdb.get_session_write_token(conn, "s1") == token
            assert len(pdb.list_projects(conn, include_archived=True)) == 1
            project = pdb.get_project(conn, project_id)
            assert project is not None and project.archived is False
        assert (isolated / "panel_folders.db").read_bytes() == before
        assert db.get_session("s1") is not None

        other = isolated / "other"
        other.mkdir()
        with pdb.connect_closing(isolated / "projects.db") as conn:
            other_id = pdb.create_project(conn, name="Later", primary_path=str(other))
        bind_session_to_project("s1", other_id, hermes_home=isolated, session_db=db)
        rolled = rollback_folder_migration(first["migration_id"], db, confirm=True, hermes_home=isolated)
        assert rolled["success"] is True
        assert rolled["fully_restored"] is False
        assert rolled["directories_deleted"] is False
        assert rolled["project_archived"] is False
        with pdb.connect_closing(isolated / "projects.db") as conn:
            assert pdb.get_session_project_id(conn, "s1") == other_id
            assert pdb.get_project(conn, project_id) is not None
        assert repo.is_dir()
        assert (isolated / "panel_folders.db").read_bytes() == before
        assert db.get_session("s1") is not None
    finally:
        db.close()


def test_rollback_restores_only_unchanged_binding(isolated: Path):
    repo = isolated / "repo"
    repo.mkdir()
    _folders(isolated, [("fld", "Work")], [("s1", "fld")])
    db = _sessions(isolated, "s1")
    try:
        created = isolated / "created"
        receipt = execute_folder_migration(
            [_mapping("fld", created, True)],
            db,
            confirm=True,
            hermes_home=isolated,
        )
        assert created.is_dir()
        rolled = rollback_folder_migration(receipt["migration_id"], db, confirm=True, hermes_home=isolated)
        assert rolled["success"] is True
        assert rolled["fully_restored"] is True
        with pdb.connect_closing(isolated / "projects.db") as conn:
            assert pdb.get_session_project_id(conn, "s1") is None
            project = pdb.get_project(conn, receipt["folders"][0]["project_id"])
            assert project is not None
            assert project.archived is False
        assert created.is_dir()
        conn = sqlite3.connect(isolated / "panel_folders.db")
        try:
            assert conn.execute("SELECT COUNT(*) FROM folders").fetchone()[0] == 1
            assert conn.execute("SELECT folder_id FROM assignments").fetchone()[0] == "fld"
        finally:
            conn.close()
    finally:
        db.close()


def test_partial_failure_is_not_success_and_resume_does_not_duplicate(isolated: Path, monkeypatch):
    repo = isolated / "repo"
    repo.mkdir()
    _folders(isolated, [("fld", "Work")], [("s1", "fld"), ("s2", "fld")])
    db = _sessions(isolated, "s1", "s2")
    real_bind = commit_session_binding
    failed = {"s2": False}

    def flaky_bind(session_id, project_id, hermes_home=None, session_db=None, force=False, **kwargs):
        if session_id == "s2" and project_id is not None and not failed["s2"]:
            failed["s2"] = True
            raise RuntimeError("injected bind failure")
        return real_bind(
            session_id,
            project_id,
            hermes_home=hermes_home,
            session_db=session_db,
            force=force,
            **kwargs,
        )

    monkeypatch.setattr("clawd_projects.migration.commit_session_binding", flaky_bind)
    try:
        partial = execute_folder_migration([_mapping("fld", repo)], db, confirm=True, hermes_home=isolated)
        assert partial["success"] is False
        assert partial["status"] == "partial"
        project_id = partial["folders"][0]["project_id"]
        resumed = execute_folder_migration(
            [_mapping("fld", repo)],
            db,
            confirm=True,
            idempotency_key=partial["idempotency_key"],
            hermes_home=isolated,
        )
        assert resumed["success"] is True
        assert resumed["folders"][0]["project_id"] == project_id
        with pdb.connect_closing(isolated / "projects.db") as conn:
            assert len(pdb.list_projects(conn, include_archived=True)) == 1
            assert pdb.get_session_project_id(conn, "s1") == project_id
            assert pdb.get_session_project_id(conn, "s2") == project_id
    finally:
        db.close()


class _Adapter:
    def __init__(self, session_db):
        self._session_db = session_db

    def _check_auth(self, request):
        if request.headers.get("Authorization") != "Bearer sk-test-key":
            return web.json_response({"error": "unauthorized"}, status=401)
        return None

    def _ensure_session_db(self):
        return self._session_db


@pytest.mark.asyncio
async def test_http_migration_requires_auth_and_confirm(isolated: Path):
    repo = isolated / "repo"
    repo.mkdir()
    before = _folders(isolated, [("fld", "Work")], [("s1", "fld")])
    db = _sessions(isolated, "s1")
    app = web.Application()
    register_project_routes(app, _Adapter(db))
    headers = {"Authorization": "Bearer sk-test-key"}
    try:
        async with TestClient(TestServer(app)) as client:
            denied = await client.get("/api/projects/migrations/preview")
            assert denied.status == 401
            preview = await client.get("/api/projects/migrations/preview", headers=headers)
            assert preview.status == 200
            body = await preview.json()
            assert body["folders"][0]["primary_path"] is None
            refused = await client.post("/api/projects/migrations", json={"confirm": False, "mappings": [_mapping("fld", repo)]}, headers=headers)
            assert refused.status == 400
            assert (await refused.json())["success"] is False
            executed = await client.post(
                "/api/projects/migrations",
                json={"confirm": True, "idempotency_key": "http", "mappings": [_mapping("fld", repo)]},
                headers=headers,
            )
            assert executed.status == 200
            receipt = await executed.json()
            assert receipt["success"] is True
            assert receipt["status"] == "applied"
            fetched = await client.get(f"/api/projects/migrations/{receipt['migration_id']}", headers=headers)
            assert fetched.status == 200
            rolled = await client.post(
                f"/api/projects/migrations/{receipt['migration_id']}/rollback",
                json={"confirm": True},
                headers=headers,
            )
            assert rolled.status == 200
            assert (await rolled.json())["success"] is True
        assert (isolated / "panel_folders.db").read_bytes() == before
    finally:
        db.close()


def test_create_project_api_has_no_explicit_id():
    params = inspect.signature(pdb.create_project).parameters
    assert "project_id" not in params
    assert "slug" in params
    assert "description" in params


def test_foreign_path_is_not_borrowed(isolated: Path):
    repo = isolated / "repo"
    repo.mkdir()
    before = _folders(isolated, [("fld", "Work")], [("s1", "fld")])
    db = _sessions(isolated, "s1")
    try:
        with pdb.connect_closing(isolated / "projects.db") as conn:
            foreign = pdb.create_project(conn, name="Other", primary_path=str(repo), description="not-ours")
        with pytest.raises(MigrationError) as err:
            execute_folder_migration([_mapping("fld", repo)], db, confirm=True, hermes_home=isolated)
        assert err.value.code == "migration_conflict"
        with pdb.connect_closing(isolated / "projects.db") as conn:
            projects = pdb.list_projects(conn, include_archived=True)
            assert [item.id for item in projects] == [foreign]
            assert pdb.get_session_project_id(conn, "s1") is None
        assert (isolated / "panel_folders.db").read_bytes() == before
    finally:
        db.close()


def test_crash_after_create_adopts_only_proven_intent(isolated: Path, monkeypatch):
    repo = isolated / "repo"
    repo.mkdir()
    (repo / "local.txt").write_text("keep")
    _folders(isolated, [("fld", "Work")], [("s1", "fld")])
    db = _sessions(isolated, "s1")
    from clawd_projects import migration as migration_mod

    real = migration_mod._create_project

    def crash(home, name, primary_path, *, slug, description):
        real(home, name, primary_path, slug=slug, description=description)
        raise RuntimeError("crash after create")

    monkeypatch.setattr(migration_mod, "_create_project", crash)
    try:
        with pytest.raises(RuntimeError, match="crash after create"):
            execute_folder_migration(
                [_mapping("fld", repo)],
                db,
                confirm=True,
                idempotency_key="crash-key",
                hermes_home=isolated,
            )
        with pdb.connect_closing(isolated / "projects.db") as conn:
            projects = pdb.list_projects(conn, include_archived=True)
            assert len(projects) == 1
            created = projects[0]
            assert created.description and created.description.startswith("clawd-migration-intent:")
            assert pdb.get_session_project_id(conn, "s1") is None
        resumed = execute_folder_migration(
            [_mapping("fld", repo)],
            db,
            confirm=True,
            idempotency_key="crash-key",
            hermes_home=isolated,
        )
        assert resumed["success"] is True
        assert resumed["folders"][0]["project_id"] == created.id
        assert resumed["folders"][0]["project_adopted"] is True
        assert resumed["folders"][0]["ownership_proof"] == "intent_token"
        with pdb.connect_closing(isolated / "projects.db") as conn:
            assert len(pdb.list_projects(conn, include_archived=True)) == 1
            assert pdb.get_session_project_id(conn, "s1") == created.id
        assert (repo / "local.txt").read_text() == "keep"
    finally:
        db.close()


def test_cleared_intent_is_not_adopted_by_path(isolated: Path, monkeypatch):
    repo = isolated / "repo"
    repo.mkdir()
    _folders(isolated, [("fld", "Work")], [("s1", "fld")])
    db = _sessions(isolated, "s1")
    from clawd_projects import migration as migration_mod

    real = migration_mod._create_project

    def crash(home, name, primary_path, *, slug, description):
        real(home, name, primary_path, slug=slug, description=description)
        raise RuntimeError("crash after create")

    monkeypatch.setattr(migration_mod, "_create_project", crash)
    try:
        with pytest.raises(RuntimeError):
            execute_folder_migration(
                [_mapping("fld", repo)],
                db,
                confirm=True,
                idempotency_key="foreign-after-crash",
                hermes_home=isolated,
            )
        with pdb.connect_closing(isolated / "projects.db") as conn:
            project = pdb.list_projects(conn, include_archived=True)[0]
            pdb.update_project(conn, project.id, description="changed-by-hand")
        with pytest.raises(MigrationError) as err:
            execute_folder_migration(
                [_mapping("fld", repo)],
                db,
                confirm=True,
                idempotency_key="foreign-after-crash",
                hermes_home=isolated,
            )
        assert err.value.code == "migration_conflict"
        with pdb.connect_closing(isolated / "projects.db") as conn:
            assert len(pdb.list_projects(conn, include_archived=True)) == 1
            assert pdb.get_session_project_id(conn, "s1") is None
    finally:
        db.close()


def test_aba_same_project_id_is_not_rolled_back(isolated: Path):
    repo = isolated / "repo"
    repo.mkdir()
    _folders(isolated, [("fld", "Work")], [("s1", "fld")])
    db = _sessions(isolated, "s1")
    try:
        receipt = execute_folder_migration([_mapping("fld", repo)], db, confirm=True, hermes_home=isolated)
        project_id = receipt["folders"][0]["project_id"]
        original = receipt["folders"][0]["sessions"][0]
        token = original["write_token"]
        bound_at = original["write_bound_at"]
        assert bind_session_to_project("s1", project_id, hermes_home=isolated, session_db=db) is True
        conn = sqlite3.connect(isolated / "projects.db")
        conn.execute("UPDATE project_sessions SET bound_at = ? WHERE session_id = ?", (bound_at, "s1"))
        conn.commit()
        conn.close()
        with pdb.connect_closing(isolated / "projects.db") as conn:
            rebound = pdb.get_session_binding(conn, "s1")
            assert rebound is not None
            assert rebound.write_token != token
            assert rebound.bound_at == bound_at
        rolled = rollback_folder_migration(receipt["migration_id"], db, confirm=True, hermes_home=isolated)
        assert rolled["folders"][0]["sessions"][0]["rollback"] == "skipped_modified"
        assert rolled["fully_restored"] is False
        assert rolled["directories_deleted"] is False
        with pdb.connect_closing(isolated / "projects.db") as conn:
            assert pdb.get_session_project_id(conn, "s1") == project_id
            assert pdb.get_session_write_token(conn, "s1") != token
    finally:
        db.close()


def test_bound_at_change_alone_still_rolls_back(isolated: Path):
    repo = isolated / "repo"
    repo.mkdir()
    _folders(isolated, [("fld", "Work")], [("s1", "fld")])
    db = _sessions(isolated, "s1")
    try:
        receipt = execute_folder_migration([_mapping("fld", repo)], db, confirm=True, hermes_home=isolated)
        conn = sqlite3.connect(isolated / "projects.db")
        conn.execute("UPDATE project_sessions SET bound_at = bound_at + 5 WHERE session_id = ?", ("s1",))
        conn.commit()
        conn.close()
        rolled = rollback_folder_migration(receipt["migration_id"], db, confirm=True, hermes_home=isolated)
        assert rolled["success"] is True
        assert rolled["fully_restored"] is True
        with pdb.connect_closing(isolated / "projects.db") as conn:
            assert pdb.get_session_project_id(conn, "s1") is None
    finally:
        db.close()


def test_unproven_bind_is_not_adopted_or_rolled_back(isolated: Path):
    repo = isolated / "repo"
    repo.mkdir()
    _folders(isolated, [("fld", "Work")], [("s1", "fld")])
    db = _sessions(isolated, "s1")
    try:
        receipt = execute_folder_migration(
            [_mapping("fld", repo)],
            db,
            confirm=True,
            idempotency_key="unproven-key",
            hermes_home=isolated,
        )
        project_id = receipt["folders"][0]["project_id"]

        def wipe_fingerprint(body):
            sess = body["folders"][0]["sessions"][0]
            sess["verified"] = False
            sess["applied"] = False
            sess["write_token"] = None
            sess["pending_write_token"] = None
            sess["write_bound_at"] = None
            sess["write_fingerprint"] = None
            sess["bind_state"] = "pending"
            sess["previous_project_id"] = None
            sess["previous_bound_at"] = None
            body["status"] = "applying"
            body["success"] = False
            body["applied"] = False

        _mutate_receipt(isolated, receipt["migration_id"], wipe_fingerprint)
        resumed = execute_folder_migration(
            [_mapping("fld", repo)],
            db,
            confirm=True,
            idempotency_key="unproven-key",
            hermes_home=isolated,
        )
        assert resumed["success"] is False
        assert resumed["status"] == "bind_unproven"
        with pdb.connect_closing(isolated / "projects.db") as conn:
            assert pdb.get_session_project_id(conn, "s1") == project_id
        rolled = rollback_folder_migration(receipt["migration_id"], db, confirm=True, hermes_home=isolated)
        assert rolled["success"] is False
        assert rolled["status"] == "rollback_incomplete"
        assert rolled["folders"][0]["sessions"][0]["rollback"] == "rollback_unproven"
        with pdb.connect_closing(isolated / "projects.db") as conn:
            assert pdb.get_session_project_id(conn, "s1") == project_id
    finally:
        db.close()


def test_crash_after_bind_claims_persisted_token_only(isolated: Path):
    repo = isolated / "repo"
    repo.mkdir()
    _folders(isolated, [("fld", "Work")], [("s1", "fld")])
    db = _sessions(isolated, "s1")
    try:
        receipt = execute_folder_migration(
            [_mapping("fld", repo)],
            db,
            confirm=True,
            idempotency_key="token-claim",
            hermes_home=isolated,
        )
        project_id = receipt["folders"][0]["project_id"]
        token = receipt["folders"][0]["sessions"][0]["pending_write_token"]

        def lose_commit_flag(body):
            sess = body["folders"][0]["sessions"][0]
            sess["verified"] = False
            sess["applied"] = False
            sess["write_token"] = None
            sess["write_bound_at"] = None
            sess["write_fingerprint"] = None
            sess["pending_write_token"] = token
            sess["bind_state"] = "pending"
            body["status"] = "applying"
            body["success"] = False
            body["applied"] = False

        _mutate_receipt(isolated, receipt["migration_id"], lose_commit_flag)
        resumed = execute_folder_migration(
            [_mapping("fld", repo)],
            db,
            confirm=True,
            idempotency_key="token-claim",
            hermes_home=isolated,
        )
        assert resumed["success"] is True
        assert resumed["folders"][0]["project_id"] == project_id
        assert resumed["folders"][0]["sessions"][0]["write_token"] == token
        assert resumed["folders"][0]["ownership_proof"] == "intent_token"
        with pdb.connect_closing(isolated / "projects.db") as conn:
            assert len(pdb.list_projects(conn, include_archived=True)) == 1
            assert pdb.get_session_write_token(conn, "s1") == token
            assert conn.execute("SELECT COUNT(*) FROM project_sessions").fetchone()[0] == 1
    finally:
        db.close()


def test_retry_reuses_only_confirmed_empty_receipt_project(isolated: Path):
    repo = isolated / "repo"
    repo.mkdir()
    other = isolated / "other"
    other.mkdir()
    before = _folders(isolated, [("fld", "Work")], [("s1", "fld")])
    db = _sessions(isolated, "s1")
    try:
        first = execute_folder_migration(
            [_mapping("fld", repo)],
            db,
            confirm=True,
            idempotency_key="first",
            hermes_home=isolated,
        )
        project_id = first["folders"][0]["project_id"]
        rolled = rollback_folder_migration(first["migration_id"], db, confirm=True, hermes_home=isolated)
        assert rolled["success"] is True
        assert rolled["reuse_eligible"] is True
        assert rolled["archive_frees_path"] is False
        preview = preview_folder_migration(isolated)
        offer = preview["folders"][0]["reuse_offer"]
        assert offer["migration_id"] == first["migration_id"]
        assert offer["requires_confirmation"] is True
        assert preview["folders"][0]["primary_path"] is None
        with pytest.raises(MigrationError) as blocked:
            execute_folder_migration(
                [_mapping("fld", repo)],
                db,
                confirm=True,
                idempotency_key="again",
                hermes_home=isolated,
            )
        assert blocked.value.code == "migration_conflict"
        with pytest.raises(MigrationError) as need_confirm:
            execute_folder_migration(
                [_mapping("fld", repo)],
                db,
                confirm=True,
                idempotency_key="again-2",
                hermes_home=isolated,
                reuse_migration_id=first["migration_id"],
                confirm_reuse=False,
            )
        assert need_confirm.value.code == "reuse_confirm_required"
        with pdb.connect_closing(isolated / "projects.db") as conn:
            foreign = pdb.create_project(conn, name="Foreign", primary_path=str(other))
        with pytest.raises(MigrationError) as wrong_path:
            execute_folder_migration(
                [_mapping("fld", other)],
                db,
                confirm=True,
                idempotency_key="again-3",
                hermes_home=isolated,
                reuse_migration_id=first["migration_id"],
                confirm_reuse=True,
            )
        assert wrong_path.value.code == "reuse_refused"
        with pdb.connect_closing(isolated / "projects.db") as conn:
            assert pdb.get_session_project_id(conn, "s1") is None
            assert pdb.get_project(conn, foreign) is not None
        reused = execute_folder_migration(
            [_mapping("fld", repo)],
            db,
            confirm=True,
            idempotency_key="again-4",
            hermes_home=isolated,
            reuse_migration_id=first["migration_id"],
            confirm_reuse=True,
        )
        assert reused["success"] is True
        assert reused["folders"][0]["project_id"] == project_id
        assert reused["folders"][0]["reused_from"] == first["migration_id"]
        with pdb.connect_closing(isolated / "projects.db") as conn:
            assert len(pdb.list_projects(conn, include_archived=True)) == 2
            assert pdb.get_session_project_id(conn, "s1") == project_id
        assert (isolated / "panel_folders.db").read_bytes() == before
    finally:
        db.close()


def test_reuse_refuses_nonempty_project(isolated: Path):
    repo = isolated / "repo"
    repo.mkdir()
    _folders(isolated, [("fld", "Work")], [("s1", "fld")])
    db = _sessions(isolated, "s1")
    try:
        first = execute_folder_migration([_mapping("fld", repo)], db, confirm=True, hermes_home=isolated)
        project_id = first["folders"][0]["project_id"]
        rollback_folder_migration(first["migration_id"], db, confirm=True, hermes_home=isolated)
        mem = isolated / "projects" / project_id / "memory"
        mem.mkdir(parents=True)
        (mem / "note.txt").write_text("kept")
        with pytest.raises(MigrationError) as refused:
            execute_folder_migration(
                [_mapping("fld", repo)],
                db,
                confirm=True,
                idempotency_key="reuse-nonempty",
                hermes_home=isolated,
                reuse_migration_id=first["migration_id"],
                confirm_reuse=True,
            )
        assert refused.value.code == "reuse_refused"
        assert (mem / "note.txt").read_text() == "kept"
        with pdb.connect_closing(isolated / "projects.db") as conn:
            assert pdb.get_session_project_id(conn, "s1") is None
    finally:
        db.close()


def test_bind_rechecks_idle_not_plan_snapshot(isolated: Path, monkeypatch):
    repo = isolated / "repo"
    repo.mkdir()
    _folders(isolated, [("fld", "Work")], [("s1", "fld")])
    db = _sessions(isolated, "s1")
    calls = {"n": 0}

    def busy(session_db, session_id):
        calls["n"] += 1
        return calls["n"] > 1

    monkeypatch.setattr("clawd_projects.migration.is_session_busy", busy)
    try:
        result = execute_folder_migration([_mapping("fld", repo)], db, confirm=True, hermes_home=isolated)
        assert result["success"] is False
        assert calls["n"] >= 2
        with pdb.connect_closing(isolated / "projects.db") as conn:
            assert pdb.get_session_project_id(conn, "s1") is None
    finally:
        db.close()


def _mutate_receipt(home: Path, migration_id: str, mutator) -> None:
    path = home / "project_folder_migrations.db"
    conn = sqlite3.connect(path)
    try:
        row = conn.execute(
            "SELECT body_json FROM migration_receipts WHERE id = ?",
            (migration_id,),
        ).fetchone()
        body = json.loads(row[0])
        mutator(body)
        conn.execute(
            "UPDATE migration_receipts SET body_json = ?, status = ?, success = ? WHERE id = ?",
            (json.dumps(body), body["status"], 1 if body.get("success") else 0, migration_id),
        )
        conn.commit()
    finally:
        conn.close()

