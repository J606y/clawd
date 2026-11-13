"""Start panel-core routes on a real loopback port for unittest-style tests.

The panel-core dispatcher runs in-process (no sidecar thread, no proxy).
The stub server emulates the old X-Panel-Key check so key-auth cases keep
their meaning; production auth is PanelServer._authed (panel cookie).
Routes are mounted at the root (prefix=""): these tests hit the dispatcher
directly, the way they used to hit the sidecar's own listener, so paths
like /live/task and /files/artifact need no /panel-core prefix.
Error responses carry Cache-Control: no-store, like the old handler.
"""
from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

from aiohttp import web

from clawd_server.panel_core import register_panel_core_routes


class _KeyStubServer:
    """Minimal server surface: panel_core module + key check."""

    def __init__(self, mod: Any, key: str):
        self.panel_core = mod
        self._key = key

    def _authed(self, request: web.Request):
        if request.headers.get("X-Panel-Key") != self._key:
            return web.json_response(
                {"error": {"code": "unauthorized", "message": "unauthorized"}},
                status=401,
                headers={"Cache-Control": "no-store"},
            )
        return None


def start_panel_core_server(mod: Any, key: str):
    """Start an aiohttp app serving panel-core routes on 127.0.0.1:0.

    Returns (loop, runner, thread, port). Stop with stop_panel_core_server.
    """
    app = web.Application()
    register_panel_core_routes(app, _KeyStubServer(mod, key), prefix="")
    loop = asyncio.new_event_loop()
    # Some tests patch time.monotonic globally (lease expiry); the event
    # loop looks it up on every tick, so pin the real one on the loop
    # itself or the loop thread dies with StopIteration.
    loop.time = time.monotonic
    runner = web.AppRunner(app)
    started = threading.Event()
    holder: dict[str, int] = {}

    def _serve() -> None:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(runner.setup())
        site = web.TCPSite(runner, "127.0.0.1", 0)
        loop.run_until_complete(site.start())
        holder["port"] = site._server.sockets[0].getsockname()[1]
        started.set()
        loop.run_forever()

    thread = threading.Thread(target=_serve, name="panel-core-fixture", daemon=True)
    thread.start()
    if not started.wait(timeout=10):
        raise AssertionError("panel-core fixture server did not start")
    return loop, runner, thread, holder["port"]


def stop_panel_core_server(loop, runner, thread) -> None:
    if loop is None:
        return

    async def _cleanup() -> None:
        await runner.cleanup()

    try:
        fut = asyncio.run_coroutine_threadsafe(_cleanup(), loop)
        fut.result(timeout=10)
    except RuntimeError:
        pass  # loop already closed/dead; just join the thread below
    try:
        loop.call_soon_threadsafe(loop.stop)
    except RuntimeError:
        pass
    thread.join(timeout=5)
