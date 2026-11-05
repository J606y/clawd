"""Exercise project routes through the real adapter's listening lifecycle."""
import secrets

import aiohttp
import pytest

from gateway.config import PlatformConfig
from gateway.platforms.api_server import APIServerAdapter
from hermes_constants import reset_hermes_home_override, set_hermes_home_override


@pytest.mark.asyncio
async def test_projects_registered_authenticated_and_persisted(tmp_path, monkeypatch):
    home = tmp_path / "state"
    home.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("CLAWD_HOME", str(home))
    token = set_hermes_home_override(str(home))
    key = secrets.token_hex(32)
    headers = {"Authorization": f"Bearer {key}"}
    project_id = None
    try:
        for iteration in range(2):
            adapter = APIServerAdapter(PlatformConfig(
                enabled=True, extra={"host": "127.0.0.1", "key": key},
            ))
            adapter._port = 0
            try:
                assert await adapter.connect()
                port = adapter._runner.addresses[0][1]
                url = f"http://127.0.0.1:{port}/api/projects"
                async with aiohttp.ClientSession() as client:
                    async with client.get(url) as response:
                        assert response.status == 401
                    if iteration == 0:
                        async with client.post(url, headers=headers, json=[]) as response:
                            assert response.status == 400
                        async with client.post(url, headers=headers, json={
                            "name": "Lifecycle project", "primary_path": str(workspace),
                        }) as response:
                            assert response.status == 201, await response.text()
                            project_id = (await response.json())["project"]["id"]
                    async with client.get(url, headers=headers) as response:
                        assert response.status == 200
                        projects = (await response.json())["projects"]
                        assert [p["id"] for p in projects] == [project_id]
                        assert projects[0]["primary_path"] == str(workspace)
            finally:
                await adapter.disconnect()
    finally:
        reset_hermes_home_override(token)
