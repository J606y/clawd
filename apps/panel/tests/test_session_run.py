"""Read-only run discovery against temporary native DBs and a local HTTP server.

No Hermes imports, credentials, production databases, or model requests.
"""
import sys as _sys
from pathlib import Path as _Path
_REPO_ROOT = _Path(__file__).resolve().parents[3]
_TESTS_DIR = _Path(__file__).resolve().parent
if str(_TESTS_DIR) not in _sys.path:
    _sys.path.insert(0, str(_TESTS_DIR))
if str(_REPO_ROOT) not in _sys.path:
    _sys.path.append(str(_REPO_ROOT))

from clawd_server import panel_core
from panel_core_http_fixture import start_panel_core_server, stop_panel_core_server

import contextlib
import http.client
import importlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from unittest.mock import patch
from urllib.parse import urlencode


class SessionRunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = panel_core
        cls.key = "fixture-session-run-key"
        cls.loop, cls.runner, cls.thread, cls.port = start_panel_core_server(
            cls.module, cls.key
        )
        cls.server_address = ("127.0.0.1", cls.port)

    @classmethod
    def tearDownClass(cls):
        stop_panel_core_server(cls.loop, cls.runner, cls.thread)

    def setUp(self):
        scratch_base = os.environ.get("PANEL_TEST_OUTPUT") or os.environ.get("TMPDIR") or "/root/hermes-workspace/p-tmp"
        os.makedirs(scratch_base, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=scratch_base)
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home #? 测试"
        self.home.mkdir()
        self.state_db = self.home / "state.db"
        self.run_db = self.home / "runs_idempotency.db"
        self.session_id = "session_abcdef"
        self.now = 1_900_000_000.0
        self.home_patch = patch.object(self.module, "HERMES_HOME", self.home)
        self.home_patch.start()
        self.addCleanup(self.home_patch.stop)
        with self.connect(self.state_db) as conn:
            conn.executescript("""
                CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT NOT NULL);
                CREATE TABLE session_turn_leases (
                    conversation_id TEXT PRIMARY KEY, holder TEXT,
                    acquired_at REAL, expires_at REAL
                );
            """)
            conn.execute("INSERT INTO sessions VALUES (?, 'api_server')", (self.session_id,))

    @contextlib.contextmanager
    def connect(self, path):
        conn = sqlite3.connect(path)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def request(self, session_id=None, *, key=None, method="GET", path=None):
        if path is None:
            path = "/sessions/run?" + urlencode({
                "session_id": self.session_id if session_id is None else session_id})
        connection = http.client.HTTPConnection(*self.server_address, timeout=3)
        try:
            connection.request(method, path, headers={"X-Panel-Key": self.key if key is None else key})
            response = connection.getresponse()
            return response.status, json.loads(response.read()), dict(response.getheaders())
        finally:
            connection.close()

    def snapshot(self):
        return {p.relative_to(self.home).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
                for p in self.home.rglob("*") if p.is_file()}

    def assert_response(self, expected, session_id=None, **kwargs):
        before = self.snapshot()
        status, payload, headers = self.request(session_id, **kwargs)
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload, expected)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(self.snapshot(), before, "reader changed or created native DB files")
        return payload

    def test_http_missing_index_returns_idle_without_creating_database(self):
        self.assert_response({"run": None, "busy": False})
        self.assertFalse(self.run_db.exists())

    def assert_error(self, expected_status, expected_code, session_id=None, **kwargs):
        before = self.snapshot()
        status, payload, headers = self.request(session_id, **kwargs)
        self.assertEqual(status, expected_status, payload)
        self.assertEqual(payload["error"]["code"], expected_code)
        self.assertEqual(set(payload), {"error"})
        self.assertEqual(set(payload["error"]), {"code", "message"})
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(self.snapshot(), before)
        return payload

    def test_rejects_invalid_session_ids_before_opening_databases(self):
        # A missing state DB distinguishes validation from a failed lookup.
        self.state_db.unlink()
        for value in ("", "short", "x" * 81, "../state", "/root/.hermes",
                      "bad\\path", "bad.id", " bad_id", "bad_id ",
                      self.session_id + "\n", "会话编号xx", "bad\x00id"):
            with self.subTest(value=value):
                self.assert_error(400, "invalid_session", value)
        for value in (None, False, 42, [], {}):
            with self.subTest(value=value):
                with self.assertRaises(self.module.ApiError) as caught:
                    self.module.get_session_run(value)
                self.assertEqual(caught.exception.status, 400)
                self.assertEqual(caught.exception.code, "invalid_session")
        self.assert_error(400, "invalid_session", path="/sessions/run")

    def test_unknown_session_is_not_found_even_if_index_is_missing(self):
        self.assert_error(404, "session_not_found", "missing_session")

    def test_authentication_is_required_before_any_database_access(self):
        self.state_db.unlink()
        for key in ("", "wrong-fixture-key"):
            with self.subTest(key=key):
                self.assert_error(401, "unauthorized", key=key)

    def test_session_run_route_has_no_post_action(self):
        self.assert_error(404, "not_found", method="POST")

    def lease(self, acquired_at, expires_at, session_id=None):
        with self.connect(self.state_db) as conn:
            conn.execute("INSERT OR REPLACE INTO session_turn_leases VALUES (?, ?, ?, ?)",
                         (session_id or self.session_id, "fixture-worker", acquired_at, expires_at))

    def test_busy_comes_from_exact_unexpired_lease_without_a_run_index(self):
        acquired_at = self.now - 60
        self.lease(acquired_at, self.now + 60)
        with self.connect(self.state_db) as conn:
            conn.executescript("""
                CREATE TABLE messages (session_id TEXT, role TEXT, content TEXT);
                INSERT INTO messages VALUES ('session_abcdef', 'assistant', 'Not a completion signal');
            """)
        with patch.object(self.module.time, "time", return_value=self.now):
            self.assert_response({"run": None, "busy": True, "acquired_at": acquired_at})
            for expires_at in (self.now, self.now - 1):
                with self.subTest(expires_at=expires_at):
                    self.lease(acquired_at, expires_at)
                    self.assert_response({"run": None, "busy": False})
            self.lease(acquired_at, self.now + 60, "another_session")
            self.assert_response({"run": None, "busy": False})

    def test_invalid_lease_data_never_means_idle_or_completed(self):
        cases = ((None, self.now + 60), ("private-bad-timestamp", self.now + 60),
                 (-1, self.now + 60), (float("inf"), self.now + 60),
                 (self.now + 120, self.now + 60), (self.now - 60, None),
                 (self.now - 60, "private-bad-expiry"),
                 (self.now - 60, float("inf")), (self.now - 60, -1),
                 (self.now * 1000, self.now * 1000 + 60),
                 ("private-bad-timestamp", self.now - 1))
        with patch.object(self.module.time, "time", return_value=self.now):
            for acquired_at, expires_at in cases:
                with self.subTest(acquired_at=acquired_at, expires_at=expires_at):
                    self.lease(acquired_at, expires_at)
                    payload = self.assert_error(503, "session_run_unavailable")
                    self.assertNotIn("private-bad", json.dumps(payload))

    def create_run_index(self):
        with self.connect(self.run_db) as conn:
            conn.executescript("""
                CREATE TABLE run_idempotency (
                    scope TEXT NOT NULL, idempotency_key TEXT NOT NULL,
                    fingerprint TEXT NOT NULL, run_id TEXT NOT NULL,
                    status_json TEXT NOT NULL, owner_pid INTEGER NOT NULL DEFAULT 0,
                    owner_started INTEGER NOT NULL DEFAULT 0,
                    retention_until REAL NOT NULL DEFAULT 0, acknowledged_at REAL,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    PRIMARY KEY (scope, idempotency_key)
                );
                CREATE UNIQUE INDEX run_idempotency_run_id ON run_idempotency(run_id);
            """)

    def add_run(self, status="running", *, session_id=None, created_at=None, updated_at=None,
                owner_pid=None, owner_started=None, **fields):
        if not self.run_db.exists():
            self.create_run_index()
        run_id = "run_" + uuid.uuid4().hex
        public = {"object": "hermes.run", "run_id": run_id,
                  "session_id": session_id or self.session_id, "status": status,
                  "created_at": self.now - 2 if created_at is None else created_at,
                  "updated_at": self.now - 1 if updated_at is None else updated_at,
                  "output": "private-model-output", "error": "private-error",
                  "pending_steer": "private-message", "input": "private-request", **fields}
        if owner_pid is None:
            owner_pid = os.getpid()
        if owner_started is None:
            stat = Path(f"/proc/{os.getpid()}/stat").read_text(encoding="ascii")
            owner_started = int(stat.rsplit(")", 1)[1].split()[19])
        with self.connect(self.run_db) as conn:
            conn.execute("INSERT INTO run_idempotency VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         ("private-scope", uuid.uuid4().hex, "private-fingerprint", run_id,
                          json.dumps(public), owner_pid, owner_started, 0, None,
                          public["created_at"], public["updated_at"]))
        return {key: public[key] for key in
                ("run_id", "session_id", "status", "created_at", "updated_at")}

    def test_returns_only_latest_exact_session_run_metadata(self):
        self.add_run(created_at=self.now - 100, updated_at=self.now + 100)
        latest = self.add_run("completed", created_at=self.now - 20, updated_at=self.now - 10)
        self.add_run(session_id="another_session", created_at=self.now, updated_at=self.now)
        payload = self.assert_response({"run": latest, "busy": False})
        self.assertNotIn("private-", json.dumps(payload))

    def test_missing_state_database_is_unavailable_and_never_created(self):
        self.state_db.unlink()
        self.assert_error(503, "session_run_unavailable")
        self.assertFalse(self.state_db.exists())

    def test_corrupt_databases_are_unavailable_without_exposing_contents(self):
        self.create_run_index()
        for path in (self.state_db, self.run_db):
            with self.subTest(database=path.name):
                saved = path.read_bytes()
                path.write_bytes(b"private-corrupt-database-contents")
                try:
                    payload = self.assert_error(503, "session_run_unavailable")
                    self.assertNotIn("private-", json.dumps(payload))
                    self.assertNotIn(str(path), json.dumps(payload))
                finally:
                    path.write_bytes(saved)

    def test_exclusive_database_locks_are_unavailable_not_idle(self):
        self.add_run("completed")
        for path in (self.state_db, self.run_db):
            with self.subTest(database=path.name):
                with self.connect(path) as writer:
                    writer.execute("BEGIN EXCLUSIVE")
                    started = time.monotonic()
                    self.assert_error(503, "session_run_unavailable")
                    self.assertLess(time.monotonic() - started, 1.5, "reader waited too long on SQLite")

    def test_incompatible_native_columns_are_unavailable_even_when_empty(self):
        self.create_run_index()
        for column in ("owner_pid", "owner_started", "retention_until", "acknowledged_at",
                       "scope", "idempotency_key", "fingerprint", "run_id", "status_json",
                       "created_at", "updated_at"):
            with self.subTest(column=column):
                with self.connect(self.run_db) as conn:
                    conn.execute(f'ALTER TABLE run_idempotency RENAME COLUMN "{column}" TO incompatible')
                try:
                    self.assert_error(503, "session_run_unavailable")
                finally:
                    with self.connect(self.run_db) as conn:
                        conn.execute(f'ALTER TABLE run_idempotency RENAME COLUMN incompatible TO "{column}"')

    def test_state_tables_require_compatible_primary_key_lookups(self):
        for table, column in (("sessions", "id"), ("session_turn_leases", "conversation_id")):
            with self.subTest(table=table):
                with self.connect(self.state_db) as conn:
                    conn.execute(f'ALTER TABLE "{table}" RENAME TO saved_table')
                    conn.execute(f'CREATE TABLE "{table}" AS SELECT * FROM saved_table')
                try:
                    self.assert_error(503, "session_run_unavailable")
                finally:
                    with self.connect(self.state_db) as conn:
                        conn.execute(f'DROP TABLE "{table}"')
                        conn.execute(f'ALTER TABLE saved_table RENAME TO "{table}"')

    def test_views_are_not_accepted_as_native_tables(self):
        self.create_run_index()
        for path, table in ((self.state_db, "sessions"),
                            (self.state_db, "session_turn_leases"),
                            (self.run_db, "run_idempotency")):
            with self.subTest(table=table):
                with self.connect(path) as conn:
                    conn.execute(f'ALTER TABLE "{table}" RENAME TO saved_table')
                    conn.execute(f'CREATE VIEW "{table}" AS SELECT * FROM saved_table')
                try:
                    self.assert_error(503, "session_run_unavailable")
                finally:
                    with self.connect(path) as conn:
                        conn.execute(f'DROP VIEW "{table}"')
                        conn.execute(f'ALTER TABLE saved_table RENAME TO "{table}"')

    def update_run(self, values):
        with self.connect(self.run_db) as conn:
            conn.execute("UPDATE run_idempotency SET " + ",".join(key + " = ?" for key in values),
                         tuple(values.values()))

    def test_invalid_public_status_never_falls_back_to_completed(self):
        self.add_run("completed")
        with self.connect(self.run_db) as conn:
            original = json.loads(conn.execute("SELECT status_json FROM run_idempotency").fetchone()[0])
        changes = (("object", None), ("object", "private-other-object"),
                   ("status", "private-unknown-status"), ("status", []),
                   ("run_id", "run_" + "0" * 32), ("run_id", "private-invalid-id"),
                   ("run_id", original["run_id"] + "\n"),
                   ("created_at", None), ("created_at", True), ("created_at", "private-date"),
                   ("created_at", -1), ("created_at", self.now * 1000),
                   ("created_at", float("inf")), ("created_at", float("nan")),
                   ("updated_at", None), ("updated_at", False), ("updated_at", "private-date"),
                   ("updated_at", -1), ("updated_at", self.now * 1000),
                   ("updated_at", original["created_at"] - 1))
        for key, value in changes:
            with self.subTest(key=key, value=value):
                self.update_run({"status_json": json.dumps({**original, key: value})})
                payload = self.assert_error(503, "session_run_unavailable")
                self.assertNotIn("private-", json.dumps(payload))
        for key in ("object", "run_id", "status", "created_at", "updated_at"):
            with self.subTest(missing=key):
                self.update_run({"status_json": json.dumps({k: v for k, v in original.items() if k != key})})
                self.assert_error(503, "session_run_unavailable")

    def test_invalid_native_record_timestamps_are_unavailable(self):
        self.add_run("completed")
        original = {"created_at": self.now - 2, "updated_at": self.now - 1}
        for changes in ({"created_at": "private-date"}, {"created_at": -1},
                        {"created_at": float("inf")}, {"created_at": self.now * 1000},
                        {"updated_at": "private-date"}, {"updated_at": -1},
                        {"updated_at": float("inf")}, {"updated_at": self.now - 3}):
            with self.subTest(changes=changes):
                self.update_run({**original, **changes})
                self.assert_error(503, "session_run_unavailable")

    def test_malformed_or_unscoped_status_json_is_unavailable_not_no_run(self):
        self.add_run("completed")
        for raw in ("private-not-json", "null", "[]", '"private-string"', "42", "{}",
                    '{"session_id":null}', '{"session_id":42}', '{"session_id":[]}'):
            with self.subTest(raw=raw):
                self.update_run({"status_json": raw})
                self.assert_error(503, "session_run_unavailable")

    def test_empty_native_index_still_checks_busy(self):
        self.create_run_index()
        self.assert_response({"run": None, "busy": False})
        acquired_at = self.now - 60
        self.lease(acquired_at, self.now + 60)
        with patch.object(self.module.time, "time", return_value=self.now):
            self.assert_response({"run": None, "busy": True, "acquired_at": acquired_at})

    def test_all_supported_active_and_terminal_states_remain_discoverable(self):
        for status in ("queued", "running", "waiting_for_approval", "stopping",
                       "completed", "cancelled", "failed", "interrupted"):
            with self.subTest(status=status):
                if self.run_db.exists():
                    with self.connect(self.run_db) as conn:
                        conn.execute("DELETE FROM run_idempotency")
                run = self.add_run(status)
                self.assert_response({"run": run, "busy": False})
        self.lease(self.now - 60, self.now + 60)
        with patch.object(self.module.time, "time", return_value=self.now):
            self.assert_response({"run": run, "busy": True, "acquired_at": self.now - 60})

    def test_dead_native_owners_are_interrupted_without_writing_status(self):
        child = subprocess.Popen([sys.executable, "-c", "pass"], cwd=self.tmp.name)
        child.wait(timeout=5)
        for status in ("queued", "running", "waiting_for_approval", "stopping"):
            with self.subTest(status=status):
                if self.run_db.exists():
                    with self.connect(self.run_db) as conn:
                        conn.execute("DELETE FROM run_idempotency")
                run = self.add_run(status, owner_pid=child.pid, owner_started=0)
                self.assert_response({"run": {**run, "status": "interrupted"}, "busy": False})
        self.update_run({"owner_pid": 0, "owner_started": 0})
        self.lease(self.now - 60, self.now + 60)
        with patch.object(self.module.time, "time", return_value=self.now):
            self.assert_response({"run": {**run, "status": "interrupted"}, "busy": True,
                                  "acquired_at": self.now - 60})

    def test_reused_owner_pid_is_not_reported_active(self):
        run = self.add_run()
        with self.connect(self.run_db) as conn:
            started = conn.execute("SELECT owner_started FROM run_idempotency").fetchone()[0]
        self.update_run({"owner_started": started + 1})
        self.assert_response({"run": {**run, "status": "interrupted"}, "busy": False})

    def test_invalid_owner_metadata_is_unavailable(self):
        self.add_run()
        with self.connect(self.run_db) as conn:
            pid, started = conn.execute("SELECT owner_pid, owner_started FROM run_idempotency").fetchone()
        for changes in ({"owner_pid": "private-pid"}, {"owner_pid": -1}, {"owner_pid": 1.5},
                        {"owner_pid": 2147483648}, {"owner_started": "private-started"},
                        {"owner_started": -1}, {"owner_started": 1.5}):
            with self.subTest(changes=changes):
                self.update_run({"owner_pid": pid, "owner_started": started, **changes})
                self.assert_error(503, "session_run_unavailable")

    def test_owner_proc_read_errors_are_unavailable_not_interrupted(self):
        self.add_run()
        with patch.object(Path, "read_text", side_effect=PermissionError("private-proc-error")):
            self.assert_error(503, "session_run_unavailable")

    def test_owner_check_reads_only_exact_pid_stat(self):
        run = self.add_run()
        paths = []
        read_text = Path.read_text

        def observe(path, *args, **kwargs):
            paths.append(path)
            return read_text(path, *args, **kwargs)

        with patch.object(Path, "read_text", observe):
            self.assert_response({"run": run, "busy": False})
        self.assertEqual(paths, [Path(f"/proc/{os.getpid()}/stat")])

    def test_reader_uses_readonly_connections_and_bounded_queries(self):
        run = self.add_run("completed")
        observed = {"connections": [], "progress": [], "query_only": [], "sql": []}
        connect = sqlite3.connect

        class AuditedConnection(sqlite3.Connection):
            def set_progress_handler(connection, progress_handler, n):
                observed["progress"].append((callable(progress_handler), n))
                return super().set_progress_handler(progress_handler, n)

            def execute(connection, sql, parameters=()):
                observed["sql"].append(sql)
                if sql.startswith("SELECT"):
                    observed["query_only"].append(super().execute("PRAGMA query_only").fetchone()[0])
                return super().execute(sql, parameters)

        def audit(database, **kwargs):
            observed["connections"].append((database, kwargs))
            return connect(database, factory=AuditedConnection, **kwargs)

        with patch.object(self.module.sqlite3, "connect", audit):
            self.assert_response({"run": run, "busy": False})
        self.assertEqual(len(observed["connections"]), 2)
        for uri, kwargs in observed["connections"]:
            self.assertIn("mode=ro", uri)
            self.assertTrue(kwargs["uri"])
            self.assertGreater(kwargs["timeout"], 0)
            self.assertLessEqual(kwargs["timeout"], 0.25)
        self.assertEqual(len(observed["progress"]), 2)
        self.assertTrue(all(callback and 0 < interval <= 1000 for callback, interval in observed["progress"]))
        self.assertTrue(observed["query_only"])
        self.assertTrue(all(value == 1 for value in observed["query_only"]))
        queries = [sql for sql in observed["sql"] if "FROM run_idempotency" in sql]
        self.assertEqual(len(queries), 1)
        self.assertIn("ORDER BY", queries[0])
        self.assertIn("LIMIT 1", queries[0])
        self.assertFalse(any("FROM messages" in sql or "SELECT *" in sql for sql in observed["sql"]))

    def test_expensive_native_scan_is_unavailable_instead_of_unbounded_or_idle(self):
        self.create_run_index()
        raw = json.dumps({"object": "hermes.run", "session_id": "another_session",
                          "status": "completed", "created_at": self.now, "updated_at": self.now})
        with self.connect(self.run_db) as conn:
            conn.executemany("INSERT INTO run_idempotency VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                             (("fixture-scope", str(index), "fixture-fingerprint",
                               "run_" + format(index, "032x"), raw, 0, 0, 0, None, self.now, self.now)
                              for index in range(30000)))
        started = time.monotonic()
        self.assert_error(503, "session_run_unavailable")
        self.assertLess(time.monotonic() - started, 1.5)

    def test_run_index_stat_errors_are_not_treated_as_a_missing_legacy_index(self):
        self.add_run("completed")
        before = self.snapshot()
        stat = Path.stat

        def deny_index(path, *args, **kwargs):
            if path == self.run_db:
                raise PermissionError("private-index-stat-error")
            return stat(path, *args, **kwargs)

        with patch.object(Path, "stat", deny_index):
            status, payload, headers = self.request()
        self.assertEqual(status, 503, payload)
        self.assertEqual(payload["error"]["code"], "session_run_unavailable")
        self.assertNotIn("private-", json.dumps(payload))
        self.assertEqual(self.snapshot(), before)

    def test_malformed_proc_stat_is_unavailable_not_completed(self):
        self.add_run()
        with patch.object(Path, "read_text", return_value="private-invalid-proc-stat"):
            self.assert_error(503, "session_run_unavailable")

    def test_duplicate_status_keys_are_unavailable_not_completed(self):
        self.add_run("completed")
        with self.connect(self.run_db) as conn:
            raw = conn.execute("SELECT status_json FROM run_idempotency").fetchone()[0]
        raw = raw.replace('"status": "completed"', '"status": "running", "status": "completed"')
        self.update_run({"status_json": raw})
        self.assert_error(503, "session_run_unavailable")

    def test_default_home_does_not_search_other_profile_databases(self):
        profile = self.home / "profiles" / "another-profile"
        profile.mkdir(parents=True)
        with patch.object(self, "run_db", profile / "runs_idempotency.db"):
            self.add_run("completed", created_at=self.now, updated_at=self.now)
        self.assert_response({"run": None, "busy": False})
        run = self.add_run()
        self.assert_response({"run": run, "busy": False})

    def test_reader_sees_committed_wal_state_without_checkpointing_it(self):
        run = self.add_run("queued")
        with self.connect(self.run_db) as run_writer, self.connect(self.state_db) as state_writer:
            self.assertEqual(run_writer.execute("PRAGMA journal_mode = WAL").fetchone()[0], "wal")
            self.assertEqual(state_writer.execute("PRAGMA journal_mode = WAL").fetchone()[0], "wal")
            run_writer.execute("UPDATE run_idempotency SET status_json = json_set(status_json, '$.status', 'completed')")
            run_writer.commit()
            state_writer.execute("INSERT INTO session_turn_leases VALUES (?, 'fixture-worker', ?, ?)",
                                 (self.session_id, self.now - 60, self.now + 60))
            state_writer.commit()
            self.assertTrue(Path(str(self.run_db) + "-wal").exists())
            self.assertTrue(Path(str(self.state_db) + "-wal").exists())
            before = self.snapshot()
            with patch.object(self.module.time, "time", return_value=self.now):
                status, payload, headers = self.request()
            self.assertEqual(status, 200, payload)
            self.assertEqual(payload, {"run": {**run, "status": "completed"}, "busy": True,
                                       "acquired_at": self.now - 60})
            after = self.snapshot()
            self.assertEqual(set(after), set(before), "reader created native DB files")
            # SQLite mode=ro readers update advisory -shm reader marks; those are
            # not database data. Main DBs and committed WAL bytes/mtimes must match.
            # immutable=1 is NOT a fix: it ignores live WAL commits and reports stale status.
            self.assertEqual({name: value for name, value in after.items() if not name.endswith("-shm")},
                             {name: value for name, value in before.items() if not name.endswith("-shm")})
            self.assertEqual(headers["Cache-Control"], "no-store")

    def test_terminal_run_does_not_need_a_live_owner_or_proc_read(self):
        run = self.add_run("completed", owner_pid=0, owner_started=0)
        with patch.object(Path, "read_text", side_effect=AssertionError("terminal status must not read /proc")):
            self.assert_response({"run": run, "busy": False})

    def test_legacy_live_owner_without_an_incarnation_is_still_discoverable(self):
        run = self.add_run(owner_started=0)
        self.assert_response({"run": run, "busy": False})

    def test_public_and_native_timestamp_storage_need_not_be_identical(self):
        # reserve()/update_status() persist their own slightly later timestamps.
        run = self.add_run("completed")
        self.update_run({"created_at": run["created_at"] + 0.01, "updated_at": run["updated_at"] + 0.01})
        self.assert_response({"run": run, "busy": False})


if __name__ == "__main__":
    unittest.main()
