"""Real SessionDB / journal / admit seams for race cases.

The marked executor is the external model boundary only. It is not a model
round. Scheduling barriers and injected clocks are marked at the call site.
Core ``decide`` / journal / idempotency results are not stubbed.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from pathlib import Path

from gateway.clawd_continuation_admit import TEST_EXECUTOR_MARKER, admit_continuation
from gateway.clawd_continuation_decision import CONTINUATION_TEXT, DISPLAY_KIND

SECRET = "SECRET-PROMPT-DO-NOT-LEAK"
PRODUCTION_HOME = Path("/root/.hermes")
OFFICIAL_INSTALL = Path("/usr/local/lib/hermes-agent")
ROUTE_MODEL = "grok-4.7"
ROUTE_PROVIDER = "sub2api"


class Clock:
    """Injected time boundary. Not a substitute for a decision."""

    def __init__(self, start: float | None = None) -> None:
        self.value = time.time() if start is None else float(start)

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class MarkedExecutor:
    """External LLM stand-in. ``test-not-llm`` is required by admit and is not a provider round."""

    clawd_executor_marker = TEST_EXECUTOR_MARKER

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return {"final_response": "test-executor-not-llm"}, {"continuation_executor": TEST_EXECUTOR_MARKER}


class Crash(RuntimeError):
    """Fault injected by the test after a real journal or admit call. Not a product error class."""


def reject_production(path: Path) -> Path:
    resolved = path.resolve()
    forbidden = (PRODUCTION_HOME.resolve(), OFFICIAL_INSTALL.resolve())
    for root in forbidden:
        if resolved == root or root in resolved.parents:
            raise RuntimeError(f"refusing production path {resolved}")
    return resolved


def isolate(monkeypatch, home: Path) -> Path:
    home = reject_production(home)
    home.mkdir(parents=True, exist_ok=True)
    tmp = home / "tmp"
    tmp.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CLAWD_HOME", str(home))
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("TMPDIR", str(tmp))
    monkeypatch.delenv("API_SERVER_KEY", raising=False)
    assert not str(home).startswith(str(PRODUCTION_HOME))
    return home


def forbid_user_turn(monkeypatch) -> list:
    """Tripwire: a blocked continuation must not become an ordinary user self-POST or internal wake."""
    calls: list[str] = []

    async def _refuse(name, *_args, **_kwargs):
        calls.append(name)
        raise AssertionError(f"continuation must not {name}")

    monkeypatch.setattr(
        "gateway.wake._self_post_chat_completion",
        lambda *a, **k: _refuse("self-POST a user turn", *a, **k),
    )
    monkeypatch.setattr(
        "gateway.wake.admit_internal_event",
        lambda *a, **k: _refuse("admit an internal user event", *a, **k),
    )
    return calls


def open_db(home: Path, name: str = "state.db"):
    from hermes_state import SessionDB

    path = reject_production(home / name)
    return SessionDB(db_path=path)


def seed_session(db, session_id: str, *, profile: str = "default") -> None:
    db.create_session(
        session_id, source="api_server", model=ROUTE_MODEL, profile_name=profile, cwd="/work/proj-a",
    )
    db.update_session_runtime_lock(
        session_id, model=ROUTE_MODEL, provider=ROUTE_PROVIDER,
        model_options={"reasoning_effort": "high"}, confirmed=True,
    )
    db.update_session_cwd(session_id, "/work/proj-a")
    db.append_message(session_id, "user", content=f"请处理 {SECRET}", timestamp=10)
    db.append_message(session_id, "assistant", content="已派出", timestamp=20)
    stored = db.get_session(session_id)
    assert stored["profile_name"] == profile
    assert stored["source"] == "api_server"


def add_notice(db, session_id: str, delegation_id: str) -> None:
    db.append_delegation_delivery(
        session_id,
        f"[ASYNC DELEGATION BATCH COMPLETE — {delegation_id}]\n结果",
        {"delegation_id": delegation_id, "task_count": 1, "completed_count": 1, "failed_count": 0},
    )


def make_adapter(db, store, executor: MarkedExecutor, *, profile: str):
    from gateway.platforms.api_server_runs import _set_run_status

    class Adapter:
        def __init__(self) -> None:
            self._session_db = db
            self._run_statuses: dict = {}
            self._run_idempotency_ids: set = set()
            self._run_idempotency_store = store
            self._run_owner_pid = os.getpid()
            self._run_owner_started = 1
            self._active_run_tasks: dict = {}
            self.clawd_continuation_executor = executor
            self.clawd_profile = profile
            self.calls = executor.calls

        def _ensure_session_db(self):
            return self._session_db

        def _set_run_status(self, run_id, status, **fields):
            return _set_run_status(self, run_id, status, **fields)

        def _concurrency_limited_response(self):
            return None

        def _draining_response(self):
            return None

        def _track_background_task(self, task):
            return None

        async def _run_agent(self, **_kwargs):
            raise AssertionError("continuation reached _run_agent; marked executor was required")

        async def run_internal_session_turn(self, **_kwargs):
            raise AssertionError("continuation must not run an internal user turn")

    return Adapter()


def open_store(path: Path):
    from gateway.platforms.api_server_run_idempotency import RunIdempotencyStore

    return RunIdempotencyStore(str(reject_production(path)))


def open_service(adapter, journal: Path, *, admit=None, now=None):
    from gateway.clawd_continuation import ContinuationService

    service = ContinuationService(
        adapter, journal_path=reject_production(journal), admit=admit or admit_continuation, now=now or time.time,
    )
    service.open()
    service.running = True
    return service


def journal_path(db) -> Path:
    return Path(db.db_path).parent / "clawd_continuation.db"


def messages(db, session_id: str) -> list[dict]:
    return db.get_messages(session_id, limit=300, latest=True)


def plain_user_rows(db, session_id: str) -> list[dict]:
    return [row for row in messages(db, session_id) if row.get("role") == "user" and not row.get("display_kind")]


def continuation_rows(db, session_id: str) -> list[dict]:
    return [row for row in messages(db, session_id) if row.get("display_kind") == DISPLAY_KIND]


def assert_not_model_round(executor: MarkedExecutor, call: dict) -> None:
    assert call["requested_model"] == ROUTE_MODEL
    assert call["requested_provider"] == ROUTE_PROVIDER
    assert call["user_message"] == CONTINUATION_TEXT
    assert SECRET not in call["user_message"]
    assert executor.clawd_executor_marker == TEST_EXECUTOR_MARKER
    assert call.get("final_response", "test-executor-not-llm") != "provider-round"


async def drain_adapter(adapter, *, turns: int = 50) -> None:
    """Yield to already-scheduled tasks. A missed schedule fails; this is not a timed race."""
    for _ in range(turns):
        pending = [task for task in adapter._active_run_tasks.values() if not task.done()]
        if not pending:
            return
        await asyncio.sleep(0)
        if all(task.done() for task in pending):
            return
    leftover = [task for task in adapter._active_run_tasks.values() if not task.done()]
    raise AssertionError(f"continuation run still scheduled: {len(leftover)}")


def install_claim_barrier(services, *, timeout: float = 3):
    """Scheduling boundary at the real claim. The first wave must all arrive; later claims do not wait."""
    barrier = threading.Barrier(len(services))
    state = {"open": True}
    lock = threading.Lock()

    def wrap(service):
        real = service.journal.claim

        def claim(*args, **kwargs):
            with lock:
                should_wait = state["open"]
            if should_wait:
                barrier.wait(timeout)
                with lock:
                    state["open"] = False
            return real(*args, **kwargs)

        service.journal.claim = claim

    for service in services:
        wrap(service)
    return barrier


def run_overlapping(runners, *, join_timeout: float = 8) -> list[dict]:
    """Start consumers together. ``join_timeout`` is a deadlock failure bound, not a pass window."""
    boxes: list[dict] = []
    threads = []

    def _guard(run, box: dict) -> None:
        try:
            box["result"] = run()
        except BaseException as exc:  # noqa: BLE001 - the parent assertion must see the consumer failure
            box["error"] = exc

    for run in runners:
        box: dict = {}
        thread = threading.Thread(target=_guard, args=(run, box))
        thread.start()
        threads.append(thread)
        boxes.append(box)
    for thread in threads:
        thread.join(join_timeout)
        assert not thread.is_alive(), "consumer did not finish; claim barrier or store lock stuck"
    errors = [box["error"] for box in boxes if "error" in box]
    assert not errors, errors
    return boxes


def thread_call(coro_factory):
    """Run one consumer on its own loop. The factory closes over that consumer's db and service."""

    def _run():
        return asyncio.run(coro_factory())

    return _run
