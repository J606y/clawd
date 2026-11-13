"""Folder API tests: isolated synthetic DBs and real authenticated HTTP."""
import concurrent.futures
import contextlib
import http.client
import json
import os
import sys
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

_REPO_ROOT = Path(__file__).resolve().parents[3]
_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

from clawd_server import panel_core
from panel_core_http_fixture import start_panel_core_server, stop_panel_core_server


class SessionFoldersTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = panel_core
        cls.key = "folder-fixture-key"
        cls.loop, cls.runner, cls.thread, cls.port = start_panel_core_server(cls.module, cls.key)
        cls.server_address = ("127.0.0.1", cls.port)

    @classmethod
    def tearDownClass(cls):
        stop_panel_core_server(cls.loop, cls.runner, cls.thread)
        assert not cls.thread.is_alive()

    def setUp(self):
        scratch_base = os.environ.get("PANEL_TEST_OUTPUT") or os.environ.get("TMPDIR") or "/root/hermes-workspace/p-tmp"
        os.makedirs(scratch_base, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=scratch_base)
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home #? 中文"
        self.home.mkdir()
        home_patch = patch.object(self.module, "HERMES_HOME", self.home)
        home_patch.start()
        self.addCleanup(home_patch.stop)
        self.state = self.home / "state.db"
        with contextlib.closing(sqlite3.connect(self.state)) as conn, conn:
            conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY)")
            conn.executemany("INSERT INTO sessions VALUES (?)", [("s1",), ("s2",)])
        self.state_before = (self.state.read_bytes(), self.state.stat().st_mtime_ns)

    def tearDown(self):
        self.assertEqual((self.state.read_bytes(), self.state.stat().st_mtime_ns), self.state_before)

    def request(self, body=None, key=None, *, path="/sessions/folders"):
        conn = http.client.HTTPConnection(*self.server_address, timeout=10)
        try:
            conn.request("GET" if body is None else "POST", path,
                         body=None if body is None else json.dumps(body),
                         headers={"X-Panel-Key": self.key if key is None else key,
                                  "Content-Type": "application/json"})
            response = conn.getresponse()
            payload = json.loads(response.read())
            self.assertEqual(response.getheader("Cache-Control"), "no-store")
            return response.status, payload
        finally:
            conn.close()

    def ok(self, body=None):
        status, payload = self.request(body)
        self.assertEqual(status, 200, payload)
        self.assertEqual(set(payload), {"folders", "assignments"})
        return payload

    def create(self, name="工作"):
        return self.ok({"action": "create", "name": name})["folders"][-1]["id"]

    # Preservation guards for transaction/auth/error behavior already established
    # by the vertical RED→GREEN slices above; no production changes for these.
    def test_authentication_blocks_reads_and_writes_before_storage(self):
        for body in [None, {"action": "create", "name": "Nope"}]:
            for key in ["", "wrong"]:
                status, payload = self.request(body, key=key)
                self.assertEqual(status, 401, payload)
        self.assertFalse((self.home / "panel_folders.db").exists())

    def test_corrupt_store_returns_visible_503_without_replacing_data(self):
        path = self.home / "panel_folders.db"
        path.write_bytes(b"not a sqlite database")
        for body in [None, {"action": "create", "name": "Valid"}]:
            status, payload = self.request(body)
            self.assertEqual(status, 503, payload)
            self.assertEqual(payload["error"]["code"], "folder_store_unavailable")
            self.assertEqual(path.read_bytes(), b"not a sqlite database")

    def test_unopenable_store_returns_visible_503(self):
        (self.home / "panel_folders.db").mkdir()
        status, payload = self.request()
        self.assertEqual(status, 503, payload)

    def test_missing_state_is_not_created_and_does_not_unassign(self):
        folder_id = self.create()
        before = self.ok({"action": "move", "sessionId": "s1", "folderId": folder_id})
        backup = self.home / "saved-state.db"
        self.state.rename(backup)
        try:
            status, payload = self.request({"action": "move", "sessionId": "s2", "folderId": folder_id})
            self.assertEqual(status, 503, payload)
            self.assertEqual(payload["error"]["code"], "session_state_unavailable")
            self.assertFalse(self.state.exists())
            self.assertEqual(self.ok(), before)
            # 移出是用户的明确操作，不依赖会话库：库不可用时也照常执行，且不会创建会话库。
            self.assertEqual(self.ok({"action": "move", "sessionId": "s1", "folderId": None})["assignments"], {})
            self.assertFalse(self.state.exists())
        finally:
            backup.rename(self.state)

    def test_deleted_session_can_still_be_moved_out(self):
        folder_id = self.create()
        with contextlib.closing(sqlite3.connect(self.home / "panel_folders.db")) as conn, conn:
            conn.execute("INSERT INTO assignments VALUES ('gone', ?)", (folder_id,))
        self.assertEqual(self.ok()["assignments"], {"gone": folder_id})
        self.assertEqual(self.ok({"action": "move", "sessionId": "gone", "folderId": None})["assignments"], {})
        status, payload = self.request({"action": "move", "sessionId": "gone", "folderId": folder_id})
        self.assertEqual(status, 404, payload)

    def test_invisible_format_characters_are_rejected(self):
        for name in ["\u200b", "a\u200bb", "\u202e工作", "\ufeffx"]:
            status, payload = self.request({"action": "create", "name": name})
            self.assertEqual(status, 400, (name, payload))
            self.assertEqual(payload["error"]["code"], "invalid_folder_name")
        self.assertEqual(self.ok()["folders"], [])

    def test_store_file_is_private(self):
        self.ok()
        self.assertEqual((self.home / "panel_folders.db").stat().st_mode & 0o777, 0o600)

    def test_failed_delete_rolls_back_cascade_and_folder(self):
        folder_id = self.create()
        before = self.ok({"action": "move", "sessionId": "s1", "folderId": folder_id})
        with contextlib.closing(sqlite3.connect(self.home / "panel_folders.db")) as conn, conn:
            conn.execute("CREATE TRIGGER fail_delete AFTER DELETE ON folders BEGIN SELECT RAISE(ABORT, 'fixture failure'); END")
        status, payload = self.request({"action": "delete", "id": folder_id})
        self.assertEqual(status, 503, payload)
        self.assertEqual(self.ok(), before)

    def test_concurrent_duplicate_create_has_one_winner(self):
        barrier = threading.Barrier(8)
        def create(index):
            barrier.wait(timeout=5)
            return self.request({"action": "create", "name": "Straße" if index % 2 else "STRASSE"})
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(create, range(8)))
        self.assertEqual(sorted(status for status, _ in results), [200] + [409] * 7)
        self.assertEqual(len(self.ok()["folders"]), 1)

    def test_concurrent_distinct_creates_do_not_lose_updates(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda index: self.request({"action": "create", "name": f"Folder {index}"}), range(16)))
        self.assertTrue(all(status == 200 for status, _ in results), results)
        snapshot = self.ok()
        self.assertEqual({folder["name"] for folder in snapshot["folders"]}, {f"Folder {index}" for index in range(16)})
        self.assertEqual(len({folder["id"] for folder in snapshot["folders"]}), 16)

    def test_locked_store_returns_503_and_preserves_snapshot(self):
        self.create()
        before = self.ok()
        with contextlib.closing(sqlite3.connect(self.home / "panel_folders.db")) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                status, payload = self.request({"action": "create", "name": "Blocked"})
                self.assertEqual(status, 503, payload)
                self.assertEqual(payload["error"]["code"], "folder_store_unavailable")
            finally:
                conn.rollback()
        self.assertEqual(self.ok(), before)

    def test_concurrent_move_delete_never_leaves_dangling_assignment(self):
        folder_id = self.create()
        barrier = threading.Barrier(2)
        def request(body):
            barrier.wait(timeout=5)
            return self.request(body)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            move = pool.submit(request, {"action": "move", "sessionId": "s1", "folderId": folder_id})
            delete = pool.submit(request, {"action": "delete", "id": folder_id})
            self.assertIn(move.result()[0], (200, 404))
            self.assertEqual(delete.result()[0], 200)
        self.assertEqual(self.ok(), {"folders": [], "assignments": {}})

    def test_connections_close_on_success_and_query_failure(self):
        real_connect = sqlite3.connect
        connections = []
        def connect(*args, **kwargs):
            conn = real_connect(*args, **kwargs)
            connections.append(conn)
            return conn
        folder_id = self.create()
        with patch.object(self.module.sqlite3, "connect", side_effect=connect):
            self.ok({"action": "move", "sessionId": "s1", "folderId": folder_id})
            status, _ = self.request({"action": "move", "sessionId": "missing", "folderId": folder_id})
            self.assertEqual(status, 404)
            with contextlib.closing(real_connect(self.state)) as conn, conn:
                conn.execute("ALTER TABLE sessions RENAME TO broken_sessions")
            try:
                status, _ = self.request({"action": "move", "sessionId": "s1", "folderId": folder_id})
                self.assertEqual(status, 503)
            finally:
                with contextlib.closing(real_connect(self.state)) as conn, conn:
                    conn.execute("ALTER TABLE broken_sessions RENAME TO sessions")
                # This fixture intentionally altered its synthetic native DB.
                self.state_before = (self.state.read_bytes(), self.state.stat().st_mtime_ns)
        self.assertGreaterEqual(len(connections), 4)
        for conn in connections:
            with self.assertRaises(sqlite3.ProgrammingError):
                conn.execute("SELECT 1")

    def test_surrogate_names_are_invalid_not_internal_errors(self):
        for name in ["bad\ud800", "bad\udfff"]:
            status, payload = self.request({"action": "create", "name": name})
            self.assertEqual(status, 400, payload)
        self.assertEqual(self.ok(), {"folders": [], "assignments": {}})

    def test_unknown_folder_returns_404_without_mutation(self):
        self.create()
        before = self.ok()
        for body in [{"action": "rename", "id": "missing", "name": "Valid"},
                     {"action": "collapse", "id": "missing", "collapsed": True},
                     {"action": "delete", "id": "missing"},
                     {"action": "move", "sessionId": "s1", "folderId": "missing"}]:
            with self.subTest(body=body):
                status, payload = self.request(body)
                self.assertEqual(status, 404, payload)
                self.assertEqual(self.ok(), before)

    def test_malformed_actions_and_identifiers_are_rejected(self):
        folder_id = self.create()
        before = self.ok()
        bodies = [{}, {"action": "unknown", "name": "Valid"}, {"action": [], "name": "Valid"},
                  {"action": "create", "name": "Nested", "parentId": folder_id},
                  {"action": "move", "sessionId": "s1"}]
        for value in [None, "", 1, True, [], {}, "bad\ud800", "bad\udfff", "bad\n"]:
            bodies.extend([{"action": "rename", "id": value, "name": "Valid"},
                           {"action": "collapse", "id": value, "collapsed": True},
                           {"action": "delete", "id": value},
                           {"action": "move", "sessionId": value, "folderId": folder_id}])
        for value in ["", 1, True, [], {}]:
            bodies.append({"action": "move", "sessionId": "s1", "folderId": value})
        for body in bodies:
            with self.subTest(body=body):
                status, payload = self.request(body)
                self.assertEqual(status, 400, payload)
                self.assertEqual(self.ok(), before)

    def test_delete_cascades_assignments_but_preserves_sessions(self):
        first = self.create("First")
        second = self.create("Second")
        self.ok({"action": "move", "sessionId": "s1", "folderId": first})
        self.ok({"action": "move", "sessionId": "s2", "folderId": second})
        result = self.ok({"action": "delete", "id": first})
        self.assertEqual(result, {"folders": [{"id": second, "name": "Second", "collapsed": False}], "assignments": {"s2": second}})
        with contextlib.closing(sqlite3.connect(self.state)) as conn:
            self.assertEqual(conn.execute("SELECT id FROM sessions ORDER BY id").fetchall(), [("s1",), ("s2",)])
        self.assertEqual(self.ok(), result)

    def test_move_assigns_reassigns_and_unassigns_existing_session(self):
        first = self.create("First")
        second = self.create("Second")
        for target in [first, second, None, None]:
            result = self.ok({"action": "move", "sessionId": "s1", "folderId": target})
            self.assertEqual(result["assignments"], {} if target is None else {"s1": target})
            self.assertEqual(len(result["folders"]), 2)
            self.assertEqual(self.ok(), result)
        before = self.ok()
        status, payload = self.request({"action": "move", "sessionId": "missing", "folderId": first})
        self.assertEqual(status, 404, payload)
        self.assertEqual(self.ok(), before)
        # 移出不存在的会话是幂等的空操作（已删除的会话也要能移出）。
        self.assertEqual(self.ok({"action": "move", "sessionId": "missing", "folderId": None}), before)

    def test_collapse_requires_boolean_and_persists(self):
        folder_id = self.create()
        for value in [True, False]:
            result = self.ok({"action": "collapse", "id": folder_id, "collapsed": value})
            self.assertIs(result["folders"][0]["collapsed"], value)
            self.assertEqual(self.ok(), result)
        for value in [None, 0, 1, "false", [], {}]:
            status, payload = self.request({"action": "collapse", "id": folder_id, "collapsed": value})
            self.assertEqual(status, 400, payload)

    def test_rename_keeps_id_and_rejects_duplicate_or_invalid_name(self):
        first = self.create("First")
        second = self.create("Second")
        result = self.ok({"action": "rename", "id": first, "name": "  FIRST  "})
        self.assertEqual(result["folders"][0], {"id": first, "name": "FIRST", "collapsed": False})
        self.assertEqual(len(result["folders"]), 2)
        for name, expected in [("second", 409), ("", 400), ("a\n", 400)]:
            status, payload = self.request({"action": "rename", "id": first, "name": name})
            self.assertEqual(status, expected, payload)
            self.assertEqual(self.ok(), result)
        self.assertEqual(self.ok()["folders"][1]["id"], second)

    def test_duplicate_names_use_unicode_casefold(self):
        self.create("Straße")
        before = self.ok()
        status, payload = self.request({"action": "create", "name": "  STRASSE  "})
        self.assertEqual(status, 409, payload)
        self.assertEqual(self.ok(), before)

    def test_invalid_names_are_rejected_without_mutation(self):
        before = self.ok()
        for name in [None, 1, True, [], {}, "", "   ", "😀" * 41, "a\u0000b", "a\u007fb", "a\u0085b", "\nabc", "abc\t"]:
            with self.subTest(name=name):
                status, payload = self.request({"action": "create", "name": name})
                self.assertEqual(status, 400, payload)
                self.assertEqual(self.ok(), before)
        self.create("😀" * 40)

    def test_create_trims_unicode_name_and_persists_snapshot(self):
        folder_id = self.create("  项目😀  ")
        self.assertIsInstance(folder_id, str)
        self.assertTrue(folder_id)
        self.assertEqual(self.ok(), {"folders": [{"id": folder_id, "name": "项目😀", "collapsed": False}], "assignments": {}})

    def test_empty_snapshot_uses_dedicated_database(self):
        self.assertEqual(self.ok(), {"folders": [], "assignments": {}})
        self.assertTrue((self.home / "panel_folders.db").is_file())

    def test_folder_list_read_returns_assigned_session_missing_from_loaded_page(self):
        """归属会话不在调用方已加载页时，文件夹读接口仍要带回可展示的摘要。"""
        with contextlib.closing(sqlite3.connect(self.state)) as conn, conn:
            for column, decl in (
                ("source", "TEXT"), ("title", "TEXT"), ("model", "TEXT"),
                ("started_at", "REAL"), ("last_activity_at", "REAL"),
                ("message_count", "INTEGER"), ("pinned", "INTEGER"), ("archived", "INTEGER"),
            ):
                conn.execute(f"ALTER TABLE sessions ADD COLUMN {column} {decl}")
            conn.execute(
                "UPDATE sessions SET source = 'tui', title = '页内会话', started_at = 10, "
                "last_activity_at = 11, message_count = 1, pinned = 0, archived = 0 WHERE id = 's1'")
            conn.execute(
                "INSERT INTO sessions (id, source, title, model, started_at, last_activity_at, "
                "message_count, pinned, archived) VALUES ('offpage', 'tui', '页外归属', NULL, 20, 21, 4, 1, 0)")
        self.state_before = (self.state.read_bytes(), self.state.stat().st_mtime_ns)
        folder_id = self.create("页外夹")
        self.ok({"action": "move", "sessionId": "offpage", "folderId": folder_id})
        plain = self.ok()
        self.assertEqual(set(plain), {"folders", "assignments"})
        self.assertEqual(plain["assignments"], {"offpage": folder_id})
        status, payload = self.request(path="/sessions/folders?sessions=1")
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["assignments"], {"offpage": folder_id})
        self.assertEqual(
            [(item.get("id"), item.get("title"), item.get("source"), item.get("pinned"))
             for item in payload.get("sessions", [])],
            [("offpage", "页外归属", "tui", True)],
        )
        self.assertNotIn("s1", {item.get("id") for item in payload.get("sessions", [])})
        self.assertEqual((self.state.read_bytes(), self.state.stat().st_mtime_ns), self.state_before)
