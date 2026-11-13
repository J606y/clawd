"""Unit tests for the CDP supervisor idle stop (live-browser point 3).

No real browser: supervisors are fakes injected into SUPERVISOR_REGISTRY,
except for the ``_cdp`` activity-tracking test, which drives a real
``CDPSupervisor`` against a stub WebSocket.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from tools.browser_supervisor import (
    CDPSupervisor,
    SUPERVISOR_REGISTRY,
    _orphan_sweep_once,
    schedule_supervisor_stop,
)


class _FakeSupervisor:
    """Stand-in with the two methods the idle stop uses."""

    def __init__(self, idle: float = 999.0) -> None:
        self._idle = idle
        self.stopped = False
        self.stop_calls = 0

    def idle_seconds(self) -> float:
        return self._idle

    def stop(self) -> None:
        self.stopped = True
        self.stop_calls += 1


@pytest.fixture
def registry_clean():
    """Snapshot + restore the process-global registry around each test."""
    with SUPERVISOR_REGISTRY._lock:
        saved_tasks = dict(SUPERVISOR_REGISTRY._by_task)
        saved_homes = dict(SUPERVISOR_REGISTRY._home_by_task)
    SUPERVISOR_REGISTRY._by_task.clear()
    SUPERVISOR_REGISTRY._home_by_task.clear()
    yield
    with SUPERVISOR_REGISTRY._lock:
        SUPERVISOR_REGISTRY._by_task.clear()
        SUPERVISOR_REGISTRY._by_task.update(saved_tasks)
        SUPERVISOR_REGISTRY._home_by_task.clear()
        SUPERVISOR_REGISTRY._home_by_task.update(saved_homes)


@pytest.fixture
def gate_free(monkeypatch):
    monkeypatch.setattr("tools.browser_supervisor._exec_gate_held", lambda: False)


def _inject(task_id: str, fake: _FakeSupervisor) -> None:
    with SUPERVISOR_REGISTRY._lock:
        SUPERVISOR_REGISTRY._by_task[task_id] = fake


def test_idle_seconds_starts_near_zero():
    sup = CDPSupervisor(task_id="t", cdp_url="ws://127.0.0.1:1/x")
    assert 0.0 <= sup.idle_seconds() < 1.0
    sup._last_used_mono -= 30.0
    assert sup.idle_seconds() >= 30.0


class _FakeWS:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, data: str) -> None:
        self.sent.append(data)


def test_cdp_send_marks_activity():
    sup = CDPSupervisor(task_id="t", cdp_url="ws://127.0.0.1:1/x")
    sup._ws = _FakeWS()
    sup._last_used_mono -= 60.0
    assert sup.idle_seconds() >= 60.0

    async def _drive() -> dict:
        task = asyncio.create_task(sup._cdp("Browser.getVersion", {}))
        for _ in range(200):
            if sup._pending_calls:
                break
            await asyncio.sleep(0.005)
        assert sup._pending_calls, "call was never sent"
        call_id, fut = next(iter(sup._pending_calls.items()))
        fut.set_result({"id": call_id, "result": {"product": "fake"}})
        return await task

    result = asyncio.run(_drive())
    assert result == {"id": 1, "result": {"product": "fake"}}
    assert sup._ws.sent, "nothing was sent on the socket"
    assert sup.idle_seconds() < 1.0


def test_stop_if_same_only_stops_same_object(registry_clean):
    a = _FakeSupervisor()
    b = _FakeSupervisor()
    _inject("task-a", a)
    assert SUPERVISOR_REGISTRY.stop_if_same("task-a", b) is False
    assert SUPERVISOR_REGISTRY.get("task-a") is a
    assert SUPERVISOR_REGISTRY.stop_if_same("task-a", a) is True
    assert SUPERVISOR_REGISTRY.get("task-a") is None


def test_deferred_stop_fires_when_idle(registry_clean, gate_free):
    fake = _FakeSupervisor(idle=999.0)
    _inject("task-idle", fake)
    assert schedule_supervisor_stop("task-idle", delay_s=0.05) is True
    time.sleep(0.3)
    assert fake.stopped
    assert SUPERVISOR_REGISTRY.get("task-idle") is None


def test_deferred_stop_skips_recently_used(registry_clean, gate_free):
    fake = _FakeSupervisor(idle=0.0)
    _inject("task-busy", fake)
    assert schedule_supervisor_stop("task-busy", delay_s=0.05) is True
    time.sleep(0.3)
    assert not fake.stopped
    assert SUPERVISOR_REGISTRY.get("task-busy") is fake


def test_deferred_stop_skips_when_gate_held(registry_clean, monkeypatch):
    monkeypatch.setattr("tools.browser_supervisor._exec_gate_held", lambda: True)
    fake = _FakeSupervisor(idle=999.0)
    _inject("task-gate", fake)
    assert schedule_supervisor_stop("task-gate", delay_s=0.05) is True
    time.sleep(0.3)
    assert not fake.stopped
    assert SUPERVISOR_REGISTRY.get("task-gate") is fake


def test_deferred_stop_skips_replaced_supervisor(registry_clean, gate_free):
    old = _FakeSupervisor(idle=999.0)
    new = _FakeSupervisor(idle=999.0)
    _inject("task-replaced", old)
    assert schedule_supervisor_stop("task-replaced", delay_s=0.2) is True
    _inject("task-replaced", new)
    time.sleep(0.4)
    assert not old.stopped
    assert not new.stopped
    assert SUPERVISOR_REGISTRY.get("task-replaced") is new


def test_schedule_returns_false_without_supervisor(registry_clean):
    assert schedule_supervisor_stop("task-none", delay_s=0.05) is False


def test_schedule_replaces_pending_timer(registry_clean, gate_free):
    fake = _FakeSupervisor(idle=999.0)
    _inject("task-rearm", fake)
    assert schedule_supervisor_stop("task-rearm", delay_s=5.0) is True
    assert schedule_supervisor_stop("task-rearm", delay_s=0.05) is True
    time.sleep(0.3)
    assert fake.stop_calls == 1


def test_orphan_sweep_stops_long_idle(registry_clean, gate_free):
    stale = _FakeSupervisor(idle=999.0)
    fresh = _FakeSupervisor(idle=5.0)
    _inject("task-stale", stale)
    _inject("task-fresh", fresh)
    _orphan_sweep_once()
    assert stale.stopped
    assert SUPERVISOR_REGISTRY.get("task-stale") is None
    assert not fresh.stopped
    assert SUPERVISOR_REGISTRY.get("task-fresh") is fresh


def test_orphan_sweep_skips_when_gate_held(registry_clean, monkeypatch):
    monkeypatch.setattr("tools.browser_supervisor._exec_gate_held", lambda: True)
    stale = _FakeSupervisor(idle=999.0)
    _inject("task-stale-gate", stale)
    _orphan_sweep_once()
    assert not stale.stopped
