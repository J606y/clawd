"""Core continuation: decision, journal, service, and API lifecycle.

Unit cases call ``decide`` with synthetic rows. Integration cases use SessionDB,
the delivery hook, and the admit seam. A GET status read must not admit a run.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from gateway.clawd_continuation_decision import (
    CONTINUATION_TEXT, DISPLAY_KIND, MARK, Decision, Gates, Snapshot, decide, is_human,
)

SID = "sess-continue-1"
SECRET = "SECRET-PROMPT-DO-NOT-LEAK"


def _row(row_id, role, content="", kind=None, meta=None, timestamp=100.0, tool_calls=None):
    row = {
        "id": row_id, "role": role, "content": content, "display_kind": kind,
        "display_metadata": meta, "timestamp": timestamp,
    }
    if tool_calls is not None:
        row["tool_calls"] = tool_calls
    return row


def _notice(row_id=5, deleg="deleg_aa", timestamp=50.0, meta=None, content=None):
    return _row(
        row_id, "user", content or f"[ASYNC DELEGATION BATCH COMPLETE — {deleg}]\nresult",
        kind="async_delegation_complete",
        meta=meta if meta is not None else {"delegation_id": deleg},
        timestamp=timestamp,
    )


def _session(**extra):
    base = {
        "id": SID, "source": "api_server", "model": "grok-4.7", "ended_at": None,
        "profile_name": None, "model_config": {
            "browser_model_lock": {
                "confirmed": True, "model": "grok-4.7", "provider": "sub2api",
                "model_options": {"reasoning_effort": "high"},
            },
        },
    }
    base.update(extra)
    return base


def _snap(rows, **extra) -> Snapshot:
    fields = dict(
        rows=tuple(rows), session=_session(), recent_route=None, lease_expires_at=None,
        latest_run_status=None, gates=Gates(), now=100.0, profile="default",
    )
    fields.update(extra)
    return Snapshot(**fields)


def _ready_rows():
    return [
        _row(1, "user", "查一下", timestamp=10),
        _row(2, "assistant", "已派", timestamp=20),
        _notice(),
    ]


def test_notice_after_assistant_and_human_admits_locked_runtime():
    decision = decide(_snap(_ready_rows()), session_id=SID)
    assert decision.action == "admit"
    assert decision.spec["model"] == "grok-4.7"
    assert decision.spec["provider"] == "sub2api"
    assert decision.spec["model_options"] == {"reasoning_effort": "high"}
    assert decision.spec["locked"] is True
    assert decision.spec["input"] == CONTINUATION_TEXT
    assert decision.spec["cwd"] == ""
    assert decision.spec["session_id"] == SID
    assert SECRET not in decision.spec["input"]
    assert "如果" not in decision.spec["input"]
    assert "等待" not in decision.spec["input"]


def test_human_after_notice_does_not_trigger():
    rows = _ready_rows() + [_row(6, "user", "先别管它", timestamp=60)]
    assert decide(_snap(rows), session_id=SID).action == "skip"


def test_answered_human_after_dispatch_does_not_veto_later_notice():
    rows = [
        _row(1, "user", "查一下", timestamp=10),
        _row(2, "assistant", "已派", timestamp=20),
        _row(3, "user", "补充一句", timestamp=30),
        _row(4, "assistant", "收到", timestamp=40),
        _notice(timestamp=50),
    ]
    assert decide(_snap(rows), session_id=SID).action == "admit"


def test_interim_suppressed_and_hidden_are_not_completions():
    rows = [
        _row(1, "user", "查", timestamp=10),
        _row(2, "assistant", "等", timestamp=20),
        _notice(3, meta={"delegation_id": "d1", "delivery_notice": "task_failure:0"}, timestamp=30),
        _notice(4, deleg="d2", meta={"delegation_id": "d2", "presentation_suppressed": True}, timestamp=40),
        _row(5, "user", "[ASYNC DELEGATION TASK FAILED — d3]\nboom", kind="async_delegation_complete", timestamp=45),
    ]
    assert decide(_snap(rows), session_id=SID).action == "skip"


def test_steer_is_human_and_continuation_mark_is_not():
    assert is_human(_row(1, "user", "插话", kind="steer"))
    assert not is_human(_row(2, "user", CONTINUATION_TEXT, kind=DISPLAY_KIND))
    assert not is_human(_notice())
    rows = _ready_rows() + [_row(6, "user", "停", kind="steer", timestamp=60)]
    assert decide(_snap(rows), session_id=SID).action == "skip"


def test_success_streak_does_not_block_or_ask_the_model():
    """Replaces the old streak_limit hard stop. A success streak is not a block.

    Failure budget and cancel still block these same rows, without asking the model.
    """
    from gateway.clawd_continuation_decision import MAX_ATTEMPTS, MAX_STREAK, streak_count

    rows = [_row(1, "user", "大任务", timestamp=1)]
    for index in range(5):
        rows.append(_row(10 + index, "user", f"{MARK} 续", timestamp=10 + index))
    rows.append(_row(20, "assistant", "又派了", timestamp=30))
    rows.append(_notice(21, timestamp=40))
    assert streak_count(rows) == MAX_STREAK == 5
    decision = decide(_snap(rows), session_id=SID)
    assert decision.action == "admit"
    assert decision.reason == "ready"
    assert decision.reason != "streak_limit"
    assert decision.spec["input"] == CONTINUATION_TEXT
    assert "发一句话即可" not in decision.spec["input"]
    assert "已连续自动继续" not in decision.spec["input"]
    exhausted = decide(_snap(rows, attempts=MAX_ATTEMPTS), session_id=SID)
    assert exhausted.action == "block"
    assert exhausted.reason == "attempts_exhausted"
    cancelled = decide(_snap(rows, latest_run_status="cancelled"), session_id=SID)
    assert cancelled.action == "block"
    assert cancelled.reason == "cancelled"


def test_plan_tool_submit_blocks_even_when_notice_is_ready():
    rows = [
        _row(1, "user", "查一下", timestamp=10),
        _row(2, "assistant", "", timestamp=20, tool_calls=[
            {"function": {"name": "plan_mode", "arguments": "{\"action\": \"submit\"}"}},
        ]),
        _notice(timestamp=50),
    ]
    decision = decide(_snap(rows), session_id=SID)
    assert decision.action == "block"
    assert decision.reason == "plan_pending"


def test_cancelled_approval_and_unknown_ownership_block():
    assert decide(_snap(_ready_rows(), latest_run_status="cancelled"), session_id=SID).reason == "cancelled"
    assert decide(_snap(_ready_rows(), gates=Gates(approval=True)), session_id=SID).reason == "approval_pending"
    assert decide(_snap(_ready_rows(), gates=Gates(clarify=True)), session_id=SID).reason == "clarify_pending"
    assert decide(_snap(_ready_rows(), gates=Gates(plan=True)), session_id=SID).reason == "plan_pending"
    assert decide(_snap(_ready_rows(), store_ok=False), session_id=SID).reason == "ownership_unconfirmed"
    assert decide(_snap(_ready_rows(), foreign_holder=None), session_id=SID).reason == "ownership_unconfirmed"
    assert decide(_snap(_ready_rows(), session=_session(source="tui")), session_id=SID).reason == "not_api_source"
    assert decide(_snap(_ready_rows(), session=_session(source="cli")), session_id=SID).reason == "not_api_source"


def test_stored_cwd_is_kept_and_not_replaced_with_a_default():
    session = _session(cwd="/work/proj-a", session_key="agent:main:api:sess-continue-1")
    decision = decide(_snap(_ready_rows(), session=session), session_id=SID)
    assert decision.spec["cwd"] == "/work/proj-a"
    assert decision.spec["session_key"] == "agent:main:api:sess-continue-1"
    assert decision.spec["session_id"] == SID
    assert os.getcwd() not in decision.spec["cwd"]
    session = _session(model="grok-4.7", model_config={})
    decision = decide(_snap(_ready_rows(), session=session, recent_route=None), session_id=SID)
    assert decision.action == "block"
    assert decision.reason == "provider_unresolved"


def test_recent_route_is_used_when_no_lock_and_mismatch_fails_closed():
    session = _session(model="grok-4.7", model_config={"reasoning_effort": "low"})
    route = {"model": "grok-4.7", "billing_provider": "sub2api"}
    decision = decide(_snap(_ready_rows(), session=session, recent_route=route), session_id=SID)
    assert decision.spec["provider"] == "sub2api"
    assert decision.spec["model_options"] == {"reasoning_effort": "low"}
    assert decision.spec["locked"] is False
    bad = decide(_snap(_ready_rows(), session=session, recent_route={"model": "other", "billing_provider": "x"}), session_id=SID)
    assert bad.reason == "provider_unresolved"


def test_busy_lease_defers_instead_of_admitting():
    decision = decide(_snap(_ready_rows(), lease_expires_at=200.0), session_id=SID)
    assert decision.action == "defer"
    assert decision.reason == "busy"
    assert decision.delay > 0


class _FakeDB:
    def __init__(self, rows, session=None):
        self.rows = list(rows)
        self.session = session if session is not None else _session()
        self.reads = 0
        self.db_path = None

    def get_session(self, _sid):
        return self.session

    def get_recent_session_model_route(self, _sid):
        return None

    def get_messages(self, _sid, limit=300, latest=True):
        self.reads += 1
        return list(self.rows)

    def _read_one(self, *_args, **_kwargs):
        return None


def _service(tmp_path, db, admit):
    from gateway.clawd_continuation import ContinuationService
    service = ContinuationService(
        adapter=type("A", (), {"_run_statuses": {}})(),
        journal_path=tmp_path / "clawd_continuation.db",
        admit=admit,
        now=lambda: 100.0,
    )
    service.adapter._run_statuses = {}
    service.open()
    service.running = True
    return service


@pytest.mark.asyncio
async def test_service_admits_once_and_get_status_has_no_side_effect(tmp_path):
    admitted = []

    async def admit(_adapter, spec, *, db, profile):
        admitted.append((spec, profile))
        return "run_once"

    service = _service(tmp_path, _FakeDB(_ready_rows()), admit)
    db = _FakeDB(_ready_rows())
    try:
        first = await service.evaluate(SID, db, profile="default")
        assert first.action == "admit"
        assert admitted[0][0]["provider"] == "sub2api"
        assert SECRET not in admitted[0][0]["input"]
        second = await service.evaluate(SID, db, profile="default")
        assert second.action == "skip"
        assert len(admitted) == 1
        before = len(admitted)
        status = service.status(SID)
        assert status["last_event"]["kind"] == "admitted"
        assert len(admitted) == before
    finally:
        service.stop()


@pytest.mark.asyncio
async def test_human_race_after_decision_does_not_admit(tmp_path):
    admitted = []

    async def admit(*_args, **_kwargs):
        admitted.append(1)
        return "run_race"

    class RaceDB(_FakeDB):
        def get_messages(self, _sid, limit=300, latest=True):
            self.reads += 1
            if self.reads >= 2:
                return _ready_rows() + [_row(9, "user", "我来了", timestamp=90)]
            return _ready_rows()

    service = _service(tmp_path, RaceDB([]), admit)
    try:
        decision = await service.evaluate(SID, RaceDB([]), profile="default")
        assert decision.action == "admit"
        assert admitted == []
    finally:
        service.stop()


@pytest.mark.asyncio
async def test_gates_block_before_admit(tmp_path):
    admitted = []

    async def admit(*_args, **_kwargs):
        admitted.append(1)
        return "nope"

    service = _service(tmp_path, _FakeDB(_ready_rows()), admit)
    service.adapter._run_statuses["run_old"] = {
        "status": "waiting_for_approval", "session_id": SID, "updated_at": 90,
    }
    try:
        decision = await service.evaluate(SID, _FakeDB(_ready_rows()), profile="default")
        assert decision.reason == "approval_pending"
        assert admitted == []
        assert service.status(SID)["last_event"]["detail"] == "approval_pending"
    finally:
        service.stop()


@pytest.mark.asyncio
async def test_busy_then_clear_admits_once(tmp_path):
    admitted = []

    async def admit(*_args, **_kwargs):
        admitted.append(1)
        return "run_busy"

    class BusyDB(_FakeDB):
        def __init__(self):
            super().__init__(_ready_rows())
            self.busy = True

        def _read_one(self, *_args, **_kwargs):
            if self.busy:
                return {"expires_at": 500.0}
            return None

    db = BusyDB()
    service = _service(tmp_path, db, admit)
    try:
        deferred = await service.evaluate(SID, db, profile="default")
        assert deferred.reason == "busy"
        assert admitted == []
        db.busy = False
        admitted_decision = await service.evaluate(SID, db, profile="default")
        assert admitted_decision.action == "admit"
        assert admitted == [1]
    finally:
        service.stop()


@pytest.mark.asyncio
async def test_restart_recovers_watched_session_once(tmp_path):
    from gateway.clawd_continuation import ContinuationService
    journal = tmp_path / "clawd_continuation.db"
    admitted = []

    async def admit(*_args, **_kwargs):
        admitted.append(1)
        return "run_recovered"

    adapter = type("A", (), {"_run_statuses": {}})()
    adapter._session_db = _FakeDB(_ready_rows())
    first = ContinuationService(adapter, journal_path=journal, admit=admit, now=lambda: 100.0)
    first.open()
    first.journal.watch(SID, 10_000.0)
    first.stop()
    second = ContinuationService(adapter, journal_path=journal, admit=admit, now=lambda: 100.0)
    second.open()
    second.running = True
    try:
        await second.recover()
        assert admitted == [1]
        await second.recover()
        assert admitted == [1]
    finally:
        second.stop()
