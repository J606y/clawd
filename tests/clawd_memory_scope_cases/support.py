"""Shared entry points for memory-scope cases.

These helpers call the real project store, session DB, and agent constructor.
They do not mock resolve/inherit/approval decisions and do not read production homes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from hermes_cli import projects_db as pdb
from hermes_constants import reset_hermes_home_override, set_hermes_home_override

AGENT_KW = {
    "model": "test-model",
    "provider": "custom",
    "base_url": "http://127.0.0.1:9999/v1",
    "api_key": "sk-test",
    "platform": "cli",
    "quiet_mode": True,
}

# The memory route now requires the profile SessionAuthority and session_id.
# This case still does not use POST as evidence of ordinary-mode refusal or
# approval. Those stay on the agent memory executor. Do not skip the archive
# read; give it the same authority the route demands.
API_MEMORY_WRITE_BLOCKER = (
    "gateway.platforms.clawd_project_routes memory handlers require a profile "
    "SessionAuthority and session_id. POST is not evidence for ordinary-mode "
    "refusal or approval. Archive coverage reads with that authority and does "
    "not call evaluate_gate."
)

# invoke_tool resolves the memory inline executor before valid_tool_names.
# Subagent write refusal is the turn gate (tool surface + validate_tool_calls),
# not a direct invoke_tool call.
INVOKE_TOOL_NOT_SUBAGENT_GATE = (
    "agent.agent_runtime_helpers.invoke_tool dispatches inline memory without checking "
    "valid_tool_names. Subagent read-only evidence is _build_child_agent plus "
    "validate_tool_calls, which is the turn path that refuses unknown tools before execution."
)


def seed_bound_project(home: Path, name: str, session_id: str, fact: str) -> str:
    """Create a real project row, bind the session, and seed project memory on disk."""
    home.mkdir(parents=True, exist_ok=True)
    workspace = home / "workspace" / name
    workspace.mkdir(parents=True, exist_ok=True)
    with pdb.connect_closing(home / "projects.db") as conn:
        pid = pdb.create_project(conn, name=name, primary_path=str(workspace))
        pdb.bind_session_project(conn, session_id, pid)
    from clawd_projects.service import get_project_memory_store

    get_project_memory_store(pid, hermes_home=home).add("memory", fact)
    return pid


def project_memory_entries(home: Path, project_id: str) -> list[str]:
    from clawd_projects.service import get_project_memory_store

    return list(get_project_memory_store(project_id, hermes_home=home).memory_entries)


def memory_file(home: Path, project_id: str) -> Path:
    from clawd_projects.service import get_project_memory_dir

    return get_project_memory_dir(project_id, hermes_home=home) / "MEMORY.md"


def open_session_db(home: Path):
    from hermes_state import SessionDB

    home.mkdir(parents=True, exist_ok=True)
    return SessionDB(db_path=home / "state.db")


def make_agent(home: Path, session_id: str, *, session_db=None, skip_context_files: bool = True):
    from run_agent import AIAgent

    return AIAgent(
        **AGENT_KW,
        session_id=session_id,
        session_db=session_db,
        skip_context_files=skip_context_files,
    )


def close_agent(agent) -> None:
    try:
        agent.close()
    except Exception:
        pass


def use_home(home: Path):
    token = set_hermes_home_override(str(home))
    return token


def drop_home(token) -> None:
    reset_hermes_home_override(token)


def memory_targets(agent) -> list[str]:
    for tool in agent.tools or []:
        fn = (tool or {}).get("function") or {}
        if fn.get("name") == "memory":
            return list(fn["parameters"]["properties"]["target"]["enum"])
    return []


def call_memory(agent, *, target: str, content: str, action: str = "add", operations=None) -> dict[str, Any]:
    """Real inline memory executor. Does not patch the approval gate."""
    from agent.inline_tool_executors import INLINE_TOOL_EXECUTORS, InlineToolContext

    args: dict[str, Any] = {"action": action, "target": target, "content": content}
    if operations is not None:
        args["operations"] = operations
    raw = INLINE_TOOL_EXECUTORS["memory"](
        agent,
        args,
        InlineToolContext(effective_task_id="memory-scope", tool_call_id="call-memory-scope"),
    )
    if isinstance(raw, dict):
        return raw
    return json.loads(raw)


def assert_rejected(result: dict[str, Any], token: str, *blobs: str) -> None:
    assert result.get("success") is False, result
    assert result.get("staged") is not True
    for blob in blobs:
        assert token not in blob


def scoped_write_outcome(result: dict[str, Any], token: str, landed: str, *absent: str) -> str:
    """Record the real gate outcome. Never treats staging as an applied write."""
    if result.get("staged") is True:
        assert token not in landed
        for blob in absent:
            assert token not in blob
        return "staged"
    assert result.get("success") is True, result
    assert token in landed
    for blob in absent:
        assert token not in blob
    return "applied"


def joined(entries: Optional[list[str]]) -> str:
    return "\n".join(entries or [])


def attach_memory_authority(adapter, db, session_id: str, *, profile_id: str = "default"):
    """Install a real SessionAuthority for this profile db. Does not mint a binding."""
    from types import SimpleNamespace

    from gateway.session_authority import LiveSession, SessionAuthority
    from hermes_state_runtime import begin_runtime_epoch

    if db.get_session(session_id) is None:
        db.create_session(session_id, "api_server")
    epoch = begin_runtime_epoch(db, instance_id="memory-scope")
    runner = getattr(adapter, "gateway_runner", None)
    if getattr(runner, "session_authorities", None) is not None:
        raise RuntimeError("adapter already has a session authority registry; refusing to hide it")
    if runner is None:
        runner = SimpleNamespace(_draining=False, session_runtime_descriptor={})
        adapter.gateway_runner = runner
    elif not hasattr(runner, "_draining"):
        runner._draining = False
    authority = SessionAuthority(
        runner,
        profile_id=profile_id,
        instance_id="memory-scope",
        db=db,
        epoch=epoch,
    )
    authority.sessions[session_id] = LiveSession(None, "api-memory")
    runner.session_authority = authority
    return authority
