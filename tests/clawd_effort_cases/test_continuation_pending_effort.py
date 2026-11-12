"""续跑 effort 冻结接线。编写时未执行本文件。

服务路径在 journal.freeze 之前，用真实 authority.db、keyed_turn、
mutation_lock、admission_held 提交 pending，再重读 spec。冻结正文就是启动
spec。已 reserve 的幂等别名不因新档位重写，也不把后到的 pin 写进那一回合。
直接 admit 仍保留调用方指纹，便于找到已经 reserve 的 run。不恢复五次成功硬停。
无 authority 不伪成功、不猜 profile、不自激。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from clawd_server.effort_service import PENDING_KEY, write_session_effort
from gateway.clawd_continuation_admit import (
    ContinuationAdmitError, admit_continuation, continuation_fingerprint, continuation_scope,
)
from gateway.clawd_continuation_decision import (
    CONTINUATION_TEXT, MAX_ATTEMPTS, continuation_identity, request_key,
)
from gateway.clawd_continuation_journal import ContinuationJournal
from tests.clawd_race_cases.helpers import (
    MarkedExecutor,
    add_notice,
    drain_adapter,
    isolate,
    journal_path,
    make_adapter,
    open_db,
    open_service,
    open_store,
)

SID = "sess-cont-effort"


def _config(session):
    raw = session.get("model_config")
    if isinstance(raw, str) and raw.strip():
        return json.loads(raw)
    return dict(raw or {})


class _Authority:
    def __init__(self, db):
        self.db = db
        self.executing = {}
        self.sessions = {}
        self._route_turns = {}

    def logical_owner(self, session_id):
        return session_id


def _seed(db, *, pending=None, lock_effort="low"):
    db.create_session(SID, source="api_server", model="grok-4.7", profile_name="fixture", cwd="/work/proj-a")
    db.update_session_runtime_lock(
        SID, model="grok-4.7", provider="sub2api",
        model_options={"temperature": 0.3, "reasoning_effort": lock_effort},
        route_source="raw_request", confirmed=True,
    )
    db.append_message(SID, "user", content="请处理", timestamp=10)
    db.append_message(SID, "assistant", content="已派出", timestamp=20)
    if pending:
        write_session_effort(db, SID, pending, running=True)


def _spec(**extra):
    spec = {
        "input": CONTINUATION_TEXT,
        "session_id": SID,
        "model": "grok-4.7",
        "provider": "sub2api",
        "model_options": {"temperature": 0.3, "reasoning_effort": "low"},
        "locked": True,
        "notice_ids": [],
        "request_key": "req-cont-effort",
        "cwd": "/work/proj-a",
        "session_key": SID,
    }
    spec.update(extra)
    return spec


def _ready(monkeypatch, tmp_path, name, *, pending="high", authority=True):
    home = isolate(monkeypatch, tmp_path / name)
    db = open_db(home)
    _seed(db, pending=pending)
    executor = MarkedExecutor()
    adapter = make_adapter(db, open_store(home / "runs_idempotency.db"), executor, profile="not-a-home")
    if authority:
        adapter.gateway_runner = type("Runner", (), {"session_authority": _Authority(db)})()
    return db, executor, adapter


@pytest.mark.asyncio
async def test_launch_uses_committed_tier_and_does_not_replace_route(tmp_path, monkeypatch):
    db, executor, adapter = _ready(monkeypatch, tmp_path, "commit-before-launch")
    try:
        run_id = await admit_continuation(adapter, _spec(), db=db, profile="not-a-home")
        await drain_adapter(adapter)
        assert run_id
        assert len(executor.calls) == 1
        call = executor.calls[0]
        assert call["requested_model"] == "grok-4.7"
        assert call["requested_provider"] == "sub2api"
        assert call["confirmed_runtime_lock"] is True
        assert call["model_options"] == {"temperature": 0.3, "reasoning_effort": "high"}
        assert call["user_message"] == CONTINUATION_TEXT
        config = _config(db.get_session(SID))
        lock = config["browser_model_lock"]
        assert lock["model"] == "grok-4.7"
        assert lock["provider"] == "sub2api"
        assert lock["route_source"] == "raw_request"
        assert lock["model_options"]["temperature"] == 0.3
        assert lock["model_options"]["reasoning_effort"] == "high"
        assert PENDING_KEY not in config
    finally:
        db.close()


@pytest.mark.asyncio
async def test_retry_reuses_caller_spec_and_does_not_consume_a_later_pin(tmp_path, monkeypatch):
    db, executor, adapter = _ready(monkeypatch, tmp_path, "reuse-keeps-later-pin")
    try:
        first = await admit_continuation(adapter, _spec(), db=db, profile="not-a-home")
        await drain_adapter(adapter)
        write_session_effort(db, SID, "ultra", running=True)
        second = await admit_continuation(adapter, _spec(), db=db, profile="not-a-home")
        await drain_adapter(adapter)
        assert second == first
        assert len(executor.calls) == 1
        assert executor.calls[0]["model_options"]["reasoning_effort"] == "high"
        assert _config(db.get_session(SID))[PENDING_KEY] == "ultra"
        assert _config(db.get_session(SID))["browser_model_lock"]["model"] == "grok-4.7"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_no_authority_with_pending_does_not_launch_or_guess(tmp_path, monkeypatch):
    db, executor, adapter = _ready(monkeypatch, tmp_path, "no-authority", authority=False)
    try:
        with pytest.raises(ContinuationAdmitError) as caught:
            await admit_continuation(adapter, _spec(), db=db, profile="default")
        assert caught.value.reason == "effort_admission_required"
        assert executor.calls == []
        config = _config(db.get_session(SID))
        assert config[PENDING_KEY] == "high"
        assert config["browser_model_lock"]["model_options"]["reasoning_effort"] == "low"
        assert config["browser_model_lock"]["provider"] == "sub2api"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_no_authority_without_pending_keeps_caller_spec(tmp_path, monkeypatch):
    db, executor, adapter = _ready(monkeypatch, tmp_path, "no-pending", pending=None, authority=False)
    try:
        await admit_continuation(adapter, _spec(), db=db, profile="not-a-home")
        await drain_adapter(adapter)
        assert len(executor.calls) == 1
        assert executor.calls[0]["model_options"] == {"temperature": 0.3, "reasoning_effort": "low"}
        assert executor.calls[0]["requested_model"] == "grok-4.7"
        assert executor.calls[0]["requested_provider"] == "sub2api"
        assert PENDING_KEY not in _config(db.get_session(SID))
    finally:
        db.close()


@pytest.mark.asyncio
async def test_route_mismatch_does_not_commit_or_overwrite_model(tmp_path, monkeypatch):
    db, executor, adapter = _ready(monkeypatch, tmp_path, "route-mismatch")
    try:
        with pytest.raises(ContinuationAdmitError) as caught:
            await admit_continuation(
                adapter, _spec(model="other-model"), db=db, profile="not-a-home",
            )
        assert caught.value.reason == "provider_unresolved"
        assert executor.calls == []
        config = _config(db.get_session(SID))
        assert config[PENDING_KEY] == "high"
        lock = config["browser_model_lock"]
        assert lock["model"] == "grok-4.7"
        assert lock["provider"] == "sub2api"
        assert lock["model_options"]["reasoning_effort"] == "low"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_draining_refuses_before_commit(tmp_path, monkeypatch):
    db, executor, adapter = _ready(monkeypatch, tmp_path, "draining")
    adapter._draining_response = lambda: object()
    try:
        with pytest.raises(ContinuationAdmitError) as caught:
            await admit_continuation(adapter, _spec(), db=db, profile="not-a-home")
        assert caught.value.reason == "draining"
        assert executor.calls == []
        config = _config(db.get_session(SID))
        assert config[PENDING_KEY] == "high"
        assert config["browser_model_lock"]["model"] == "grok-4.7"
        assert config["browser_model_lock"]["provider"] == "sub2api"
    finally:
        db.close()


PROFILE = "fixture"
DELEG = "deleg_freeze"


class _RecordingLock:
    def __init__(self):
        self._inner = asyncio.Lock()
        self.entries = 0

    async def __aenter__(self):
        await self._inner.acquire()
        self.entries += 1
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self._inner.release()
        return False


def _options(spec):
    options = spec.get("model_options") if isinstance(spec, dict) else None
    return dict(options) if isinstance(options, dict) else {}


def _notice_identity(db):
    rows = db.get_messages(SID, limit=300, latest=True)
    notice = [row for row in rows if row.get("display_kind") == "async_delegation_complete"][-1]
    return continuation_identity(SID, notice)


def _service_case(monkeypatch, tmp_path, name, *, pending="high", authority=True, notice=True):
    home = isolate(monkeypatch, tmp_path / name)
    db = open_db(home)
    _seed(db, pending=pending)
    if notice:
        add_notice(db, SID, DELEG)
    executor = MarkedExecutor()
    adapter = make_adapter(db, open_store(home / "runs_idempotency.db"), executor, profile=PROFILE)
    lock = _RecordingLock()
    if authority:
        holder = _Authority(db)
        holder.sessions[SID] = type("Live", (), {"mutation_lock": lock})()
        adapter.gateway_runner = type("Runner", (), {"session_authority": holder})()
    service = open_service(adapter, journal_path(db), now=lambda: 1_000_000.0)
    return db, executor, adapter, service, lock


def _watch_freeze(service, db, seen, lock=None):
    real = service.journal.freeze

    def recording(session_id, key, aliases, body, now):
        seen.append({
            "body": json.loads(json.dumps(body)),
            "pending": _config(db.get_session(SID)).get(PENDING_KEY),
            "key": key,
            "entries": None if lock is None else lock.entries,
        })
        return real(session_id, key, aliases, body, now)

    service.journal.freeze = recording


@pytest.mark.asyncio
async def test_service_commits_before_freeze_and_launch_matches_journal(tmp_path, monkeypatch):
    db, executor, adapter, service, lock = _service_case(monkeypatch, tmp_path, "freeze-before")
    seen = []
    shots = []
    _watch_freeze(service, db, seen, lock)

    async def capturing(adapter_arg, spec, *, db, profile):
        stored = service.journal.load_request(SID)
        shots.append({
            "spec": _options(spec),
            "journal": _options(stored["body"]) if stored else None,
        })
        return await admit_continuation(adapter_arg, spec, db=db, profile=profile)

    service.admit = capturing
    try:
        decision = await service.evaluate(SID, db, profile=PROFILE)
        await drain_adapter(adapter)
        assert decision.action == "admit"
        assert lock.entries >= 1
        assert seen[0]["entries"] >= 1
        assert seen[0]["pending"] is None
        assert seen[0]["body"]["model_options"]["reasoning_effort"] == "high"
        assert seen[0]["body"]["model_options"]["temperature"] == 0.3
        assert shots[0]["journal"] == shots[0]["spec"] == {"temperature": 0.3, "reasoning_effort": "high"}
        assert executor.calls[0]["model_options"] == shots[0]["spec"]
        assert executor.calls[0]["requested_model"] == "grok-4.7"
        assert executor.calls[0]["requested_provider"] == "sub2api"
        assert executor.calls[0]["confirmed_runtime_lock"] is True
        config = _config(db.get_session(SID))
        lock_row = config["browser_model_lock"]
        assert lock_row["model"] == "grok-4.7"
        assert lock_row["provider"] == "sub2api"
        assert lock_row["route_source"] == "raw_request"
        assert PENDING_KEY not in config
        write_session_effort(db, SID, "ultra", running=True)
        again = await service.evaluate(SID, db, profile=PROFILE)
        await drain_adapter(adapter)
        assert again.action == "skip"
        assert len(executor.calls) == 1
        assert not service._timers
        assert _config(db.get_session(SID))[PENDING_KEY] == "ultra"
    finally:
        service.stop()
        db.close()


@pytest.mark.asyncio
async def test_reserved_alias_keeps_old_body_and_does_not_consume_later_pin(tmp_path, monkeypatch):
    db, executor, adapter, service, lock = _service_case(
        monkeypatch, tmp_path, "reserved-alias", pending="ultra",
    )
    identity = _notice_identity(db)
    key = request_key(SID, [identity])
    body = {
        "input": CONTINUATION_TEXT,
        "session_id": SID,
        "model": "grok-4.7",
        "provider": "sub2api",
        "model_options": {"temperature": 0.3, "reasoning_effort": "low"},
        "locked": True,
        "notice_ids": [DELEG],
        "request_key": key,
        "cwd": "/work/proj-a",
        "session_key": SID,
    }
    service.journal.freeze(SID, key, [identity], body, 1.0)
    run_id = "run_reserved_effort"
    status = {
        "object": "hermes.run", "run_id": run_id, "status": "completed",
        "session_id": SID, "created_at": 1, "updated_at": 1,
    }
    adapter._run_idempotency_store.reserve(
        continuation_scope(PROFILE), key, continuation_fingerprint(body), run_id, status,
        owner_pid=1, owner_started=1,
    )
    seen = []
    _watch_freeze(service, db, seen, lock)
    try:
        await service.evaluate(SID, db, profile=PROFILE)
        await drain_adapter(adapter)
        assert executor.calls == []
        assert lock.entries == 0
        assert seen[0]["pending"] == "ultra"
        assert seen[0]["body"]["model_options"]["reasoning_effort"] == "low"
        assert seen[0]["key"] == key
        stored = service.journal.receipt_record(identity)
        assert stored["run_id"] == run_id
        assert stored["reason"] == "admitted"
        assert _config(db.get_session(SID))[PENDING_KEY] == "ultra"
        assert _config(db.get_session(SID))["browser_model_lock"]["model_options"]["reasoning_effort"] == "low"
    finally:
        service.stop()
        db.close()


@pytest.mark.asyncio
async def test_no_authority_pending_is_observable_and_does_not_self_schedule(tmp_path, monkeypatch):
    db, executor, _adapter, service, lock = _service_case(
        monkeypatch, tmp_path, "no-authority-service", authority=False,
    )
    try:
        decision = await service.evaluate(SID, db, profile=PROFILE)
        await drain_adapter(service.adapter)
        assert decision.reason != "streak_limit"
        assert executor.calls == []
        assert lock.entries == 0
        assert not service._timers
        assert service.journal.load_request(SID) is None
        assert service.status(SID)["last_event"]["detail"] == "effort_admission_required"
        config = _config(db.get_session(SID))
        assert config[PENDING_KEY] == "high"
        assert config["browser_model_lock"]["model_options"]["reasoning_effort"] == "low"
        assert config["browser_model_lock"]["provider"] == "sub2api"
    finally:
        service.stop()
        db.close()


@pytest.mark.asyncio
async def test_profile_mismatch_is_observable_and_does_not_guess(tmp_path, monkeypatch):
    db, executor, adapter, service, _lock = _service_case(
        monkeypatch, tmp_path, "profile-mismatch", authority=False,
    )
    other_home = tmp_path / "profile-mismatch" / "other-home"
    other = open_db(isolate(monkeypatch, other_home))
    adapter.gateway_runner = type("Runner", (), {"session_authority": _Authority(other)})()
    try:
        await service.evaluate(SID, db, profile=PROFILE)
        await drain_adapter(adapter)
        assert executor.calls == []
        assert not service._timers
        assert service.status(SID)["last_event"]["detail"] == "profile_mismatch"
        assert _config(db.get_session(SID))[PENDING_KEY] == "high"
        assert other.get_session(SID) is None
    finally:
        service.stop()
        db.close()
        other.close()


@pytest.mark.asyncio
async def test_draining_skips_commit_then_clear_aligns_journal_with_launch(tmp_path, monkeypatch):
    db, executor, adapter, service, lock = _service_case(monkeypatch, tmp_path, "drain-then-align")
    adapter._draining_response = lambda: object()
    seen = []
    shots = []
    _watch_freeze(service, db, seen, lock)

    async def capturing(adapter_arg, spec, *, db, profile):
        stored = service.journal.load_request(SID)
        shots.append({"spec": _options(spec), "journal": _options(stored["body"])})
        return await admit_continuation(adapter_arg, spec, db=db, profile=profile)

    service.admit = capturing
    try:
        await service.evaluate(SID, db, profile=PROFILE)
        await drain_adapter(adapter)
        assert executor.calls == []
        assert lock.entries == 0
        assert seen[0]["pending"] == "high"
        assert shots[0]["journal"]["reasoning_effort"] == "low"
        assert shots[0]["spec"] == shots[0]["journal"]
        stored = service.journal.load_request(SID)
        assert stored["attempts"] == 1
        assert stored["last_error"] == "draining"
        assert stored["state"] == "pending"
        assert service._timers
        service._cancel_timers()
        adapter._draining_response = lambda: None
        await service.evaluate(SID, db, profile=PROFILE)
        await drain_adapter(adapter)
        assert shots[1]["journal"] == shots[1]["spec"] == {"temperature": 0.3, "reasoning_effort": "high"}
        assert executor.calls[0]["model_options"] == shots[1]["spec"]
        assert executor.calls[0]["requested_model"] == "grok-4.7"
        assert executor.calls[0]["requested_provider"] == "sub2api"
        assert PENDING_KEY not in _config(db.get_session(SID))
        assert lock.entries >= 1
    finally:
        service.stop()
        db.close()


@pytest.mark.asyncio
async def test_gates_and_empty_work_do_not_commit_or_schedule(tmp_path, monkeypatch):
    db, executor, _adapter, service, lock = _service_case(monkeypatch, tmp_path, "approval-before")
    service.adapter._run_statuses["run_old"] = {
        "status": "waiting_for_approval", "session_id": SID, "updated_at": 50,
    }
    try:
        decision = await service.evaluate(SID, db, profile=PROFILE)
        assert decision.reason == "approval_pending"
        assert executor.calls == []
        assert lock.entries == 0
        assert not service._timers
        assert _config(db.get_session(SID))[PENDING_KEY] == "high"
    finally:
        service.stop()
        db.close()

    db, executor, adapter, service, lock = _service_case(monkeypatch, tmp_path, "cancel-before")
    adapter._run_statuses["run_old"] = {"status": "cancelled", "session_id": SID, "updated_at": 50}
    try:
        decision = await service.evaluate(SID, db, profile=PROFILE)
        assert decision.reason == "cancelled"
        assert lock.entries == 0
        identity = _notice_identity(db)
        assert service.journal.receipt_record(identity)["reason"] == "cancelled"
        assert _config(db.get_session(SID))[PENDING_KEY] == "high"
    finally:
        service.stop()
        db.close()

    db, executor, _adapter, service, lock = _service_case(
        monkeypatch, tmp_path, "no-work", notice=False,
    )
    try:
        decision = await service.evaluate(SID, db, profile=PROFILE)
        assert decision.action == "skip"
        assert decision.reason == "no_trigger"
        assert lock.entries == 0
        assert not service._timers
        assert _config(db.get_session(SID))[PENDING_KEY] == "high"
    finally:
        service.stop()
        db.close()

    db, _executor, _adapter, service, lock = _service_case(monkeypatch, tmp_path, "attempts-before")
    try:
        service.journal.freeze(SID, "seed-key", ["seed"], {"input": "seed", "request_key": "seed-key"}, 1.0)
        service.journal.record_attempt(SID, MAX_ATTEMPTS, "", 1.0, 1.0, "pending")
        decision = await service.evaluate(SID, db, profile=PROFILE)
        assert decision.reason == "attempts_exhausted"
        assert lock.entries == 0
        assert _config(db.get_session(SID))[PENDING_KEY] == "high"
        identity = _notice_identity(db)
        assert service.journal.receipt_record(identity)["reason"] == "attempts_exhausted"
    finally:
        service.stop()
        db.close()


def test_freeze_alias_does_not_rewrite_and_replace_keeps_receipts(tmp_path):
    journal = ContinuationJournal(tmp_path / "clawd_continuation.db")
    journal.open()
    try:
        # A receipt consumes the current request. Seed it before the in-flight
        # freeze whose alias preservation is the behavior under test.
        journal.add_receipts(["keep-me"], "s", None, 0.5, reason="cancelled")
        journal.freeze("s", "key-1", ["a"], {
            "request_key": "key-1", "model_options": {"reasoning_effort": "low"},
        }, 1.0)
        again = journal.freeze("s", "key-2", ["a", "b"], {
            "request_key": "key-2", "model_options": {"reasoning_effort": "high"},
        }, 3.0)
        assert again["request_key"] == "key-1"
        assert again["body"]["model_options"]["reasoning_effort"] == "low"
        assert journal.replace_inflight_body("s", "key-2", {
            "request_key": "key-2", "model_options": {"reasoning_effort": "high"},
        }, 4.0) is False
        assert journal.replace_inflight_body("s", "key-1", {
            "request_key": "key-1", "model_options": {"reasoning_effort": "high"},
        }, 4.0) is True
        stored = journal.load_request("s")
        assert stored["request_key"] == "key-1"
        assert stored["aliases"] == ["a"]
        assert stored["attempts"] == 0
        assert stored["body"]["model_options"]["reasoning_effort"] == "high"
        assert journal.receipt_record("keep-me")["reason"] == "cancelled"
        assert journal.receipt_record("keep-me")["run_id"] is None
    finally:
        journal.close()
