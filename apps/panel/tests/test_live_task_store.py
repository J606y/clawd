"""Native profile registry replaces the retired shadow store. Permissions stay real."""
import os
import sqlite3
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from native_browser_fixture import bind_profile, ensure_repo


class LiveTaskStoreTests(unittest.TestCase):
    def setUp(self):
        ensure_repo()
        scratch = os.environ.get("PANEL_TEST_OUTPUT") or os.environ.get("TMPDIR") or "/root/hermes-workspace/p-tmp"
        os.makedirs(scratch, exist_ok=True)
        self.tmp_dir = tempfile.TemporaryDirectory(dir=scratch)
        self.home = Path(self.tmp_dir.name) / "profile"
        bind_profile(self, self.home)
        from tools.browser_task_registry import registry_path

        self.db_path = registry_path()

    def tearDown(self):
        self.tmp_dir.cleanup()

    def _register(self, session, task, target, browser="/devtools/browser/b1", turn="turn-1"):
        from tools.browser_task_registry import register_created

        register_created(
            task_id=task,
            session_id=session,
            turn_id=turn,
            target_id=target,
            browser_id=browser,
            endpoint="http://127.0.0.1:9",
        )

    def test_reselecting_owned_target_is_idempotent_and_updates_current(self):
        from tools.browser_task_registry import active_task_targets, session_view

        self._register("s", "t", "ONE", turn="turn-a")
        self._register("s", "t", "TWO", turn="turn-a")
        import time
        time.sleep(0.002)
        self._register("s", "t", "ONE", turn="turn-a")
        current = session_view("s", "t")
        self.assertIsNotNone(current)
        self.assertEqual(current["targetId"], "ONE")
        self.assertEqual({item["targetId"] for item in active_task_targets("t")}, {"ONE", "TWO"})

    def test_file_and_parent_permissions_on_creation(self):
        self._register("s", "t", "ONE")
        parent_mode = stat.S_IMODE(self.db_path.parent.stat().st_mode)
        file_mode = stat.S_IMODE(self.db_path.stat().st_mode)
        self.assertEqual(parent_mode, 0o700, "Parent dir must be 0700")
        self.assertEqual(file_mode, 0o600, "DB file must be 0600")

    def test_read_only_mode_fails_if_db_missing(self):
        from tools.browser_task_registry import RegistryError, _connect

        with self.assertRaises(RegistryError):
            _connect(read_only=True)
        self.assertFalse(self.db_path.exists())

    def test_read_only_mode_allows_read_blocks_write(self):
        from tools.browser_task_registry import _connect, session_view

        self._register("sess-1", "task-1", "T1")
        self.assertEqual(session_view("sess-1", "task-1")["targetId"], "T1")
        conn = _connect(read_only=True)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute(
                    "INSERT INTO created_targets (target_id, browser_id, task_id, session_id, turn_id, endpoint, status, created_at, updated_at) VALUES ('x','/devtools/browser/b1','task-1','sess-1','turn-1','http://127.0.0.1:9','active',0,0)"
                )
        finally:
            conn.close()

    def test_chmod_failure_must_not_be_swallowed(self):
        with patch("os.chmod", side_effect=OSError("chmod failed")):
            with self.assertRaises(OSError):
                self._register("s", "t", "ONE")

    def test_record_active_cannot_resurrect_closing_or_ended_task(self):
        from tools.browser_task_registry import RegistryError, _mark_ended, session_view

        self._register("s1", "t1", "T1")
        _mark_ended("t1", "")
        with self.assertRaises(RegistryError) as caught:
            self._register("s1", "t1", "T2")
        self.assertEqual(caught.exception.code, "target_closed")
        self.assertEqual(session_view("s1", "t1")["status"], "ended")

    def test_global_target_unique_ownership(self):
        from tools.browser_task_registry import RegistryError

        self._register("s1", "t1", "TARGET-X", browser="/devtools/browser/b1")
        with self.assertRaises(RegistryError) as caught:
            self._register("s2", "t2", "TARGET-X", browser="/devtools/browser/b1")
        self.assertEqual(caught.exception.code, "target_taken")

    def test_foreign_key_and_orphan_cleanup(self):
        from tools.browser_task_registry import _connect, _mark_ended, session_view

        self._register("s1", "t1", "T1")
        self._register("s1", "t1", "T2")
        self._register("s-other", "t-other", "OTHER", browser="/devtools/browser/other")
        _mark_ended("t1", "")
        self.assertEqual(session_view("s-other", "t-other")["status"], "active")
        conn = _connect(read_only=True)
        try:
            fk_on = conn.execute("PRAGMA foreign_keys;").fetchone()[0]
            self.assertEqual(fk_on, 1)
        finally:
            conn.close()

    def test_record_ended_cleans_up_orphaned_targets_and_limits_history(self):
        from tools.browser_task_registry import _MAX_ENDED, _connect, _mark_ended

        for index in range(_MAX_ENDED + 3):
            self._register(f"s{index}", f"t{index}", f"T{index}", browser=f"/devtools/browser/b{index}")
            _mark_ended(f"t{index}", "")
        conn = _connect(read_only=True)
        try:
            ended = conn.execute("SELECT COUNT(*) FROM created_targets WHERE status = 'ended'").fetchone()[0]
            self.assertLessEqual(ended, _MAX_ENDED)
        finally:
            conn.close()

    def test_latest_empty_returns_none(self):
        from tools.browser_task_registry import session_view

        self.assertIsNone(session_view("session-1"))
        self.assertIsNone(session_view("session-1", "task-1"))

    def test_record_and_latest_format(self):
        from tools.browser_task_registry import session_view

        self._register("session-a", "task-a", "TARGET-1", browser="/devtools/browser/test-uuid")
        task = session_view("session-a")
        self.assertIsNotNone(task)
        self.assertEqual(task["taskId"], "task-a")
        self.assertEqual(task["sessionId"], "session-a")
        self.assertEqual(task["status"], "active")
        self.assertEqual(task["targetId"], "TARGET-1")
        self.assertEqual(task["browserId"], "/devtools/browser/test-uuid")
        self.assertEqual(task["cleanupError"], "")
        self.assertIsInstance(task["updatedAt"], float)
        self.assertEqual(session_view("session-a", "task-a"), task)
        self.assertIsNone(session_view("session-a", "other-task"))

    def test_owns_behavior(self):
        from tools.browser_task_registry import owns

        self._register("s1", "t1", "TARGET-1", browser="/devtools/browser/b1")
        self.assertTrue(owns("s1", "t1", "TARGET-1", "/devtools/browser/b1"))
        self.assertFalse(owns("s2", "t1", "TARGET-1", "/devtools/browser/b1"))
        self.assertFalse(owns("s1", "t2", "TARGET-1", "/devtools/browser/b1"))
        self.assertFalse(owns("s1", "t1", "TARGET-2", "/devtools/browser/b1"))
        self.assertFalse(owns("s1", "t1", "TARGET-1", "/devtools/browser/b2"))

    def test_record_multiple_targets_and_owns(self):
        from tools.browser_task_registry import owns, session_view

        self._register("s1", "t1", "T1")
        self._register("s1", "t1", "T2")
        self.assertTrue(owns("s1", "t1", "T1", "/devtools/browser/b1"))
        self.assertTrue(owns("s1", "t1", "T2", "/devtools/browser/b1"))
        self.assertEqual(session_view("s1", "t1")["targetId"], "T2")

    def test_record_ended_and_closing(self):
        from tools.browser_task_registry import _mark_ended, owns, session_view

        self._register("s1", "t1", "T1")
        _mark_ended("t1", "could not close tab")
        latest = session_view("s1", "t1")
        self.assertEqual(latest["status"], "ended")
        self.assertEqual(latest["cleanupError"], "could not close tab")
        self.assertTrue(owns("s1", "t1", "T1", "/devtools/browser/b1"))

    def test_concurrency_race_on_tasks_and_targets(self):
        import concurrent.futures

        from tools.browser_task_registry import owns, session_view

        def worker(idx):
            for j in range(20):
                self._register(f"sess-{idx}", f"task-{idx}", f"T-{idx}-{j}", browser=f"/devtools/browser/worker-{idx}")
                owns(f"sess-{idx}", f"task-{idx}", f"T-{idx}-{j}", f"/devtools/browser/worker-{idx}")
            from tools.browser_task_registry import _mark_ended

            _mark_ended(f"task-{idx}", "")

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(worker, i) for i in range(8)]
            for future in concurrent.futures.as_completed(futures):
                future.result()
        for i in range(8):
            latest = session_view(f"sess-{i}", f"task-{i}")
            self.assertIsNotNone(latest)
            self.assertEqual(latest["status"], "ended")
            self.assertTrue(owns(f"sess-{i}", f"task-{i}", f"T-{i}-0", f"/devtools/browser/worker-{i}"))


if __name__ == "__main__":
    unittest.main()
