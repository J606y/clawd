"""Tests for the clawd-native kanban board reads (clawd_server/kanban_native.py).

Builds a throwaway kanban.db with the real kanban schema and pins it with
HERMES_KANBAN_DB. Never touches the real /root/.clawd/kanban.db.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

from clawd_server import kanban_native
from hermes_cli import kanban_db, kanban_db_connect


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        self.status = status
        self.code = code
        self.message = message
        super().__init__(message)


class KanbanNativeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="clawd-kanban-test-"))
        self.db = self.tmp / "kanban.db"
        self._env = mock.patch.dict(os.environ, {"HERMES_KANBAN_DB": str(self.db)})
        self._env.start()
        self.addCleanup(self._env.stop)
        self.conn = kanban_db_connect.connect(db_path=self.db)
        self.ready_id = kanban_db.create_task(
            self.conn, title="做 A", body="内容 A", assignee="default",
            session_id="sess-1", initial_status="running",
        )
        self.conn.execute(
            "UPDATE tasks SET status = 'ready' WHERE id = ?", (self.ready_id,)
        )
        self.running_id = kanban_db.create_task(
            self.conn, title="做 B", initial_status="running",
        )
        self.blocked_id = kanban_db.create_task(
            self.conn, title="做 C", initial_status="blocked",
        )
        self.done_id = kanban_db.create_task(
            self.conn, title="做 D", initial_status="running",
        )
        self.conn.execute(
            "UPDATE tasks SET status = 'done', completed_at = 1700000000 "
            "WHERE id = ?",
            (self.done_id,),
        )
        self.archived_id = kanban_db.create_task(
            self.conn, title="做 E", initial_status="running",
        )
        self.conn.execute(
            "UPDATE tasks SET status = 'archived' WHERE id = ?",
            (self.archived_id,),
        )
        self.conn.commit()
        kanban_db.add_comment(self.conn, self.ready_id, "worker", "进展不错")
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _mod(self) -> Any:
        return SimpleNamespace(ApiError=ApiError, HERMES_HOME=str(self.tmp))

    def test_list_returns_all_non_archived(self) -> None:
        result = kanban_native.list_kanban_tasks(self._mod())
        ids = [t["id"] for t in result["tasks"]]
        self.assertEqual(len(ids), 4)
        self.assertNotIn(self.archived_id, ids)
        for task in result["tasks"]:
            for key in (
                "id", "title", "status", "assignee", "created_at",
                "started_at", "workspace", "session_id",
            ):
                self.assertIn(key, task)
        self.assertIn("ready", result["statuses"])
        self.assertIn("archived", result["statuses"])

    def test_list_filters_by_status(self) -> None:
        result = kanban_native.list_kanban_tasks(self._mod(), "blocked")
        self.assertEqual([t["id"] for t in result["tasks"]], [self.blocked_id])
        archived = kanban_native.list_kanban_tasks(self._mod(), "archived")
        self.assertEqual([t["id"] for t in archived["tasks"]], [self.archived_id])
        # A task created with initial_status="running" is stored as "ready"
        # until the dispatcher claims it.
        ready = kanban_native.list_kanban_tasks(self._mod(), "ready")
        self.assertEqual(
            [t["id"] for t in ready["tasks"]], [self.ready_id, self.running_id]
        )

    def test_list_invalid_status_is_400(self) -> None:
        with self.assertRaises(ApiError) as ctx:
            kanban_native.list_kanban_tasks(self._mod(), "nope")
        self.assertEqual(ctx.exception.status, 400)
        self.assertEqual(ctx.exception.code, "invalid_status")

    def test_list_missing_db_is_503(self) -> None:
        with mock.patch.dict(os.environ, {"HERMES_KANBAN_DB": str(self.tmp / "nope.db")}):
            with self.assertRaises(ApiError) as ctx:
                kanban_native.list_kanban_tasks(self._mod())
        self.assertEqual(ctx.exception.status, 503)
        self.assertEqual(ctx.exception.code, "kanban_unavailable")

    def test_detail_returns_body_comments_events_session(self) -> None:
        detail = kanban_native.get_kanban_task(self._mod(), self.ready_id)
        self.assertEqual(detail["title"], "做 A")
        self.assertEqual(detail["body"], "内容 A")
        self.assertEqual(detail["status"], "ready")
        self.assertEqual(detail["session_id"], "sess-1")
        self.assertEqual(len(detail["comments"]), 1)
        self.assertEqual(detail["comments"][0]["author"], "worker")
        self.assertEqual(detail["comments"][0]["body"], "进展不错")
        kinds = [e["kind"] for e in detail["events"]]
        self.assertIn("commented", kinds)

    def test_detail_unknown_task_is_404(self) -> None:
        with self.assertRaises(ApiError) as ctx:
            kanban_native.get_kanban_task(self._mod(), "t_doesnotexist")
        self.assertEqual(ctx.exception.status, 404)
        self.assertEqual(ctx.exception.code, "kanban_task_not_found")

    def test_detail_bad_task_id_is_400(self) -> None:
        with self.assertRaises(ApiError) as ctx:
            kanban_native.get_kanban_task(self._mod(), "a/b")
        self.assertEqual(ctx.exception.status, 400)
        self.assertEqual(ctx.exception.code, "invalid_task_id")

    def test_install_binds_callables(self) -> None:
        mod = self._mod()
        kanban_native.install_kanban(mod)
        self.assertTrue(mod._clawd_kanban_native)
        self.assertTrue(callable(mod.list_kanban_tasks))
        self.assertTrue(callable(mod.get_kanban_task))
        result = mod.list_kanban_tasks(status="blocked")
        self.assertEqual([t["id"] for t in result["tasks"]], [self.blocked_id])
        detail = mod.get_kanban_task(self.running_id)
        self.assertEqual(detail["title"], "做 B")


if __name__ == "__main__":
    unittest.main()
