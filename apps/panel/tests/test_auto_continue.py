"""服务端自动续跑：临时 state.db + 本地假 Hermes API。不连真实数据库、不读真实密钥、不发模型请求。"""
import contextlib
import importlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

SCHEMA = """
CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT, model TEXT, parent_session_id TEXT, ended_at REAL,
  started_at REAL NOT NULL, last_activity_at REAL);
CREATE INDEX idx_sessions_source_id ON sessions(source, id);
CREATE INDEX idx_sessions_parent ON sessions(parent_session_id);
CREATE INDEX idx_sessions_effective_activity ON sessions(COALESCE(last_activity_at, started_at) DESC, started_at DESC);
CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, display_kind TEXT,
  display_metadata TEXT, timestamp REAL, active INTEGER DEFAULT 1, display_order INTEGER);
CREATE INDEX idx_messages_session ON messages(session_id, timestamp);
CREATE INDEX idx_messages_session_active ON messages(session_id, active, timestamp);
CREATE TABLE session_turn_leases (conversation_id TEXT PRIMARY KEY, holder TEXT, acquired_at REAL, expires_at REAL);
CREATE TABLE async_delegations (delegation_id TEXT PRIMARY KEY, origin_session TEXT NOT NULL,
  origin_ui_session_id TEXT NOT NULL DEFAULT '', parent_session_id TEXT, state TEXT NOT NULL,
  dispatched_at REAL NOT NULL, completed_at REAL, updated_at REAL NOT NULL, event_json TEXT, result_json TEXT,
  delivery_state TEXT NOT NULL DEFAULT 'pending', delivery_attempts INTEGER NOT NULL DEFAULT 0,
  delivered_at REAL, owner_pid INTEGER, owner_started_at INTEGER, task_json TEXT,
  delivery_claim TEXT, delivery_claimed_at REAL, origin_session_id TEXT NOT NULL DEFAULT '');
CREATE INDEX idx_async_delegations_delivery ON async_delegations(delivery_state, completed_at);
"""


class FakeHermes(BaseHTTPRequestHandler):
    """记录 /v1/runs 请求；按 cls.status 回应。"""
    posts: list = []
    status = 202
    options = {"providers": [{"slug": "cpa", "authenticated": True, "models": ["gpt-6.1-sol"]}]}

    def log_message(self, *args):
        pass

    def _send(self, code, body):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self._send(200, self.options if self.path == "/api/model/options" else {})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeHermes.posts.append({"path": self.path, "key": self.headers.get("Idempotency-Key"),
                                 "auth": self.headers.get("Authorization"), "body": body})
        self._send(FakeHermes.status, {"run_id": "run_" + "0" * 32, "status": "started"} if FakeHermes.status < 300 else {})


class AutoContinueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = importlib.import_module("sidecar.hermes_panel_sidecar")
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeHermes)
        cls.thread = threading.Thread(target=cls.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        conn = sqlite3.connect(self.home / "state.db")
        conn.executescript(SCHEMA)
        conn.commit()
        conn.close()
        # Model the pinned producer's durable native admission store, even empty.
        self.native_admission(None, None)
        FakeHermes.posts = []
        FakeHermes.status = 202
        self.m._continue_done.clear()
        self.m._continue_failures.clear()
        self.m._continue_warned.clear()
        self.m._continue_route_cache.update(at=0.0, map={})
        self.m._continue_watch.clear()
        self.m._continue_sweep.update(home=None, at=0.0)
        self.patches = [patch.object(self.m, "HERMES_HOME", self.home),
                        patch.object(self.m, "CONTINUE_API", f"http://127.0.0.1:{self.server.server_address[1]}")]
        for p in self.patches:
            p.start()
        self.order = 0

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def add(self, sid, role, content, kind=None, meta=None, ago=60, active=1):
        self.order += 1
        conn = sqlite3.connect(self.home / "state.db")
        conn.execute("INSERT OR IGNORE INTO sessions (id, source, model, started_at) VALUES (?, 'api_server', 'gpt-6.1-sol', ?)", (sid, time.time() - 600))
        cur = conn.execute("INSERT INTO messages (session_id, role, content, display_kind, display_metadata, timestamp, active, display_order) "
                           "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (sid, role, content, kind, meta, time.time() - ago, active, self.order))
        conn.commit()
        conn.close()
        return cur.lastrowid

    def waiting_session(self, sid="api_1_aaaa"):
        self.add(sid, "user", "查一下这个问题", ago=300)
        self.add(sid, "assistant", "", ago=290)
        self.add(sid, "tool", json.dumps({"status": "dispatched", "mode": "background", "delegation_id": "deleg_ab12"}), ago=290)
        self.add(sid, "assistant", "已派子代理核对，等结果", ago=280)
        return self.add(sid, "user", "[ASYNC DELEGATION BATCH COMPLETE — deleg_ab12]\n结果……", kind="async_delegation_complete", ago=10)

    def pending_parent(self, sid="api_batch_parent"):
        self.add(sid, "user", "synthetic task", ago=300)
        self.add(sid, "assistant", "synthetic waiting", ago=280)
        return sid

    def batch(self, sid, did="deleg_batch", state="completed", ago=20, results=None, indexes=None):
        # Public producer tools/async_delegation.py _persist_dispatch/_push_completion_event.
        indexes = [0, 1] if indexes is None else indexes
        results = ([{"task_index": i, "status": "completed", "summary": f"synthetic result {did}/{i}"}
                    for i in indexes] if results is None else results)
        task = {"is_batch": True, "goals": ["synthetic A", "synthetic B"], "task_indexes": indexes}
        completed = time.time() - ago
        dispatched = time.time() - 290
        event = {"type": "async_delegation", "delegation_id": did, "origin_session_id": sid,
                 "parent_session_id": sid, "is_batch": True, "status": state, "goals": task["goals"],
                 "dispatched_at": dispatched, "completed_at": completed, "results": results}
        with contextlib.closing(sqlite3.connect(self.home / "state.db")) as conn, conn:
            conn.execute("INSERT INTO async_delegations (delegation_id, origin_session, origin_session_id, parent_session_id, "
                         "state, dispatched_at, completed_at, updated_at, event_json, result_json, task_json) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (did, "session:api_server:synthetic", sid, sid, state, dispatched,
                          completed if state != "running" else None, completed,
                          json.dumps(event), json.dumps({"results": results}), json.dumps(task)))
        return event

    def test_running_batch_with_one_ended_child_does_not_continue(self):
        sid = self.pending_parent()
        self.batch(sid, state="running", results=[{"task_index": 0, "status": "completed", "summary": "synthetic A only"}])
        with contextlib.closing(sqlite3.connect(self.home / "state.db")) as conn, conn:
            conn.execute("INSERT INTO sessions (id, source, parent_session_id, started_at, ended_at) VALUES "
                         "('synthetic_sub_A', 'subagent', ?, ?, ?)", (sid, time.time() - 290, time.time() - 20))
            conn.execute("INSERT INTO sessions (id, source, parent_session_id, started_at) VALUES "
                         "('synthetic_sub_B', 'subagent', ?, ?)", (sid, time.time() - 290))
        self.m._continue_tick("fixture-key")
        self.assertEqual(FakeHermes.posts, [], "one ended child does not establish batch completion")

    def test_old_parent_lost_batch_supplies_all_real_results(self):
        sid = self.pending_parent()
        event = self.batch(sid)
        with contextlib.closing(sqlite3.connect(self.home / "state.db")) as conn, conn:
            conn.execute("UPDATE sessions SET started_at = ?", (time.time() - 86400,))
        self.m._continue_tick("fixture-key")
        self.assertEqual(len(FakeHermes.posts), 1)
        text = FakeHermes.posts[0]["body"]["input"]
        self.assertIn("deleg_batch", text)
        for result in event["results"]:
            self.assertIn(result["summary"], text)
        self.assertNotIn("结果见上方的完成通知", text, "no fabricated delivery row")

    def test_old_A_notification_cannot_hide_lost_B(self):
        sid = self.pending_parent()
        self.batch(sid, "deleg_B")
        self.add(sid, "user", "synthetic A result", kind="async_delegation_complete",
                 meta=json.dumps({"delegation_id": "deleg_A"}), ago=30)
        self.add(sid, "assistant", "synthetic A handled; waiting B", ago=25)
        self.m._continue_tick("fixture-key")
        self.assertEqual(len(FakeHermes.posts), 1)
        self.assertIn("deleg_B", FakeHermes.posts[0]["body"]["input"])

    def test_A_reply_after_B_completed_recovers_only_undelivered_B(self):
        sid = self.pending_parent()
        self.batch(sid, "deleg_B", ago=20)
        self.add(sid, "user", "[ASYNC DELEGATION BATCH COMPLETE — deleg_A]\nsynthetic A only",
                 kind="async_delegation_complete", meta=json.dumps({"delegation_id": "deleg_A"}), ago=30)
        self.add(sid, "assistant", "synthetic A handled; waiting B", ago=5)
        self.m._continue_tick("fixture-key")
        self.assertEqual(len(FakeHermes.posts), 1)
        text = FakeHermes.posts[0]["body"]["input"]
        self.assertIn("synthetic result deleg_B/0", text)
        self.assertNotIn("synthetic A only", text)
        self.m._continue_tick("fixture-key")
        self.assertEqual(len(FakeHermes.posts), 1)

    def test_terminal_batch_missing_one_result_is_not_recovered(self):
        sid = self.pending_parent()
        self.batch(sid, results=[{"task_index": 0, "status": "completed", "summary": "synthetic A only"}])
        self.m._continue_tick("fixture-key")
        self.assertEqual(FakeHermes.posts, [], "terminal state alone cannot prove the whole result set")

    def test_nonterminal_or_empty_result_cannot_wake_parent(self):
        sid = self.pending_parent()
        self.batch(sid, results=[{"task_index": 0, "status": "running", "summary": "synthetic incomplete"},
                                 {"task_index": 1, "status": "completed", "summary": ""}])
        self.m._continue_tick("fixture-key")
        self.assertEqual(FakeHermes.posts, [])

    def test_fallback_then_late_delivery_reuses_batch_identity(self):
        sid = self.pending_parent()
        self.batch(sid)
        self.m._continue_tick("fixture-key")
        self.assertEqual(len(FakeHermes.posts), 1)
        self.add(sid, "user", "[ASYNC DELEGATION BATCH COMPLETE — deleg_batch]\nsynthetic results",
                 kind="async_delegation_complete", meta=json.dumps({"delegation_id": "deleg_batch"}), ago=5)
        self.m._continue_tick("fixture-key")
        self.assertEqual(len(FakeHermes.posts), 1, "late normal delivery is the same completed batch")

    def test_multiple_lost_batches_are_delivered_once_with_all_results(self):
        sid = self.pending_parent()
        self.batch(sid, "deleg_A", ago=30)
        self.batch(sid, "deleg_B", ago=20)
        self.m._continue_tick("fixture-key")
        text = FakeHermes.posts[0]["body"]["input"]
        self.assertIn("synthetic result deleg_A/0", text)
        self.assertIn("synthetic result deleg_B/0", text, "another batch cannot be stranded behind the continuation")
        self.m._continue_tick("fixture-key")
        self.assertEqual(len(FakeHermes.posts), 1)

    def test_healthy_delivery_claim_waits_before_fallback(self):
        sid = self.pending_parent()
        self.batch(sid)
        with contextlib.closing(sqlite3.connect(self.home / "state.db")) as conn, conn:
            conn.execute("UPDATE async_delegations SET delivery_claim = 'synthetic-consumer', delivery_claimed_at = ?", (time.time() - 5,))
        self.m._continue_tick("fixture-key")
        self.assertEqual(FakeHermes.posts, [], "do not compete with an admitted delivery consumer")

    def test_interim_failure_notice_cannot_consume_final_batch(self):
        sid = self.pending_parent()
        self.batch(sid, state="running")
        self.add(sid, "user", "[ASYNC DELEGATION TASK FAILED — deleg_batch, task 1/2]",
                 kind="async_delegation_complete", meta=json.dumps({"delegation_id": "deleg_batch", "delivery_notice": "task_failure:0"}), ago=10)
        self.m._continue_tick("fixture-key")
        self.assertEqual(FakeHermes.posts, [], "interim failure is not the batch terminal event")

    def test_delivered_A_and_lost_B_share_one_turn_without_losing_B(self):
        sid = self.pending_parent()
        self.batch(sid, "deleg_B")
        self.add(sid, "user", "[ASYNC DELEGATION BATCH COMPLETE — deleg_A]\nsynthetic A",
                 kind="async_delegation_complete", meta=json.dumps({"delegation_id": "deleg_A"}), ago=10)
        self.m._continue_tick("fixture-key")
        self.assertEqual(len(FakeHermes.posts), 1)
        self.assertIn("synthetic result deleg_B/1", FakeHermes.posts[0]["body"]["input"])
        self.m._continue_tick("fixture-key")
        self.assertEqual(len(FakeHermes.posts), 1)

    def test_user_takes_over_before_completion_disables_fallback(self):
        sid = self.pending_parent()
        self.batch(sid)
        self.add(sid, "user", "synthetic takeover before the child ended", ago=40)
        self.add(sid, "assistant", "synthetic takeover answered", ago=35)
        self.m._continue_tick("fixture-key")
        self.assertEqual(FakeHermes.posts, [])

    def test_mismatched_event_owner_is_not_recovered(self):
        sid = self.pending_parent()
        event = self.batch(sid)
        event["origin_session_id"] = "api_wrong_parent"
        with contextlib.closing(sqlite3.connect(self.home / "state.db")) as conn, conn:
            conn.execute("UPDATE async_delegations SET event_json = ?", (json.dumps(event),))
        self.m._continue_tick("fixture-key")
        self.assertEqual(FakeHermes.posts, [])

    def test_invalid_event_times_are_not_completion_evidence(self):
        sid = self.pending_parent()
        event = self.batch(sid)
        event["completed_at"] = event["dispatched_at"] - 1
        with contextlib.closing(sqlite3.connect(self.home / "state.db")) as conn, conn:
            conn.execute("UPDATE async_delegations SET event_json = ?", (json.dumps(event),))
        self.m._continue_tick("fixture-key")
        self.assertEqual(FakeHermes.posts, [])

    def test_all_normal_batches_keep_their_idempotent_receipts(self):
        sid = self.pending_parent()
        for did in ("deleg_A", "deleg_B"):
            self.add(sid, "user", f"[ASYNC DELEGATION BATCH COMPLETE — {did}]\nsynthetic result",
                     kind="async_delegation_complete", meta=json.dumps({"delegation_id": did}), ago=10)
        self.m._continue_tick("fixture-key")
        self.assertEqual(len(FakeHermes.posts), 1)
        for did in ("deleg_A", "deleg_B"):
            self.assertIn(f"hp-continue-batch-{sid}-{did}", self.m._continue_done)

    def test_exact_legacy_envelope_prevents_recovery_of_handled_batch(self):
        sid = self.pending_parent()
        self.batch(sid)
        self.add(sid, "user", "[ASYNC DELEGATION BATCH COMPLETE — deleg_batch]\nsynthetic result",
                 kind="async_delegation_complete", ago=30)
        self.add(sid, "assistant", "synthetic batch handled", ago=25)
        self.m._continue_tick("fixture-key")
        self.assertEqual(FakeHermes.posts, [])

    def test_hidden_suppressed_delivery_is_not_recovered(self):
        sid = self.pending_parent()
        self.batch(sid)
        self.add(sid, "user", "synthetic suppressed result", kind="hidden",
                 meta=json.dumps({"delegation_id": "deleg_batch", "presentation_suppressed": True}), ago=10)
        self.m._continue_tick("fixture-key")
        self.assertEqual(FakeHermes.posts, [])

    def test_interim_failure_does_not_hide_missing_final_results(self):
        sid = self.pending_parent()
        self.batch(sid)
        self.add(sid, "user", "[ASYNC DELEGATION TASK FAILED — deleg_batch, task 1/2]",
                 kind="async_delegation_complete", meta=json.dumps({"delegation_id": "deleg_batch", "delivery_notice": "task_failure:0"}), ago=30)
        self.m._continue_tick("fixture-key")
        self.assertEqual(len(FakeHermes.posts), 1)
        self.assertIn("synthetic result deleg_batch/1", FakeHermes.posts[0]["body"]["input"])

    def test_closed_parent_does_not_restart_for_late_notification(self):
        sid = "api_1_aaaa"
        self.waiting_session(sid)
        with contextlib.closing(sqlite3.connect(self.home / "state.db")) as conn, conn:
            conn.execute("UPDATE sessions SET ended_at = ?", (time.time() - 15,))
        self.m._continue_tick("fixture-key")
        self.assertEqual(FakeHermes.posts, [])

    def test_answered_followup_before_notice_still_wakes(self):
        """派发后的人话若已被助手答完，完成通知落在该回复之后，仍要唤醒。

        用 dispatched_at 之后出现过任何人话来否决，会把这种已落库通知永久跳过。
        人写在通知之后仍不插话，见 test_human_message_after_completion_owns_the_turn。
        通知缺失时的接管仍不恢复，见 test_user_takes_over_before_completion_disables_fallback。
        """
        sid = self.pending_parent()
        self.batch(sid)
        self.add(sid, "user", "synthetic follow-up while waiting", ago=40)
        self.add(sid, "assistant", "synthetic follow-up answered", ago=35)
        self.add(sid, "user", "[ASYNC DELEGATION BATCH COMPLETE — deleg_batch]\nsynthetic result",
                 kind="async_delegation_complete", meta=json.dumps({"delegation_id": "deleg_batch"}), ago=10)
        self.m._continue_tick("fixture-key")
        self.assertEqual(len(FakeHermes.posts), 1, "answered follow-up must not bury a later completion notice")
        self.assertEqual(FakeHermes.posts[0]["body"]["session_id"], sid)
        self.assertIn("deleg_batch", FakeHermes.posts[0]["key"])

    def test_restart_memory_loss_does_not_reconsume_old_batches_or_drop_new_B(self):
        sid = self.pending_parent()
        self.batch(sid, "deleg_A", ago=30)
        self.m._continue_tick("fixture-key")
        self.m._continue_done.clear()  # isolated process restart simulation
        self.batch(sid, "deleg_B", ago=20)
        self.m._continue_tick("fixture-key")
        self.assertEqual(len(FakeHermes.posts), 2)
        self.assertNotIn("synthetic result deleg_A/0", FakeHermes.posts[-1]["body"]["input"])
        self.assertIn("synthetic result deleg_B/0", FakeHermes.posts[-1]["body"]["input"])
        self.assertNotEqual(FakeHermes.posts[0]["key"], FakeHermes.posts[1]["key"])

    def test_retry_request_is_frozen_when_another_batch_arrives(self):
        sid = self.pending_parent()
        self.batch(sid, "deleg_A", ago=30)
        FakeHermes.status = 500
        self.m._continue_tick("fixture-key")
        first = FakeHermes.posts[0]
        self.batch(sid, "deleg_B", ago=20)
        self.m._continue_tick("fixture-key")
        self.assertEqual(FakeHermes.posts[1]["key"], first["key"])
        self.assertEqual(FakeHermes.posts[1]["body"], first["body"], "same idempotency key must never acquire another batch payload")

    def test_pending_group_is_not_replayed_when_one_batch_is_taken_over(self):
        sid = self.pending_parent()
        event_a = self.batch(sid, "deleg_A", ago=30)
        self.batch(sid, "deleg_B", ago=20)
        event_a["dispatched_at"] = time.time() - 40
        with contextlib.closing(sqlite3.connect(self.home / "state.db")) as conn, conn:
            conn.execute("UPDATE async_delegations SET dispatched_at = ?, event_json = ? WHERE delegation_id = 'deleg_A'",
                         (event_a["dispatched_at"], json.dumps(event_a)))
        FakeHermes.status = 500
        self.m._continue_tick("fixture-key")
        first = FakeHermes.posts[0]
        self.add(sid, "user", "synthetic delayed takeover of B before A dispatched", ago=50)
        self.m._continue_tick("fixture-key")
        last = FakeHermes.posts[-1]
        self.assertNotIn("synthetic result deleg_B/0", last["body"]["input"])
        self.assertNotEqual(first["key"], last["key"], "a reduced batch set cannot mutate the saved request")

    def test_group_500_then_202_then_two_windows_leaves_zero_failure_keys(self):
        sid = self.pending_parent()
        self.batch(sid, "deleg_A", ago=30)
        self.batch(sid, "deleg_B", ago=20)
        FakeHermes.status = 500
        self.m._continue_tick("fixture-key")
        request_key = FakeHermes.posts[0]["key"]
        self.assertTrue(request_key.startswith("hp-continue-set-"))
        self.assertEqual(self.m._continue_failures[request_key], 1)
        FakeHermes.status = 202
        self.m._continue_tick("fixture-key")
        self.assertNotIn(request_key, self.m._continue_failures, "success clears the actual group request key")
        with patch.object(self.m.time, "time", return_value=time.time() + self.m.CONTINUE_WINDOW_S * 2 + 30):
            self.m._continue_tick("fixture-key")
        self.assertEqual(self.m._continue_done, {})
        self.assertEqual(self.m._continue_failures, {})
        with contextlib.closing(sqlite3.connect(self.home / "panel_auto_continue.db")) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM receipts").fetchone()[0], 0)

    def test_invalidated_group_request_clears_its_actual_failure_key(self):
        sid = self.pending_parent()
        event_a = self.batch(sid, "deleg_A", ago=30)
        self.batch(sid, "deleg_B", ago=20)
        event_a["dispatched_at"] = time.time() - 40
        with contextlib.closing(sqlite3.connect(self.home / "state.db")) as conn, conn:
            conn.execute("UPDATE async_delegations SET dispatched_at = ?, event_json = ? WHERE delegation_id = 'deleg_A'",
                         (event_a["dispatched_at"], json.dumps(event_a)))
        FakeHermes.status = 500
        self.m._continue_tick("fixture-key")
        old_key = FakeHermes.posts[0]["key"]
        self.add(sid, "user", "synthetic takeover of B before A dispatched", ago=50)
        self.m._continue_tick("fixture-key")
        self.assertNotIn(old_key, self.m._continue_failures)
        self.assertEqual(len(self.m._continue_failures), 1)

    def test_whole_group_takeover_invalidates_failure_without_a_replacement(self):
        sid = self.pending_parent()
        self.batch(sid, "deleg_A", ago=30)
        self.batch(sid, "deleg_B", ago=20)
        FakeHermes.status = 500
        self.m._continue_tick("fixture-key")
        old_key = FakeHermes.posts[0]["key"]
        self.add(sid, "user", "synthetic human owns both batches now", ago=5)
        self.m._continue_tick("fixture-key")
        self.assertEqual(len(FakeHermes.posts), 1)
        self.assertNotIn(old_key, self.m._continue_failures)
        with contextlib.closing(sqlite3.connect(self.home / "panel_auto_continue.db")) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0], 0)

    def test_failure_cache_has_independent_quantity_and_time_limits(self):
        FakeHermes.status = 500
        with patch.object(self.m, "CONTINUE_FAILURE_CACHE", 3, create=True):
            for i in range(4):
                sid = self.pending_parent(f"api_cache_{i}")
                self.batch(sid, f"deleg_A_{i}", ago=30)
                self.batch(sid, f"deleg_B_{i}", ago=20)
                self.m._continue_tick("fixture-key")
            self.assertLessEqual(len(self.m._continue_failures), 3)
        # Neither failed requests nor their cache require a successful receipt
        # to age out, and eviction must not reset the journal's retry budget.
        with patch.object(self.m.time, "time", return_value=time.time() + self.m.CONTINUE_WINDOW_S * 2 + 30):
            self.m._continue_tick("fixture-key")
        self.assertEqual(self.m._continue_failures, {})
        with contextlib.closing(sqlite3.connect(self.home / "panel_auto_continue.db")) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0], 0)

    def test_group_cache_eviction_does_not_reset_exhaustion_budget(self):
        for i in range(2):
            sid = self.pending_parent(f"api_budget_{i}")
            self.batch(sid, f"deleg_A_{i}", ago=30)
            self.batch(sid, f"deleg_B_{i}", ago=20)
        FakeHermes.status = 500
        with patch.object(self.m, "CONTINUE_FAILURE_CACHE", 1), patch.object(self.m, "CONTINUE_MAX_ATTEMPTS", 2):
            for _ in range(4):
                self.m._continue_tick("fixture-key")
        self.assertEqual(len(FakeHermes.posts), 4, "two requests retain exactly two attempts each despite cache eviction")
        self.assertEqual(self.m._continue_failures, {})
        with contextlib.closing(sqlite3.connect(self.home / "panel_auto_continue.db")) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0], 0)

    def fresh_process_ticks(self, ticks=2):
        """Real new interpreter; only the run/model API boundary is mocked."""
        import os
        import subprocess
        import sys
        script = """
import json, sqlite3, sys
from pathlib import Path
from unittest.mock import patch
sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])
from sidecar import hermes_panel_sidecar as m
m.HERMES_HOME = Path(sys.argv[2])
posts, errors = [], []
def api(method, path, key, body=None, headers=None):
    if method == 'GET':
        return 200, {'providers': [{'slug': 'cpa', 'authenticated': True, 'models': ['gpt-6.1-sol']}]}
    posts.append({'key': headers['Idempotency-Key'], 'body': body})
    return 202, {}
with patch.object(m, '_continue_api', side_effect=api):
    for _ in range(int(sys.argv[3])):
        try:
            m._continue_tick('fixture-key')
        except (OSError, ValueError, sqlite3.Error) as exc:
            errors.append(type(exc).__name__)
with sqlite3.connect(m.HERMES_HOME / 'panel_auto_continue.db') as conn:
    receipts = [r[0] for r in conn.execute('SELECT identity FROM receipts ORDER BY identity')]
    pending = conn.execute('SELECT request_key, aliases_json, body_json, attempts FROM requests').fetchall()
print(json.dumps({'posts': posts, 'errors': errors, 'receipts': receipts, 'pending': pending,
                  'warned': sorted(m._continue_warned)}))
"""
        proc = subprocess.run([sys.executable, '-B', '-c', script,
                               str(Path(str(self.m.__file__)).resolve().parents[1]), str(self.home), str(ticks)],
                              capture_output=True, text=True, timeout=30,
                              env={**os.environ, 'HERMES_HOME': str(self.home)})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def mixed_group_lost_ack(self):
        sid = self.pending_parent()
        self.batch(sid, 'deleg_B', ago=20)
        self.add(sid, 'user', '[ASYNC DELEGATION BATCH COMPLETE — deleg_A]\nsynthetic A',
                 kind='async_delegation_complete', meta=json.dumps({'delegation_id': 'deleg_A'}), ago=10)
        receipts = self.m._continue_receipts
        def failing_ack(now, aliases=None, session_id=None):
            if aliases:
                raise OSError('synthetic receipt write failure after HTTP202')
            return receipts(now, aliases, session_id)
        with patch.object(self.m, '_continue_receipts', side_effect=failing_ack):
            with self.assertRaises(OSError):
                self.m._continue_tick('fixture-key')
        first = FakeHermes.posts[0]
        self.assertTrue(first['key'].startswith('hp-continue-set-'))
        self.assertIn('synthetic result deleg_B/0', first['body']['input'])
        return sid, first

    def test_admitted_mixed_group_ack_loss_A_reply_fresh_process_restores_all_receipts(self):
        sid, first = self.mixed_group_lost_ack()
        self.native_admission(sid, first['key'])  # Original group, not A's key, durably entered.
        self.add(sid, 'assistant', 'synthetic A handled; awaiting B', ago=0)
        result = self.fresh_process_ticks()
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['posts'], [], 'read-only original-key proof must not admit B again')
        self.assertEqual(result['receipts'], [f'hp-continue-batch-{sid}-deleg_A',
                                              f'hp-continue-batch-{sid}-deleg_B'])
        self.assertEqual(result['pending'], [])

    def unadmitted_mixed_group_guard(self, mode):
        sid = self.pending_parent()
        self.batch(sid, 'deleg_B', ago=20)
        self.add(sid, 'user', '[ASYNC DELEGATION BATCH COMPLETE — deleg_A]\nsynthetic A',
                 kind='async_delegation_complete', meta=json.dumps({'delegation_id': 'deleg_A'}), ago=10)
        FakeHermes.status = 500  # Rejected, with no original group reservation.
        self.m._continue_tick('fixture-key')
        self.assertEqual(len(FakeHermes.posts), 1)
        if mode == 'stopped':
            self.native_admission(sid, 'synthetic-human-stop')
            with contextlib.closing(sqlite3.connect(self.home / 'runs_idempotency.db')) as conn, conn:
                conn.execute("UPDATE run_idempotency SET status_json = json_set(status_json, '$.status', 'cancelled')")
        else:
            self.add(sid, 'user', 'synthetic human takeover of both batches', ago=0)
        result = self.fresh_process_ticks()
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['posts'], [], 'no original admission and real stop/takeover must not restart')
        self.assertEqual(result['receipts'], [], 'non-admission must not fabricate processed aliases')
        self.assertEqual(result['pending'], [])

    def test_unadmitted_mixed_group_stopped_never_restarts(self):
        self.unadmitted_mixed_group_guard('stopped')

    def test_unadmitted_mixed_group_takeover_never_restarts(self):
        self.unadmitted_mixed_group_guard('takeover')

    def test_mixed_group_unknown_native_admission_keeps_original_request(self):
        sid, first = self.mixed_group_lost_ack()
        self.add(sid, 'assistant', 'synthetic A handled; awaiting B', ago=0)
        (self.home / 'runs_idempotency.db').unlink()
        result = self.fresh_process_ticks()
        self.assertEqual(result['posts'], [])
        self.assertEqual(result['receipts'], [])
        self.assertEqual(result['errors'], [], 'uncertainty skips the session, it does not abort the tick')
        self.assertTrue(any(w.endswith(':OperationalError') for w in result['warned']), result['warned'])
        self.assertEqual(len(result['pending']), 1)
        self.assertEqual(result['pending'][0][0], first['key'])
        self.assertEqual(json.loads(result['pending'][0][2]), first['body'])

    def test_mixed_group_other_session_admission_cannot_acknowledge_aliases(self):
        sid, first = self.mixed_group_lost_ack()
        self.native_admission('api_other_parent', first['key'])
        self.add(sid, 'assistant', 'synthetic A handled; awaiting B', ago=0)
        result = self.fresh_process_ticks()
        self.assertEqual(result['posts'], [])
        self.assertEqual(result['receipts'], [])
        self.assertEqual(result['errors'], [])
        self.assertTrue(any(w.endswith(':ValueError') for w in result['warned']), result['warned'])
        self.assertEqual(result['pending'][0][0], first['key'])
        self.assertEqual(json.loads(result['pending'][0][2]), first['body'])

    def test_mixed_group_receipts_do_not_consume_a_truly_new_B(self):
        sid, first = self.mixed_group_lost_ack()
        self.native_admission(sid, first['key'])
        self.add(sid, 'assistant', 'synthetic A handled; awaiting B', ago=0)
        self.assertEqual(self.fresh_process_ticks()['posts'], [])
        self.batch(sid, 'deleg_new_B', ago=20)
        result = self.fresh_process_ticks()
        self.assertEqual(result['errors'], [])
        self.assertEqual(len(result['posts']), 1)
        self.assertEqual(result['posts'][0]['key'], f'hp-continue-batch-{sid}-deleg_new_B')
        self.assertIn('synthetic result deleg_new_B/0', result['posts'][0]['body']['input'])
        self.assertNotIn('synthetic result deleg_B/0', result['posts'][0]['body']['input'])
        self.assertEqual(result['receipts'], [f'hp-continue-batch-{sid}-deleg_A',
                                              f'hp-continue-batch-{sid}-deleg_B',
                                              f'hp-continue-batch-{sid}-deleg_new_B'])

    def test_pending_request_journal_is_private(self):
        import stat
        self.waiting_session()
        FakeHermes.status = 500
        self.m._continue_tick("fixture-key")
        self.assertEqual(stat.S_IMODE((self.home / "panel_auto_continue.db").stat().st_mode), 0o600)

    def test_optional_malformed_results_do_not_block_normal_deliveries(self):
        sid = self.pending_parent()
        self.batch(sid, results=[{"task_index": 0, "status": {"malformed": True}, "summary": "synthetic A"},
                                 {"task_index": 1, "status": "completed", "summary": "synthetic B"}])
        self.waiting_session("api_normal_other")
        failure = None
        try:
            self.m._continue_tick("fixture-key")
        except Exception as exc:
            failure = exc
        self.assertIsNone(failure, "optional malformed ledger rows must be isolated from normal deliveries")
        self.assertEqual([p["body"]["session_id"] for p in FakeHermes.posts], ["api_normal_other"])

    def test_parent_only_optional_schema_cannot_block_normal_delivery(self):
        self.waiting_session()
        with contextlib.closing(sqlite3.connect(self.home / "state.db")) as conn, conn:
            conn.executescript("DROP TABLE async_delegations; CREATE TABLE async_delegations "
                               "(delegation_id TEXT PRIMARY KEY, parent_session_id TEXT, state TEXT, completed_at REAL);")
        self.m._continue_tick("fixture-key")
        self.assertEqual(len(FakeHermes.posts), 1)

    def test_optional_ledger_query_error_cannot_block_normal_delivery(self):
        self.waiting_session()
        with contextlib.closing(sqlite3.connect(self.home / "state.db")) as conn, conn:
            conn.executescript("DROP TABLE async_delegations; CREATE VIEW async_delegations AS SELECT * FROM synthetic_missing_table;")
        self.m._continue_tick("fixture-key")
        self.assertEqual(len(FakeHermes.posts), 1)

    def test_long_task_recent_completion_is_not_selected_by_parent_age(self):
        sid = self.pending_parent()
        event = self.batch(sid)
        event["dispatched_at"] = time.time() - 5 * 3600
        with contextlib.closing(sqlite3.connect(self.home / "state.db")) as conn, conn:
            conn.execute("UPDATE sessions SET started_at = ?", (time.time() - 86400,))
            conn.execute("UPDATE messages SET timestamp = ? WHERE role = 'user'", (time.time() - 5 * 3600 - 10,))
            conn.execute("UPDATE messages SET timestamp = ? WHERE role = 'assistant'", (time.time() - 5 * 3600 + 10,))
            conn.execute("UPDATE async_delegations SET dispatched_at = ?, event_json = ?", (event["dispatched_at"], json.dumps(event)))
        self.m._continue_tick("fixture-key")
        self.assertEqual(len(FakeHermes.posts), 1)

    def test_candidate_history_100_vs_5000_has_bounded_sqlite_vm_work(self):
        counts = []
        for size in (100, 5000):
            with contextlib.closing(sqlite3.connect(self.home / "state.db")) as conn, conn:
                conn.execute("DELETE FROM sessions")
                conn.executemany("INSERT INTO sessions(id, source, started_at) VALUES (?, 'api_server', ?)",
                                 [(f"api_old_{i}", time.time() - 86400) for i in range(size)])
            steps = [0]
            real_connect = sqlite3.connect
            def measured_connect(*args, **kwargs):
                conn = real_connect(*args, **kwargs)
                def progress():
                    steps[0] += 1
                    return 0
                conn.set_progress_handler(progress, 1)
                return conn
            with patch.object(self.m.sqlite3, "connect", side_effect=measured_connect):
                self.m._continue_tick("fixture-key")
            counts.append(steps[0])
        self.assertLessEqual(counts[1], counts[0] + 100, counts)
        self.assertLess(counts[1], 3000, counts)

    def stale_session_waiting_notice(self, history=100000):
        """会话活跃时间停在一天前、无委派账本行、通知在 110 分钟前：只有全表兜底能发现它。"""
        start = time.time()
        with contextlib.closing(sqlite3.connect(self.home / 'state.db')) as conn, conn:
            conn.executemany("INSERT INTO messages(session_id, role, content, timestamp, display_order) "
                             "VALUES ('synthetic_history', 'tool', 'historical', ?, ?)",
                             ((start - 86400, i) for i in range(history)))
        row = self.waiting_session()
        with contextlib.closing(sqlite3.connect(self.home / 'state.db')) as conn, conn:
            conn.execute('UPDATE sessions SET started_at = ?, last_activity_at = ?', (start - 86400, start - 86400))
            conn.execute('UPDATE messages SET timestamp = ? WHERE id = ?', (start - 6600, row))
        self.m._continue_sweep.update(home=None, at=0.0)
        self.m._continue_watch.clear()
        return start

    def measured_ticks(self, start, offsets):
        counts, queries = [], []
        real_connect = sqlite3.connect
        for offset in offsets:
            steps = [0]
            def measured_connect(*args, **kwargs):
                conn = real_connect(*args, **kwargs)
                def progress():
                    steps[0] += 1
                    return 0
                conn.set_progress_handler(progress, 1)
                conn.set_trace_callback(queries.append)
                return conn
            with patch.object(self.m.sqlite3, 'connect', side_effect=measured_connect), \
                 patch.object(self.m.time, 'time', return_value=start + offset):
                self.m._continue_tick('fixture-key')
            counts.append(steps[0])
        return counts, queries

    def test_stale_session_notice_is_found_by_the_first_sweep(self):
        start = self.stale_session_waiting_notice()
        with patch.object(self.m.time, 'time', return_value=start):
            self.m._continue_tick('fixture-key')
        self.assertEqual([p['key'] for p in FakeHermes.posts], ['hp-continue-batch-api_1_aaaa-deleg_ab12'])

    def test_full_sweep_runs_only_every_sweep_interval_and_ticks_between_are_cheap(self):
        start = self.stale_session_waiting_notice()
        poll = self.m.CONTINUE_POLL_S
        counts, queries = self.measured_ticks(start, [0, poll, 2 * poll, self.m.CONTINUE_SWEEP_S])
        sweeps = [q for q in queries if 'GROUP BY session_id' in q]
        self.assertEqual(len(sweeps), 2, 'one sweep at start, the next one after CONTINUE_SWEEP_S')
        self.assertGreater(counts[0], 100000, counts)
        self.assertLess(max(counts[1:3]), 20000, f'ticks between sweeps must not scan 100000 rows: {counts}')

    def test_index_paths_for_activity_and_per_session_notice_queries(self):
        self.waiting_session()
        _, queries = self.measured_ticks(time.time(), [0])
        activity = next(q for q in queries if q.startswith('SELECT id, source FROM sessions WHERE COALESCE'))
        notice = next(q for q in queries if q.startswith('SELECT MAX(timestamp) FROM messages WHERE session_id ='))
        with contextlib.closing(sqlite3.connect(self.home / 'state.db')) as conn:
            plan = [r[3] for r in conn.execute('EXPLAIN QUERY PLAN ' + activity)]
            self.assertTrue(any('idx_sessions_effective_activity' in d for d in plan), plan)
            plan = [r[3] for r in conn.execute('EXPLAIN QUERY PLAN ' + notice)]
            self.assertTrue(any('idx_messages_session_active' in d for d in plan), plan)

    def test_new_notice_in_active_session_is_found_next_tick_without_a_sweep(self):
        start = self.stale_session_waiting_notice(history=1000)
        with patch.object(self.m.time, 'time', return_value=start):
            self.m._continue_tick('fixture-key')
        self.waiting_session('api_new_notice')  # started_at 10 分钟前：最近活跃
        with patch.object(self.m.time, 'time', return_value=start + self.m.CONTINUE_POLL_S):
            self.m._continue_tick('fixture-key')
        self.assertIn('api_new_notice', [p['body']['session_id'] for p in FakeHermes.posts])

    def test_failed_post_is_retried_on_the_next_tick_even_with_large_history(self):
        start = self.stale_session_waiting_notice(history=20000)
        FakeHermes.status = 500
        with patch.object(self.m.time, 'time', return_value=start):
            self.m._continue_tick('fixture-key')
        FakeHermes.status = 202
        with patch.object(self.m.time, 'time', return_value=start + self.m.CONTINUE_POLL_S):
            self.m._continue_tick('fixture-key')
        self.assertEqual(len(FakeHermes.posts), 2, 'retry 5 seconds later, not after a full history pass')
        self.assertEqual(FakeHermes.posts[0]['key'], FakeHermes.posts[1]['key'])

    def test_busy_lease_is_rechecked_every_tick(self):
        start = self.stale_session_waiting_notice(history=20000)
        with contextlib.closing(sqlite3.connect(self.home / 'state.db')) as conn, conn:
            conn.execute("INSERT INTO session_turn_leases VALUES ('api_1_aaaa', 'synthetic', ?, ?)", (start, start + 7))
        with patch.object(self.m.time, 'time', return_value=start):
            self.m._continue_tick('fixture-key')
        self.assertEqual(FakeHermes.posts, [])
        with patch.object(self.m.time, 'time', return_value=start + 2 * self.m.CONTINUE_POLL_S):
            self.m._continue_tick('fixture-key')
        self.assertEqual(len(FakeHermes.posts), 1, 'continued as soon as the lease expired')

    def test_exhausted_dropped_delivery_is_recovered(self):
        # 线上形态：父会话回合持租约，通知写入 8 次被拒后转 dropped。
        sid = self.pending_parent()
        self.batch(sid, 'deleg_dropped')
        with contextlib.closing(sqlite3.connect(self.home / 'state.db')) as conn, conn:
            conn.execute("UPDATE async_delegations SET delivery_state = 'dropped', delivery_attempts = 8")
        self.m._continue_tick('fixture-key')
        self.assertEqual(len(FakeHermes.posts), 1)
        self.assertIn('synthetic result deleg_dropped/0', FakeHermes.posts[0]['body']['input'])

    def test_explicitly_dropped_delivery_is_not_recovered(self):
        sid = self.pending_parent()
        self.batch(sid, 'deleg_reset')
        with contextlib.closing(sqlite3.connect(self.home / 'state.db')) as conn, conn:
            conn.execute("UPDATE async_delegations SET delivery_state = 'dropped', delivery_attempts = 1")
        self.m._continue_tick('fixture-key')
        self.assertEqual(FakeHermes.posts, [])

    def test_ledger_missing_recovery_columns_is_reported_once(self):
        self.pending_parent()
        with contextlib.closing(sqlite3.connect(self.home / 'state.db')) as conn, conn:
            conn.execute('ALTER TABLE async_delegations DROP COLUMN task_json')
        with self.assertLogs(self.m.log, 'WARNING') as logs:
            self.m._continue_tick('fixture-key')
            self.m._continue_tick('fixture-key')
        self.assertEqual(sum('lost-notice recovery disabled' in line for line in logs.output), 1, logs.output)

    def test_valid_group_subset_recovers_only_its_expected_task_indexes(self):
        sid = self.pending_parent()
        self.batch(sid, indexes=[1])
        self.m._continue_tick("fixture-key")
        self.assertIn("synthetic result deleg_batch/1", FakeHermes.posts[0]["body"]["input"])
        self.assertNotIn("synthetic result deleg_batch/0", FakeHermes.posts[0]["body"]["input"])

    def test_cancelled_parent_never_recovers_a_lost_batch(self):
        sid = self.pending_parent()
        self.batch(sid)
        with contextlib.closing(sqlite3.connect(self.home / "runs_idempotency.db")) as conn, conn:
            conn.execute("DROP TABLE run_idempotency")
            conn.execute("CREATE TABLE run_idempotency (status_json TEXT, created_at REAL)")
            conn.execute("INSERT INTO run_idempotency VALUES (?, ?)",
                         (json.dumps({"session_id": sid, "status": "cancelled"}), time.time() - 100))
        self.m._continue_tick("fixture-key")
        self.assertEqual(FakeHermes.posts, [])

    def test_inactive_assistant_is_not_evidence_results_were_processed(self):
        sid = self.pending_parent()
        self.batch(sid)
        self.add(sid, "assistant", "synthetic inactive reply", ago=5, active=0)
        self.m._continue_tick("fixture-key")
        self.assertEqual(len(FakeHermes.posts), 1)

    def test_failed_batch_provides_its_actual_errors(self):
        sid = self.pending_parent()
        self.batch(sid, state="error", results=[{"task_index": i, "status": "error", "summary": None,
                                                "error": f"synthetic real error {i}"} for i in range(2)])
        self.m._continue_tick("fixture-key")
        self.assertIn("synthetic real error 0", FakeHermes.posts[0]["body"]["input"])
        self.assertIn("synthetic real error 1", FakeHermes.posts[0]["body"]["input"])

    def test_fallback_keeps_the_auto_continue_streak_cap(self):
        sid = self.pending_parent()
        self.batch(sid)
        for i in range(self.m.AUTO_CONTINUE_LIMIT):
            self.add(sid, "user", self.m.AUTO_CONTINUE_TEXT, ago=250 - i)
        self.m._continue_tick("fixture-key")
        self.assertEqual(FakeHermes.posts, [])

    def test_pending_payload_survives_ack_write_failure_and_process_restart(self):
        sid = self.pending_parent()
        self.batch(sid, "deleg_A", ago=30)
        receipts = self.m._continue_receipts
        def failing_ack(now, aliases=None, session_id=None):
            if aliases:
                raise OSError("synthetic ack write failure")
            return receipts(now, aliases, session_id)
        with patch.object(self.m, "_continue_receipts", side_effect=failing_ack):
            with self.assertRaises(OSError):
                self.m._continue_tick("fixture-key")
        first = FakeHermes.posts[0]
        self.m._continue_done.clear()
        self.m._continue_failures.clear()
        self.batch(sid, "deleg_B", ago=20)
        FakeHermes.status = 409  # Exact frozen retry was already admitted by Hermes.
        self.m._continue_tick("fixture-key")
        self.assertEqual(FakeHermes.posts[1]["key"], first["key"])
        self.assertEqual(FakeHermes.posts[1]["body"], first["body"])
        FakeHermes.status = 202
        self.m._continue_tick("fixture-key")
        self.assertIn("synthetic result deleg_B/0", FakeHermes.posts[-1]["body"]["input"])
        self.assertNotIn("synthetic result deleg_A/0", FakeHermes.posts[-1]["body"]["input"])

    def test_identity_journal_io_failure_stops_before_any_run_request(self):
        self.waiting_session()
        with patch.object(self.m, "_continue_receipts", side_effect=OSError("synthetic unavailable identity store")):
            with self.assertRaises(OSError):
                self.m._continue_tick("fixture-key")
        self.assertEqual(FakeHermes.posts, [])

    def test_retry_budget_survives_process_restart_after_exhausted_receipt_failure(self):
        self.waiting_session()
        receipts = self.m._continue_receipts
        def failing_done(now, aliases=None, session_id=None):
            if aliases:
                raise OSError("synthetic exhausted receipt failure")
            return receipts(now, aliases, session_id)
        FakeHermes.status = 500
        with patch.object(self.m, "_continue_receipts", side_effect=failing_done):
            for _ in range(self.m.CONTINUE_MAX_ATTEMPTS - 1):
                self.m._continue_tick("fixture-key")
            with self.assertRaises(OSError):
                self.m._continue_tick("fixture-key")
        self.m._continue_done.clear()
        self.m._continue_failures.clear()
        self.m._continue_tick("fixture-key")
        self.assertEqual(len(FakeHermes.posts), self.m.CONTINUE_MAX_ATTEMPTS)

    def test_old_parent_recent_notification_is_continued(self):
        self.waiting_session()
        with contextlib.closing(sqlite3.connect(self.home / "state.db")) as conn, conn:
            conn.execute("UPDATE sessions SET started_at = ?, last_activity_at = ?", (time.time() - 86400, time.time() - 86400))
        self.m._continue_tick("fixture-key")
        self.assertEqual(len(FakeHermes.posts), 1, "recent event, not parent creation, selects the session")

    def native_admission(self, sid, key):
        # Pinned public api_server_run_idempotency.py:88-101; synthetic only.
        with contextlib.closing(sqlite3.connect(self.home / "runs_idempotency.db")) as conn, conn:
            conn.execute("CREATE TABLE IF NOT EXISTS run_idempotency (scope TEXT NOT NULL, idempotency_key TEXT NOT NULL, "
                         "fingerprint TEXT NOT NULL, run_id TEXT NOT NULL, status_json TEXT NOT NULL, "
                         "owner_pid INTEGER NOT NULL DEFAULT 0, owner_started INTEGER NOT NULL DEFAULT 0, "
                         "retention_until REAL NOT NULL DEFAULT 0, acknowledged_at REAL, "
                         "created_at REAL NOT NULL, updated_at REAL NOT NULL, PRIMARY KEY(scope, idempotency_key))")
            if key is None:
                return
            now = time.time()
            rid = "run_" + "1" * 32
            status = {"object": "hermes.run", "run_id": rid, "session_id": sid, "status": "interrupted",
                      "created_at": now - 30, "updated_at": now - 20}
            conn.execute("INSERT INTO run_idempotency VALUES (?, ?, ?, ?, ?, 0, 0, 0, NULL, ?, ?)",
                         ("synthetic-scope", key, "synthetic-fingerprint", rid, json.dumps(status), now - 30, now - 20))

    def test_upgrade_legacy_admission_with_empty_journal_never_reposts_batch(self):
        row = self.waiting_session()
        self.assertEqual(row, 5)
        self.native_admission("api_1_aaaa", "hp-continue-api_1_aaaa-5")
        self.assertFalse((self.home / "panel_auto_continue.db").exists())
        self.m._continue_tick("fixture-key")
        self.assertEqual(FakeHermes.posts, [], "existing legacy admission must not change into a new batch key")
        self.assertIn("hp-continue-batch-api_1_aaaa-deleg_ab12", self.m._continue_done)
        self.m._continue_done.clear()
        self.m._continue_tick("fixture-key")
        self.assertEqual(FakeHermes.posts, [])

    def test_legacy_A_admission_cannot_consume_new_lost_B(self):
        row = self.waiting_session()
        self.native_admission("api_1_aaaa", f"hp-continue-api_1_aaaa-{row}")
        self.batch("api_1_aaaa", "deleg_B", ago=20)
        self.m._continue_tick("fixture-key")
        self.assertEqual(len(FakeHermes.posts), 1)
        self.assertEqual(FakeHermes.posts[0]["key"], "hp-continue-batch-api_1_aaaa-deleg_B")
        self.assertIn("synthetic result deleg_B/0", FakeHermes.posts[0]["body"]["input"])
        self.m._continue_tick("fixture-key")
        self.assertEqual(len(FakeHermes.posts), 1)

    def test_native_legacy_admission_uncertainty_fails_closed(self):
        self.waiting_session()
        native = self.home / "runs_idempotency.db"
        for mode in ("missing", "incompatible", "corrupt"):
            native.unlink(missing_ok=True)
            if mode == "incompatible":
                with contextlib.closing(sqlite3.connect(native)) as conn, conn:
                    conn.execute("CREATE TABLE run_idempotency (status_json TEXT, created_at REAL)")
            if mode == "corrupt":
                native.write_bytes(b"synthetic corrupt native store")
            with self.subTest(mode=mode):
                self.m._continue_warned.clear()
                self.m._continue_tick("fixture-key")
                self.assertEqual(FakeHermes.posts, [])
                self.assertTrue(any(w.startswith("admission:") for w in self.m._continue_warned), self.m._continue_warned)

    def test_one_sessions_unknown_admission_does_not_stall_other_sessions(self):
        self.waiting_session("api_1_aaaa")
        sid = self.pending_parent("api_fresh_parent")
        self.batch(sid, "deleg_fresh")
        real = self.m._continue_native_admitted
        def flaky(session_id, key):
            if session_id == "api_1_aaaa":
                raise sqlite3.OperationalError("interrupted")
            return real(session_id, key)
        with patch.object(self.m, "_continue_native_admitted", side_effect=flaky):
            self.m._continue_tick("fixture-key")
        self.assertEqual([p["body"]["session_id"] for p in FakeHermes.posts], ["api_fresh_parent"])
        self.m._continue_tick("fixture-key")
        self.assertEqual([p["body"]["session_id"] for p in FakeHermes.posts], ["api_fresh_parent", "api_1_aaaa"],
                         "the skipped session is retried on the next tick")

    def test_trigger_posts_one_continuation_with_route_and_key(self):
        row = self.waiting_session()
        self.m._continue_tick("fixture-key")
        self.assertEqual(len(FakeHermes.posts), 1)
        post = FakeHermes.posts[0]
        self.assertEqual(post["path"], "/v1/runs")
        self.assertEqual(post["key"], "hp-continue-batch-api_1_aaaa-deleg_ab12")
        self.assertEqual(post["auth"], "Bearer fixture-key")
        self.assertEqual(post["body"], {"input": self.m.AUTO_CONTINUE_TEXT, "session_id": "api_1_aaaa",
                                        "model": "gpt-6.1-sol", "provider": "cpa"})
        self.m._continue_tick("fixture-key")
        self.assertEqual(len(FakeHermes.posts), 1, "same completion never posted twice")

    def test_human_message_after_completion_owns_the_turn(self):
        self.waiting_session()
        self.add("api_1_aaaa", "user", "先别管它，做别的", ago=5)
        self.m._continue_tick("k")
        self.assertEqual(FakeHermes.posts, [])

    def test_completion_already_answered_is_not_continued(self):
        self.waiting_session()
        self.add("api_1_aaaa", "assistant", "结果已经用上了", ago=5)
        self.m._continue_tick("k")
        self.assertEqual(FakeHermes.posts, [])

    def test_busy_lease_waits(self):
        self.waiting_session()
        conn = sqlite3.connect(self.home / "state.db")
        conn.execute("INSERT INTO session_turn_leases VALUES ('api_1_aaaa', 'x', ?, ?)", (time.time() - 5, time.time() + 600))
        conn.commit()
        conn.close()
        self.m._continue_tick("k")
        self.assertEqual(FakeHermes.posts, [])

    def test_user_stopped_task_is_not_revived(self):
        self.waiting_session()
        conn = sqlite3.connect(self.home / "runs_idempotency.db")
        conn.execute("DROP TABLE run_idempotency")
        conn.execute("CREATE TABLE run_idempotency (scope TEXT, idempotency_key TEXT, status_json TEXT, created_at REAL)")
        conn.execute("INSERT INTO run_idempotency VALUES ('s', 'k1', ?, ?)",
                     (json.dumps({"session_id": "api_1_aaaa", "status": "completed"}), time.time() - 400))
        conn.execute("INSERT INTO run_idempotency VALUES ('s', 'k2', ?, ?)",
                     (json.dumps({"session_id": "api_1_aaaa", "status": "cancelled"}), time.time() - 200))
        conn.commit()
        conn.close()
        self.m._continue_tick("k")
        self.assertEqual(FakeHermes.posts, [], "latest run was stopped by the user")

    def test_streak_limit_until_user_speaks(self):
        sid = "api_1_aaaa"
        self.add(sid, "user", "大任务", ago=5000)
        for i in range(self.m.AUTO_CONTINUE_LIMIT):
            self.add(sid, "assistant", "等", ago=4000 - i * 100)
            self.add(sid, "user", f"[ASYNC DELEGATION BATCH COMPLETE — deleg_{i}]", kind="async_delegation_complete", ago=3990 - i * 100)
            self.add(sid, "user", self.m.AUTO_CONTINUE_TEXT, ago=3980 - i * 100)
        self.add(sid, "assistant", "又派了一个", ago=100)
        self.add(sid, "user", "[ASYNC DELEGATION BATCH COMPLETE — deleg_z]", kind="async_delegation_complete", ago=10)
        self.m._continue_tick("k")
        self.assertEqual(FakeHermes.posts, [], "capped after the limit")

    def test_old_completions_and_other_sources_are_ignored(self):
        self.add("api_1_old", "user", "很早的任务", ago=10 * 3600)
        self.add("api_1_old", "assistant", "等", ago=10 * 3600 - 10)
        self.add("api_1_old", "user", "[ASYNC DELEGATION BATCH COMPLETE — deleg_o]", kind="async_delegation_complete", ago=9 * 3600)
        conn = sqlite3.connect(self.home / "state.db")
        conn.execute("INSERT INTO sessions (id, source, model, started_at) VALUES ('tg_1', 'telegram', 'gpt-6.1-sol', ?)", (time.time() - 300,))
        conn.execute("INSERT INTO messages (session_id, role, content, display_kind, timestamp, display_order) VALUES "
                     "('tg_1', 'user', '[ASYNC DELEGATION BATCH COMPLETE — deleg_t]', 'async_delegation_complete', ?, 1000)", (time.time() - 5,))
        conn.commit()
        conn.close()
        self.m._continue_tick("k")
        self.assertEqual(FakeHermes.posts, [])

    def test_conflict_counts_as_done(self):
        self.waiting_session()
        FakeHermes.status = 409
        self.m._continue_tick("k")
        self.m._continue_tick("k")
        self.assertEqual(len(FakeHermes.posts), 1, "409: run already exists, do not repost")

    def test_failures_give_up_after_bounded_retries(self):
        self.waiting_session()
        FakeHermes.status = 500
        for _ in range(self.m.CONTINUE_MAX_ATTEMPTS + 2):
            self.m._continue_tick("k")
        self.assertEqual(len(FakeHermes.posts), self.m.CONTINUE_MAX_ATTEMPTS, "bounded retries")

    def test_unknown_model_route_is_skipped_not_guessed(self):
        self.waiting_session()
        conn = sqlite3.connect(self.home / "state.db")
        conn.execute("UPDATE sessions SET model = 'no-such-model'")
        conn.commit()
        conn.close()
        self.m._continue_tick("k")
        self.assertEqual([p for p in FakeHermes.posts if p["path"] == "/v1/runs"], [])

    def test_structured_content_and_scaffold_rows(self):
        m = self.m
        rows = [
            (1, "user", None, m._CONTENT_JSON_PREFIX + json.dumps([{"type": "text", "text": "看图"}]), None),
            (2, "assistant", None, "等结果", None),
            (3, "user", None, "[Your active task list was preserved across context compression]", None),
            (4, "user", "async_delegation_complete", "[ASYNC DELEGATION BATCH COMPLETE — deleg_q]", json.dumps({"presentation_suppressed": False})),
        ]
        self.assertEqual(m.continuation_trigger(rows), (4, False), "scaffold row is not a human message")
        rows[3] = (4, "user", "async_delegation_complete", "x", json.dumps({"presentation_suppressed": True}))
        self.assertEqual(m.continuation_trigger(rows), (None, False), "suppressed notices never continue")

    def test_schema_drift_is_skipped_quietly(self):
        conn = sqlite3.connect(self.home / "state.db")
        conn.executescript("DROP TABLE session_turn_leases;")
        conn.close()
        self.waiting_session()
        with self.assertRaises(sqlite3.Error):
            self.m._continue_tick("k")
        self.assertEqual(FakeHermes.posts, [], "unknown schema: no request")

    def test_unassociated_ended_child_does_not_continue(self):
        sid = "api_fallback_1"
        self.add(sid, "user", "执行长任务", ago=300)
        self.add(sid, "assistant", "已派发子任务，稍后处理", ago=280)
        # 写入一个已结束的子会话，但父会话中缺少 async_delegation_complete 通知行（竞态丢失）
        conn = sqlite3.connect(self.home / "state.db")
        conn.execute("INSERT INTO sessions (id, source, model, parent_session_id, ended_at, started_at) "
                     "VALUES ('sub_1', 'subagent', 'gpt-6.1-sol', ?, ?, ?)", (sid, time.time() - 20, time.time() - 100))
        conn.commit()
        conn.close()

        self.m._continue_tick("fixture-key")
        self.assertEqual(FakeHermes.posts, [], "an ended session has no proven batch or recoverable results")
        self.m._continue_tick("fixture-key")
        self.assertEqual(FakeHermes.posts, [])

    def test_subagent_ended_with_notification_does_not_duplicate_continuation(self):
        sid = "api_fallback_2"
        self.add(sid, "user", "执行长任务", ago=300)
        self.add(sid, "assistant", "已派发", ago=280)
        # 子会话已结束
        conn = sqlite3.connect(self.home / "state.db")
        conn.execute("INSERT INTO sessions (id, source, model, parent_session_id, ended_at, started_at) "
                     "VALUES ('sub_2', 'subagent', 'gpt-6.1-sol', ?, ?, ?)", (sid, time.time() - 20, time.time() - 100))
        conn.commit()
        conn.close()
        # 父会话正常收到了通知行
        self.add(sid, "user", "[ASYNC DELEGATION BATCH COMPLETE]", kind="async_delegation_complete", ago=10)

        self.m._continue_tick("fixture-key")
        # 走正常通知路径续跑，不产生额外的 fallback 续跑
        self.assertEqual(len(FakeHermes.posts), 1)
        self.assertTrue(FakeHermes.posts[0]["key"].startswith(f"hp-continue-{sid}-"))
        self.assertFalse(FakeHermes.posts[0]["key"].startswith(f"hp-continue-fallback-{sid}-"))

    def test_subagent_ended_human_took_over_does_not_trigger_fallback(self):
        sid = "api_fallback_3"
        self.batch(self.pending_parent(sid), "deleg_takeover")
        self.add(sid, "user", "执行长任务", ago=300)
        self.add(sid, "assistant", "已派发", ago=280)
        conn = sqlite3.connect(self.home / "state.db")
        conn.execute("INSERT INTO sessions (id, source, model, parent_session_id, ended_at, started_at) "
                     "VALUES ('sub_3', 'subagent', 'gpt-6.1-sol', ?, ?, ?)", (sid, time.time() - 20, time.time() - 100))
        conn.commit()
        conn.close()
        # 用户在子任务结束后发送了新消息
        self.add(sid, "user", "我改主意了，做别的", ago=5)

        self.m._continue_tick("fixture-key")
        self.assertEqual(FakeHermes.posts, [])

    def test_subagent_ended_assistant_already_answered_does_not_trigger_fallback(self):
        sid = "api_fallback_4"
        self.batch(self.pending_parent(sid), "deleg_answered")
        self.add(sid, "user", "执行长任务", ago=300)
        self.add(sid, "assistant", "已派发", ago=280)
        conn = sqlite3.connect(self.home / "state.db")
        conn.execute("INSERT INTO sessions (id, source, model, parent_session_id, ended_at, started_at) "
                     "VALUES ('sub_4', 'subagent', 'gpt-6.1-sol', ?, ?, ?)", (sid, time.time() - 20, time.time() - 100))
        conn.commit()
        conn.close()
        # A reply timestamp is not proof of delivery. The sidecar-owned receipt
        # records the exact batch previously admitted (see _continue_receipts).
        self.m._continue_receipts(time.time(), [f"hp-continue-batch-{sid}-deleg_answered"])
        # 助手在已交付的子任务结束后已回复
        self.add(sid, "assistant", "任务已处理完成", ago=5)

        self.m._continue_tick("fixture-key")
        self.assertEqual(FakeHermes.posts, [])


if __name__ == "__main__":
    unittest.main()
