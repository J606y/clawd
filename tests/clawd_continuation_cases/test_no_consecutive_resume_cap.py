"""取消成功次数硬停后的契约。编写时未执行本文件。

第 5/6/8 次及更长连续完成应 admit。旧 streak_limit 回执仅在能归因时释放。
run_id 为空的取消、失败预算、无事件、同时刻含糊、以及无法绑定身份的
后续安全停止不得清除。新回执必须带原因。不另写调度器，不把助手 done 当成完成。
"""

from __future__ import annotations

import pytest

from gateway.clawd_continuation_admit import ContinuationAdmitError
from gateway.clawd_continuation_decision import (
    BACKOFF_S,
    CONTINUATION_TEXT,
    DISPLAY_KIND,
    LEGACY_MARK,
    MARK,
    MAX_ATTEMPTS,
    MAX_STREAK,
    Decision,
    Gates,
    Snapshot,
    continuation_identity,
    decide,
    streak_count,
)

SID = "sess-no-five-cap"
SECRET = "SECRET-PROMPT-DO-NOT-LEAK"


def _row(row_id, role, content="", kind=None, meta=None, timestamp=100.0, tool_calls=None):
    row = {
        "id": row_id,
        "role": role,
        "content": content,
        "display_kind": kind,
        "display_metadata": meta,
        "timestamp": timestamp,
    }
    if tool_calls is not None:
        row["tool_calls"] = tool_calls
    return row


def _notice(row_id=5, deleg="deleg_next", timestamp=50.0):
    return _row(
        row_id,
        "user",
        f"[ASYNC DELEGATION BATCH COMPLETE — {deleg}]\nresult",
        kind="async_delegation_complete",
        meta={"delegation_id": deleg},
        timestamp=timestamp,
    )


def _session(**extra):
    base = {
        "id": SID,
        "source": "api_server",
        "model": "grok-4.7",
        "ended_at": None,
        "profile_name": None,
        "cwd": "/work/proj-a",
        "session_key": "agent:main:api:sess-no-five-cap",
        "model_config": {
            "browser_model_lock": {
                "confirmed": True,
                "model": "grok-4.7",
                "provider": "sub2api",
                "model_options": {"reasoning_effort": "high"},
            },
        },
    }
    base.update(extra)
    return base


def _snap(rows, **extra) -> Snapshot:
    fields = dict(
        rows=tuple(rows),
        session=_session(),
        recent_route=None,
        lease_expires_at=None,
        latest_run_status=None,
        gates=Gates(),
        now=100.0,
        profile="default",
    )
    fields.update(extra)
    return Snapshot(**fields)


def _ready_rows():
    return [
        _row(1, "user", "查一下", timestamp=10),
        _row(2, "assistant", "已派", timestamp=20),
        _notice(),
    ]


def _streak_rows(n, *, legacy=False, deleg="deleg_next"):
    rows = [_row(1, "user", "大任务", timestamp=1)]
    for index in range(n):
        if legacy:
            rows.append(_row(10 + index, "user", f"{LEGACY_MARK} 续{index}", timestamp=10 + index))
        else:
            rows.append(
                _row(10 + index, "user", f"{MARK} 续{index}", kind=DISPLAY_KIND, timestamp=10 + index)
            )
    rows.append(_row(80, "assistant", "又派了", timestamp=30))
    rows.append(_notice(90, deleg=deleg, timestamp=40))
    return rows


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


def _service(tmp_path, admit):
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


def test_success_streak_counter_stays_observable_but_does_not_block():
    """第 5、6 次和更长连续完成仍 admit。计数可留，不得硬停。"""
    assert MAX_STREAK == 5
    assert "已连续自动继续" not in CONTINUATION_TEXT
    assert "发一句话即可" not in CONTINUATION_TEXT
    assert SECRET not in CONTINUATION_TEXT
    for count in (5, 6, 8):
        rows = _streak_rows(count, deleg=f"deleg_{count}")
        assert streak_count(rows) == count
        decision = decide(_snap(rows), session_id=SID)
        assert decision.action == "admit", decision.reason
        assert decision.reason == "ready"
        assert decision.spec["input"] == CONTINUATION_TEXT
        assert decision.spec["model"] == "grok-4.7"
        assert decision.spec["provider"] == "sub2api"
        assert decision.reason != "streak_limit"
    legacy = _streak_rows(5, legacy=True, deleg="deleg_legacy")
    assert streak_count(legacy) == 5
    assert decide(_snap(legacy), session_id=SID).action == "admit"


@pytest.mark.asyncio
async def test_old_streak_receipt_does_not_stick_after_restart(tmp_path):
    """旧 streak_limit 回执（run_id 空且同时刻只有该事件）重启后应放行一次。"""
    from gateway.clawd_continuation import ContinuationService

    rows = _streak_rows(5, deleg="deleg_stuck")
    identity = continuation_identity(SID, rows[-1])
    assert identity
    admitted = []

    async def admit(*_args, **_kwargs):
        admitted.append(1)
        return "run_unstuck"

    journal = tmp_path / "clawd_continuation.db"
    first = ContinuationService(
        type("A", (), {"_run_statuses": {}})(),
        journal_path=journal,
        admit=admit,
        now=lambda: 100.0,
    )
    first.open()
    first.journal.add_receipts([identity], SID, None, 90.0)
    first.journal.event(SID, "blocked", "streak_limit", 90.0)
    first.journal.watch(SID, 10_000.0)
    first.stop()

    adapter = type("A", (), {"_run_statuses": {}, "_session_db": _FakeDB(rows)})()
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


@pytest.mark.asyncio
async def test_duplicate_completion_admits_once(tmp_path):
    admitted = []

    async def admit(_adapter, spec, *, db, profile):
        admitted.append(spec["request_key"])
        return "run_once"

    service = _service(tmp_path, admit)
    db = _FakeDB(_ready_rows())
    try:
        first = await service.evaluate(SID, db, profile="default")
        second = await service.evaluate(SID, db, profile="default")
        assert first.action == "admit"
        assert second.action == "skip"
        assert admitted == [first.spec["request_key"]]
        before = len(admitted)
        service.status(SID)
        assert len(admitted) == before
    finally:
        service.stop()


@pytest.mark.asyncio
async def test_no_open_notice_does_not_spin(tmp_path):
    admitted = []

    async def admit(*_args, **_kwargs):
        admitted.append(1)
        return "run_idle"

    rows = [
        _row(1, "user", "没有新委派", timestamp=10),
        _row(2, "assistant", "做完了", timestamp=20),
    ]
    service = _service(tmp_path, admit)
    try:
        first = await service.evaluate(SID, _FakeDB(rows), profile="default")
        second = await service.evaluate(SID, _FakeDB(rows), profile="default")
        assert first.action == "skip"
        assert first.reason == "no_trigger"
        assert second.reason == "no_trigger"
        assert admitted == []
    finally:
        service.stop()


@pytest.mark.asyncio
async def test_cancel_human_and_approval_still_block(tmp_path):
    admitted = []

    async def admit(*_args, **_kwargs):
        admitted.append(1)
        return "run_blocked"

    service = _service(tmp_path, admit)
    try:
        service.adapter._run_statuses["run_cancel"] = {
            "status": "cancelled",
            "session_id": SID,
            "updated_at": 90,
        }
        cancelled = await service.evaluate(SID, _FakeDB(_ready_rows()), profile="default")
        assert cancelled.reason == "cancelled"
        service.adapter._run_statuses.clear()

        human_rows = _ready_rows() + [_row(6, "user", "先停", timestamp=60)]
        human = await service.evaluate(SID, _FakeDB(human_rows), profile="default")
        assert human.action == "skip"

        steer_rows = _ready_rows() + [_row(7, "user", "插话", kind="steer", timestamp=70)]
        steer = await service.evaluate(SID, _FakeDB(steer_rows), profile="default")
        assert steer.action == "skip"

        service.adapter._run_statuses["run_wait"] = {
            "status": "waiting_for_approval",
            "session_id": SID,
            "updated_at": 95,
        }
        waiting = await service.evaluate(SID, _FakeDB(_ready_rows()), profile="default")
        assert waiting.reason == "approval_pending"
        assert admitted == []
    finally:
        service.stop()


def test_plan_and_clarify_gates_still_block_without_a_prompt():
    plan_rows = [
        _row(1, "user", "查一下", timestamp=10),
        _row(2, "assistant", "", timestamp=20, tool_calls=[
            {"function": {"name": "plan_mode", "arguments": "{\"action\": \"submit\"}"}},
        ]),
        _notice(timestamp=50),
    ]
    plan = decide(_snap(plan_rows), session_id=SID)
    assert plan.action == "block"
    assert plan.reason == "plan_pending"
    clarify = decide(_snap(_ready_rows(), gates=Gates(clarify=True)), session_id=SID)
    assert clarify.reason == "clarify_pending"
    assert "如果" not in CONTINUATION_TEXT


@pytest.mark.asyncio
async def test_new_human_before_admit_still_blocks(tmp_path):
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

    service = _service(tmp_path, admit)
    try:
        decision = await service.evaluate(SID, RaceDB([]), profile="default")
        assert decision.action == "admit"
        assert admitted == []
    finally:
        service.stop()


@pytest.mark.asyncio
async def test_failure_budget_and_draining_backoff_remain(tmp_path):
    """另一个 5 是失败重试预算。busy/draining 退避不得删除。"""
    assert MAX_ATTEMPTS == 5
    assert BACKOFF_S == (2.0, 5.0, 10.0, 20.0, 40.0)
    admitted = []

    async def admit(*_args, **_kwargs):
        admitted.append(1)
        raise ContinuationAdmitError("draining")

    rows = _ready_rows()
    service = _service(tmp_path, admit)
    try:
        service.journal.freeze(SID, "seed-key", ["seed"], {"input": "seed"}, 100.0)
        service.journal.record_attempt(SID, MAX_ATTEMPTS, "draining", 100.0, 100.0, "pending")
        exhausted = await service.evaluate(SID, _FakeDB(rows), profile="default")
        assert exhausted.reason == "attempts_exhausted"
        assert admitted == []
        identity = continuation_identity(SID, rows[-1])
        assert identity in service.journal.receipted([identity])
        exhausted_receipt = service.journal.receipt_record(identity)
        assert exhausted_receipt["reason"] == "attempts_exhausted"
        assert exhausted_receipt["run_id"] is None
    finally:
        service.stop()

    calls = []

    async def drain_admit(*_args, **_kwargs):
        calls.append(1)
        raise ContinuationAdmitError("draining")

    drain_home = tmp_path / "drain"
    drain_home.mkdir()
    service = _service(drain_home, drain_admit)
    try:
        await service.evaluate(SID, _FakeDB(_ready_rows()), profile="default")
        stored = service.journal.load_request(SID)
        assert calls == [1]
        assert stored["attempts"] == 1
        assert stored["last_error"] == "draining"
        assert stored["state"] == "pending"
        assert stored["next_attempt_at"] == 100.0 + BACKOFF_S[0]
        assert service._timers
        drain_identity = continuation_identity(SID, _ready_rows()[-1])
        assert drain_identity not in service.journal.receipted([drain_identity])
        assert service.journal.receipt_record(drain_identity) is None
    finally:
        service.stop()


def test_busy_lease_still_defers():
    decision = decide(_snap(_ready_rows(), lease_expires_at=200.0), session_id=SID)
    assert decision.action == "defer"
    assert decision.reason == "busy"
    assert decision.delay == BACKOFF_S[0]
    assert isinstance(decision, Decision)


def _plant(journal, session_id, identity, at, *, run_id=None, reason=None, event=None):
    journal.add_receipts([identity], session_id, run_id, at, reason=reason)
    if event is not None:
        journal.event(session_id, "blocked", event, at)


def test_null_receipts_are_not_bulk_cleared(tmp_path):
    """只释放能证明的 streak_limit 锁。空 run_id、取消、耗尽、无事件和含糊记录都留着。"""
    import sqlite3

    from gateway.clawd_continuation_journal import ContinuationJournal

    path = tmp_path / "clawd_continuation.db"
    journal = ContinuationJournal(path)
    journal.open()
    try:
        journal.freeze("s-keep-request", "seed-key", ["seed"], {"input": "seed"}, 1.0)
        _plant(journal, "s-streak", "streak-a", 90.0, event="streak_limit")
        _plant(journal, "s-streak", "streak-b", 90.0)
        _plant(journal, "s-streak", "streak-bare", 91.0)
        _plant(journal, "s-cancel", "cancel-null", 80.0, event="cancelled")
        _plant(journal, "s-exhausted", "exhausted-null", 70.0, event="attempts_exhausted")
        _plant(journal, "s-bare", "bare-null", 60.0)
        _plant(journal, "s-ambiguous", "ambiguous-null", 50.0, event="streak_limit")
        journal.event("s-ambiguous", "blocked", "cancelled", 50.0)
        _plant(journal, "s-later", "later-null", 40.0, event="streak_limit")
        journal.event("s-later", "blocked", "cancelled", 41.0)
        _plant(journal, "s-explicit", "explicit-streak", 95.0, reason="streak_limit")
        _plant(journal, "s-explicit-later", "explicit-later", 30.0, reason="streak_limit")
        journal.event("s-explicit-later", "blocked", "attempts_exhausted", 31.0)
        _plant(journal, "s-bound", "bound-streak", 20.0, reason="streak_limit")
        _plant(journal, "s-bound", "bound-cancel", 21.0, reason="cancelled", event="cancelled")
        _plant(journal, "s-admitted", "admitted-row", 10.0, run_id="run_kept", reason="admitted", event="streak_limit")
        _plant(journal, "s-recorded-cancel", "recorded-cancel", 15.0, reason="cancelled", event="streak_limit")
        _plant(journal, "s-other", "other-null", 90.0)

        assert journal.reconcile_legacy_streak_receipts("s-streak") == 2
        assert journal.reconcile_legacy_streak_receipts("s-streak") == 0
        assert journal.receipt_record("streak-a") is None
        assert journal.receipt_record("streak-b") is None
        assert journal.receipt_record("streak-bare")["reason"] is None
        assert journal.reconcile_legacy_streak_receipts("s-cancel") == 0
        assert journal.receipt_record("cancel-null")["run_id"] is None
        assert journal.reconcile_legacy_streak_receipts("s-exhausted") == 0
        assert journal.receipt_record("exhausted-null") is not None
        assert journal.reconcile_legacy_streak_receipts("s-bare") == 0
        assert journal.receipt_record("bare-null") is not None
        assert journal.reconcile_legacy_streak_receipts("s-ambiguous") == 0
        assert journal.receipt_record("ambiguous-null") is not None
        assert journal.reconcile_legacy_streak_receipts("s-later") == 0
        assert journal.receipt_record("later-null") is not None
        assert journal.reconcile_legacy_streak_receipts("s-explicit") == 1
        assert journal.receipt_record("explicit-streak") is None
        assert journal.reconcile_legacy_streak_receipts("s-explicit-later") == 0
        assert journal.receipt_record("explicit-later")["reason"] == "streak_limit"
        assert journal.reconcile_legacy_streak_receipts("s-bound") == 1
        assert journal.receipt_record("bound-streak") is None
        assert journal.receipt_record("bound-cancel")["reason"] == "cancelled"
        assert journal.reconcile_legacy_streak_receipts("s-admitted") == 0
        assert journal.receipt_record("admitted-row")["run_id"] == "run_kept"
        assert journal.reconcile_legacy_streak_receipts("s-recorded-cancel") == 0
        assert journal.receipt_record("recorded-cancel")["reason"] == "cancelled"
        assert journal.reconcile_legacy_streak_receipts("s-other") == 0
        assert journal.receipt_record("other-null") is not None
        assert journal.load_request("s-keep-request")["request_key"] == "seed-key"
        raw = sqlite3.connect(path)
        try:
            details = [row[0] for row in raw.execute("SELECT detail FROM events ORDER BY id")]
        finally:
            raw.close()
        assert "streak_limit" in details
        assert "cancelled" in details
        assert "attempts_exhausted" in details
    finally:
        journal.close()
    assert path.exists()


def test_old_schema_migration_does_not_clear_null_receipts(tmp_path):
    """旧库没有 reason 列时只加列。能归因的次数锁可释放，取消和裸空回执留下。"""
    import sqlite3

    from gateway.clawd_continuation_journal import ContinuationJournal

    path = tmp_path / "clawd_continuation.db"
    raw = sqlite3.connect(path)
    raw.execute(
        "CREATE TABLE receipts ("
        "identity TEXT PRIMARY KEY, session_id TEXT NOT NULL, accepted_at REAL NOT NULL, run_id TEXT)"
    )
    raw.execute(
        "CREATE TABLE events ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, "
        "kind TEXT NOT NULL, detail TEXT NOT NULL, created_at REAL NOT NULL)"
    )
    raw.executemany(
        "INSERT INTO receipts (identity, session_id, accepted_at, run_id) VALUES (?, ?, ?, NULL)",
        (
            ("old-streak", "sess-old", 90.0),
            ("old-cancel", "sess-old", 80.0),
            ("old-bare", "sess-old", 60.0),
        ),
    )
    raw.executemany(
        "INSERT INTO events (session_id, kind, detail, created_at) VALUES (?, 'blocked', ?, ?)",
        (
            ("sess-old", "streak_limit", 90.0),
            ("sess-old", "cancelled", 80.0),
        ),
    )
    raw.commit()
    raw.close()

    journal = ContinuationJournal(path)
    journal.open()
    try:
        assert journal.reconcile_legacy_streak_receipts("sess-old") == 1
        assert journal.receipt_record("old-streak") is None
        assert journal.receipt_record("old-cancel")["reason"] is None
        assert journal.receipt_record("old-cancel")["run_id"] is None
        assert journal.receipt_record("old-bare")["reason"] is None
    finally:
        journal.close()
    assert path.exists()


@pytest.mark.asyncio
async def test_legacy_cancel_receipt_survives_restart(tmp_path):
    """无法按次数锁归因的空 run_id 回执，重启后仍不得 admit。"""
    from gateway.clawd_continuation import ContinuationService

    rows = _ready_rows()
    identity = continuation_identity(SID, rows[-1])
    assert identity
    admitted = []

    async def admit(*_args, **_kwargs):
        admitted.append(1)
        return "run_should_not"

    journal = tmp_path / "clawd_continuation.db"
    first = ContinuationService(
        type("A", (), {"_run_statuses": {}})(),
        journal_path=journal,
        admit=admit,
        now=lambda: 100.0,
    )
    first.open()
    first.journal.add_receipts([identity], SID, None, 90.0)
    first.journal.event(SID, "blocked", "cancelled", 90.0)
    first.journal.watch(SID, 10_000.0)
    first.stop()

    db = _FakeDB(rows)
    adapter = type("A", (), {"_run_statuses": {}, "_session_db": db})()
    second = ContinuationService(adapter, journal_path=journal, admit=admit, now=lambda: 100.0)
    second.open()
    second.running = True
    try:
        await second.recover()
        assert db.reads >= 1
        assert admitted == []
        assert identity in second.journal.receipted([identity])
        record = second.journal.receipt_record(identity)
        assert record["run_id"] is None
        assert record["reason"] is None
    finally:
        second.stop()


@pytest.mark.asyncio
async def test_new_block_and_admit_receipts_record_reason(tmp_path):
    """新回执记下原因。取消原因不会被后来的 reconcile 清掉。"""
    cancelled_calls = []

    async def cancel_admit(*_args, **_kwargs):
        cancelled_calls.append(1)
        return "run_cancel_should_not"

    service = _service(tmp_path, cancel_admit)
    try:
        service.adapter._run_statuses["run_cancel"] = {
            "status": "cancelled",
            "session_id": SID,
            "updated_at": 90,
        }
        decision = await service.evaluate(SID, _FakeDB(_ready_rows()), profile="default")
        assert decision.reason == "cancelled"
        identity = continuation_identity(SID, _ready_rows()[-1])
        record = service.journal.receipt_record(identity)
        assert record["reason"] == "cancelled"
        assert record["run_id"] is None
        assert cancelled_calls == []
        service.journal.event(SID, "blocked", "streak_limit", 10.0)
        again = await service.evaluate(SID, _FakeDB(_ready_rows()), profile="default")
        assert again.action == "skip"
        assert service.journal.receipt_record(identity)["reason"] == "cancelled"
    finally:
        service.stop()

    admitted = []

    async def admit(*_args, **_kwargs):
        admitted.append(1)
        return "run_recorded"

    admit_home = tmp_path / "admit"
    admit_home.mkdir()
    service = _service(admit_home, admit)
    try:
        decision = await service.evaluate(SID, _FakeDB(_ready_rows()), profile="default")
        assert decision.action == "admit"
        identity = continuation_identity(SID, _ready_rows()[-1])
        record = service.journal.receipt_record(identity)
        assert record["reason"] == "admitted"
        assert record["run_id"] == "run_recorded"
        assert admitted == [1]
    finally:
        service.stop()


@pytest.mark.asyncio
async def test_effort_admission_refusal_does_not_clear_receipts_or_self_schedule(tmp_path):
    """无 authority 的 pending 拒绝可观测。不批删空回执，不自激，不恢复次数硬停。"""
    from clawd_server.effort_service import PENDING_KEY

    admitted = []

    async def admit(*_args, **_kwargs):
        admitted.append(1)
        return "run_should_not"

    service = _service(tmp_path, admit)
    session = _session()
    config = dict(session["model_config"])
    config[PENDING_KEY] = "high"
    session["model_config"] = config
    try:
        _plant(service.journal, SID, "cancel-keep", 80.0, reason="cancelled", event="cancelled")
        _plant(service.journal, SID, "bare-keep", 60.0)
        decision = await service.evaluate(SID, _FakeDB(_ready_rows(), session=session), profile="default")
        assert decision.action == "admit"
        assert decision.reason != "streak_limit"
        assert admitted == []
        assert not service._timers
        assert service.journal.receipt_record("cancel-keep")["reason"] == "cancelled"
        assert service.journal.receipt_record("cancel-keep")["run_id"] is None
        bare = service.journal.receipt_record("bare-keep")
        assert bare["reason"] is None
        assert bare["run_id"] is None
        assert service.status(SID)["last_event"]["detail"] == "effort_admission_required"
        assert config[PENDING_KEY] == "high"
    finally:
        service.stop()
