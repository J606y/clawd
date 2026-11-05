"""Tests for GET /sessions/active endpoint in hermes_panel_sidecar.

RED-GREEN TDD suite:
- Valid unexpired session_turn_leases joined with sessions
- Excludes cron/webhook sources
- Excludes known dead lease processes via _session_lease_process
- Excludes expired leases and idle sessions
- Checks schema validation / absence / incompatibility returns 503
- Checks authentication required (401/403)
- Contract: id, source, title, model, started_at, last_active, message_count, preview, pinned, archived
"""
import contextlib
import http.client
import importlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch


class ActiveSessionsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = importlib.import_module("sidecar.hermes_panel_sidecar")

        class TestHandler(cls.module.Handler):
            key = "fixture-active-sessions-key"

        cls.key = TestHandler.key
        cls.server = cls.module.ThreadingHTTPServer(("127.0.0.1", 0), TestHandler)
        cls.server.daemon_threads = True
        cls.thread = threading.Thread(
            target=cls.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        if cls.thread.is_alive():
            raise AssertionError("fixture HTTP server did not stop")

    def setUp(self):
        scratch_base = os.environ.get("PANEL_TEST_OUTPUT") or os.environ.get("TMPDIR") or "/root/hermes-workspace/p-tmp"
        os.makedirs(scratch_base, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=scratch_base)
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "hermes_home"
        self.home.mkdir()
        self.state_db = self.home / "state.db"
        self.home_patch = patch.object(self.module, "HERMES_HOME", self.home)
        self.home_patch.start()
        self.addCleanup(self.home_patch.stop)

        self.init_schema()

    @contextlib.contextmanager
    def connect(self, path=None):
        conn = sqlite3.connect(path or self.state_db)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def init_schema(self):
        with self.connect() as conn:
            conn.executescript("""
                CREATE TABLE sessions (
                    id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    title TEXT,
                    model TEXT,
                    started_at REAL NOT NULL,
                    last_activity_at REAL,
                    message_count INTEGER DEFAULT 0,
                    pinned INTEGER DEFAULT 0,
                    archived INTEGER DEFAULT 0
                );
                CREATE TABLE session_turn_leases (
                    conversation_id TEXT PRIMARY KEY,
                    holder TEXT NOT NULL,
                    acquired_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                );
            """)

    def request(self, path="/sessions/active", method="GET", key=None):
        conn = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        try:
            headers = {"X-Panel-Key": self.key if key is None else key}
            conn.request(method, path, headers=headers)
            response = conn.getresponse()
            body = response.read()
            data = json.loads(body) if body else None
            return response.status, data, dict(response.getheaders())
        finally:
            conn.close()

    def test_endpoint_route_registered(self):
        status, payload, _ = self.request()
        self.assertEqual(status, 200)
        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertIn("data", payload)
        self.assertEqual(payload["data"], [])

    def test_unauthorized_access(self):
        status, payload, _ = self.request(key="wrong-key")
        self.assertEqual(status, 401)

    def test_active_session_returned_with_full_contract(self):
        now = time.time()
        # Mock lease process check to report alive
        with patch.object(self.module, "_session_lease_process", return_value=(True, 12345)):
            with self.connect() as conn:
                conn.execute(
                    "INSERT INTO sessions (id, source, title, model, started_at, last_activity_at, message_count, pinned, archived) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    ("sess_live_1", "api_server", "Live Conversation", "gpt-4", 1000.0, 1050.0, 5, 1, 0)
                )
                conn.execute(
                    "INSERT INTO session_turn_leases (conversation_id, holder, acquired_at, expires_at) "
                    "VALUES (?, ?, ?, ?)",
                    ("sess_live_1", "pid=123:pidns=1:turn=sess_live_1:platform=api_server", now - 10, now + 60)
                )

            status, payload, _ = self.request()
            self.assertEqual(status, 200)
            self.assertIsNotNone(payload)
            assert payload is not None
            data = payload.get("data")
            self.assertEqual(len(data), 1)
            item = data[0]
            self.assertEqual(item["id"], "sess_live_1")
            self.assertEqual(item["source"], "api_server")
            self.assertEqual(item["title"], "Live Conversation")
            self.assertEqual(item["model"], "gpt-4")
            self.assertEqual(item["started_at"], 1000.0)
            self.assertEqual(item["last_active"], 1050.0)
            self.assertEqual(item["message_count"], 5)
            self.assertIsNone(item["preview"])
            self.assertIs(item["pinned"], True)
            self.assertIs(item["archived"], False)

    def test_excludes_cron_and_webhook_sources(self):
        now = time.time()
        with patch.object(self.module, "_session_lease_process", return_value=(True, 12345)):
            with self.connect() as conn:
                # Active api_server session
                conn.execute(
                    "INSERT INTO sessions (id, source, title, model, started_at, message_count) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    ("sess_api", "api_server", "API", "m1", now - 10, 2)
                )
                conn.execute(
                    "INSERT INTO session_turn_leases (conversation_id, holder, acquired_at, expires_at) "
                    "VALUES (?, ?, ?, ?)",
                    ("sess_api", "pid=123:pidns=1:turn=sess_api:platform=api_server", now - 5, now + 50)
                )
                # Active cron session
                conn.execute(
                    "INSERT INTO sessions (id, source, title, model, started_at, message_count) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    ("sess_cron", "cron", "Cron Job", "m1", now - 10, 1)
                )
                conn.execute(
                    "INSERT INTO session_turn_leases (conversation_id, holder, acquired_at, expires_at) "
                    "VALUES (?, ?, ?, ?)",
                    ("sess_cron", "pid=124:pidns=1:turn=sess_cron:platform=cli", now - 5, now + 50)
                )
                # Active webhook session
                conn.execute(
                    "INSERT INTO sessions (id, source, title, model, started_at, message_count) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    ("sess_hook", "webhook", "Webhook Call", "m1", now - 10, 1)
                )
                conn.execute(
                    "INSERT INTO session_turn_leases (conversation_id, holder, acquired_at, expires_at) "
                    "VALUES (?, ?, ?, ?)",
                    ("sess_hook", "pid=125:pidns=1:turn=sess_hook:platform=api_server", now - 5, now + 50)
                )

            status, payload, _ = self.request()
            self.assertEqual(status, 200)
            assert payload is not None
            ids = [s["id"] for s in payload["data"]]
            self.assertEqual(ids, ["sess_api"])

    def test_excludes_expired_leases(self):
        now = time.time()
        with patch.object(self.module, "_session_lease_process", return_value=(True, 12345)):
            with self.connect() as conn:
                conn.execute(
                    "INSERT INTO sessions (id, source, started_at) VALUES (?, ?, ?)",
                    ("sess_expired", "api_server", now - 100)
                )
                # Lease expired 10 seconds ago
                conn.execute(
                    "INSERT INTO session_turn_leases (conversation_id, holder, acquired_at, expires_at) "
                    "VALUES (?, ?, ?, ?)",
                    ("sess_expired", "pid=123:pidns=1:turn=sess_expired:platform=api_server", now - 70, now - 10)
                )

            status, payload, _ = self.request()
            self.assertEqual(status, 200)
            assert payload is not None
            self.assertEqual(payload["data"], [])

    def test_excludes_dead_lease_process(self):
        now = time.time()
        # _session_lease_process reports False (dead)
        with patch.object(self.module, "_session_lease_process", return_value=(False, 0)):
            with self.connect() as conn:
                conn.execute(
                    "INSERT INTO sessions (id, source, started_at) VALUES (?, ?, ?)",
                    ("sess_dead", "api_server", now - 10)
                )
                conn.execute(
                    "INSERT INTO session_turn_leases (conversation_id, holder, acquired_at, expires_at) "
                    "VALUES (?, ?, ?, ?)",
                    ("sess_dead", "pid=999:pidns=1:turn=sess_dead:platform=api_server", now - 5, now + 50)
                )

            status, payload, _ = self.request()
            self.assertEqual(status, 200)
            assert payload is not None
            self.assertEqual(payload["data"], [])

    def test_unknown_lease_process_treated_as_busy(self):
        now = time.time()
        # _session_lease_process reports None (unknown pidns or unverified container) -> busy=True
        with patch.object(self.module, "_session_lease_process", return_value=(None, 0)):
            with self.connect() as conn:
                conn.execute(
                    "INSERT INTO sessions (id, source, started_at) VALUES (?, ?, ?)",
                    ("sess_unknown", "cli", now - 10)
                )
                conn.execute(
                    "INSERT INTO session_turn_leases (conversation_id, holder, acquired_at, expires_at) "
                    "VALUES (?, ?, ?, ?)",
                    ("sess_unknown", "pid=999:pidns=2:turn=sess_unknown:platform=cli", now - 5, now + 50)
                )

            status, payload, _ = self.request()
            self.assertEqual(status, 200)
            assert payload is not None
            self.assertEqual(len(payload["data"]), 1)
            self.assertEqual(payload["data"][0]["id"], "sess_unknown")

    def test_excludes_sessions_without_leases(self):
        now = time.time()
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO sessions (id, source, started_at) VALUES (?, ?, ?)",
                ("sess_idle", "api_server", now - 10)
            )

        status, payload, _ = self.request()
        self.assertEqual(status, 200)
        assert payload is not None
        self.assertEqual(payload["data"], [])

    def test_excludes_active_subagents_but_keeps_parent_conversation(self):
        now = time.time()
        with self.connect() as conn:
            for sid, source in (("sess_parent", "api_server"), ("sess_child", "subagent")):
                conn.execute("INSERT INTO sessions (id, source, started_at) VALUES (?, ?, ?)", (sid, source, now - 10))
                conn.execute("INSERT INTO session_turn_leases VALUES (?, ?, ?, ?)", (sid, "fixture-holder", now - 5, now + 60))
        with patch.object(self.module, "_session_lease_process", return_value=(True, 1)):
            status, payload, _ = self.request()
        self.assertEqual(status, 200)
        assert payload is not None
        self.assertEqual([s["id"] for s in payload["data"]], ["sess_parent"])

    def test_missing_database_returns_503(self):
        self.state_db.unlink()
        status, payload, _ = self.request()
        self.assertEqual(status, 503)
        assert payload is not None
        self.assertEqual(payload["error"]["code"], "session_active_unavailable")

    def test_incompatible_schema_returns_503(self):
        # Drop table sessions and recreate with missing required column
        with self.connect() as conn:
            conn.execute("DROP TABLE sessions")
            conn.execute("CREATE TABLE sessions (wrong_col TEXT PRIMARY KEY)")

        status, payload, _ = self.request()
        self.assertEqual(status, 503)
        assert payload is not None
        self.assertEqual(payload["error"]["code"], "session_active_unavailable")

    def test_invalid_lease_epoch_returns_503(self):
        # Invalid lease timestamps (e.g. acquired_at > expires_at or negative or string)
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO sessions (id, source, started_at) VALUES (?, ?, ?)",
                ("sess_corrupt", "api_server", 1000.0)
            )
            conn.execute(
                "INSERT INTO session_turn_leases (conversation_id, holder, acquired_at, expires_at) "
                "VALUES (?, ?, ?, ?)",
                ("sess_corrupt", "holder", 2000.0, 1000.0)  # acquired > expires
            )

        status, payload, _ = self.request()
        self.assertEqual(status, 503)
        assert payload is not None
        self.assertEqual(payload["error"]["code"], "session_active_unavailable")


if __name__ == "__main__":
    unittest.main()
