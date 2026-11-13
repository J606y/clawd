"""Panel-core kanban routes: dispatch, uninstalled placeholder, install wiring.

Ported from the ui-kanban-board branch: the sidecar module is gone, the
same stubs and routes now live in clawd_server.panel_core.
"""

from __future__ import annotations

import unittest

from clawd_server import kanban_native, panel_core


class KanbanRoutesTests(unittest.TestCase):
    def setUp(self):
        self.mod = panel_core
        self._orig_list = self.mod.list_kanban_tasks
        self._orig_get = self.mod.get_kanban_task
        self._orig_flag = getattr(self.mod, "_clawd_kanban_native", None)

    def tearDown(self):
        self.mod.list_kanban_tasks = self._orig_list
        self.mod.get_kanban_task = self._orig_get
        if self._orig_flag is None:
            if hasattr(self.mod, "_clawd_kanban_native"):
                delattr(self.mod, "_clawd_kanban_native")
        else:
            self.mod._clawd_kanban_native = self._orig_flag

    def _route(self, method, path, query=None):
        handler = object.__new__(self.mod.Handler)
        return handler._route_admin(method, path, query or {})

    def test_tasks_route_passes_status_filter(self):
        seen = {}

        def fake_list(status=None):
            seen["status"] = status
            return {"tasks": [], "statuses": ["ready"]}

        self.mod.list_kanban_tasks = fake_list
        result = self._route("GET", "/kanban/tasks", {"status": "blocked"})
        self.assertEqual(seen["status"], "blocked")
        self.assertEqual(result, {"tasks": [], "statuses": ["ready"]})
        self._route("GET", "/kanban/tasks")
        self.assertIsNone(seen["status"])

    def test_task_route_passes_task_id(self):
        seen = {}

        def fake_get(task_id):
            seen["task_id"] = task_id
            return {"id": task_id, "title": "做 A"}

        self.mod.get_kanban_task = fake_get
        result = self._route("GET", "/kanban/tasks/t_123")
        self.assertEqual(seen["task_id"], "t_123")
        self.assertEqual(result["title"], "做 A")

    def test_uninstalled_placeholder_is_503_not_500(self):
        with self.assertRaises(self.mod.ApiError) as ctx:
            self._route("GET", "/kanban/tasks")
        self.assertEqual(ctx.exception.status, 503)
        self.assertEqual(ctx.exception.code, "kanban_unavailable")
        with self.assertRaises(self.mod.ApiError) as ctx:
            self._route("GET", "/kanban/tasks/t_123")
        self.assertEqual(ctx.exception.status, 503)

    def test_install_kanban_binds_onto_panel_core_module(self):
        kanban_native.install_kanban(self.mod)
        self.assertTrue(self.mod._clawd_kanban_native)
        self.assertTrue(callable(self.mod.list_kanban_tasks))
        self.assertTrue(callable(self.mod.get_kanban_task))


if __name__ == "__main__":
    unittest.main()
