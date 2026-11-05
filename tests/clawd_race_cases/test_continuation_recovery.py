"""Journal interrupt recovery and the human-then-assistant timeline.

A crash is injected after a real journal or admit call returns. The next
service uses the real admit path. The marked executor is not a model round.
Panel process, sidecar, and self-POST are absent. No skip and no xfail.
"""

from __future__ import annotations

import pytest

from gateway.clawd_continuation_admit import TEST_EXECUTOR_MARKER, admit_continuation
from gateway.clawd_continuation_decision import CONTINUATION_TEXT
from gateway.clawd_continuation_journal import JournalUnavailable

from tests.clawd_race_cases.helpers import (
    SECRET, Clock, Crash, MarkedExecutor, add_notice, continuation_rows, drain_adapter,
    forbid_user_turn, isolate, journal_path, make_adapter, open_db, open_service, open_store,
    plain_user_rows, seed_session,
)


def _ready(home, session_id: str, delegation_id: str):
    db = open_db(home)
    seed_session(db, session_id)
    add_notice(db, session_id, delegation_id)
    clock = Clock()
    executor = MarkedExecutor()
    adapter = make_adapter(db, open_store(home / "runs_idempotency.db"), executor, profile="default")
    service = open_service(adapter, journal_path(db), now=clock)
    return db, clock, executor, adapter, service


@pytest.mark.asyncio
async def test_interrupt_after_freeze_before_claim_recovers_once(tmp_path, monkeypatch):
    leaked = forbid_user_turn(monkeypatch)
    home = isolate(monkeypatch, tmp_path / "freeze-interrupt")
    db, clock, executor, adapter, service = _ready(home, "freeze-session", "deleg_freeze")
    recovered = None
    real_claim = service.journal.claim

    def claim(*args, **kwargs):
        raise JournalUnavailable("injected interrupt after freeze, before claim")

    service.journal.claim = claim
    try:
        with pytest.raises(JournalUnavailable):
            await service.evaluate("freeze-session", db, profile="default")
        await drain_adapter(adapter)
        assert executor.calls == []
        assert leaked == []
        assert continuation_rows(db, "freeze-session") == []
        service.journal.claim = real_claim
        service.stop()
        recovered = open_service(adapter, journal_path(db), admit=admit_continuation, now=clock)
        await recovered.recover()
        await drain_adapter(adapter)
        assert len(executor.calls) == 1
        assert executor.calls[0]["user_message"] == CONTINUATION_TEXT
        assert SECRET not in executor.calls[0]["user_message"]
        assert executor.calls[0]["requested_provider"] == "sub2api"
        assert len(continuation_rows(db, "freeze-session")) == 1
        assert len(plain_user_rows(db, "freeze-session")) == 1
        await recovered.recover()
        await drain_adapter(adapter)
        assert len(executor.calls) == 1
        assert leaked == []
    finally:
        if recovered is not None and recovered.running:
            recovered.stop()
        if service.running:
            service.stop()
        db.close()


@pytest.mark.asyncio
async def test_interrupt_after_admit_before_receipt_does_not_relaunch(tmp_path, monkeypatch):
    leaked = forbid_user_turn(monkeypatch)
    home = isolate(monkeypatch, tmp_path / "admit-interrupt")
    db, clock, executor, adapter, service = _ready(home, "admit-session", "deleg_admit")

    async def crash_after_real_admit(adapter_arg, spec, *, db, profile):
        # Fault injection only: the real admit has already reserved and launched.
        run_id = await admit_continuation(adapter_arg, spec, db=db, profile=profile)
        raise Crash(run_id)

    service.admit = crash_after_real_admit
    recovered = None
    try:
        with pytest.raises(Crash):
            await service.evaluate("admit-session", db, profile="default")
        await drain_adapter(adapter)
        assert len(executor.calls) == 1
        assert adapter._run_statuses
        assert list(adapter._run_statuses.values())[-1]["continuation_executor"] == TEST_EXECUTOR_MARKER
        assert list(adapter._run_statuses.values())[-1]["output"] == "test-executor-not-llm"
        service.stop()
        clock.advance(31)
        recovered = open_service(adapter, journal_path(db), admit=admit_continuation, now=clock)
        await recovered.recover()
        await drain_adapter(adapter)
        assert len(executor.calls) == 1
        assert len(continuation_rows(db, "admit-session")) == 1
        assert leaked == []
        identity = "clawd-continue-admit-session-deleg_admit"
        assert identity in recovered.journal.receipted([identity])
    finally:
        if recovered is not None and recovered.running:
            recovered.stop()
        if service.running:
            service.stop()
        db.close()


@pytest.mark.asyncio
async def test_human_then_assistant_then_later_notice_is_recoverable(tmp_path, monkeypatch):
    leaked = forbid_user_turn(monkeypatch)
    home = isolate(monkeypatch, tmp_path / "timeline")
    db, _clock, executor, adapter, service = _ready(home, "timeline-session", "deleg_old")
    sid = "timeline-session"
    try:
        db.append_message(sid, "user", content="我补充一句", timestamp=30)
        blocked = await service.evaluate(sid, db, profile="default")
        await drain_adapter(adapter)
        assert blocked.action == "skip"
        assert executor.calls == []
        db.append_message(sid, "assistant", content="收到补充", timestamp=40)
        still_closed = await service.evaluate(sid, db, profile="default")
        await drain_adapter(adapter)
        assert still_closed.action == "skip"
        assert executor.calls == []
        add_notice(db, sid, "deleg_later")
        admitted = await service.evaluate(sid, db, profile="default")
        await drain_adapter(adapter)
        assert admitted.action == "admit"
        assert len(executor.calls) == 1
        assert executor.calls[0]["user_message"] == CONTINUATION_TEXT
        assert SECRET not in executor.calls[0]["user_message"]
        assert leaked == []
        assert len(plain_user_rows(db, sid)) == 2
        assert len(continuation_rows(db, sid)) == 1
        assert service.status(sid)["last_event"]["kind"] == "admitted"
    finally:
        service.stop()
        db.close()


@pytest.mark.asyncio
async def test_panel_closed_still_continues_without_self_post(tmp_path, monkeypatch):
    """No panel process and no sidecar thread. ``continuation=started`` is not a model round."""
    import threading

    leaked = forbid_user_turn(monkeypatch)
    home = isolate(monkeypatch, tmp_path / "no-panel")
    db = open_db(home)
    sid = "no-panel"
    seed_session(db, sid)
    executor = MarkedExecutor()
    adapter = make_adapter(db, open_store(home / "runs_idempotency.db"), executor, profile="default")
    from gateway.clawd_continuation import arm_adapter, disarm_adapter, ready_fields, service_for
    from gateway.wake import persist_delegation_delivery

    assert ready_fields(object())["continuation"] == "not-started"
    arm_adapter(adapter)
    try:
        live = ready_fields(adapter)
        assert live["continuation"] == "started"
        assert live["continuationRunning"] is True
        assert service_for(adapter) is not None
        await persist_delegation_delivery(
            adapter, text="[ASYNC DELEGATION BATCH COMPLETE — deleg_no_panel]\n结果",
            session_id=sid, evt={"delegation_id": "deleg_no_panel", "source": "internal",
                                 "results": [{"status": "completed"}]},
        )
        for _ in range(50):
            service = service_for(adapter)
            pending = [task for task in service._tasks if not task.done()]
            runs = [task for task in adapter._active_run_tasks.values() if not task.done()]
            if executor.calls and not pending and not runs:
                break
            import asyncio
            await asyncio.sleep(0)
        else:
            raise AssertionError("panel-closed continuation did not admit through the real hook")
        assert len(executor.calls) == 1
        assert executor.calls[0]["user_message"] == CONTINUATION_TEXT
        assert executor.calls[0]["requested_model"] == "grok-4.7"
        assert SECRET not in executor.calls[0]["user_message"]
        statuses = [item for item in adapter._run_statuses.values() if item.get("session_id") == sid]
        assert statuses[-1]["status"] == "completed"
        assert statuses[-1]["continuation_executor"] == TEST_EXECUTOR_MARKER
        assert statuses[-1]["output"] == "test-executor-not-llm"
        assert leaked == []
        assert len(plain_user_rows(db, sid)) == 1
        assert "auto-continue" not in {thread.name for thread in threading.enumerate()}
        assert service_for(adapter).status(sid)["last_event"]["kind"] == "admitted"
    finally:
        disarm_adapter(adapter)
        assert service_for(adapter) is None
        db.close()
