"""Unrun freeze, schema, and subagent tool-boundary checks. invoke_tool is not the gate."""

from __future__ import annotations

import json
from types import SimpleNamespace

from tools.memory_tool import MemoryStore, apply_memory_pending, apply_memory_schema_to_agent, memory_tool


def test_snapshot_is_not_replaced_by_a_second_load(tmp_path):
    from clawd_projects.project_memory import MemoryWriteDenied, ProjectMemoryStore
    from clawd_projects.service import get_project_memory_store

    global_store = MemoryStore(memory_dir=tmp_path / "memories")
    global_store.load_from_disk()
    global_store.add("memory", "GLOBAL-BEFORE")
    project = get_project_memory_store("p_alpha", hermes_home=tmp_path)
    project.add("memory", "PROJECT-BEFORE")
    composite = ProjectMemoryStore(global_store, project, "p_alpha", "Alpha")
    composite.load_from_disk()
    frozen = composite.format_for_system_prompt("memory")
    project.add("memory", "PROJECT-AFTER")
    composite.load_from_disk()
    assert composite.format_for_system_prompt("memory") == frozen
    assert "PROJECT-AFTER" not in (frozen or "")
    composite.memory_writes_allowed = False
    try:
        composite.add("memory", "NO")
    except MemoryWriteDenied as exc:
        assert exc.code == "subagent_readonly"
    else:
        raise AssertionError("read-only composite wrote")


def test_memory_tool_readonly_is_not_staged_and_does_not_replace_approval(tmp_path, monkeypatch):
    store = MemoryStore(memory_dir=tmp_path / "memories")
    store.load_from_disk()
    store.add("memory", "keep")
    before = (tmp_path / "memories" / "MEMORY.md").read_bytes()
    store.memory_writes_allowed = False
    called = {"gate": 0}

    def _gate(*_a, **_k):
        called["gate"] += 1
        raise AssertionError("read-only tool write must not reach evaluate_gate")

    monkeypatch.setattr("tools.write_approval.evaluate_gate", _gate)
    refused = json.loads(memory_tool(action="add", target="memory", content="SUBAGENT-WRITE", store=store))
    assert refused["success"] is False
    assert refused.get("staged") is not True
    assert refused["code"] == "subagent_readonly"
    assert (tmp_path / "memories" / "MEMORY.md").read_bytes() == before
    replay = apply_memory_pending(
        {"action": "add", "target": "memory", "content": "REPLAY"}, store,
    )
    assert replay["success"] is False
    assert called["gate"] == 0

    writable = MemoryStore(memory_dir=tmp_path / "other")
    writable.load_from_disk()
    from tools.write_approval import GateDecision

    monkeypatch.setattr(
        "tools.write_approval.evaluate_gate",
        lambda *_a, **_k: GateDecision(stage=True, message="staged"),
    )
    monkeypatch.setattr("tools.write_approval.stage_write", lambda *_a, **_k: {"id": "pend"})
    staged = json.loads(memory_tool(action="add", target="memory", content="needs approval", store=writable))
    assert staged.get("staged") is True
    assert "needs approval" not in writable.memory_entries


def test_running_schema_is_not_rebuilt(tmp_path):
    from clawd_projects.project_memory import ProjectMemoryStore
    from clawd_projects.service import get_project_memory_store

    global_store = MemoryStore(memory_dir=tmp_path / "g")
    global_store.load_from_disk()
    project = get_project_memory_store("p_alpha", hermes_home=tmp_path)
    composite = ProjectMemoryStore(global_store, project, "p_alpha", "Alpha")
    composite.load_from_disk()
    from tools.memory_tool import MEMORY_SCHEMA
    import copy

    agent = SimpleNamespace(
        _memory_store=composite,
        tools=[{"type": "function", "function": copy.deepcopy(MEMORY_SCHEMA)}],
        _current_turn_id="",
    )
    apply_memory_schema_to_agent(agent)
    enum = agent.tools[0]["function"]["parameters"]["properties"]["target"]["enum"]
    assert "project" in enum
    agent._current_turn_id = "turn-1"
    agent._memory_store = MemoryStore(memory_dir=tmp_path / "plain")
    apply_memory_schema_to_agent(agent)
    assert agent.tools[0]["function"]["parameters"]["properties"]["target"]["enum"] == enum


def test_subagent_gate_is_tool_surface_and_validator_not_invoke_tool():
    from tools.delegate_tool_toolsets import DELEGATE_BLOCKED_TOOLS, _resolve_child_toolsets

    assert "memory" in DELEGATE_BLOCKED_TOOLS
    parent = SimpleNamespace(
        enabled_toolsets=["terminal", "memory"],
        valid_tool_names={"terminal", "memory"},
        disabled_toolsets=[],
    )
    enabled, disabled = _resolve_child_toolsets(parent, None, "leaf")
    assert "memory" not in enabled
    assert "memory" in disabled

    from agent.turn_tool_validation import validate_tool_calls

    agent = SimpleNamespace(
        valid_tool_names={"terminal"},
        _invalid_tool_retries=0,
        log_prefix="",
        _uniquify_tool_call_ids=lambda *_a, **_k: None,
        _repair_tool_call=lambda _name: None,
        _vprint=lambda *_a, **_k: None,
        _buffer_vprint=lambda *_a, **_k: None,
        _build_assistant_message=lambda *_a, **_k: {"role": "assistant", "content": ""},
    )
    messages = []
    verdict = validate_tool_calls(
        agent,
        SimpleNamespace(
            content="",
            tool_calls=[SimpleNamespace(
                id="call_memory",
                type="function",
                function=SimpleNamespace(name="memory", arguments="{}"),
            )],
        ),
        "tool_calls",
        messages=messages,
        conversation_history=[],
        api_call_count=1,
        effective_task_id="child",
    )
    assert verdict.action == "continue"
    assert any(item.get("role") == "tool" for item in messages)
