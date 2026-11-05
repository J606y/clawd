"""Todo acceptance status stays parent-owned. Execution is a pure overlay.

Not run in this change. Do not treat this file as a passing result.
"""

from tools.todo_tool import TodoStore, project_todo_execution, todo_tool
import json


def _exec(delegation_id="", child_id="", status="running", session_id="sess-parent", profile_key="default", todo_id=""):
    record = {
        "session_id": session_id,
        "profile_key": profile_key,
        "status": status,
    }
    if delegation_id:
        record["delegation_id"] = delegation_id
    if child_id:
        record["child_id"] = child_id
    if todo_id:
        record["todo_id"] = todo_id
    return record


class TestAssociationPersistence:
    def test_absent_link_keeps_legacy_item_shape(self):
        store = TodoStore()
        assert store.write([{"id": "1", "content": "Task", "status": "pending"}]) == [
            {"id": "1", "content": "Task", "status": "pending"},
        ]

    def test_explicit_ids_round_trip_and_do_not_change_status(self):
        store = TodoStore()
        items = store.write([{
            "id": "child",
            "content": "Do the child",
            "status": "pending",
            "delegation_id": "deleg_a",
            "child_id": "sess_child",
        }])
        assert items[0]["status"] == "pending"
        assert items[0]["delegations"] == [{"delegation_id": "deleg_a", "child_id": "sess_child"}]
        payload = json.loads(todo_tool(store=store))
        assert payload["todos"][0]["delegations"][0]["delegation_id"] == "deleg_a"
        assert payload["summary"]["pending"] == 1
        assert payload["summary"]["completed"] == 0

    def test_merge_omission_keeps_link_and_empty_list_clears(self):
        store = TodoStore()
        store.write([{"id": "1", "content": "Task", "status": "pending", "delegation_id": "deleg_a"}])
        kept = store.write([{"id": "1", "status": "pending"}], merge=True)
        assert kept[0]["delegations"] == [{"delegation_id": "deleg_a"}]
        cleared = store.write([{"id": "1", "delegations": []}], merge=True)
        assert "delegations" not in cleared[0]
        assert cleared[0]["status"] == "pending"

    def test_scalar_does_not_guess_among_several_links(self):
        store = TodoStore()
        store.write([{
            "id": "1",
            "content": "Shared",
            "status": "pending",
            "delegations": [
                {"delegation_id": "deleg_a", "child_id": "c1"},
                {"delegation_id": "deleg_b", "child_id": "c2"},
            ],
        }])
        items = store.write([{"id": "1", "delegation_id": "deleg_c"}], merge=True)
        assert items[0]["delegations"] == [
            {"delegation_id": "deleg_a", "child_id": "c1"},
            {"delegation_id": "deleg_b", "child_id": "c2"},
        ]


class TestExecutionProjection:
    def _items(self):
        return [
            {"id": "parent", "content": "Schedule", "status": "in_progress"},
            {"id": "a", "content": "Own task", "status": "pending", "delegations": [{"delegation_id": "deleg_a", "child_id": "child_a"}]},
            {"id": "b", "content": "Other task", "status": "pending", "delegations": [{"delegation_id": "deleg_b", "child_id": "child_b"}]},
            {"id": "bare", "content": "Unlinked", "status": "pending"},
        ]

    def test_parallel_children_mark_only_their_items(self):
        projected = project_todo_execution(self._items(), [
            _exec("deleg_a", "child_a", "running"),
            _exec("deleg_b", "child_b", "running"),
        ], session_id="sess-parent", profile_key="default")
        by_id = {item["id"]: item for item in projected}
        assert by_id["a"]["execution"] == "running"
        assert by_id["b"]["execution"] == "running"
        assert by_id["a"]["status"] == "pending"
        assert by_id["b"]["status"] == "pending"
        assert "execution" not in by_id["parent"]
        assert "execution" not in by_id["bare"]
        assert by_id["parent"]["status"] == "in_progress"
        assert by_id["bare"]["status"] == "pending"

    def test_foreign_session_profile_and_title_do_not_match(self):
        items = [{"id": "a", "content": "Same words", "status": "pending", "delegations": [{"delegation_id": "deleg_a"}]}]
        projected = project_todo_execution(items, [
            _exec("deleg_a", status="running", session_id="other", profile_key="default"),
            {"session_id": "sess-parent", "profile_key": "other", "delegation_id": "deleg_a", "status": "running"},
            {"session_id": "sess-parent", "profile_key": "default", "title": "Same words", "status": "running"},
        ], session_id="sess-parent", profile_key="default")
        assert projected[0]["execution"] == "unknown"
        assert projected[0]["status"] == "pending"

    def test_shared_item_waits_for_every_child_and_failure_is_not_complete(self):
        item = {
            "id": "shared",
            "content": "Two children",
            "status": "pending",
            "delegations": [
                {"delegation_id": "deleg_a"},
                {"delegation_id": "deleg_b"},
            ],
        }
        running = project_todo_execution([item], [
            _exec("deleg_a", status="running"),
            _exec("deleg_b", status="returned"),
        ], session_id="sess-parent", profile_key="default")
        assert running[0]["execution"] == "running"
        assert running[0]["status"] == "pending"
        failed = project_todo_execution([item], [
            _exec("deleg_a", status="failed"),
            _exec("deleg_b", status="returned"),
        ], session_id="sess-parent", profile_key="default")
        assert failed[0]["execution"] == "failed"
        assert failed[0]["status"] == "pending"
        missing = project_todo_execution([item], [
            _exec("deleg_a", status="returned"),
        ], session_id="sess-parent", profile_key="default")
        assert missing[0]["execution"] == "unknown"
        ready = project_todo_execution([item], [
            _exec("deleg_a", status="returned"),
            _exec("deleg_b", status="returned"),
        ], session_id="sess-parent", profile_key="default")
        assert ready[0]["execution"] == "awaiting_acceptance"
        assert ready[0]["status"] == "pending"

    def test_ambiguous_id_marks_neither_sibling_running(self):
        items = [
            {"id": "a", "content": "A", "status": "pending", "delegations": [{"delegation_id": "deleg_same"}]},
            {"id": "b", "content": "B", "status": "pending", "delegations": [{"delegation_id": "deleg_same"}]},
        ]
        projected = project_todo_execution(items, [
            _exec("deleg_same", status="running"),
        ], session_id="sess-parent", profile_key="default")
        assert projected[0]["execution"] == "unknown"
        assert projected[1]["execution"] == "unknown"
        assert projected[0]["status"] == "pending"

    def test_closed_acceptance_is_not_reopened_and_calls_do_not_cache(self):
        closed = project_todo_execution(
            [{"id": "a", "content": "Done", "status": "completed", "delegations": [{"delegation_id": "deleg_a"}]}],
            [_exec("deleg_a", status="running")],
            session_id="sess-parent",
            profile_key="default",
        )
        assert "execution" not in closed[0]
        assert closed[0]["status"] == "completed"
        first = project_todo_execution(
            [{"id": "a", "content": "Open", "status": "pending", "delegations": [{"child_id": "child_a"}]}],
            [_exec(child_id="child_a", status="running")],
            session_id="sess-parent",
            profile_key="default",
        )
        second = project_todo_execution(
            [{"id": "a", "content": "Open", "status": "pending", "delegations": [{"child_id": "child_a"}]}],
            [],
            session_id="sess-parent",
            profile_key="other-profile",
        )
        assert first[0]["execution"] == "running"
        assert "execution" not in second[0]

    def test_blank_scope_and_non_list_leave_items_unchanged(self):
        items = [{"id": "a", "content": "Open", "status": "pending", "delegations": [{"delegation_id": "deleg_a"}]}]
        assert "execution" not in project_todo_execution(items, [_exec("deleg_a")], session_id="", profile_key="default")[0]
        assert "execution" not in project_todo_execution(items, [_exec("deleg_a")], session_id="sess-parent", profile_key="")[0]
        assert "execution" not in project_todo_execution(items, None, session_id="sess-parent", profile_key="default")[0]
