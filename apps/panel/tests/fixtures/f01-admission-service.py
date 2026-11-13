"""Synthetic durable admission service; only fixture-owned temporary databases are touched.

Serves panel-core routes at the root via aiohttp (the same dispatcher the
panel serves at /panel-core), plus two unauthenticated fixture endpoints:
GET /__fixture/queries and POST /__fixture/admit. The /sessions/run/resolve
paths the driver issues are recorded for later assertion.
"""
import asyncio
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aiohttp import web

from clawd_server import panel_core
from clawd_server.panel_core import register_panel_core_routes

home = Path(sys.argv[1]).resolve()
assert home.is_dir() and home.name.startswith("hp-f01-exact-")
panel_core.HERMES_HOME = home
with sqlite3.connect(home / "state.db") as db:
    db.executescript("CREATE TABLE sessions(id TEXT PRIMARY KEY,source TEXT NOT NULL);"
                     "CREATE TABLE session_turn_leases(conversation_id TEXT PRIMARY KEY,holder TEXT,acquired_at REAL,expires_at REAL);")
with sqlite3.connect(home / "runs_idempotency.db") as db:
    db.execute("CREATE TABLE run_idempotency(scope TEXT NOT NULL,idempotency_key TEXT NOT NULL,fingerprint TEXT NOT NULL,"
               "run_id TEXT NOT NULL,status_json TEXT NOT NULL,owner_pid INTEGER NOT NULL,owner_started INTEGER NOT NULL,"
               "retention_until REAL NOT NULL,acknowledged_at REAL,created_at REAL NOT NULL,updated_at REAL NOT NULL,"
               "PRIMARY KEY(scope,idempotency_key))")
    db.execute("CREATE UNIQUE INDEX run_idempotency_run_id ON run_idempotency(run_id)")

queries = []


class _Stub:
    """Minimal server surface: panel_core module + key check."""

    def __init__(self):
        self.panel_core = panel_core

    def _authed(self, request):
        if request.headers.get("X-Panel-Key") != "synthetic-fixture-auth":
            return web.json_response(
                {"error": {"code": "unauthorized", "message": "unauthorized"}},
                status=401,
                headers={"Cache-Control": "no-store"},
            )
        return None


@web.middleware
async def _record_resolve(request, handler):
    if request.path.startswith("/sessions/run/resolve"):
        queries.append(request.path_qs)
    return await handler(request)


async def _queries(request):
    return web.json_response({"queries": queries})


async def _admit(request):
    body = await request.json()
    sid, key = body["session_id"], body["key"]
    now = time.time()
    public = {"object": "hermes.run", "run_id": "run_" + uuid.uuid4().hex, "session_id": sid,
              "status": body.get("status", "running"), "created_at": now, "updated_at": now}
    with sqlite3.connect(home / "state.db") as db:
        db.execute("INSERT OR IGNORE INTO sessions VALUES (?, 'api_server')", (sid,))
    started = int(Path(f"/proc/{os.getpid()}/stat").read_text().rsplit(")", 1)[1].split()[19])
    with sqlite3.connect(home / "runs_idempotency.db") as db:
        db.execute("INSERT INTO run_idempotency VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   ("fixture", key, "fixture", public["run_id"], json.dumps(public), os.getpid(), started, 0, None, now, now))
    return web.json_response(public)


app = web.Application(middlewares=[_record_resolve])
app.router.add_get("/__fixture/queries", _queries)
app.router.add_post("/__fixture/admit", _admit)
register_panel_core_routes(app, _Stub(), prefix="")

loop = asyncio.new_event_loop()
runner = web.AppRunner(app, access_log=None)
loop.run_until_complete(runner.setup())
site = web.TCPSite(runner, "127.0.0.1", 0)
loop.run_until_complete(site.start())
print(json.dumps({"port": site._server.sockets[0].getsockname()[1]}), flush=True)
try:
    loop.run_forever()
finally:
    loop.run_until_complete(runner.cleanup())
