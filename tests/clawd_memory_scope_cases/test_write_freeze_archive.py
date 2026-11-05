"""Ordinary-mode refusal, frozen write targets, and archive that keeps memory files.

Not executed in this change. Approval is the live gate: staging is not forced
through, and a staged write is not treated as saved.
"""

from __future__ import annotations

import secrets

import aiohttp
import pytest

from tests.clawd_memory_scope_cases.support import (
    API_MEMORY_WRITE_BLOCKER,
    assert_rejected,
    attach_memory_authority,
    call_memory,
    close_agent,
    drop_home,
    joined,
    make_agent,
    memory_file,
    memory_targets,
    open_session_db,
    project_memory_entries,
    scoped_write_outcome,
    seed_bound_project,
    use_home,
)


def test_ordinary_mode_rejects_project_writes(tmp_path):
    home = tmp_path / "ordinary"
    home.mkdir()
    db = open_session_db(home)
    db.create_session("ordinary", "cli")
    token = use_home(home)
    try:
        agent = make_agent(home, "ordinary", session_db=db)
        try:
            targets = memory_targets(agent)
            assert "project" not in targets and "global_memory" not in targets
            assert "project" not in agent._memory_store.accepted_targets()
            refused = "ORDINARY-PROJECT-WRITE"
            for target in ("project", "global_memory"):
                result = call_memory(agent, target=target, content=refused)
                assert_rejected(result, refused)
            batch = call_memory(
                agent,
                target="project",
                content=refused,
                operations=[{"action": "add", "content": refused}],
            )
            assert_rejected(batch, refused)
            project_root = home / "projects"
            landed = ""
            if project_root.exists():
                landed = "\n".join(
                    path.read_text(encoding="utf-8") for path in project_root.rglob("MEMORY.md")
                )
            assert refused not in landed
            global_text = ""
            global_file = home / "memories" / "MEMORY.md"
            if global_file.exists():
                global_text = global_file.read_text(encoding="utf-8")
            assert refused not in global_text
        finally:
            close_agent(agent)
    finally:
        drop_home(token)
        db.close()


def test_global_and_project_targets_stay_frozen(tmp_path):
    home = tmp_path / "frozen"
    pid_a = seed_bound_project(home, "Alpha", "sess", "ALPHA-FROZEN-FACT")
    workspace_b = home / "workspace" / "Beta"
    workspace_b.mkdir(parents=True)
    from hermes_cli import projects_db as pdb

    with pdb.connect_closing(home / "projects.db") as conn:
        pid_b = pdb.create_project(conn, name="Beta", primary_path=str(workspace_b))
    db = open_session_db(home)
    db.create_session("sess", "cli")
    token = use_home(home)
    try:
        agent = make_agent(home, "sess", session_db=db, skip_context_files=False)
        try:
            from clawd_projects.service import bind_session_to_project

            before_targets = memory_targets(agent)
            assert "project" in before_targets and "global_memory" in before_targets
            before_prompt = agent._build_system_prompt()
            before_snapshot = agent._memory_store.format_for_system_prompt("memory")
            assert "ALPHA-FROZEN-FACT" in (before_snapshot or "")
            assert bind_session_to_project("sess", pid_b, hermes_home=home, session_db=db) is True
            assert memory_targets(agent) == before_targets
            assert agent._memory_store.project_id == pid_a

            project_token = "LIVE-PROJECT-WRITE"
            global_token = "LIVE-GLOBAL-WRITE"
            project_result = call_memory(agent, target="project", content=project_token)
            global_result = call_memory(agent, target="global_memory", content=global_token)
            assert agent._build_system_prompt() == before_prompt
            assert agent._memory_store.format_for_system_prompt("memory") == before_snapshot
            project_blob = joined(project_memory_entries(home, pid_a))
            other_blob = joined(project_memory_entries(home, pid_b))
            global_file = home / "memories" / "MEMORY.md"
            global_blob = global_file.read_text(encoding="utf-8") if global_file.exists() else ""
            scoped_write_outcome(project_result, project_token, project_blob, other_blob, global_blob)
            scoped_write_outcome(global_result, global_token, global_blob, project_blob, other_blob)
            assert project_token not in (agent._memory_store.format_for_system_prompt("memory") or "")
            assert global_token not in (agent._memory_store.format_for_system_prompt("memory") or "")
        finally:
            close_agent(agent)
    finally:
        drop_home(token)
        db.close()


@pytest.mark.asyncio
async def test_archive_keeps_directory_memory(tmp_path, monkeypatch):
    """Real listening adapter. Archive must not delete the memory directory or session."""
    home = tmp_path / "archive-home"
    workspace = home / "workspace" / "Kept"
    fact = "ARCHIVE-KEEPS-THIS-MEMORY"
    pid = seed_bound_project(home, "Kept", "sess-arch", fact)
    db = open_session_db(home)
    db.create_session("sess-arch", "api_server")
    mem = memory_file(home, pid)
    before = mem.read_bytes()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("CLAWD_HOME", str(home))
    token = use_home(home)
    key = secrets.token_hex(32)
    headers = {"Authorization": f"Bearer {key}"}
    try:
        from gateway.config import PlatformConfig
        from gateway.platforms.api_server import APIServerAdapter

        adapter = APIServerAdapter(PlatformConfig(
            enabled=True, extra={"host": "127.0.0.1", "key": key},
        ))
        adapter._port = 0
        assert await adapter.connect()
        port = adapter._runner.addresses[0][1]
        base = f"http://127.0.0.1:{port}"
        try:
            async with aiohttp.ClientSession() as client:
                async with client.patch(
                    f"{base}/api/projects/{pid}",
                    headers=headers,
                    json={"archived": True},
                ) as response:
                    assert response.status == 200, await response.text()
                    body = await response.json()
                    assert body["project"]["archived"] is True
                    assert body["project"]["primary_path"] == str(workspace)
                async with client.get(
                    f"{base}/api/projects/{pid}/memory",
                    headers=headers,
                ) as response:
                    assert response.status == 401, await response.text()
                    naked = await response.json()
                    assert naked.get("error") == "missing_identity"
                other = home / "workspace" / "Other"
                other.mkdir()
                from hermes_cli import projects_db as pdb

                with pdb.connect_closing(home / "projects.db") as conn:
                    other_id = pdb.create_project(conn, name="Other", primary_path=str(other))
                attach_memory_authority(adapter, db, "sess-arch")
                async with client.get(
                    f"{base}/api/projects/{other_id}/memory",
                    headers=headers,
                    params={"session_id": "sess-arch"},
                ) as response:
                    assert response.status == 403, await response.text()
                    mismatch = await response.json()
                    assert mismatch.get("error") == "project_mismatch"
                async with client.get(
                    f"{base}/api/projects/{pid}/memory",
                    headers=headers,
                    params={"session_id": "sess-arch"},
                ) as response:
                    assert response.status == 200, await response.text()
                    readback = await response.json()
                    assert fact in readback["memory"]["entries"]
        finally:
            await adapter.disconnect()
        assert mem.read_bytes() == before
        assert workspace.is_dir()
        assert db.get_session("sess-arch") is not None
        from clawd_projects.service import resolve_session_project

        assert resolve_session_project("sess-arch", hermes_home=home).id == pid
        assert fact in project_memory_entries(home, pid)
        assert API_MEMORY_WRITE_BLOCKER
    finally:
        drop_home(token)
        db.close()
