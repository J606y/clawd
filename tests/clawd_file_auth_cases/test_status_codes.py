"""Business 403/409 must not be relabeled as login-expired 401."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from tests.clawd_file_auth_cases.helpers import (
    dispatch,
    error_code,
    isolated_panel_core,
    make_handler,
    panel_request,
    require_server,
)


def test_panel_core_denial_status_is_not_401(tmp_path: Path):
    with isolated_panel_core(tmp_path) as (home, mod):
        (home / "uploads" / "id_rsa").write_bytes(b"secret")
        status, _headers, body = dispatch(mod, "GET", "/files/download?root=uploads&path=id_rsa")
        assert status == 403
        assert error_code(body) != "unauthorized"
        status, _headers, body = dispatch(
            mod, "POST", "/services/clawd-panel/restart", body=b"{}"
        )
        assert status == 403
        assert error_code(body) == "restart_disabled"


def test_name_exhausted_is_409_not_401(tmp_path: Path):
    with isolated_panel_core(tmp_path) as (home, mod):
        folder = home / "uploads" / "busy"
        folder.mkdir()
        (folder / "same.txt").write_bytes(b"")
        for index in range(1, 1000):
            (folder / f"same ({index}).txt").write_bytes(b"")
        handler = make_handler(mod, headers={"Content-Length": "1"}, body=b"x")
        with pytest.raises(mod.ApiError) as exc:
            handler._upload({"root": "uploads", "name": "same.txt", "dir": "busy"})
        assert exc.value.status == 409
        assert exc.value.code == "name_exhausted"
        status, _headers, body = dispatch(
            mod,
            "POST",
            "/files/upload?root=uploads&name=same.txt&dir=busy",
            body=b"x",
        )
        assert status == 409
        assert error_code(body) == "name_exhausted"


def test_proxy_preserves_panel_core_403_and_409(tmp_path: Path):
    asyncio.run(_proxy(tmp_path))


async def _proxy(tmp_path: Path) -> None:
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from clawd_server.auth import add_user, credential_hash
    from clawd_server.panel_core import register_panel_core_routes

    with isolated_panel_core(tmp_path) as (home, mod):
        add_user(home, "ops", "secret")
        digest = credential_hash("ops", "secret")
        PanelServer = require_server()
        server = PanelServer(home, None)
        server.panel_core = mod
        app = web.Application()
        register_panel_core_routes(app, server)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            headers = {"Cookie": f"hp_auth={digest}", "Content-Type": "application/json"}
            denied = await client.post(
                "/panel-core/services/clawd-panel/restart", data=b"{}", headers=headers
            )
            assert denied.status == 403
            assert (await denied.json())["error"]["code"] == "restart_disabled"
            folder = home / "uploads" / "busy"
            folder.mkdir()
            (folder / "same.txt").write_bytes(b"")
            for index in range(1, 1000):
                (folder / f"same ({index}).txt").write_bytes(b"")
            busy = await client.post(
                "/panel-core/files/upload?root=uploads&name=same.txt&dir=busy",
                data=b"x",
                headers=headers,
            )
            assert busy.status == 409, await busy.read()
            assert (await busy.json())["error"]["code"] == "name_exhausted"
        finally:
            await client.close()
