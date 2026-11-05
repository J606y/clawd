"""Unrun checks for project-memory identity. Do not treat this file as a passed run."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from hermes_cli import projects_db as pdb
from hermes_state_runtime import RuntimeStoreError


def _actor(profile: str, caps=frozenset({"session:read", "session:control"})):
    return SimpleNamespace(subject="api", profile_id=profile, capabilities=caps, transport_id="test")


class _Authority:
    def __init__(self, home: Path, profile: str, sessions: set[str], *, generation=None, state="idle"):
        self.profile_id = profile
        self.db = SimpleNamespace(db_path=home / "state.db")
        self.sessions = sessions
        self.generation = generation
        self.state = state

    def authorize(self, actor, ref, capability):
        if actor.profile_id != self.profile_id or ref.profile_id != self.profile_id:
            raise RuntimeStoreError("profile_mismatch")
        if capability not in actor.capabilities:
            raise RuntimeStoreError("permission_denied")
        if ref.session_id not in self.sessions:
            raise RuntimeStoreError("not_found")

    def check_approval_generation(self, session_id, generation):
        if session_id not in self.sessions or self.generation != generation or self.state != "running":
            raise RuntimeStoreError("stale_generation")


def _project(home: Path, name: str, session_id: str) -> str:
    home.mkdir(parents=True)
    workspace = home / "workspace" / name
    workspace.mkdir(parents=True)
    with pdb.connect_closing(home / "projects.db") as conn:
        pid = pdb.create_project(conn, name=name, primary_path=str(workspace))
        pdb.bind_session_project(conn, session_id, pid)
    return pid


def test_missing_identity_and_wrong_profile_do_not_write(tmp_path):
    from clawd_projects.memory_authorization import (
        MemoryAuthorizationError,
        authorize_user_project_memory_write,
    )

    home = tmp_path / "a"
    pid = _project(home, "Alpha", "sess")
    authority = _Authority(home, "default", {"sess"})
    with pytest.raises(MemoryAuthorizationError) as missing:
        authorize_user_project_memory_write(
            None, session_id="sess", project_id=pid, authority=authority,
        )
    assert missing.value.code == "missing_identity"
    other = _Authority(home, "other", {"sess"})
    with pytest.raises(MemoryAuthorizationError) as mismatch:
        authorize_user_project_memory_write(
            _actor("default"), session_id="sess", project_id=pid, authority=other,
        )
    assert mismatch.value.code == "profile_mismatch"
    assert not (home / "projects" / pid / "memory" / "MEMORY.md").exists() or "NEW" not in (
        home / "projects" / pid / "memory" / "MEMORY.md"
    ).read_text(encoding="utf-8")


def test_same_session_id_in_another_profile_does_not_authorize(tmp_path):
    from clawd_projects.memory_authorization import (
        MemoryAuthorizationError,
        apply_authorized_project_memory,
        authorize_user_project_memory_write,
    )

    home_a = tmp_path / "a"
    home_b = tmp_path / "b"
    pid_a = _project(home_a, "Alpha", "same")
    pid_b = _project(home_b, "Beta", "same")
    authority_b = _Authority(home_b, "beta", {"same"})
    edit = authorize_user_project_memory_write(
        _actor("beta"), session_id="same", project_id=pid_b, authority=authority_b,
    )
    applied = apply_authorized_project_memory(
        edit, [{"action": "add", "content": "BETA-ONLY"}], actor=_actor("beta"), authority=authority_b,
    )
    assert applied["success"] is True
    from clawd_projects.service import get_project_memory_store

    assert get_project_memory_store(pid_b, hermes_home=home_b).memory_entries == ["BETA-ONLY"]
    assert get_project_memory_store(pid_a, hermes_home=home_a).memory_entries == []
    assert get_project_memory_store(pid_a, hermes_home=home_a).profile_home_key != (
        get_project_memory_store(pid_b, hermes_home=home_b).profile_home_key
    )
    with pytest.raises(MemoryAuthorizationError) as wrong:
        authorize_user_project_memory_write(
            _actor("beta"), session_id="same", project_id=pid_a, authority=authority_b,
        )
    assert wrong.value.code == "project_not_found"


def test_unbound_session_and_other_project_are_refused(tmp_path):
    from clawd_projects.memory_authorization import MemoryAuthorizationError, authorize_user_project_memory_write

    home = tmp_path / "home"
    pid = _project(home, "Alpha", "bound")
    workspace = home / "workspace" / "Beta"
    workspace.mkdir()
    with pdb.connect_closing(home / "projects.db") as conn:
        other = pdb.create_project(conn, name="Beta", primary_path=str(workspace))
    authority = _Authority(home, "default", {"bound", "plain"})
    with pytest.raises(MemoryAuthorizationError) as unbound:
        authorize_user_project_memory_write(
            _actor("default"), session_id="plain", project_id=pid, authority=authority,
        )
    assert unbound.value.code == "not_bound"
    with pytest.raises(MemoryAuthorizationError) as mismatch:
        authorize_user_project_memory_write(
            _actor("default"), session_id="bound", project_id=other, authority=authority,
        )
    assert mismatch.value.code == "project_mismatch"


def test_stale_generation_is_refused_and_idle_user_edit_is_not_staged(tmp_path, monkeypatch):
    from clawd_projects.memory_authorization import (
        USER_EDIT_APPROVAL,
        MemoryAuthorizationError,
        apply_authorized_project_memory,
        authorize_user_project_memory_write,
    )

    home = tmp_path / "home"
    pid = _project(home, "Alpha", "sess")
    actor = _actor("default")
    running = _Authority(home, "default", {"sess"}, generation=3, state="running")
    with pytest.raises(MemoryAuthorizationError) as stale:
        authorize_user_project_memory_write(
            actor, session_id="sess", project_id=pid, generation=2, authority=running,
        )
    assert stale.value.code == "stale_generation" and stale.value.status == 409
    with pytest.raises(MemoryAuthorizationError):
        authorize_user_project_memory_write(
            actor, session_id="sess", project_id=pid, generation="3", authority=running,
        )
    idle = _Authority(home, "default", {"sess"})
    edit = authorize_user_project_memory_write(
        actor, session_id="sess", project_id=pid, authority=idle,
    )
    assert edit.approval == USER_EDIT_APPROVAL
    assert edit.generation is None

    def _boom(*_a, **_k):
        raise AssertionError("user edit must not call evaluate_gate")

    monkeypatch.setattr("tools.write_approval.evaluate_gate", _boom)
    result = apply_authorized_project_memory(
        edit, [{"action": "add", "content": "USER-EDIT"}], actor=actor, authority=idle,
    )
    assert result["success"] is True
    from clawd_projects.service import get_project_memory_store

    assert get_project_memory_store(pid, hermes_home=home).memory_entries == ["USER-EDIT"]
    assert not (home / "memories" / "MEMORY.md").exists()


def test_write_token_is_forwarded_or_refused_not_dropped(tmp_path, monkeypatch):
    from clawd_projects.service import BindingTokenUnsupported, commit_session_binding

    home = tmp_path / "tok"
    home.mkdir()
    seen = {}

    def _bind(conn, session_id, project_id, write_token=None, expected_write_token=None):
        seen["args"] = (session_id, project_id, write_token, expected_write_token)
        return {"applied": True, "write_token": write_token or "db-minted"}

    monkeypatch.setattr("hermes_cli.projects_db.bind_session_project", _bind)
    result = commit_session_binding("sess", "p_alpha", hermes_home=home, force=True, write_token="handed")
    assert result.applied is True
    assert result.write_token == "handed"
    assert seen["args"][2] == "handed"

    def _old(conn, session_id, project_id):
        seen["wrote"] = True
        return True

    monkeypatch.setattr("hermes_cli.projects_db.bind_session_project", _old)
    with pytest.raises(BindingTokenUnsupported):
        commit_session_binding("sess", None, hermes_home=home, force=True, expected_write_token="prev")
    assert "wrote" not in seen


def test_missing_write_token_reader_still_fails_closed(tmp_path, monkeypatch):
    from clawd_projects.service import BindingTokenUnsupported, read_session_write_token

    home = tmp_path / "missing-reader"
    home.mkdir()
    monkeypatch.delattr("hermes_cli.projects_db.get_session_write_token", raising=False)
    with pytest.raises(BindingTokenUnsupported):
        read_session_write_token("sess", hermes_home=home)


def test_read_session_write_token_uses_current_db_reader(tmp_path):
    """Current DB reader is get_session_write_token(conn, session_id). None is absence, not a missing reader."""
    from clawd_projects.service import commit_session_binding, read_session_write_token

    home = tmp_path / "live-token"
    workspace = home / "workspace"
    workspace.mkdir(parents=True)
    with pdb.connect_closing(home / "projects.db") as conn:
        pid = pdb.create_project(conn, name="Tok", primary_path=str(workspace))
    handed = pdb.new_write_token()
    forwarded = commit_session_binding(
        "sess", pid, hermes_home=home, force=True, write_token=handed,
    )
    assert forwarded.applied is True
    assert forwarded.write_token == handed
    assert read_session_write_token("sess", hermes_home=home) == handed
    minted = commit_session_binding("sess-mint", pid, hermes_home=home, force=True)
    assert minted.write_token and minted.write_token.startswith("wt_")
    assert read_session_write_token("sess-mint", hermes_home=home) == minted.write_token
    assert read_session_write_token("unbound", hermes_home=home) is None
