"""Concurrent continuation wakes. Barriers schedule the real claim; they do not decide.

A marked executor is the external model boundary. ``started`` is not a model round.
Failure must leave no ordinary user self-POST. No skip and no xfail.
"""

from __future__ import annotations

import threading

import pytest

from gateway.clawd_continuation_admit import admit_continuation
from gateway.clawd_continuation_decision import CONTINUATION_TEXT

from tests.clawd_race_cases.helpers import (
    SECRET, Clock, MarkedExecutor, add_notice, continuation_rows, drain_adapter, forbid_user_turn,
    install_claim_barrier, isolate, journal_path, make_adapter, messages, open_db, open_service,
    open_store, plain_user_rows, run_overlapping, seed_session, thread_call,
)


def _pair(home, session_id: str, *, profile: str = "default"):
    """Two connections to one session store, one journal file, and one idempotency file."""
    db_a = open_db(home)
    seed_session(db_a, session_id, profile=profile)
    add_notice(db_a, session_id, "deleg_race")
    db_a.close()
    store_path = home / "runs_idempotency.db"
    journal = home / "clawd_continuation.db"
    built = []
    for _ in range(2):
        db = open_db(home)
        executor = MarkedExecutor()
        adapter = make_adapter(db, open_store(store_path), executor, profile=profile)
        service = open_service(adapter, journal, now=Clock())
        built.append((db, executor, adapter, service))
    return built


def _calls(built) -> list[dict]:
    return [call for _db, executor, _adapter, _service in built for call in executor.calls]


@pytest.mark.asyncio
async def test_duplicate_completion_events_admit_at_most_once(tmp_path, monkeypatch):
    leaked = forbid_user_turn(monkeypatch)
    home = isolate(monkeypatch, tmp_path / "duplicate")
    sid = "duplicate-session"
    built = _pair(home, sid)
    install_claim_barrier([item[3] for item in built])
    try:
        def evaluate(item):
            db, _executor, adapter, service = item

            async def _go():
                decision = await service.evaluate(sid, db, profile="default")
                await drain_adapter(adapter)
                return decision

            return thread_call(_go)

        run_overlapping([evaluate(item) for item in built])
        calls = _calls(built)
        assert len(calls) == 1
        assert calls[0]["user_message"] == CONTINUATION_TEXT
        assert SECRET not in calls[0]["user_message"]
        assert calls[0]["requested_model"] == "grok-4.7"
        assert leaked == []
        db = built[0][0]
        assert len(plain_user_rows(db, sid)) == 1
        assert len(continuation_rows(db, sid)) == 1
        replay = await built[0][3].evaluate(sid, db, profile="default")
        await drain_adapter(built[0][2])
        assert replay.action == "skip"
        assert len(_calls(built)) == 1
        assert built[0][3].status(sid)["last_event"]["kind"] == "admitted"
    finally:
        for db, _executor, _adapter, service in built:
            service.stop()
            db.close()


def test_two_consumers_recover_and_evaluate_admit_once(tmp_path, monkeypatch):
    """A restart recover and a live evaluate share the journal and must not start two turns."""
    leaked = forbid_user_turn(monkeypatch)
    home = isolate(monkeypatch, tmp_path / "two-consumers")
    sid = "two-consumers"
    built = _pair(home, sid)
    recover_service = built[0][3]
    live_service = built[1][3]
    install_claim_barrier([recover_service, live_service])
    try:
        async def _recover():
            await recover_service.recover()
            await drain_adapter(built[0][2])

        async def _live():
            decision = await live_service.evaluate(sid, built[1][0], profile="default")
            await drain_adapter(built[1][2])
            return decision

        run_overlapping([thread_call(_recover), thread_call(_live)])
        calls = _calls(built)
        assert len(calls) == 1
        assert calls[0]["session_id"] == sid
        assert calls[0]["cwd"] == "/work/proj-a"
        assert SECRET not in calls[0]["user_message"]
        assert leaked == []
        assert len(continuation_rows(built[0][0], sid)) == 1
        identity = f"clawd-continue-{sid}-deleg_race"
        assert identity in recover_service.journal.receipted([identity]) or identity in live_service.journal.receipted([identity])
        names = {thread.name for thread in threading.enumerate()}
        assert "auto-continue" not in names
    finally:
        for db, _executor, _adapter, service in built:
            service.stop()
            db.close()


@pytest.mark.asyncio
async def test_cancel_and_human_in_the_recheck_window_do_not_admit(tmp_path, monkeypatch):
    """Both arrive after the first decision and before admit. The returned decision is the first snapshot."""
    leaked = forbid_user_turn(monkeypatch)
    home = isolate(monkeypatch, tmp_path / "window")
    db = open_db(home)
    sid = "window-session"
    seed_session(db, sid)
    add_notice(db, sid, "deleg_window")
    executor = MarkedExecutor()
    adapter = make_adapter(db, open_store(home / "runs_idempotency.db"), executor, profile="default")
    service = open_service(adapter, journal_path(db))
    real_claim = service.journal.claim
    arrived = {"human": False, "cancel": False}

    def claim(*args, **kwargs):
        ok = real_claim(*args, **kwargs)
        if ok:
            db.append_message(sid, "user", content="先停一下", display_kind="steer", timestamp=10**9)
            adapter._run_statuses["intruder"] = {
                "status": "cancelled", "session_id": sid, "updated_at": 10**12,
            }
            arrived["human"] = True
            arrived["cancel"] = True
        return ok

    service.journal.claim = claim
    before = len(plain_user_rows(db, sid))
    try:
        first = await service.evaluate(sid, db, profile="default")
        await drain_adapter(adapter)
        assert first.action == "admit"
        assert arrived == {"human": True, "cancel": True}
        assert executor.calls == []
        assert leaked == []
        assert len(plain_user_rows(db, sid)) == before
        assert continuation_rows(db, sid) == []
        event = service.status(sid)["last_event"]
        assert event is None or event["kind"] != "admitted"
        service.journal.claim = real_claim
        second = await service.evaluate(sid, db, profile="default")
        await drain_adapter(adapter)
        assert second.action != "admit"
        assert executor.calls == []
    finally:
        service.stop()
        db.close()


@pytest.mark.asyncio
async def test_cancel_arriving_in_the_recheck_window_blocks(tmp_path, monkeypatch):
    leaked = forbid_user_turn(monkeypatch)
    home = isolate(monkeypatch, tmp_path / "cancel-window")
    db = open_db(home)
    sid = "cancel-window"
    seed_session(db, sid)
    add_notice(db, sid, "deleg_cancel_window")
    executor = MarkedExecutor()
    adapter = make_adapter(db, open_store(home / "runs_idempotency.db"), executor, profile="default")
    service = open_service(adapter, journal_path(db))
    real_claim = service.journal.claim

    def claim(*args, **kwargs):
        ok = real_claim(*args, **kwargs)
        if ok:
            adapter._run_statuses["intruder"] = {
                "status": "cancelled", "session_id": sid, "updated_at": 10**12,
            }
        return ok

    service.journal.claim = claim
    try:
        await service.evaluate(sid, db, profile="default")
        await drain_adapter(adapter)
        assert executor.calls == []
        assert leaked == []
        assert continuation_rows(db, sid) == []
        assert service.status(sid)["last_event"]["kind"] == "blocked"
        assert service.status(sid)["last_event"]["detail"] == "cancelled"
        identity = f"clawd-continue-{sid}-deleg_cancel_window"
        assert identity in service.journal.receipted([identity])
    finally:
        service.stop()
        db.close()


@pytest.mark.asyncio
async def test_cross_profile_isolation(tmp_path, monkeypatch):
    leaked = forbid_user_turn(monkeypatch)
    alpha_home = isolate(monkeypatch, tmp_path / "alpha")
    beta_home = isolate(monkeypatch, tmp_path / "beta")
    sid = "shared-id"
    alpha_db = open_db(alpha_home)
    seed_session(alpha_db, sid, profile="alpha")
    add_notice(alpha_db, sid, "deleg_alpha")
    beta_db = open_db(beta_home)
    seed_session(beta_db, sid, profile="beta")
    alpha_exec, beta_exec = MarkedExecutor(), MarkedExecutor()
    alpha = make_adapter(alpha_db, open_store(alpha_home / "runs_idempotency.db"), alpha_exec, profile="alpha")
    beta = make_adapter(beta_db, open_store(beta_home / "runs_idempotency.db"), beta_exec, profile="beta")
    alpha_service = open_service(alpha, journal_path(alpha_db))
    beta_service = open_service(beta, journal_path(beta_db))
    try:
        assert journal_path(alpha_db) != journal_path(beta_db)
        admitted = await alpha_service.evaluate(sid, alpha_db, profile="alpha")
        await drain_adapter(alpha)
        assert admitted.action == "admit"
        assert len(alpha_exec.calls) == 1
        assert SECRET not in alpha_exec.calls[0]["user_message"]
        skipped = await beta_service.evaluate(sid, beta_db, profile="beta")
        assert skipped.action == "skip"
        assert beta_exec.calls == []
        blocked = await beta_service.evaluate(sid, alpha_db, profile="beta")
        await drain_adapter(alpha)
        await drain_adapter(beta)
        assert blocked.reason == "ownership_unconfirmed"
        assert len(alpha_exec.calls) == 1
        assert beta_exec.calls == []
        assert leaked == []
        identity = f"clawd-continue-{sid}-deleg_alpha"
        assert identity in alpha_service.journal.receipted([identity])
        assert identity not in beta_service.journal.receipted([identity])
        from gateway.clawd_continuation_admit import _scope

        assert _scope("alpha") != _scope("beta")
        stored = alpha._run_idempotency_store._conn.execute(
            "SELECT scope, idempotency_key, fingerprint FROM run_idempotency",
        ).fetchone()
        assert stored is not None and stored[0] == _scope("alpha")
        assert beta._run_idempotency_store.lookup(_scope("beta"), stored[1], stored[2])[0] == "missing"
        assert not any(row.get("display_kind") == "async_delegation_complete" for row in messages(beta_db, sid))
    finally:
        alpha_service.stop()
        beta_service.stop()
        alpha_db.close()
        beta_db.close()
