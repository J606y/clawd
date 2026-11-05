"""Same session id across profiles, and concurrent scopes that ignore process env.

Not executed in this change. Uses temp homes and the real resolver / agent init.
Does not mock project resolution and does not read production credentials.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

from tests.clawd_memory_scope_cases.support import (
    close_agent,
    drop_home,
    joined,
    make_agent,
    project_memory_entries,
    seed_bound_project,
    use_home,
)

SID = "same-sid"
ALPHA = "ALPHA-ONLY-FACT"
BETA = "BETA-ONLY-FACT"
DECOY = "DECOY-ONLY-FACT"


def _bind(home: Path, name: str, fact: str) -> str:
    return seed_bound_project(home, name, SID, fact)


def test_same_session_id_does_not_cross_profiles(tmp_path, monkeypatch):
    home_a = tmp_path / "profile-a"
    home_b = tmp_path / "profile-b"
    decoy = tmp_path / "decoy-env"
    pid_a = _bind(home_a, "Alpha", ALPHA)
    pid_b = _bind(home_b, "Beta", BETA)
    pid_decoy = _bind(decoy, "Decoy", DECOY)
    monkeypatch.setenv("HERMES_HOME", str(decoy))
    monkeypatch.setenv("CLAWD_HOME", str(decoy))

    from clawd_projects.service import get_project_memory_dir, resolve_session_project

    assert resolve_session_project(SID, hermes_home=home_a).id == pid_a
    assert resolve_session_project(SID, hermes_home=home_b).id == pid_b
    assert ALPHA in project_memory_entries(home_a, pid_a)
    assert BETA not in joined(project_memory_entries(home_a, pid_a))
    assert DECOY not in joined(project_memory_entries(home_a, pid_a))
    assert BETA in project_memory_entries(home_b, pid_b)
    assert ALPHA not in joined(project_memory_entries(home_b, pid_b))
    assert get_project_memory_dir(pid_a, hermes_home=home_a) != get_project_memory_dir(
        pid_b, hermes_home=home_b
    )
    # No override: process env is the decoy, so the unscoped call must not see A or B.
    assert resolve_session_project(SID).id == pid_decoy

    token = use_home(home_a)
    try:
        agent = make_agent(home_a, SID)
        try:
            text = agent._memory_store.format_for_system_prompt("memory") or ""
            assert agent.project_id == pid_a
            assert ALPHA in text
            assert BETA not in text and DECOY not in text
        finally:
            close_agent(agent)
    finally:
        drop_home(token)

    token = use_home(home_b)
    try:
        agent = make_agent(home_b, SID)
        try:
            text = agent._memory_store.format_for_system_prompt("memory") or ""
            assert agent.project_id == pid_b
            assert BETA in text
            assert ALPHA not in text and DECOY not in text
        finally:
            close_agent(agent)
    finally:
        drop_home(token)


def test_concurrent_scopes_ignore_process_env(tmp_path, monkeypatch):
    home_a = tmp_path / "profile-a"
    home_b = tmp_path / "profile-b"
    decoy = tmp_path / "decoy-env"
    pid_a = _bind(home_a, "Alpha", ALPHA)
    pid_b = _bind(home_b, "Beta", BETA)
    _bind(decoy, "Decoy", DECOY)
    monkeypatch.setenv("HERMES_HOME", str(decoy))
    monkeypatch.setenv("CLAWD_HOME", str(decoy))

    barrier = threading.Barrier(2)
    errors: dict[str, BaseException] = {}

    def _explicit(name: str, home: Path, pid: str, fact: str, foreign: str) -> None:
        try:
            from clawd_projects.service import get_project_memory_store, resolve_session_project

            barrier.wait(timeout=60)
            assert os.environ.get("HERMES_HOME") == str(decoy)
            proj = resolve_session_project(SID, hermes_home=home)
            entries = get_project_memory_store(proj.id, hermes_home=home).memory_entries
            assert proj.id == pid
            assert fact in entries
            assert foreign not in entries and DECOY not in entries
        except BaseException as exc:
            errors[name] = exc
            try:
                barrier.abort()
            except Exception:
                pass

    threads = [
        threading.Thread(target=_explicit, args=("explicit-a", home_a, pid_a, ALPHA, BETA)),
        threading.Thread(target=_explicit, args=("explicit-b", home_b, pid_b, BETA, ALPHA)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
    assert not any(thread.is_alive() for thread in threads)
    assert errors == {}

    barrier = threading.Barrier(2)

    def _override(name: str, home: Path, pid: str, fact: str, foreign: str) -> None:
        token = use_home(home)
        try:
            from clawd_projects.service import get_project_memory_store, resolve_session_project

            barrier.wait(timeout=60)
            assert os.environ.get("HERMES_HOME") == str(decoy)
            assert os.environ.get("CLAWD_HOME") == str(decoy)
            proj = resolve_session_project(SID)
            entries = get_project_memory_store(proj.id).memory_entries
            assert proj.id == pid
            assert fact in entries and foreign not in entries and DECOY not in entries
            agent = make_agent(home, SID)
            try:
                text = agent._memory_store.format_for_system_prompt("memory") or ""
                assert agent.project_id == pid
                assert fact in text and foreign not in text and DECOY not in text
            finally:
                close_agent(agent)
        except BaseException as exc:
            errors[name] = exc
            try:
                barrier.abort()
            except Exception:
                pass
        finally:
            drop_home(token)

    threads = [
        threading.Thread(target=_override, args=("agent-a", home_a, pid_a, ALPHA, BETA)),
        threading.Thread(target=_override, args=("agent-b", home_b, pid_b, BETA, ALPHA)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=180)
    assert not any(thread.is_alive() for thread in threads)
    assert errors == {}
