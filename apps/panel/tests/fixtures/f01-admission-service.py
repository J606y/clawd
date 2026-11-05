"""Synthetic durable admission service; only fixture-owned temporary databases are touched."""
import contextlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from sidecar import hermes_panel_sidecar as sidecar

home = Path(sys.argv[1]).resolve()
assert home.is_dir() and home.name.startswith("hp-f01-exact-")
sidecar.HERMES_HOME = home
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


class FixtureHandler(sidecar.Handler):
    key = "synthetic-fixture-auth"

    def do_GET(self):
        if self.path == "/__fixture/queries":
            self._send(200, {"queries": queries})
            return
        if self.path.startswith("/sessions/run/resolve?"):
            queries.append(self.path)
        super().do_GET()

    def do_POST(self):
        if self.path != "/__fixture/admit":
            super().do_POST()
            return
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
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
        self._send(200, public)

    def log_message(self, *_args):
        return  # Fixture request traffic is asserted explicitly by the Node driver.


server = sidecar.ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
server.daemon_threads = True
print(json.dumps({"port": server.server_address[1]}), flush=True)
try:
    server.serve_forever(poll_interval=0.01)
finally:
    server.server_close()
