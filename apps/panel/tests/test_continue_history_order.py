import sys as _sys
from pathlib import Path as _Path
_REPO_ROOT = _Path(__file__).resolve().parents[3]
_TESTS_DIR = _Path(__file__).resolve().parent
if str(_TESTS_DIR) not in _sys.path:
    _sys.path.insert(0, str(_TESTS_DIR))
if str(_REPO_ROOT) not in _sys.path:
    _sys.path.append(str(_REPO_ROOT))

from clawd_server import panel_core

import contextlib
import importlib
import json
from pathlib import Path
import sqlite3
import tempfile
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

class TestContinueHistoryOrder(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = panel_core

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        conn = sqlite3.connect(self.home / "state.db")
        conn.executescript(SCHEMA)
        conn.commit()
        conn.close()
        self.patches = [
            patch.object(self.m, "HERMES_HOME", self.home),
            patch.object(self.m, "CONTINUE_API", "http://127.0.0.1:9"),
            patch.dict(self.m._continue_watch, {}, clear=True),
            patch.dict(self.m._continue_done, {}, clear=True),
            patch.dict(self.m._continue_failures, {}, clear=True),
            patch.dict(self.m._continue_warned, {}, clear=True),
            patch.dict(self.m._continue_route_cache, {"at": 0.0, "map": {}}, clear=True),
            patch.dict(self.m._continue_sweep, {"at": 0.0, "home": ""}, clear=True),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def _run_tick_with_fake_api(self):
        posts = []
        def fake_api(method, path, key, body=None, headers=None):
            if method == "GET":
                return 200, {"providers": [{"slug": "cpa", "authenticated": True, "models": ["gpt-model"]}]}
            posts.append({"path": path, "key": (headers or {}).get("Idempotency-Key"), "body": body})
            return 202, {"run_id": "r1"}

        with patch.object(self.m, "_continue_api", side_effect=fake_api):
            self.m._continue_tick("dummy-key")
        return posts

    def test_null_display_order_latest_notice_not_omitted_by_300_limit(self):
        """RED test: 当存在 >= 300 条带数字序号的历史时，最新 NULL display_order 完成通知不可被遗漏。"""
        now = time.time()
        sid = "api_test_null_order_truncation"
        conn = sqlite3.connect(self.home / "state.db")
        conn.execute("INSERT INTO sessions VALUES (?, 'api_server', 'gpt-model', NULL, NULL, ?, ?)", (sid, now - 600, now - 5))
        # 300 条历史消息，display_order 从 1 到 300，id 从 1 到 300
        for i in range(1, 301):
            conn.execute(
                "INSERT INTO messages (id, session_id, role, content, display_kind, timestamp, display_order, active) "
                "VALUES (?, ?, 'user', ?, NULL, ?, ?, 1)",
                (i, sid, f"msg {i}", now - 350 + i, i)
            )
        # 第 301 条消息：最新到来的 async_delegation_complete 通知，display_order 为 NULL
        conn.execute(
            "INSERT INTO messages (id, session_id, role, display_kind, content, timestamp, display_order, active) "
            "VALUES (301, ?, 'user', 'async_delegation_complete', '[ASYNC DELEGATION BATCH COMPLETE — d1]\\nres', ?, NULL, 1)",
            (sid, now - 2)
        )
        conn.commit()
        conn.close()

        posts = self._run_tick_with_fake_api()
        self.assertTrue(len(posts) > 0, "Failed to trigger auto-continue for message 301 with NULL display_order")
        self.assertIn("301", posts[0]["key"])

    def test_all_numeric_display_order_stable(self):
        """全数字 display_order 场景：按 display_order, id 正确排序与窗口截取。"""
        now = time.time()
        sid = "api_test_all_numeric"
        conn = sqlite3.connect(self.home / "state.db")
        conn.execute("INSERT INTO sessions VALUES (?, 'api_server', 'gpt-model', NULL, NULL, ?, ?)", (sid, now - 600, now - 5))
        for i in range(1, 10):
            conn.execute(
                "INSERT INTO messages (id, session_id, role, content, display_kind, timestamp, display_order, active) "
                "VALUES (?, ?, 'user', ?, NULL, ?, ?, 1)",
                (i, sid, f"msg {i}", now - 100 + i, i)
            )
        conn.execute(
            "INSERT INTO messages (id, session_id, role, display_kind, content, timestamp, display_order, active) "
            "VALUES (10, ?, 'user', 'async_delegation_complete', '[ASYNC DELEGATION BATCH COMPLETE — d_num]\\nres', ?, 10, 1)",
            (sid, now - 2)
        )
        conn.commit()
        conn.close()

        posts = self._run_tick_with_fake_api()
        self.assertTrue(len(posts) > 0)
        self.assertIn("10", posts[0]["key"])

    def test_all_null_display_order_stable(self):
        """全 NULL display_order 场景：退回按 id 顺序，最新通知正确处于窗口末尾。"""
        now = time.time()
        sid = "api_test_all_null"
        conn = sqlite3.connect(self.home / "state.db")
        conn.execute("INSERT INTO sessions VALUES (?, 'api_server', 'gpt-model', NULL, NULL, ?, ?)", (sid, now - 600, now - 5))
        for i in range(1, 305):
            conn.execute(
                "INSERT INTO messages (id, session_id, role, content, display_kind, timestamp, display_order, active) "
                "VALUES (?, ?, 'user', ?, NULL, ?, NULL, 1)",
                (i, sid, f"msg {i}", now - 400 + i)
            )
        conn.execute(
            "INSERT INTO messages (id, session_id, role, display_kind, content, timestamp, display_order, active) "
            "VALUES (305, ?, 'user', 'async_delegation_complete', '[ASYNC DELEGATION BATCH COMPLETE — d_allnull]\\nres', ?, NULL, 1)",
            (sid, now - 2)
        )
        conn.commit()
        conn.close()

        posts = self._run_tick_with_fake_api()
        self.assertTrue(len(posts) > 0)
        self.assertIn("305", posts[0]["key"])

    def test_mixed_numeric_and_null_order(self):
        """混合序号：旧历史有数字 display_order，后续插入了无序号（NULL）的消息和通知。"""
        now = time.time()
        sid = "api_test_mixed_order"
        conn = sqlite3.connect(self.home / "state.db")
        conn.execute("INSERT INTO sessions VALUES (?, 'api_server', 'gpt-model', NULL, NULL, ?, ?)", (sid, now - 600, now - 5))
        # 1..50 有 display_order
        for i in range(1, 51):
            conn.execute(
                "INSERT INTO messages (id, session_id, role, content, display_kind, timestamp, display_order, active) "
                "VALUES (?, ?, 'user', ?, NULL, ?, ?, 1)",
                (i, sid, f"msg {i}", now - 200 + i, i)
            )
        # 51..60 display_order 为 NULL
        for i in range(51, 61):
            conn.execute(
                "INSERT INTO messages (id, session_id, role, content, display_kind, timestamp, display_order, active) "
                "VALUES (?, ?, 'user', ?, NULL, ?, NULL, 1)",
                (i, sid, f"msg {i}", now - 100 + i)
            )
        # 61 是完成通知，display_order 为 NULL
        conn.execute(
            "INSERT INTO messages (id, session_id, role, display_kind, content, timestamp, display_order, active) "
            "VALUES (61, ?, 'user', 'async_delegation_complete', '[ASYNC DELEGATION BATCH COMPLETE — d_mix]\\nres', ?, NULL, 1)",
            (sid, now - 2)
        )
        conn.commit()
        conn.close()

        posts = self._run_tick_with_fake_api()
        self.assertTrue(len(posts) > 0)
        self.assertIn("61", posts[0]["key"])

    def test_same_display_order_stability(self):
        """同序号稳定性：多条消息 display_order 相同时按 id 决胜。"""
        now = time.time()
        sid = "api_test_same_order"
        conn = sqlite3.connect(self.home / "state.db")
        conn.execute("INSERT INTO sessions VALUES (?, 'api_server', 'gpt-model', NULL, NULL, ?, ?)", (sid, now - 600, now - 5))
        # 消息 1 和 2 具有相同的 display_order = 1
        conn.execute("INSERT INTO messages (id, session_id, role, content, timestamp, display_order, active) VALUES (1, ?, 'user', 'm1', ?, 1, 1)", (sid, now - 20))
        conn.execute("INSERT INTO messages (id, session_id, role, content, timestamp, display_order, active) VALUES (2, ?, 'assistant', 'a1', ?, 1, 1)", (sid, now - 15))
        # 消息 3 是通知，display_order = 2
        conn.execute("INSERT INTO messages (id, session_id, role, display_kind, content, timestamp, display_order, active) VALUES (3, ?, 'user', 'async_delegation_complete', '[ASYNC DELEGATION BATCH COMPLETE — d_same]\\nres', ?, 2, 1)", (sid, now - 2))
        conn.commit()
        conn.close()

        posts = self._run_tick_with_fake_api()
        self.assertTrue(len(posts) > 0)
        self.assertIn("3", posts[0]["key"])

    def test_compacted_copy_order_preservation(self):
        """压缩复制顺序保留：压缩/恢复场景下复制行保留原 display_order，排序不被简单按 id 破坏。"""
        now = time.time()
        sid = "api_test_compacted_copy"
        conn = sqlite3.connect(self.home / "state.db")
        conn.execute("INSERT INTO sessions VALUES (?, 'api_server', 'gpt-model', NULL, NULL, ?, ?)", (sid, now - 600, now - 5))
        # 原始行 id=1, display_order=1
        conn.execute("INSERT INTO messages (id, session_id, role, content, timestamp, display_order, active) VALUES (1, ?, 'user', 'm1', ?, 1, 1)", (sid, now - 50))
        # 后续行 id=2, display_order=2
        conn.execute("INSERT INTO messages (id, session_id, role, content, timestamp, display_order, active) VALUES (2, ?, 'assistant', 'm2', ?, 2, 1)", (sid, now - 40))
        # 压缩复制行 id=100, 但它的 display_order=1 (属于原始逻辑消息在展示中的对应槽位)
        conn.execute("INSERT INTO messages (id, session_id, role, content, timestamp, display_order, active) VALUES (100, ?, 'user', 'm1_copy', ?, 1, 1)", (sid, now - 30))
        # 新通知 id=101, display_order=3
        conn.execute("INSERT INTO messages (id, session_id, role, display_kind, content, timestamp, display_order, active) VALUES (101, ?, 'user', 'async_delegation_complete', '[ASYNC DELEGATION BATCH COMPLETE — d_comp]\\nres', ?, 3, 1)", (sid, now - 2))
        conn.commit()
        conn.close()

        posts = self._run_tick_with_fake_api()
        self.assertTrue(len(posts) > 0)
        self.assertIn("101", posts[0]["key"])
