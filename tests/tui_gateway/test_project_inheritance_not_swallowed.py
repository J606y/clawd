"""TUI seed, prompt, and compute-host paths must not swallow a binding failure."""
from __future__ import annotations

import contextlib
import threading

import pytest

from clawd_projects.inheritance import ProjectInheritanceError


def test_row_create_and_seed_reraise_binding_failure(monkeypatch):
    from tui_gateway import server

    class DB:
        def create_session(self, *_args, **_kwargs):
            raise ProjectInheritanceError("parent project is missing")

        def append_messages_batch(self, *_args, **_kwargs):
            raise ProjectInheritanceError("parent project is missing")

    @contextlib.contextmanager
    def owner_db(_session, _failure_message=None):
        yield DB()

    monkeypatch.setattr(server, "_workdir_owner_db", owner_db)
    monkeypatch.setattr(server, "_session_db", owner_db)
    monkeypatch.setattr(server, "_workdir_row_model_config", lambda _session: ("m", {}))
    monkeypatch.setattr(server, "_session_source", lambda _session: "tui")
    monkeypatch.setattr(server, "_persisted_session_cwd", lambda _session: None)
    session = {
        "session_key": "child",
        "parent_session_id": "parent",
        "history_lock": threading.Lock(),
        "history": [{"role": "user", "content": "hi"}],
        "seeded": True,
    }
    with pytest.raises(ProjectInheritanceError):
        server._ensure_session_db_row(session)
    with pytest.raises(ProjectInheritanceError):
        server._persist_branch_seed(session)


def test_prompt_persist_returns_inheritance_error_and_does_not_look_like_storage(monkeypatch):
    from tui_gateway import server

    def boom(_session):
        raise ProjectInheritanceError("parent project is missing")

    monkeypatch.setattr(server, "_ensure_session_db_row", boom)
    session = {"history_lock": threading.Lock(), "running": True, "session_key": "child"}
    error = server._persist_session_row_for_submit("rid", session, "hello")
    assert error["error"]["code"] == 5073
    assert "Project inheritance failed" in error["error"]["message"]
    assert "storage_unavailable" not in error["error"]["message"]
    assert session["running"] is False


def test_compute_host_does_not_start_the_turn(monkeypatch):
    from tui_gateway import compute_host, server

    session = {"history_lock": threading.Lock(), "running": False, "session_key": "child"}
    calls = []
    replies = []
    host = compute_host.ComputeHost.__new__(compute_host.ComputeHost)
    host._reply = lambda *args, **kwargs: replies.append((args, kwargs))
    host._ensure_server_session = lambda _server, _frame: session
    monkeypatch.setattr(server, "_install_borrowed_lease", lambda *_a, **_k: None)
    monkeypatch.setattr(server, "_start_inflight_turn", lambda *_a, **_k: None)
    monkeypatch.setattr(server, "_clear_inflight_turn", lambda *_a, **_k: None)
    monkeypatch.setattr(server, "_sessions", {"sid": session})
    monkeypatch.setattr(
        server, "_ensure_session_db_row",
        lambda _session: (_ for _ in ()).throw(ProjectInheritanceError("parent project is missing")),
    )
    monkeypatch.setattr(server, "_run_prompt_submit", lambda *_a, **_k: calls.append("run"))

    host._run_real_turn({"sid": "sid", "request_id": "req", "text": "hello"})

    assert calls == []
    assert replies[-1][0][0] == "turn.error"
    assert replies[-1][1]["reason"] == "project_inheritance_failed"
    assert session["running"] is False
