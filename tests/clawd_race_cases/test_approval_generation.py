"""Approval generation expiry. A stale request_id or execution_generation is refused.

``source=internal`` is not a capability on these doors. Extra fields are rejected
by the real authority body check; a late callback still has to pass
``check_approval_generation``. The marked continuation executor, when used, is
not a model round. No skip and no xfail.
"""

from __future__ import annotations

import uuid

import pytest

from gateway.session_authority import LiveSession, SessionAuthority
from gateway.session_contract import Principal, SessionRef
from hermes_state_runtime import (
    RuntimeStoreError, admit_session_input, begin_runtime_epoch, claim_session_input, settle_session_input,
)
from tools.approval import _lock as approval_lock
from tools.approval import list_gateway_approvals
from tools.approval_gateway_wait import _ApprovalEntry

from tests.clawd_race_cases.helpers import (
    MarkedExecutor, add_notice, drain_adapter, forbid_user_turn, isolate, journal_path, make_adapter,
    open_db, open_service, open_store, plain_user_rows, seed_session,
)


def _enqueue(route: str, request_id: str, generation: int) -> _ApprovalEntry:
    data = {
        "request_id": request_id, "command": "rm -rf build", "description": "delete build",
        "allow_session": False, "allow_permanent": False,
    }
    entry = _ApprovalEntry(data)
    entry.data["request_id"] = request_id
    from tools import approval as approval_mod

    with approval_lock:
        approval_mod._gateway_queues.setdefault(route, []).append(entry)
    return entry


def _drop_queue(route: str) -> None:
    from tools import approval as approval_mod

    with approval_lock:
        approval_mod._gateway_queues.pop(route, None)


def _running(tmp_path, monkeypatch):
    home = isolate(monkeypatch, tmp_path / "approval-home")
    db = open_db(home)
    sid = "approve-" + uuid.uuid4().hex[:8]
    db.create_session(sid, source="api_server", model="grok-4.7", profile_name="owned")
    epoch = begin_runtime_epoch(db, instance_id="race")
    authority = SessionAuthority(
        type("Runner", (), {"_draining": False})(), profile_id="owned", instance_id="race", db=db, epoch=epoch,
    )
    route = "race-route-" + uuid.uuid4().hex[:8]
    live = LiveSession(None, route)
    authority.sessions[sid] = live
    run_id = "run_" + uuid.uuid4().hex[:8]
    admit_session_input(
        db, epoch=epoch, principal_id="api", session_id=sid, request_id=run_id, payload={"text": "owned work"},
    )
    claimed = claim_session_input(db, epoch=epoch, session_id=sid)
    assert claimed and claimed["status"] == "started" and type(claimed["generation"]) is int
    generation = claimed["generation"]
    request_id = "apr_" + uuid.uuid4().hex[:8]
    entry = _enqueue(route, request_id, generation)
    live.controls.pending[request_id] = (route, {
        "kind": "approval", "prompt_id": request_id, "execution_generation": generation,
        "command": "rm -rf build", "description": "delete build", "choices": ["once", "deny"],
    })
    live.event_stream.execution = {"admission_id": claimed["admission_id"]}
    actor = Principal("api", "owned", frozenset({"session:read", "session:approve", "session:respond"}), "race-transport")
    return {
        "db": db, "sid": sid, "epoch": epoch, "authority": authority, "route": route, "run_id": run_id,
        "generation": generation, "request_id": request_id, "entry": entry, "actor": actor,
        "admission_id": claimed["admission_id"],
    }


def _adapter(env):
    class Adapter:
        gateway_runner = type("Runner", (), {"_draining": False, "session_authority": env["authority"]})()

    return Adapter()


@pytest.mark.asyncio
async def test_stale_request_and_generation_rejected_internal_source_cannot_bypass(tmp_path, monkeypatch):
    env = _running(tmp_path, monkeypatch)
    from gateway.platforms.api_server_authority_runs import respond_run
    from gateway.session_api_turn import publish_api_tool_event
    from gateway.session_controls import AuthorityConnection

    adapter = _adapter(env)
    authority, sid, generation, request_id = env["authority"], env["sid"], env["generation"], env["request_id"]
    route, entry = env["route"], env["entry"]
    seen: list[str] = []
    authority.api_observers = {env["admission_id"]: [{
        "tool_start_callback": lambda *args, **kwargs: seen.append("delivered"),
    }]}
    try:
        publish_api_tool_event(
            authority, sid, generation, "tool.start", "c-live", "terminal", {"source": "internal"},
        )
        assert seen == ["delivered"]

        with pytest.raises(RuntimeStoreError) as extra:
            await respond_run(adapter, env["run_id"], {
                "request_id": request_id, "execution_generation": generation, "choice": "once",
                "source": "internal",
            }, kind="approval")
        assert extra.value.reason == "invalid_params"
        assert entry.result is None
        assert list_gateway_approvals(route)

        viewer = AuthorityConnection(authority, object(), {"user_id": "owner"})
        dispatched = await viewer.dispatch({
            "id": 1, "method": "approval.respond",
            "params": {
                "session_id": sid, "execution_generation": generation, "prompt_id": request_id,
                "choice": "once", "source": "internal",
            },
        })
        assert dispatched["error"]["message"] == "invalid_params"
        assert entry.result is None

        with pytest.raises(RuntimeStoreError) as missing:
            await respond_run(adapter, env["run_id"], {"request_id": request_id, "choice": "once"}, kind="approval")
        assert missing.value.reason == "invalid_params"

        with pytest.raises(RuntimeStoreError) as stale:
            await respond_run(adapter, env["run_id"], {
                "request_id": request_id, "execution_generation": generation + 1, "choice": "once",
            }, kind="approval")
        assert stale.value.reason == "stale_generation"
        assert entry.result is None

        internal_actor = Principal(
            "internal", "owned", frozenset({"session:read", "session:approve", "session:respond"}), "internal-transport",
        )
        snapshot = await authority.attach(internal_actor, SessionRef("owned", sid), history=False)
        try:
            with pytest.raises(RuntimeStoreError) as attached:
                await authority.respond(
                    internal_actor, SessionRef("owned", sid), generation + 1, request_id, {"choice": "once"},
                )
            assert attached.value.reason == "stale_generation"
        finally:
            await authority.detach(internal_actor, snapshot.subscription_id)
        assert entry.result is None

        with pytest.raises(RuntimeStoreError) as wrong_id:
            await respond_run(adapter, env["run_id"], {
                "request_id": "other-request", "execution_generation": generation, "choice": "once",
            }, kind="approval")
        assert wrong_id.value.reason == "approval_not_pending"
        assert entry.result is None
        assert [item["request_id"] for item in list_gateway_approvals(route)] == [request_id]

        settle_session_input(
            env["db"], epoch=env["epoch"], admission_id=env["admission_id"], generation=generation,
            outcome="completed",
        )
        with pytest.raises(RuntimeStoreError) as expired:
            await respond_run(adapter, env["run_id"], {
                "request_id": request_id, "execution_generation": generation, "choice": "once",
            }, kind="approval")
        assert expired.value.reason == "stale_generation"
        publish_api_tool_event(
            authority, sid, generation, "tool.start", "c-late", "terminal", {"source": "internal"},
        )
        assert seen == ["delivered"]
        assert entry.result is None
        with pytest.raises(RuntimeStoreError) as check:
            authority.check_approval_generation(sid, generation)
        assert check.value.reason == "stale_generation"
    finally:
        _drop_queue(route)
        env["db"].close()


@pytest.mark.asyncio
async def test_exact_generation_resolves_once_and_does_not_start_a_turn(tmp_path, monkeypatch):
    env = _running(tmp_path, monkeypatch)
    from gateway.platforms.api_server_authority_runs import respond_run

    before = len(env["db"].get_messages(env["sid"]))
    try:
        resolved = await respond_run(_adapter(env), env["run_id"], {
            "request_id": env["request_id"], "execution_generation": env["generation"], "choice": "once",
        }, kind="approval")
        assert resolved["status"] == "resolved"
        assert resolved["prompt_id"] == env["request_id"]
        assert env["entry"].result == "once"
        assert list_gateway_approvals(env["route"]) == []
        with pytest.raises(RuntimeStoreError) as again:
            await respond_run(_adapter(env), env["run_id"], {
                "request_id": env["request_id"], "execution_generation": env["generation"], "choice": "once",
            }, kind="approval")
        assert again.value.reason == "approval_not_pending"
        assert env["entry"].result == "once"
        assert len(env["db"].get_messages(env["sid"])) == before
    finally:
        _drop_queue(env["route"])
        env["db"].close()


@pytest.mark.asyncio
async def test_waiting_approval_blocks_continuation_even_if_event_source_is_internal(tmp_path, monkeypatch):
    """A completion tagged source=internal still hits the approval gate and does not self-POST."""
    leaked = forbid_user_turn(monkeypatch)
    home = isolate(monkeypatch, tmp_path / "gate-home")
    db = open_db(home)
    sid = "approval-gate"
    seed_session(db, sid)
    add_notice(db, sid, "deleg_internal_gate")
    executor = MarkedExecutor()
    adapter = make_adapter(db, open_store(home / "runs_idempotency.db"), executor, profile="default")
    adapter._run_statuses["run_wait"] = {
        "status": "waiting_for_approval", "session_id": sid, "updated_at": 10**12,
        "approval": {"request_id": "apr_pending", "source": "internal"},
    }
    service = open_service(adapter, journal_path(db))
    before = len(plain_user_rows(db, sid))
    try:
        decision = await service.evaluate(sid, db, profile="default")
        await drain_adapter(adapter)
        assert decision.reason == "approval_pending"
        assert executor.calls == []
        assert leaked == []
        assert len(plain_user_rows(db, sid)) == before
        assert service.status(sid)["last_event"]["detail"] == "approval_pending"
        assert service.status(sid)["last_event"]["kind"] != "admitted"
    finally:
        service.stop()
        db.close()
