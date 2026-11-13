"""Real SQLite fixtures; never connects to a production database."""
import contextlib
import importlib.util
import sys
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
import uuid


_REPO_ROOT = Path(__file__).resolve().parents[3]
_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

from clawd_server import panel_core
from panel_core_http_fixture import start_panel_core_server, stop_panel_core_server


class MessageEditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR"))
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "state.db"
        with self.connect() as conn:
            conn.executescript("""
                CREATE TABLE sessions (
                    id TEXT PRIMARY KEY, source TEXT NOT NULL, model TEXT,
                    model_config TEXT, system_prompt TEXT, system_prompt_hash TEXT,
                    parent_session_id TEXT REFERENCES sessions(id), started_at REAL NOT NULL,
                    ended_at REAL, end_reason TEXT, message_count INTEGER DEFAULT 0,
                    tool_call_count INTEGER DEFAULT 0, title TEXT UNIQUE,
                    profile_name TEXT, cwd TEXT, transport_profile TEXT, tool_names TEXT,
                    created_source TEXT, input_tokens INTEGER DEFAULT 0,
                    archived INTEGER DEFAULT 0, pinned INTEGER DEFAULT 0
                );
                CREATE TABLE messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL REFERENCES sessions(id), role TEXT NOT NULL,
                    content TEXT, tool_call_id TEXT, tool_calls TEXT, tool_name TEXT,
                    timestamp REAL NOT NULL, token_count INTEGER, finish_reason TEXT,
                    reasoning TEXT, reasoning_content TEXT, reasoning_details TEXT,
                    codex_reasoning_items TEXT, codex_message_items TEXT,
                    platform_message_id TEXT, observed INTEGER DEFAULT 1,
                    active INTEGER DEFAULT 1, compacted INTEGER DEFAULT 0,
                    api_content TEXT, display_kind TEXT, display_metadata TEXT,
                    _compressed_summary INTEGER DEFAULT 0, display_identity TEXT,
                    display_order INTEGER, message_uid TEXT, absorbed_message_uids TEXT,
                    tool_call_uids TEXT, tool_call_uid TEXT,
                    future_metadata TEXT
                );
                CREATE TABLE compression_locks (
                    session_id TEXT PRIMARY KEY, holder TEXT, acquired_at REAL, expires_at REAL
                );
                CREATE TABLE session_turn_leases (
                    conversation_id TEXT PRIMARY KEY, holder TEXT, acquired_at REAL, expires_at REAL
                );
            """)
            conn.execute("""INSERT INTO sessions
                (id, source, model, model_config, system_prompt, system_prompt_hash,
                 started_at, ended_at, end_reason, message_count, tool_call_count,
                 title, profile_name, cwd, transport_profile, tool_names,
                 created_source, input_tokens, archived, pinned)
                VALUES ('original', 'cli', 'old-model', ?, 'prompt', 'prompt-hash',
                        1, 999, 'done', 6, 1, 'Original', 'work', '/work',
                        'local', '["read_file"]', 'cli', 900, 1, 1)""",
                (json.dumps({"old_setting": True}),))
        self.add_message("user", "first", 10, future_metadata="keep unknown")
        self.add_message("assistant", "calling", 11,
                         tool_calls=json.dumps([{"id": "call-1", "function": {"name": "read_file", "arguments": "{}"}}]),
                         reasoning_content="private reasoning", codex_message_items='[{"type":"message"}]')
        self.add_message("tool", "result", 12, tool_call_id="call-1", tool_name="read_file",
                         api_content='[{"type":"text","text":"result"}]',
                         message_uid="msg-result", display_metadata='{"label":"file"}')
        self.add_message("assistant", "answer", 13, finish_reason="stop")
        self.add_message("user", "edit me", 14)
        self.add_message("assistant", "discarded suffix", 15)
        self.payload = {"sessionId": "original", "fromEnd": 2,
                        "expectedContent": "edit me", "expectedTimestamp": 14,
                        "requestId": str(uuid.uuid4()), "model": "new-model", "provider": "custom"}

    @contextlib.contextmanager
    def connect(self):
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def add_message(self, role, content, timestamp, **metadata):
        values = {"session_id": "original", "role": role, "content": content,
                  "timestamp": timestamp, **metadata}
        with self.connect() as conn:
            keys = list(values)
            conn.execute(f"INSERT INTO messages ({','.join(keys)}) VALUES ({','.join('?' for _ in keys)})",
                         tuple(values.values()))

    def module(self):
        module = panel_core
        self.assertTrue(hasattr(module, "create_edit_branch"), "edit branch feature is missing")
        return module

    def snapshot(self):
        with self.connect() as conn:
            return {t: [dict(r) for r in conn.execute(f'SELECT * FROM "{t}" ORDER BY rowid')]
                    for t in ("sessions", "messages", "compression_locks", "session_turn_leases")}

    def test_copies_complete_prefix_metadata_without_mutating_original(self):
        before = self.snapshot()
        result = self.module().create_edit_branch(self.db, self.payload)
        summary = result["session"]
        self.assertEqual(summary["id"], "api_edit_" + self.payload["requestId"])
        self.assertEqual(summary["source"], "api_server")
        self.assertEqual(summary["model"], "new-model")
        self.assertEqual(summary["message_count"], 4)
        self.assertEqual(summary["last_active"], 13)
        self.assertFalse(summary["pinned"])
        self.assertFalse(summary["archived"])
        after = self.snapshot()
        self.assertEqual(after["sessions"][0], before["sessions"][0])
        self.assertEqual(after["messages"][:6], before["messages"])
        copied = after["messages"][6:]
        for source, dest in zip(before["messages"][:4], copied):
            self.assertEqual({k: v for k, v in source.items() if k not in ("id", "session_id")},
                             {k: v for k, v in dest.items() if k not in ("id", "session_id")})
        self.assertEqual(len(copied), 4)
        branch = after["sessions"][1]
        self.assertIsNone(branch["parent_session_id"])
        self.assertIsNone(branch["ended_at"])
        self.assertIsNone(branch["end_reason"])
        self.assertEqual(branch["tool_call_count"], 1)
        self.assertEqual(branch["input_tokens"], 0)
        for key in ("system_prompt", "system_prompt_hash", "profile_name", "cwd", "transport_profile", "tool_names"):
            self.assertEqual(branch[key], before["sessions"][0][key])
        config = json.loads(branch["model_config"])
        self.assertEqual(set(config), {"_panel_edit_branch"})
        self.assertEqual(config["_panel_edit_branch"]["request"], self.payload)
        self.assertIn(self.payload["requestId"][:8], summary["title"])
    def test_retry_is_idempotent_even_if_source_grows(self):
        module = self.module()
        first = module.create_edit_branch(self.db, self.payload)
        self.add_message("user", "later activity", 20)
        before = self.snapshot()
        self.assertEqual(module.create_edit_branch(self.db, self.payload), first)
        self.assertEqual(self.snapshot(), before)
        for key, value in (("model", "different"), ("provider", "different"),
                           ("expectedContent", "different"), ("expectedTimestamp", 15),
                           ("fromEnd", 3), ("sessionId", "different")):
            with self.subTest(key=key):
                self.reject({**self.payload, key: value}, "edit_request_conflict", 409)

    def test_rejects_invalid_payload_without_writes(self):
        changes = [None, [], {}, {**self.payload, "fromEnd": True},
                   {**self.payload, "fromEnd": 0}, {**self.payload, "fromEnd": 1.5},
                   {**self.payload, "requestId": "not-a-uuid"},
                   {**self.payload, "requestId": self.payload["requestId"].replace("-", "")},
                   {**self.payload, "requestId": self.payload["requestId"].upper()},
                   {**self.payload, "expectedContent": []},
                   {**self.payload, "expectedTimestamp": True},
                   {**self.payload, "expectedTimestamp": float("nan")},
                   {**self.payload, "expectedTimestamp": float("inf")},
                   {**self.payload, "model": ""}, {**self.payload, "provider": ""},
                   {**self.payload, "extra": "ignored?"}]
        for payload in changes:
            with self.subTest(payload=payload):
                before = self.snapshot()
                module = self.module()
                with self.assertRaises(module.ApiError) as caught:
                    module.create_edit_branch(self.db, payload)
                self.assertEqual(caught.exception.status, 400)
                self.assertEqual(self.snapshot(), before)

    def test_rejects_unsafe_or_stale_boundary(self):
        for change in ({"fromEnd": 100}, {"expectedContent": "stale"},
                       {"expectedTimestamp": 14.00001}, {"sessionId": "missing"},
                       {"fromEnd": 1, "expectedContent": "discarded suffix", "expectedTimestamp": 15}):
            with self.subTest(change=change):
                self.reject({**self.payload, **change})
        contents = ["edit me\n--- 附件：x", "[附件已上传：x]", "[screenshot]",
                    "[screenshot: /images/a.png]", "[screenshot captured]",
                    "[OUT-OF-BAND USER MESSAGE — a direct message from the user]x",
                    '[' * 2000 + '0' + ']' * 2000,
                    '[{"type":"text","text":"hello"}]',
                    '{"content":[{"type":"image_url","image_url":{"url":"x"}}]}']
        for content in contents:
            with self.subTest(content=content):
                self.update("messages", {"content": content}, "id = 5")
                self.reject({**self.payload, "expectedContent": content}, "unsafe_edit_boundary", 409)
        self.update("messages", {"content": "edit me"}, "id = 5")
        for field, value in (("api_content", '[{"type":"image_url"}]'),
                             ("display_kind", "image"),
                             ("display_metadata", '{"attachments":["x"]}'),
                             ("tool_call_id", "pending"), ("tool_calls", "[]")):
            with self.subTest(field=field):
                self.update("messages", {field: value}, "id = 5")
                self.reject(code="unsafe_edit_boundary", status=409)
                self.update("messages", {field: None}, "id = 5")

    def test_rejects_compression_lineage_and_active_leases(self):
        for field, value in (("parent_session_id", "original"), ("end_reason", "compression"),
                             ("model_config", '{"compression_parent":"older"}'),
                             ("model_config", '{"nested":{"compacted_history":"old"}}'),
                             ("model_config", "not-json")):
            with self.subTest(field=field, value=value):
                old = self.snapshot()["sessions"][0][field]
                self.update("sessions", {field: value}, "id = 'original'")
                self.reject(code="unsupported_edit_history", status=409)
                self.update("sessions", {field: old}, "id = 'original'")
        for field, value in (("active", 0), ("compacted", 1), ("_compressed_summary", 1)):
            with self.subTest(field=field):
                old = self.snapshot()["messages"][0][field]
                self.update("messages", {field: value}, "id = 1")
                self.reject(code="unsupported_edit_history", status=409)
                self.update("messages", {field: old}, "id = 1")
        with self.connect() as conn:
            conn.execute("INSERT INTO sessions (id, source, started_at, title, parent_session_id) VALUES ('child','cli',1,'Child','original')")
        self.reject(code="unsupported_edit_history", status=409)
        with self.connect() as conn:
            conn.execute("DELETE FROM sessions WHERE id = 'child'")
        for table, column in (("compression_locks", "session_id"), ("session_turn_leases", "conversation_id")):
            with self.subTest(table=table):
                with self.connect() as conn:
                    conn.execute(f"INSERT INTO {table} VALUES ('original','worker',0,1e20)")
                self.reject(code="session_busy", status=409)
                with self.connect() as conn:
                    conn.execute(f"DELETE FROM {table}")
        with self.connect() as conn:
            conn.execute("INSERT INTO compression_locks VALUES ('original','old-worker',0,1)")
            conn.execute("INSERT INTO session_turn_leases VALUES ('original','old-worker',0,1)")
        self.assertEqual(self.module().create_edit_branch(self.db, self.payload)["session"]["message_count"], 4)

    def test_rejects_ambiguous_or_differently_ordered_boundary(self):
        self.update("messages", {"content": "edit me", "timestamp": 14}, "id = 4")
        self.reject(code="ambiguous_edit_boundary", status=409)
        self.update("messages", {"content": "answer", "timestamp": 13}, "id = 4")
        self.update("messages", {"timestamp": 16}, "id = 4")
        self.reject(code="unsupported_message_order", status=409)
        self.update("messages", {"timestamp": 13}, "id = 4")
        with self.connect() as conn:
            conn.execute("UPDATE messages SET display_order = id")
            conn.execute("UPDATE messages SET display_order = 0 WHERE id = 5")
        self.reject(code="unsupported_message_order", status=409)
        self.update("messages", {"display_order": 5}, "id = 5")
        self.assertEqual(self.module().create_edit_branch(self.db, self.payload)["session"]["message_count"], 4)

    def test_timestamp_regression_is_fine_when_display_order_is_strict(self):
        # 真实会话里时间戳会个别倒退，但 display_order 严格递增时展示顺序与 id 顺序一致，应当允许。
        with self.connect() as conn:
            conn.execute("UPDATE messages SET display_order = id")
        self.update("messages", {"timestamp": 16}, "id = 4")
        self.update("messages", {"timestamp": 1}, "id = 2")
        self.assertEqual(self.module().create_edit_branch(self.db, self.payload)["session"]["message_count"], 4)

    def test_rejects_incomplete_tool_pairs_and_lost_screenshot_prefix(self):
        cases = [(3, "tool_call_id", "orphan"), (3, "tool_call_id", None),
                 (3, "role", "assistant"), (2, "tool_calls", "not-json"),
                 (2, "tool_calls", '{}'), (2, "tool_calls", '[{"function":{}}]'),
                 (2, "tool_calls", '[{"id":"call-1"},{"id":"call-1"}]'),
                 (2, "role", "user"), (4, "tool_call_id", "call-1")]
        for row_id, field, value in cases:
            with self.subTest(row=row_id, field=field, value=value):
                old = self.snapshot()["messages"][row_id - 1][field]
                self.update("messages", {field: value}, f"id = {row_id}")
                self.reject(code="incomplete_tool_history", status=409)
                self.update("messages", {field: old}, f"id = {row_id}")
        self.update("messages", {"content": "lost [screenshot captured]"}, "id = 1")
        self.reject(code="unsupported_edit_history", status=409)
        upload = "first\n[附件已上传：/root/hermes-uploads/doc.txt]\n--- 附件：\ncontents"
        self.update("messages", {"content": upload}, "id = 1")
        summary = self.module().create_edit_branch(self.db, self.payload)["session"]
        with self.connect() as conn:
            copied = conn.execute("SELECT content FROM messages WHERE session_id = ? ORDER BY id", (summary["id"],)).fetchone()
        self.assertEqual(copied["content"], upload)

    def test_schema_mismatch_and_insert_failure_roll_back(self):
        with self.connect() as conn:
            conn.execute("ALTER TABLE messages RENAME COLUMN active TO incompatible_active")
        self.reject(code="unsupported_edit_schema", status=409)
        with self.connect() as conn:
            conn.execute("ALTER TABLE messages RENAME COLUMN incompatible_active TO active")
            conn.execute("CREATE TRIGGER fail_prefix BEFORE INSERT ON messages WHEN NEW.session_id != 'original' AND NEW.role = 'tool' BEGIN SELECT RAISE(ABORT, 'fixture write failure'); END")
        self.reject(code="edit_write_failed", status=409)
        with self.connect() as conn:
            conn.execute("DROP TRIGGER fail_prefix")
            # An unknown required session field is NOT silently copied or defaulted.
            schema = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'sessions'").fetchone()[0]
            schema = schema.replace("CREATE TABLE sessions (", "CREATE TABLE sessions_new (future_required TEXT NOT NULL,", 1)
            conn.execute(schema)
            columns = [r["name"] for r in conn.execute("PRAGMA table_info(sessions)")]
            conn.execute(f"INSERT INTO sessions_new (future_required,{','.join(columns)}) SELECT 'old-only',{','.join(columns)} FROM sessions")
            conn.execute("DROP TABLE sessions")
            conn.execute("ALTER TABLE sessions_new RENAME TO sessions")
        self.reject(code="unsupported_edit_schema", status=409)

    def test_rejects_prefix_over_message_or_byte_budget(self):
        self.update("messages", {"future_metadata": "x" * (8 * 1024 * 1024)}, "id = 1")
        self.reject(code="edit_prefix_too_large", status=413)
        self.update("messages", {"future_metadata": "keep unknown"}, "id = 1")
        with self.connect() as conn:
            conn.executemany("INSERT INTO messages (session_id,role,content,timestamp) VALUES ('original','user',?,?)",
                             (("padding " + str(i), 30 + i) for i in range(10001)))
        self.reject({**self.payload, "fromEnd": 1, "expectedContent": "padding 10000", "expectedTimestamp": 10030},
                    "edit_prefix_too_large", 413)

    def test_suffix_is_also_bounded_before_materializing_history(self):
        self.update("messages", {"content": "x" * (8 * 1024 * 1024)}, "id = 6")
        self.reject(code="edit_history_too_large", status=413)
        self.update("messages", {"content": "suffix"}, "id = 6")
        with self.connect() as conn:
            conn.executemany("INSERT INTO messages (session_id,role,content,timestamp) VALUES ('original','assistant','suffix',?)", ((30 + i,) for i in range(10001)))
        self.reject({**self.payload, "fromEnd": 10003}, "edit_history_too_large", 413)

    def test_http_route_creates_branch_and_rejects_oversize_body(self):
        import http.client
        import threading
        from unittest.mock import patch
        module = self.module()

        loop, runner, thread, port = start_panel_core_server(module, "fixture-only-key")
        try:
            with patch.object(module, "HERMES_HOME", self.db.parent):
                before = self.snapshot()
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                try:
                    connection.request("POST", "/sessions/edit-branch", json.dumps(self.payload),
                                       {"X-Panel-Key": "fixture-only-key", "Content-Type": "application/json"})
                    response = connection.getresponse()
                    body = json.loads(response.read())
                    self.assertEqual(response.status, 200, body)
                    self.assertEqual(body["session"]["id"], "api_edit_" + self.payload["requestId"])
                finally:
                    connection.close()
                after = self.snapshot()
                self.assertEqual(after["sessions"][0], before["sessions"][0])
                for raw, headers, status, code in (
                    (b"x" * (module.MAX_BODY_BYTES + 1), {"X-Panel-Key": "fixture-only-key"}, 413, "too_large"),
                    (b"{}", {}, 401, "unauthorized"),
                    (b"not-json", {"X-Panel-Key": "fixture-only-key"}, 400, "invalid_json")):
                    with self.subTest(status=status):
                        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                        try:
                            connection.request("POST", "/sessions/edit-branch", raw, headers)
                            response = connection.getresponse()
                            body = json.loads(response.read())
                            self.assertEqual(response.status, status, body)
                            self.assertEqual(body["error"]["code"], code)
                        finally:
                            connection.close()
                        self.assertEqual(self.snapshot(), after)
        finally:
            stop_panel_core_server(loop, runner, thread)

    def test_new_session_flags_do_not_inherit_nonzero_defaults(self):
        with self.connect() as conn:
            schema = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'sessions'").fetchone()[0]
            schema = schema.replace("CREATE TABLE sessions (", "CREATE TABLE sessions_new (", 1)
            schema = schema.replace("archived INTEGER DEFAULT 0", "archived INTEGER DEFAULT 1")
            schema = schema.replace("pinned INTEGER DEFAULT 0", "pinned INTEGER DEFAULT 1")
            conn.execute(schema)
            conn.execute("INSERT INTO sessions_new SELECT * FROM sessions")
            conn.execute("DROP TABLE sessions")
            conn.execute("ALTER TABLE sessions_new RENAME TO sessions")
        result = self.module().create_edit_branch(self.db, self.payload)["session"]
        with self.connect() as conn:
            branch = conn.execute("SELECT archived,pinned FROM sessions WHERE id = ?", (result["id"],)).fetchone()
        self.assertEqual(tuple(branch), (0, 0))

    def test_existing_arbitrary_id_is_not_accepted_as_retry(self):
        session_id = "api_edit_" + self.payload["requestId"]
        with self.connect() as conn:
            conn.execute("INSERT INTO sessions (id,source,model,started_at,title,model_config) VALUES (?,'api_server','new-model',1,'Unrelated','{}')", (session_id,))
        self.reject(code="edit_request_conflict", status=409)
        self.update("sessions", {"model_config": json.dumps({"_panel_edit_branch": {"version": 99, "request": self.payload}})}, f"id = '{session_id}'")
        self.reject(code="edit_request_conflict", status=409)

    def test_missing_database_is_not_created_and_sqlite_lock_is_visible(self):
        module = self.module()
        missing = self.db.parent / "missing.db"
        with self.assertRaises(module.ApiError) as caught:
            module.create_edit_branch(missing, self.payload)
        self.assertEqual(caught.exception.code, "edit_database_unavailable")
        self.assertFalse(missing.exists())
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self.reject(code="session_busy", status=409)
        self.assertEqual(module.create_edit_branch(self.db, self.payload)["session"]["message_count"], 4)

    def test_editing_first_message_creates_empty_independent_session(self):
        payload = {**self.payload, "fromEnd": 6, "expectedContent": "first", "expectedTimestamp": 10}
        before = self.snapshot()
        result = self.module().create_edit_branch(self.db, payload)["session"]
        self.assertEqual(result["message_count"], 0)
        self.assertIsNone(result["last_active"])
        self.assertIsNone(result["preview"])
        after = self.snapshot()
        self.assertEqual(after["messages"], before["messages"])
        self.assertEqual(after["sessions"][0], before["sessions"][0])

    def test_foreign_key_failure_rolls_back_complete_branch(self):
        with self.connect() as conn:
            conn.execute("CREATE TRIGGER invalid_reference AFTER INSERT ON messages WHEN NEW.session_id != 'original' BEGIN UPDATE messages SET session_id = 'missing-parent' WHERE id = NEW.id; END")
        self.reject(code="edit_write_failed", status=409)
        with self.connect() as conn:
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_rejects_composite_message_primary_key(self):
        with self.connect() as conn:
            schema = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'messages'").fetchone()[0]
            schema = schema.replace("CREATE TABLE messages (", "CREATE TABLE messages_new (", 1)
            schema = schema.replace("id INTEGER PRIMARY KEY AUTOINCREMENT", "id INTEGER")
            schema = schema.rsplit(")", 1)[0] + ", PRIMARY KEY (id,session_id))"
            conn.execute(schema)
            conn.execute("INSERT INTO messages_new SELECT * FROM messages")
            conn.execute("DROP TABLE messages")
            conn.execute("ALTER TABLE messages_new RENAME TO messages")
        self.reject(code="unsupported_edit_schema", status=409)

    def update(self, table, values, where):
        with self.connect() as conn:
            conn.execute(f'UPDATE {table} SET {",".join(k + " = ?" for k in values)} WHERE {where}',
                         tuple(values.values()))

    def reject(self, payload=None, code=None, status=None):
        before = self.snapshot()
        module = self.module()
        with self.assertRaises(module.ApiError) as caught:
            module.create_edit_branch(self.db, self.payload if payload is None else payload)
        if code is not None:
            self.assertEqual(caught.exception.code, code)
        if status is not None:
            self.assertEqual(caught.exception.status, status)
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
