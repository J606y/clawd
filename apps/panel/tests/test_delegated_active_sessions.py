"""Tests for delegated subagent active sessions discovery in hermes_panel_sidecar.

RED-GREEN TDD suite:
- Parent session with no direct lease appears in active sessions if an active subagent exists
- Subagents themselves (source == 'subagent') are excluded from active list
- Excludes cron/webhook parents and subagents
- Dead PID or expired lease of subagent does not keep parent active
- Multiple active subagents under the same parent deduplicate to one parent entry
- Nested subagents (child spawning subagent) trace up to the non-subagent root parent
- Incompatible schema / DB error returns 503 session_active_unavailable
- Closed / ended / failed subagents do not keep parent active
- Parent with own active lease continues to work normally
- Only read-only queries against DB, bounded schema validation
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


class DelegatedActiveSessionsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = importlib.import_module("sidecar.hermes_panel_sidecar")

        class TestHandler(cls.module.Handler):
            key = "fixture-delegated-active-sessions-key"

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
                    parent_session_id TEXT,
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

    def test_parent_without_lease_included_when_subagent_is_active(self):
        """Case 1: Parent has no turn lease, but a subagent of it has an active lease & live process."""
        now = time.time()
        with self.connect() as conn:
            # Parent session (e.g. api_server) with no lease
            conn.execute(
                "INSERT INTO sessions (id, source, title, model, started_at, last_activity_at, message_count) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("sess_parent_1", "api_server", "Parent Conversation", "gpt-4", now - 100, now - 10, 10)
            )
            # Child subagent session
            conn.execute(
                "INSERT INTO sessions (id, source, title, model, parent_session_id, started_at, last_activity_at, message_count) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("subagent_child_1", "subagent", "Subagent Task", "gpt-4", "sess_parent_1", now - 50, now - 5, 4)
            )
            # Lease belongs to the subagent child
            conn.execute(
                "INSERT INTO session_turn_leases (conversation_id, holder, acquired_at, expires_at) "
                "VALUES (?, ?, ?, ?)",
                ("subagent_child_1", "pid=123:pidns=1:turn=subagent_child_1:platform=subagent", now - 5, now + 60)
            )

        with patch.object(self.module, "_session_lease_process", return_value=(True, 12345)):
            status, payload, _ = self.request()
            self.assertEqual(status, 200)
            self.assertIsNotNone(payload)
            assert payload is not None
            items = payload.get("data", [])
            # Must contain parent session, and MUST NOT contain the subagent child
            item_ids = [s["id"] for s in items]
            self.assertEqual(item_ids, ["sess_parent_1"])
            self.assertEqual(items[0]["title"], "Parent Conversation")
            self.assertEqual(items[0]["source"], "api_server")

    def test_dead_subagent_process_or_expired_lease_does_not_include_parent(self):
        """Case 2: Dead child process or expired child lease leaves parent idle/absent."""
        now = time.time()
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO sessions (id, source, title, started_at) VALUES (?, ?, ?, ?)",
                ("sess_parent_dead", "api_server", "Parent", now - 100)
            )
            conn.execute(
                "INSERT INTO sessions (id, source, title, parent_session_id, started_at) VALUES (?, ?, ?, ?, ?)",
                ("subagent_dead", "subagent", "Child Dead", "sess_parent_dead", now - 50)
            )
            # Expired lease
            conn.execute(
                "INSERT INTO session_turn_leases VALUES (?, ?, ?, ?)",
                ("subagent_dead", "pid=123:pidns=1:turn=subagent_dead:platform=subagent", now - 60, now - 10)
            )

        with patch.object(self.module, "_session_lease_process", return_value=(True, 12345)):
            status, payload, _ = self.request()
            self.assertEqual(status, 200)
            self.assertIsNotNone(payload)
            assert payload is not None
            self.assertEqual(payload["data"], [])

        # Now test active unexpired lease but dead process (alive=False)
        with self.connect() as conn:
            conn.execute(
                "UPDATE session_turn_leases SET expires_at = ? WHERE conversation_id = ?",
                (now + 60, "subagent_dead")
            )
        with patch.object(self.module, "_session_lease_process", return_value=(False, 0)):
            status, payload, _ = self.request()
            self.assertEqual(status, 200)
            self.assertIsNotNone(payload)
            assert payload is not None
            self.assertEqual(payload["data"], [])

    def test_multiple_subagents_deduplicate_to_single_parent(self):
        """Case 3: Multiple running subagents under the same parent produce only one parent entry."""
        now = time.time()
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO sessions (id, source, title, started_at) VALUES (?, ?, ?, ?)",
                ("sess_parent_multi", "api_server", "Parent Multi", now - 100)
            )
            for i in range(3):
                cid = f"subagent_multi_{i}"
                conn.execute(
                    "INSERT INTO sessions (id, source, title, parent_session_id, started_at) VALUES (?, ?, ?, ?, ?)",
                    (cid, "subagent", f"Child {i}", "sess_parent_multi", now - 40 + i)
                )
                conn.execute(
                    "INSERT INTO session_turn_leases VALUES (?, ?, ?, ?)",
                    (cid, f"pid=12{i}:pidns=1:turn={cid}:platform=subagent", now - 10, now + 60)
                )

        with patch.object(self.module, "_session_lease_process", return_value=(True, 12345)):
            status, payload, _ = self.request()
            self.assertEqual(status, 200)
            self.assertIsNotNone(payload)
            assert payload is not None
            items = payload.get("data", [])
            self.assertEqual([s["id"] for s in items], ["sess_parent_multi"])

    def test_cron_or_webhook_parent_still_excluded(self):
        """Case 4: If parent is cron or webhook, even with active subagents it must not appear."""
        now = time.time()
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO sessions (id, source, title, started_at) VALUES (?, ?, ?, ?)",
                ("sess_cron_parent", "cron", "Cron Parent", now - 100)
            )
            conn.execute(
                "INSERT INTO sessions (id, source, title, parent_session_id, started_at) VALUES (?, ?, ?, ?, ?)",
                ("subagent_cron_child", "subagent", "Child", "sess_cron_parent", now - 40)
            )
            conn.execute(
                "INSERT INTO session_turn_leases VALUES (?, ?, ?, ?)",
                ("subagent_cron_child", "pid=123:pidns=1:turn=sub:platform=subagent", now - 10, now + 60)
            )

        with patch.object(self.module, "_session_lease_process", return_value=(True, 12345)):
            status, payload, _ = self.request()
            self.assertEqual(status, 200)
            self.assertIsNotNone(payload)
            assert payload is not None
            self.assertEqual(payload["data"], [])

    def test_nested_subagent_delegation_finds_root_parent(self):
        """Case 5: Nested subagent (parent -> child subagent -> grandchild subagent). Root parent should be returned."""
        now = time.time()
        with self.connect() as conn:
            # Root parent: web conversation
            conn.execute(
                "INSERT INTO sessions (id, source, title, started_at) VALUES (?, ?, ?, ?)",
                ("sess_root", "api_server", "Root Session", now - 200)
            )
            # Level 1 child subagent (source='subagent', parent_session_id='sess_root')
            conn.execute(
                "INSERT INTO sessions (id, source, title, parent_session_id, started_at) VALUES (?, ?, ?, ?, ?)",
                ("subagent_l1", "subagent", "Subagent L1", "sess_root", now - 150)
            )
            # Level 2 grandchild subagent (source='subagent', parent_session_id='subagent_l1')
            conn.execute(
                "INSERT INTO sessions (id, source, title, parent_session_id, started_at) VALUES (?, ?, ?, ?, ?)",
                ("subagent_l2", "subagent", "Subagent L2", "subagent_l1", now - 100)
            )
            # Only grandchild holds an active lease
            conn.execute(
                "INSERT INTO session_turn_leases VALUES (?, ?, ?, ?)",
                ("subagent_l2", "pid=456:pidns=1:turn=subagent_l2:platform=subagent", now - 10, now + 60)
            )

        with patch.object(self.module, "_session_lease_process", return_value=(True, 12345)):
            status, payload, _ = self.request()
            self.assertEqual(status, 200)
            self.assertIsNotNone(payload)
            assert payload is not None
            items = payload.get("data", [])
            self.assertEqual([s["id"] for s in items], ["sess_root"])
            self.assertEqual(items[0]["title"], "Root Session")

    def test_parent_with_own_active_lease_and_subagent_does_not_duplicate(self):
        """Case 6: If parent itself also holds an active lease while subagent runs, only 1 parent entry is returned."""
        now = time.time()
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO sessions (id, source, title, started_at) VALUES (?, ?, ?, ?)",
                ("sess_both", "api_server", "Both Active", now - 100)
            )
            conn.execute(
                "INSERT INTO session_turn_leases VALUES (?, ?, ?, ?)",
                ("sess_both", "pid=100:pidns=1:turn=sess_both:platform=api_server", now - 10, now + 60)
            )
            conn.execute(
                "INSERT INTO sessions (id, source, title, parent_session_id, started_at) VALUES (?, ?, ?, ?, ?)",
                ("subagent_both", "subagent", "Subagent", "sess_both", now - 50)
            )
            conn.execute(
                "INSERT INTO session_turn_leases VALUES (?, ?, ?, ?)",
                ("subagent_both", "pid=101:pidns=1:turn=subagent_both:platform=subagent", now - 10, now + 60)
            )

        with patch.object(self.module, "_session_lease_process", return_value=(True, 12345)):
            status, payload, _ = self.request()
            self.assertEqual(status, 200)
            self.assertIsNotNone(payload)
            assert payload is not None
            items = payload.get("data", [])
            self.assertEqual([s["id"] for s in items], ["sess_both"])

    def test_background_ledger_without_any_lease_keeps_exact_parent_live(self):
        """Pinned async producer writes its own owner identity, not a child turn lease."""
        now = time.time()
        owner_started = int(Path('/proc/self/stat').read_text().rpartition(')')[2].split()[19])
        with self.connect() as conn:
            conn.execute("ALTER TABLE sessions ADD COLUMN ended_at REAL")
            conn.executescript("""
                CREATE TABLE async_delegations (
                    delegation_id TEXT PRIMARY KEY, origin_session TEXT,
                    origin_session_id TEXT, parent_session_id TEXT, state TEXT,
                    dispatched_at REAL, completed_at REAL, owner_pid INTEGER,
                    owner_started_at INTEGER
                );
            """)
            conn.execute("INSERT INTO sessions (id, source, started_at) VALUES (?, 'api_server', ?)",
                         ('background_parent', now - 100))
            conn.execute("INSERT INTO sessions (id, source, parent_session_id, started_at) "
                         "VALUES (?, 'subagent', ?, ?)", ('background_child', 'background_parent', now - 50))
            # Deliberately conflicting routing/fallback IDs: origin_session_id is authoritative.
            conn.execute("INSERT INTO async_delegations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         ('deleg_fixture', 'session:api_server:not-a-session-id', 'background_parent',
                          'unrelated_fallback', 'running', now - 50, None, os.getpid(), owner_started))
        for state in ('running', 'stalling', 'finalizing'):
            with self.connect() as conn:
                conn.execute("UPDATE async_delegations SET state = ?", (state,))
            status, payload, _ = self.request()
            self.assertEqual(status, 200)
            assert payload is not None
            self.assertEqual([item['id'] for item in payload['data']], ['background_parent'], state)
        for state in ('completed', 'error', 'interrupted', 'stalled'):
            with self.connect() as conn:
                conn.execute("UPDATE async_delegations SET state = ?, completed_at = ?", (state, now))
            status, payload, _ = self.request()
            self.assertEqual(status, 200)
            assert payload is not None
            self.assertEqual(payload['data'], [], state)
        # A recycled PID cannot keep an old dispatch alive.
        with self.connect() as conn:
            conn.execute("UPDATE async_delegations SET state = 'running', completed_at = NULL, owner_started_at = ?",
                         (owner_started + 1,))
        status, payload, _ = self.request()
        self.assertEqual(status, 200)
        assert payload is not None
        self.assertEqual(payload['data'], [])
        # Origin may be a nested delegated session; render only the root conversation.
        with self.connect() as conn:
            conn.execute("UPDATE async_delegations SET owner_started_at = ?, origin_session_id = 'background_child'",
                         (owner_started,))
        status, payload, _ = self.request()
        self.assertEqual(status, 200)
        assert payload is not None
        self.assertEqual([item['id'] for item in payload['data']], ['background_parent'])
        # An explicitly ended parent must not be resurrected by a remaining ledger row.
        with self.connect() as conn:
            conn.execute("UPDATE sessions SET ended_at = ? WHERE id = 'background_parent'", (now,))
        status, payload, _ = self.request()
        self.assertEqual(status, 200)
        assert payload is not None
        self.assertEqual(payload['data'], [])


if __name__ == "__main__":
    unittest.main()
