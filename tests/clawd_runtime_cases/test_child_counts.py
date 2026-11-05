"""Child-count contracts. Prepared only. Not a pass.

``count_live_subagents`` is exercised against a real session database.
In-process completion units and simulated read failures are labeled.
This file does not start HTTP and does not change ``active_count`` into a
panel headcount.
"""

from __future__ import annotations

import ast
import inspect
import json
import time
from pathlib import Path

import pytest

from gateway.readiness import collect_runtime_readiness
from hermes_constants import (
    get_hermes_home,
    reset_hermes_home_override,
    set_hermes_home_override,
)
from hermes_state import SessionDB
from hermes_state_runtime import RuntimeStoreError
from hermes_state_subagents import count_live_subagents
from tools.async_delegation import active_count, active_task_count

_BANNED = (
    "/root/.hermes",
    "/usr/local/lib/hermes-agent",
    "/root/hermes-workspace/hermes-panel",
)
_PARENT = "parent1"
_OTHER_PARENT = "parent2"
_DONE = ("completed", "success", "error", "failed", "interrupted", "cancelled")

# Later isolated HTTP only. Not executed here and not an end-to-end activity page.
REAL_HTTP_INTEGRATION_NOT_E2E = (
    {
        "entry": "GET /health/detailed",
        "panel_proxy": "GET /panel-api/health/detailed",
        "requires": "gateway process, isolated home, Bearer auth, no production state.db",
        "not_e2e": "not the activity page, not a 10 second refresh, not a browser render of 不可用",
    },
)


class SimulatedReadFailure:
    """SIMULATED store. ``_read_ctx`` raises; this is not a SessionDB."""

    def _read_ctx(self):
        raise OSError("simulated subagent read failure")


def _refuse(path: Path) -> Path:
    resolved = path.resolve()
    text = str(resolved)
    for banned in _BANNED:
        if text == banned or text.startswith(banned + "/"):
            raise RuntimeError(f"refusing non-isolated path: {text}")
    return resolved


def _home(tmp_path: Path, name: str) -> Path:
    home = _refuse(tmp_path / name)
    home.mkdir()
    return home


def _method(path: Path, class_name: str, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == name:
                    return item
    raise AssertionError(f"{class_name}.{name} missing from {path}")


def _delegation(db, delegation_id: str, state: str, parent_id: str, task: dict, result: dict | None, result_raw: str | None = None):
    now = time.time()
    raw_result = result_raw if result_raw is not None else (None if result is None else json.dumps(result))
    db._write_sql(
        "INSERT INTO async_delegations ("
        "delegation_id, origin_session, origin_ui_session_id, parent_session_id, "
        "state, dispatched_at, updated_at, task_json, result_json, origin_session_id"
        ") VALUES (?, ?, '', ?, ?, ?, ?, ?, ?, '')",
        (
            delegation_id,
            parent_id,
            parent_id,
            state,
            now,
            now,
            json.dumps(task),
            raw_result,
        ),
    )


@pytest.fixture(autouse=True)
def _restore_process_records():
    from tools import async_delegation as delegation

    with delegation._records_lock:
        saved = dict(delegation._records)
    try:
        yield delegation
    finally:
        with delegation._records_lock:
            delegation._records.clear()
            delegation._records.update(saved)


def test_count_signatures_stay_distinct_from_the_panel_headcount():
    live = inspect.signature(count_live_subagents)
    assert list(live.parameters) == ["db", "parent_id"]
    assert live.parameters["parent_id"].default == ""
    assert list(inspect.signature(active_count).parameters) == []
    assert list(inspect.signature(active_task_count).parameters) == []
    readiness = inspect.signature(collect_runtime_readiness)
    assert all(item.kind is inspect.Parameter.KEYWORD_ONLY for item in readiness.parameters.values())
    assert "active_children" in readiness.parameters
    assert "active_delegations" in readiness.parameters


def test_http_child_count_entry_is_listed_not_executed():
    assert REAL_HTTP_INTEGRATION_NOT_E2E[0]["entry"] == "GET /health/detailed"
    assert "not the activity page" in REAL_HTTP_INTEGRATION_NOT_E2E[0]["not_e2e"]
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            called = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
            assert called not in {"ThreadingHTTPServer", "urlopen", "urlretrieve"}


def test_open_subagent_is_counted_and_ended_or_other_source_is_not(tmp_path):
    home = _home(tmp_path, "stored-children")
    with SessionDB(db_path=home / "state.db") as db:
        db.create_session(_PARENT, "api_server")
        db.create_session("child01", "subagent", parent_session_id=_PARENT)
        db.create_session("child02", "subagent", parent_session_id=_PARENT)
        db.create_session("notchild", "api_server", parent_session_id=_PARENT)
        db.end_session("child02", "completed")
        assert count_live_subagents(db) == 1
        assert count_live_subagents(db, _PARENT) == 1
        assert count_live_subagents(db, _OTHER_PARENT) == 0


def test_unstored_receipt_is_counted_and_duplicate_id_is_one(tmp_path):
    home = _home(tmp_path, "dedupe")
    with SessionDB(db_path=home / "state.db") as db:
        db.create_session(_PARENT, "api_server")
        db.create_session("child01", "subagent", parent_session_id=_PARENT)
        _delegation(
            db,
            "deleg-1",
            "running",
            _PARENT,
            {
                "child_links": [
                    {"child_session_id": "child01"},
                    {"child_session_id": "child01", "task_index": 0},
                    {"child_session_id": "child99", "task_index": 1},
                ]
            },
            None,
        )
        _delegation(
            db,
            "deleg-2",
            "finalizing",
            _PARENT,
            {"child_links": [{"child_session_id": "child99"}]},
            None,
        )
        assert count_live_subagents(db, _PARENT) == 2
        assert count_live_subagents(db) == 2


@pytest.mark.parametrize("status", _DONE)
def test_finished_receipt_removes_child_even_when_the_session_row_is_open(tmp_path, status):
    home = _home(tmp_path, f"done-{status}")
    with SessionDB(db_path=home / "state.db") as db:
        db.create_session(_PARENT, "api_server")
        db.create_session("child01", "subagent", parent_session_id=_PARENT)
        _delegation(
            db,
            "deleg-done",
            "running",
            _PARENT,
            {"child_links": [{"child_session_id": "child01"}, {"child_session_id": "child99"}]},
            {"results": [{"status": status, "child_session_id": "child01", "task_index": 0}]},
        )
        assert count_live_subagents(db, _PARENT) == 1


def test_non_terminal_receipt_status_does_not_remove_the_child(tmp_path):
    home = _home(tmp_path, "still-running")
    with SessionDB(db_path=home / "state.db") as db:
        db.create_session(_PARENT, "api_server")
        db.create_session("child01", "subagent", parent_session_id=_PARENT)
        _delegation(
            db,
            "deleg-live",
            "running",
            _PARENT,
            {"child_links": [{"child_session_id": "child01"}]},
            {"results": [{"status": "running", "child_session_id": "child01"}]},
        )
        assert count_live_subagents(db) == 1


def test_missing_session_id_counts_extra_and_batch_index_is_not_identity(tmp_path):
    home = _home(tmp_path, "no-sid")
    with SessionDB(db_path=home / "state.db") as db:
        db.create_session(_PARENT, "api_server")
        _delegation(
            db,
            "deleg-extra",
            "running",
            _PARENT,
            {
                "child_links": [
                    {"task_index": 0},
                    {"task_index": 0, "child_session_id": ""},
                    {"task_index": 1, "subagent_id": "batch-0"},
                    "not-a-link",
                    None,
                ]
            },
            {"results": [{"status": "completed", "child_session_id": 1, "task_index": 0}]},
        )
        assert count_live_subagents(db, _PARENT) == 3


def test_terminal_delegation_row_does_not_add_links(tmp_path):
    home = _home(tmp_path, "terminal-unit")
    with SessionDB(db_path=home / "state.db") as db:
        db.create_session(_PARENT, "api_server")
        _delegation(
            db,
            "deleg-closed",
            "completed",
            _PARENT,
            {"child_links": [{"child_session_id": "child99"}, {"task_index": 4}]},
            None,
        )
        assert count_live_subagents(db) == 0


def test_parent_filter_ignores_other_parent_receipts(tmp_path):
    home = _home(tmp_path, "parent-filter")
    with SessionDB(db_path=home / "state.db") as db:
        db.create_session(_PARENT, "api_server")
        db.create_session(_OTHER_PARENT, "api_server")
        db.create_session("child01", "subagent", parent_session_id=_OTHER_PARENT)
        _delegation(
            db,
            "deleg-other",
            "running",
            _OTHER_PARENT,
            {"child_links": [{"child_session_id": "child88"}]},
            None,
        )
        assert count_live_subagents(db, _PARENT) == 0
        assert count_live_subagents(db, _OTHER_PARENT) == 2


def test_empty_database_is_zero_but_unreadable_receipt_is_not_zero(tmp_path):
    home = _home(tmp_path, "unreadable")
    with SessionDB(db_path=home / "state.db") as db:
        db.create_session(_PARENT, "api_server")
        assert count_live_subagents(db) == 0
        _delegation(db, "deleg-bad-result", "running", _PARENT, {"child_links": []}, None, result_raw="{")
        with pytest.raises(RuntimeStoreError) as bad_result:
            count_live_subagents(db)
        assert bad_result.value.reason == "storage_unavailable"
        db._write_sql("DELETE FROM async_delegations")
        db._write_sql(
            "INSERT INTO async_delegations ("
            "delegation_id, origin_session, origin_ui_session_id, parent_session_id, "
            "state, dispatched_at, updated_at, task_json, origin_session_id"
            ") VALUES ('deleg-bad-task', ?, '', ?, 'running', ?, ?, '{', '')",
            (_PARENT, _PARENT, time.time(), time.time()),
        )
        with pytest.raises(RuntimeStoreError) as bad_task:
            count_live_subagents(db)
        assert bad_task.value.reason == "storage_unavailable"


def test_simulated_read_failure_raises_and_is_not_zero():
    """SIMULATED store. Read failure must not collapse to the empty-database zero."""
    with pytest.raises(RuntimeStoreError) as caught:
        count_live_subagents(SimulatedReadFailure())
    assert caught.value.reason == "storage_unavailable"
    assert caught.value.reason != "not_found"


def test_invalid_parent_is_invalid_params_not_zero(tmp_path):
    home = _home(tmp_path, "bad-parent")
    with SessionDB(db_path=home / "state.db") as db:
        with pytest.raises(RuntimeStoreError) as caught:
            count_live_subagents(db, "p" * 513)
    assert caught.value.reason == "invalid_params"


def test_readiness_preserves_null_and_does_not_coerce_it_to_zero(tmp_path):
    home = _home(tmp_path, "readiness-null")
    token = set_hermes_home_override(str(home))
    try:
        assert _refuse(get_hermes_home()) == home
        missing = collect_runtime_readiness(
            configured_model="fixture-model",
            runtime_status={"gateway_state": "running", "platforms": {}},
            active_children=None,
            active_delegations=0,
        )
        zero = collect_runtime_readiness(
            configured_model="fixture-model",
            runtime_status={"gateway_state": "running", "platforms": {}},
            active_children=0,
            active_delegations=0,
        )
    finally:
        reset_hermes_home_override(token)
    queues = missing["checks"]["background_queues"]
    assert "active_children" in queues
    assert queues["active_children"] is None
    assert zero["checks"]["background_queues"]["active_children"] == 0


def test_health_handler_source_maps_child_read_failure_to_null_not_suppress():
    root = Path(__file__).resolve().parents[2]
    method = _method(root / "gateway/platforms/api_server.py", "APIServerAdapter", "_handle_health_detailed")
    text = ast.get_source_segment((root / "gateway/platforms/api_server.py").read_text(encoding="utf-8"), method)
    assert text is not None
    assert "count_live_subagents" in text
    assert text.count("active_children = None") >= 2
    assert "suppress" not in text
    called = False
    for node in ast.walk(method):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "collect_runtime_readiness":
            called = True
            keywords = {item.arg: item.value for item in node.keywords}
            assert isinstance(keywords["active_children"], ast.Name)
            assert keywords["active_children"].id == "active_children"
    assert called
    readiness = (root / "gateway/readiness.py").read_text(encoding="utf-8")
    assert "active_children=None if active_children is None else max(0, int(active_children))" in readiness


def test_drain_still_uses_completion_units_not_child_headcount():
    root = Path(__file__).resolve().parents[2]
    method = _method(root / "gateway/run_shutdown.py", "GatewayShutdownMixin", "_scale_to_zero_has_live_background_work")
    names = [node.id for node in ast.walk(method) if isinstance(node, ast.Name)]
    assert "active_count" in names
    assert "count_live_subagents" not in names
    assert "active_task_count" not in names
    counts = _method(root / "gateway/platforms/api_server.py", "APIServerAdapter", "_readiness_work_counts")
    count_names = [node.id for node in ast.walk(counts) if isinstance(node, ast.Name)]
    assert "active_count" in count_names
    assert "suppress" in count_names
    assert "count_live_subagents" not in count_names


def test_active_count_is_one_completion_unit_when_children_are_many(tmp_path, monkeypatch, _restore_process_records):
    delegation = _restore_process_records
    home = _home(tmp_path, "completion-units")
    token = set_hermes_home_override(str(home))
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("CLAWD_HOME", str(home))
    try:
        assert _refuse(get_hermes_home()) == home
        with delegation._records_lock:
            delegation._records.clear()
            delegation._records["unit-1"] = {
                "status": "running",
                "delegation_id": "unit-1",
                "child_links": [
                    {"child_session_id": "child01"},
                    {"child_session_id": "child02"},
                    {"child_session_id": "child03"},
                ],
            }
            delegation._records["unit-2"] = {
                "status": "completed",
                "delegation_id": "unit-2",
                "child_links": [{"child_session_id": "child04"}],
            }
            delegation._records["unit-3"] = {
                "status": "stalling",
                "delegation_id": "unit-3",
                "child_links": [{"child_session_id": "child05"}, {"child_session_id": "child06"}],
            }
        assert active_count() == 2
        assert active_task_count() == 3
    finally:
        reset_hermes_home_override(token)


def test_durable_children_do_not_change_process_completion_units(tmp_path, _restore_process_records):
    delegation = _restore_process_records
    home = _home(tmp_path, "durable-not-units")
    with SessionDB(db_path=home / "state.db") as db:
        db.create_session(_PARENT, "api_server")
        for name in ("child01", "child02", "child03"):
            db.create_session(name, "subagent", parent_session_id=_PARENT)
        with delegation._records_lock:
            delegation._records.clear()
        assert count_live_subagents(db) == 3
        assert active_count() == 0
        assert active_task_count() == 0
        assert active_count() != count_live_subagents(db)


def test_active_task_count_subtracts_only_this_process_finished_children(tmp_path, monkeypatch, _restore_process_records):
    delegation = _restore_process_records
    home = _home(tmp_path, "this-process")
    with SessionDB(db_path=home / "state.db") as db:
        _delegation(
            db,
            "unit-live",
            "running",
            _PARENT,
            {"child_links": [{"child_session_id": "child01"}]},
            {"results": [{"status": "completed", "child_session_id": "child01"}]},
        )
    token = set_hermes_home_override(str(home))
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("CLAWD_HOME", str(home))
    try:
        assert _refuse(get_hermes_home()) == home
        with delegation._records_lock:
            delegation._records.clear()
            delegation._records["unit-live"] = {
                "status": "running",
                "delegation_id": "unit-live",
                "child_links": [
                    {"child_session_id": "child01"},
                    {"child_session_id": "child02"},
                    None,
                    {"task_index": 0},
                ],
            }
        assert active_task_count() == 2
        assert active_count() == 1

        def boom():
            raise RuntimeError("simulated ledger read failure")

        monkeypatch.setattr(delegation, "_connect", boom)
        assert active_task_count() == 3
        assert active_task_count() is not None
        assert active_count() == 1
    finally:
        reset_hermes_home_override(token)
