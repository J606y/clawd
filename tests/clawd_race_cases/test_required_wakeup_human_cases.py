"""Wake-entry idempotency and cancel/human generation fences.

Not executed while this file was written. A marked executor is not a model
round. ``continuation=started`` is not a pass. No skip and no xfail.

These cases do not close the known unfixed continuation findings. A second
wake that receipts after admit is not proof the receipt waited for a model
round, and a generation fence is not proof a human line inserted after admit
is honored.
"""

from __future__ import annotations

import asyncio
import threading
import uuid

import pytest

from gateway.clawd_continuation_decision import CONTINUATION_TEXT
from gateway.session_authority import LiveSession, SessionAuthority
from gateway.session_contract import Principal, SessionRef
from hermes_state_runtime import (
    RuntimeStoreError,
    admit_session_input,
    begin_runtime_epoch,
    claim_session_input,
)

from tests.clawd_race_cases.helpers import (
    SECRET,
    MarkedExecutor,
    continuation_rows,
    drain_adapter,
    forbid_user_turn,
    isolate,
    make_adapter,
    open_db,
    open_store,
    plain_user_rows,
    seed_session,
)


class _Runner:
    """Runner seam ``interrupt`` already calls. It does not decide the generation."""

    _draining = False
    _running_agents: dict = {}

    def _cached_agent_for(self, _key):
        return None


class _LaterAgent:
    def __init__(self) -> None:
        self.steers: list[str] = []
        self.interrupts = 0
        self.clears = 0

    def steer(self, text: str) -> bool:
        self.steers.append(text)
        return True

    def interrupt(self, *_args, **_kwargs) -> None:
        self.interrupts += 1

    def clear_interrupt(self) -> None:
        self.clears += 1


class _Conn:
    def __init__(self, authority, actor) -> None:
        self.authority = authority
        self.actor = actor


def _actor(profile: str = "owned", subject: str = "api") -> Principal:
    return Principal(
        subject,
        profile,
        frozenset({"session:read", "session:control"}),
        subject + "-transport",
    )


def _running(tmp_path, monkeypatch):
    home = isolate(monkeypatch, tmp_path / "generation-home")
    db = open_db(home)
    sid = "gen-" + uuid.uuid4().hex[:8]
    db.create_session(sid, source="api_server", model="grok-4.7", profile_name="owned")
    epoch = begin_runtime_epoch(db, instance_id="race")
    authority = SessionAuthority(
        _Runner(), profile_id="owned", instance_id="race", db=db, epoch=epoch,
    )
    live = LiveSession(None, "race-route-" + uuid.uuid4().hex[:8])
    authority.sessions[sid] = live
    run_id = "run_" + uuid.uuid4().hex[:8]
    admit_session_input(
        db, epoch=epoch, principal_id="api", session_id=sid, request_id=run_id,
        payload={"text": "owned work"},
    )
    claimed = claim_session_input(db, epoch=epoch, session_id=sid)
    assert claimed and claimed["status"] == "started" and type(claimed["generation"]) is int
    generation = claimed["generation"]
    ref = SessionRef("owned", sid)
    handle = authority._handle(ref)
    assert handle.execution_generation == generation
    assert handle.execution_state == "running"
    live.event_stream.execution = {"admission_id": claimed["admission_id"]}
    return {
        "db": db, "sid": sid, "authority": authority, "generation": generation,
        "ref": ref, "actor": _actor(),
    }


async def _wait_first_wake(adapter, executor) -> None:
    from gateway.clawd_continuation import service_for

    for _ in range(50):
        service = service_for(adapter)
        assert service is not None
        pending = [task for task in service._tasks if not task.done()]
        runs = [task for task in adapter._active_run_tasks.values() if not task.done()]
        if executor.calls and not pending and not runs:
            return
        await asyncio.sleep(0)
    raise AssertionError("first wakeup did not settle through the real delivery hook")


async def _wait_idle(adapter) -> None:
    from gateway.clawd_continuation import service_for

    for _ in range(50):
        service = service_for(adapter)
        pending = [task for task in service._tasks if not task.done()]
        runs = [task for task in adapter._active_run_tasks.values() if not task.done()]
        if not pending and not runs:
            return
        await asyncio.sleep(0)
    raise AssertionError("later wakeup did not finish")


def _task_errors(adapter) -> list:
    from gateway.clawd_continuation import service_for

    errors = []
    for task in list(service_for(adapter)._tasks):
        if not task.done() or task.cancelled():
            continue
        exc = task.exception()
        if exc is not None:
            errors.append(exc)
    return errors


@pytest.mark.asyncio
async def test_duplicate_wakeup_persisted_delivery_admits_once(tmp_path, monkeypatch):
    """Same delegation through the real wake entry twice, then the scheduler again."""
    leaked = forbid_user_turn(monkeypatch)
    home = isolate(monkeypatch, tmp_path / "dup-wake")
    db = open_db(home)
    sid = "dup-wake"
    seed_session(db, sid)
    executor = MarkedExecutor()
    adapter = make_adapter(db, open_store(home / "runs_idempotency.db"), executor, profile="default")
    from gateway.clawd_continuation import (
        arm_adapter, disarm_adapter, note_persisted_delivery, ready_fields, service_for,
    )
    from gateway.clawd_continuation_admit import TEST_EXECUTOR_MARKER
    from gateway.wake import persist_delegation_delivery

    evt = {
        "delegation_id": "deleg_dup_wake",
        "source": "internal",
        "results": [{"status": "completed"}],
    }
    text = "[ASYNC DELEGATION BATCH COMPLETE — deleg_dup_wake]\n结果"
    arm_adapter(adapter)
    try:
        assert ready_fields(adapter)["continuation"] == "started"
        await persist_delegation_delivery(adapter, text=text, session_id=sid, evt=evt)
        await _wait_first_wake(adapter, executor)
        await drain_adapter(adapter)
        await persist_delegation_delivery(adapter, text=text, session_id=sid, evt=evt)
        note_persisted_delivery(adapter, sid, evt)
        await _wait_idle(adapter)
        await drain_adapter(adapter)
        assert _task_errors(adapter) == []
        assert len(executor.calls) == 1
        assert executor.calls[0]["user_message"] == CONTINUATION_TEXT
        assert executor.calls[0]["requested_model"] == "grok-4.7"
        assert SECRET not in executor.calls[0]["user_message"]
        assert leaked == []
        assert len(plain_user_rows(db, sid)) == 1
        assert len(continuation_rows(db, sid)) == 1
        statuses = [item for item in adapter._run_statuses.values() if item.get("session_id") == sid]
        assert statuses[-1]["continuation_executor"] == TEST_EXECUTOR_MARKER
        assert statuses[-1]["output"] == "test-executor-not-llm"
        assert "auto-continue" not in {thread.name for thread in threading.enumerate()}
        identity = f"clawd-continue-{sid}-deleg_dup_wake"
        assert identity in service_for(adapter).journal.receipted([identity])
    finally:
        disarm_adapter(adapter)
        db.close()


@pytest.mark.asyncio
async def test_stale_generation_cancel_and_human_steer_do_not_latch(tmp_path, monkeypatch):
    """A stale generation, an internal actor, or another profile must not latch stop or steer."""
    env = _running(tmp_path, monkeypatch)
    from gateway.session_busy_controls import correct

    authority, sid, generation, ref, actor = (
        env["authority"], env["sid"], env["generation"], env["ref"], env["actor"],
    )
    try:
        with pytest.raises(RuntimeStoreError) as stale_stop:
            await authority.interrupt(actor, ref, generation + 1)
        assert stale_stop.value.reason == "stale_generation"

        with pytest.raises(RuntimeStoreError) as stale_steer:
            await correct(_Conn(authority, actor), ref, {
                "session_id": sid, "text": "先停一下", "execution_generation": generation + 1,
            }, verb="steer")
        assert stale_steer.value.reason == "stale_generation"

        internal = _actor(subject="internal")
        with pytest.raises(RuntimeStoreError) as internal_stop:
            await authority.interrupt(internal, ref, generation + 1)
        assert internal_stop.value.reason == "stale_generation"
        with pytest.raises(RuntimeStoreError) as internal_steer:
            await correct(_Conn(authority, internal), ref, {
                "session_id": sid, "text": "内部人话", "execution_generation": generation + 1,
            }, verb="steer")
        assert internal_steer.value.reason == "stale_generation"

        with pytest.raises(RuntimeStoreError) as extra:
            await correct(_Conn(authority, actor), ref, {
                "session_id": sid, "text": "多字段", "execution_generation": generation,
                "source": "internal",
            }, verb="steer")
        assert extra.value.reason == "invalid_params"

        foreign = _actor("other")
        with pytest.raises(RuntimeStoreError) as mismatch:
            await authority.interrupt(foreign, ref, generation)
        assert mismatch.value.reason == "profile_mismatch"
        with pytest.raises(RuntimeStoreError) as foreign_steer:
            await correct(_Conn(authority, foreign), ref, {
                "session_id": sid, "text": "别的 profile", "execution_generation": generation,
            }, verb="steer")
        assert foreign_steer.value.reason == "profile_mismatch"
        assert sid not in authority.pending_stops
        assert sid not in authority.pending_steers
    finally:
        env["db"].close()


@pytest.mark.asyncio
async def test_exact_generation_steer_and_stop_do_not_reach_a_later_generation(tmp_path, monkeypatch):
    """Exact generation latches only that claim. A later adopt must not receive it."""
    env = _running(tmp_path, monkeypatch)
    from gateway.session_busy_controls import correct

    authority, sid, generation, ref, actor = (
        env["authority"], env["sid"], env["generation"], env["ref"], env["actor"],
    )
    try:
        steered = await correct(_Conn(authority, actor), ref, {
            "session_id": sid, "text": "只给这一代", "execution_generation": generation,
        }, verb="steer")
        assert steered["status"] == "queued"
        assert steered["execution_generation"] == generation
        assert authority.pending_steers[sid] == (generation, ["只给这一代"])

        await authority.interrupt(actor, ref, generation)
        assert authority.pending_stops[sid] == generation

        later = _LaterAgent()
        authority.adopt_agent(sid, generation + 1, later)
        assert later.steers == []
        assert later.interrupts == 0
        assert later.clears == 0
        assert authority.pending_stops.get(sid) == generation
        assert sid not in authority.pending_steers

        with pytest.raises(RuntimeStoreError) as again:
            await correct(_Conn(authority, actor), ref, {
                "session_id": sid, "text": "下一代", "execution_generation": generation + 1,
            }, verb="steer")
        assert again.value.reason == "stale_generation"
        assert sid not in authority.pending_steers
        assert authority.pending_stops.get(sid) == generation
    finally:
        env["db"].close()
