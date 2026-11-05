"""Business 403/409 must not be relabeled as login-expired 401."""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path

import pytest

from tests.clawd_file_auth_cases.helpers import (
    dispatch,
    error_code,
    isolated_sidecar,
    make_handler,
    panel_request,
    require_server,
)


def test_sidecar_denial_status_is_not_401(tmp_path: Path):
    with isolated_sidecar(tmp_path) as (home, mod):
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
    with isolated_sidecar(tmp_path) as (home, mod):
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


def test_proxy_preserves_sidecar_403_and_409(tmp_path: Path):
    asyncio.run(_proxy(tmp_path))


async def _proxy(tmp_path: Path) -> None:
    from aiohttp import ClientSession, ClientTimeout, web

    from clawd_server.auth import add_user, credential_hash

    with isolated_sidecar(tmp_path) as (home, mod):
        add_user(home, "ops", "secret")
        digest = credential_hash("ops", "secret")
        key = "panel-sidecar-test-key"

        class Isolated(mod.Handler):
            pass

        Isolated.key = key
        httpd = mod.ThreadingHTTPServer(("127.0.0.1", 0), Isolated)
        httpd.daemon_threads = True
        thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        client = None
        runner = None
        try:
            PanelServer = require_server()
            server = PanelServer(home, None)
            server.sidecar_key = key
            server.sidecar_base = f"http://127.0.0.1:{httpd.server_address[1]}"
            client = ClientSession(timeout=ClientTimeout(total=10))
            server.client = client
            app = web.Application()

            async def route(request):
                return await server._proxy_sidecar(request)

            app.router.add_route("*", "/panel-sidecar/{tail:.*}", route)
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, "127.0.0.1", 0)
            await site.start()
            sockets = getattr(site._server, "sockets", None) or []
            port = sockets[0].getsockname()[1]
            denied = await _panel_status(
                port, digest, "POST", "/panel-sidecar/services/clawd-panel/restart", b"{}"
            )
            assert denied[0] == 403
            assert json.loads(denied[1])["error"]["code"] == "restart_disabled"
            folder = home / "uploads" / "busy"
            folder.mkdir()
            (folder / "same.txt").write_bytes(b"")
            for index in range(1, 1000):
                (folder / f"same ({index}).txt").write_bytes(b"")
            busy = await _panel_status(
                port,
                digest,
                "POST",
                "/panel-sidecar/files/upload?root=uploads&name=same.txt&dir=busy",
                b"x",
            )
            assert busy[0] == 409, busy[1]
            assert json.loads(busy[1])["error"]["code"] == "name_exhausted"
        finally:
            if client is not None:
                await client.close()
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)
            if runner is not None:
                await runner.cleanup()


async def _panel_status(port: int, digest: str, method: str, path: str, body: bytes) -> tuple[int, bytes]:
    import urllib.error
    import urllib.request

    host = f"127.0.0.1:{port}"
    request = urllib.request.Request(
        f"http://{host}{path}",
        data=body,
        method=method,
        headers={
            "Host": host,
            "Origin": f"http://{host}",
            "Cookie": f"hp_auth={digest}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
