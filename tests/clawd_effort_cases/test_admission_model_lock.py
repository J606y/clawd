"""同一请求提交档位后，新模型锁不得整段丢掉持久 reasoning_effort。

编写时未执行本文件。不恢复五次成功硬停。无 authority 不得伪称已提交。
"""

from __future__ import annotations

import json

import pytest

from clawd_server.effort_service import (
    PENDING_KEY,
    effort_for_lock_replace,
    model_options_for_lock_replace,
    write_session_effort,
)
from gateway.platforms.api_server import APIServerAdapter
from gateway.session_api_turn import committed_effort_for_lock, prepare_admitted_session_chat

SID = "sess-lock-effort"


def _config(session):
    raw = session.get("model_config")
    if isinstance(raw, str) and raw.strip():
        return json.loads(raw)
    return dict(raw or {})


def test_committed_tier_replaces_only_reasoning_effort():
    options = model_options_for_lock_replace(
        {"temperature": 0.2, "reasoning_effort": "low", "top_p": 0.9},
        stored_effort="medium",
        committed_effort="high",
    )
    assert options == {"temperature": 0.2, "reasoning_effort": "high", "top_p": 0.9}


def test_omitted_tier_keeps_stored_effort_and_not_pending():
    session = {
        "id": SID,
        "model_config": {
            "reasoning_effort": "medium",
            PENDING_KEY: "ultra",
            "browser_model_lock": {
                "model": "grok-4.7",
                "provider": "sub2api",
                "route_source": "raw_request",
                "confirmed": True,
                "model_options": {"temperature": 0.4, "reasoning_effort": "low"},
            },
        },
    }
    assert effort_for_lock_replace(session) == "low"
    options = model_options_for_lock_replace(
        {"temperature": 0.1},
        stored_effort=effort_for_lock_replace(session),
        committed_effort=None,
    )
    assert options == {"temperature": 0.1, "reasoning_effort": "low"}
    assert PENDING_KEY not in options


def test_pending_alone_is_not_invented_into_the_new_lock():
    session = {"id": SID, "model_config": {PENDING_KEY: "high", "browser_model_lock": {
        "model": "grok-4.7", "provider": "sub2api", "model_options": {"temperature": 0.2},
    }}}
    assert effort_for_lock_replace(session) is None
    options = model_options_for_lock_replace({"temperature": 0.2}, stored_effort=None)
    assert "reasoning_effort" not in options
    assert options["temperature"] == 0.2


def test_explicit_body_tier_stays_when_this_request_did_not_commit():
    options = model_options_for_lock_replace(
        {"reasoning_effort": "low", "temperature": 0.5},
        stored_effort="high",
        committed_effort=None,
    )
    assert options["reasoning_effort"] == "low"
    assert options["temperature"] == 0.5


def test_invalid_committed_effort_does_not_invent_a_tier():
    options = model_options_for_lock_replace({"temperature": 1}, committed_effort="nope")
    assert options == {"temperature": 1}


class _RecordingDB:
    def __init__(self, session):
        self.session = session
        self.written = None

    def get_session(self, _sid):
        return self.session

    def update_session_runtime_lock(self, session_id, *, model, provider, model_options, route_source, confirmed):
        self.written = {
            "session_id": session_id,
            "model": model,
            "provider": provider,
            "model_options": model_options,
            "route_source": route_source,
            "confirmed": confirmed,
        }


def _persist(db, runtime_request, committed=None):
    stub = type("Stub", (), {})()
    stub.name = "test"
    stub._ensure_session_db = lambda: db
    stub._requested_ids = APIServerAdapter._requested_ids
    return APIServerAdapter._persist_session_runtime_lock(stub, SID, runtime_request, committed)


def test_persist_writes_committed_tier_without_replacing_route():
    db = _RecordingDB({
        "id": SID,
        "model_config": {
            "browser_model_lock": {
                "model": "old-model",
                "provider": "old-provider",
                "model_options": {"reasoning_effort": "low", "temperature": 0.8},
            },
        },
    })
    ok = _persist(db, {
        "require_model_lock": True,
        "requested": {"model": "grok-4.7", "provider": "sub2api"},
        "route_source": "raw_request",
        "model_options": {"temperature": 0.2, "reasoning_effort": "low"},
    }, "high")
    assert ok is True
    written = db.written
    assert written["model"] == "grok-4.7"
    assert written["provider"] == "sub2api"
    assert written["route_source"] == "raw_request"
    assert written["confirmed"] is True
    assert written["model_options"] == {"temperature": 0.2, "reasoning_effort": "high"}


def test_persist_does_not_copy_pending_when_body_omits_effort():
    db = _RecordingDB({
        "id": SID,
        "model_config": {
            PENDING_KEY: "ultra",
            "reasoning_effort": "medium",
            "browser_model_lock": {
                "model": "grok-4.7",
                "provider": "sub2api",
                "model_options": {"temperature": 0.4},
            },
        },
    })
    ok = _persist(db, {
        "require_model_lock": True,
        "requested": {"model": "grok-4.7", "provider": "sub2api"},
        "route_source": "model_routes",
        "model_options": {"temperature": 0.4},
    })
    assert ok is True
    assert db.written["model_options"] == {"temperature": 0.4, "reasoning_effort": "medium"}
    assert db.written["model"] == "grok-4.7"
    assert db.written["provider"] == "sub2api"


class _Authority:
    def __init__(self, db):
        self.db = db
        self.executing = {}
        self.sessions = {}
        self._route_turns = {}

    def logical_owner(self, session_id):
        return session_id


class _Request:
    def __init__(self, session_id):
        self.match_info = {"session_id": session_id}


def _open(monkeypatch, tmp_path, name):
    from tests.clawd_race_cases.helpers import isolate, open_db

    home = isolate(monkeypatch, tmp_path / name)
    db = open_db(home)
    db.create_session(SID, source="api_server", model="old-model", profile_name="fixture")
    db.update_session_runtime_lock(
        SID, model="old-model", provider="old-provider",
        model_options={"temperature": 0.8, "reasoning_effort": "low"},
        route_source="raw_request", confirmed=True,
    )
    write_session_effort(db, SID, "high", running=True)
    return db


@pytest.mark.asyncio
async def test_admitted_chat_persists_committed_tier_on_the_new_lock(tmp_path, monkeypatch):
    db = _open(monkeypatch, tmp_path, "admitted-lock")
    authority = _Authority(db)
    seen = {}

    class Adapter:
        name = "test"
        gateway_runner = type("Runner", (), {"session_authority": authority})()

        def _ensure_session_db(self):
            return db

        _requested_ids = staticmethod(APIServerAdapter._requested_ids)

        async def _prepare_session_chat(self, request):
            seen["effort"] = committed_effort_for_lock.get()
            runtime_request = {
                "require_model_lock": True,
                "requested": {"model": "grok-4.7", "provider": "sub2api"},
                "route_source": "raw_request",
                "model_options": {"temperature": 0.2, "reasoning_effort": "low"},
            }
            ok = APIServerAdapter._persist_session_runtime_lock(
                self, request.match_info["session_id"], runtime_request, seen["effort"],
            )
            assert ok is True
            return {
                "runtime_request": runtime_request,
                "run_kwargs": {
                    "requested_model": "grok-4.7",
                    "requested_provider": "sub2api",
                    "model_options": {"temperature": 0.2, "reasoning_effort": "low"},
                },
            }, None

    try:
        ctx, err = await prepare_admitted_session_chat(Adapter(), _Request(SID))
        assert err is None
        assert seen["effort"] == "high"
        assert ctx["run_kwargs"]["requested_model"] == "grok-4.7"
        assert ctx["run_kwargs"]["requested_provider"] == "sub2api"
        assert ctx["run_kwargs"]["model_options"]["reasoning_effort"] == "high"
        assert ctx["run_kwargs"]["model_options"]["temperature"] == 0.2
        lock = _config(db.get_session(SID))["browser_model_lock"]
        assert lock["model"] == "grok-4.7"
        assert lock["provider"] == "sub2api"
        assert lock["route_source"] == "raw_request"
        assert lock["model_options"]["temperature"] == 0.2
        assert lock["model_options"]["reasoning_effort"] == "high"
        assert PENDING_KEY not in _config(db.get_session(SID))
    finally:
        db.close()


@pytest.mark.asyncio
async def test_chat_without_authority_does_not_claim_the_pin_committed(tmp_path, monkeypatch):
    db = _open(monkeypatch, tmp_path, "no-authority")

    class Adapter:
        def _ensure_session_db(self):
            return db

        async def _prepare_session_chat(self, _request):
            assert committed_effort_for_lock.get() is None
            return {"runtime_request": {}, "run_kwargs": {"model_options": {"temperature": 0.2}}}, None

    try:
        ctx, err = await prepare_admitted_session_chat(Adapter(), _Request(SID))
        assert err is None
        assert ctx["run_kwargs"]["model_options"] == {"temperature": 0.2}
        config = _config(db.get_session(SID))
        assert config[PENDING_KEY] == "high"
        assert config["browser_model_lock"]["model"] == "old-model"
        assert config["browser_model_lock"]["provider"] == "old-provider"
    finally:
        db.close()
