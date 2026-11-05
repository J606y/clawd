"""Usage summary contracts. Prepared only. Not a pass.

Real SessionDB fixtures stay under the test tmp path. A raising reader is
simulated and labeled. This file does not start HTTP and does not open a
private sqlite connection.
"""

from __future__ import annotations

import ast
from datetime import datetime
from pathlib import Path

import pytest

from hermes_state import SessionDB
from hermes_state_runtime import RuntimeStoreError
from hermes_state_usage_summary import USAGE_DAYS, summarize_usage

_BANNED = ("/root/.hermes", "/usr/local/lib/hermes-agent")
_REPO = Path(__file__).resolve().parents[2]


class SimulatedReadFailure:
    """SIMULATED store. Not a SessionDB. The read raises instead of returning zeros."""

    def __init__(self, message: str = "simulated usage read failure"):
        self.message = message

    def flush_for_read(self) -> bool:
        return True

    def _read_ctx(self):
        raise OSError(self.message)


class SimulatedFlushTimeout:
    """SIMULATED flush timeout. The underlying SessionDB still has committed rows."""

    def __init__(self, db):
        self._db = db

    def flush_for_read(self) -> bool:
        return False

    def _read_ctx(self):
        return self._db._read_ctx()


def _refuse(path: Path) -> Path:
    text = str(path.resolve())
    for banned in _BANNED:
        if text == banned or text.startswith(banned + "/"):
            raise RuntimeError(f"refusing non-isolated path: {text}")
    return path


def _open(tmp_path: Path, name: str) -> SessionDB:
    return SessionDB(db_path=_refuse(tmp_path / name / "state.db"))


def _seed(db, session_id: str, source: str, model: str | None, provider: str | None, started: float, inp: int, out: int, *, cache: int = 0, calls: int = 1, title: str | None = None, messages: int = 1) -> None:
    db.create_session(session_id, source or "cli", model=model or None)
    db._write_sql(
        "UPDATE sessions SET started_at=?, source=?, model=?, billing_provider=?, input_tokens=?, "
        "output_tokens=?, cache_read_tokens=?, api_call_count=?, title=?, message_count=? WHERE id=?",
        (started, source, model, provider, inp, out, cache, calls, title, messages, session_id),
    )


def _aux(db, session_id: str, task: str, model: str, provider: str, inp: int, out: int, calls: int = 1) -> None:
    db.record_auxiliary_usage(session_id, task, model=model, billing_provider=provider, input_tokens=inp, output_tokens=out, api_call_count=calls)


def test_summary_module_does_not_open_sqlite_or_guess_home():
    tree = ast.parse((_REPO / "hermes_state_usage_summary.py").read_text(encoding="utf-8"))
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    attrs = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    called = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            called.add(node.func.attr)
    assert "get_hermes_home" not in names | attrs
    assert "sqlite3" not in names | attrs
    assert "connect" not in called
    assert not any(isinstance(node, ast.JoinedStr) for node in ast.walk(tree))
    text = (_REPO / "hermes_state_usage_summary.py").read_text(encoding="utf-8")
    assert "started_at > ?" in text
    assert "unixepoch" in text and "localtime" in text
    assert "GROUP BY day" in text


def test_empty_store_is_an_empty_structure_not_an_error(tmp_path):
    db = _open(tmp_path, "empty")
    try:
        view = summarize_usage(db, 30)
    finally:
        db.close()
    assert view == {
        "days": 30,
        "totals": {"sessions": 0, "input": 0, "output": 0, "cacheRead": 0, "apiCalls": 0},
        "daily": [],
        "byModel": [],
        "bySource": [],
        "top": [],
        "aux": [],
    }


def test_illegal_days_and_booleans_do_not_read():
    db = SimulatedReadFailure("must not read")
    for days in (True, False, 1, 0, 8, 7.0, "7", None):
        with pytest.raises(RuntimeStoreError) as caught:
            summarize_usage(db, days)
        assert caught.value.reason == "invalid_params"
        assert "state.db" not in str(caught.value)
    assert USAGE_DAYS == (7, 30, 90)


def test_read_failure_is_not_an_empty_payload():
    boom = SimulatedReadFailure("unable to open /root/.hermes/state.db SELECT sk-secret")
    with pytest.raises(RuntimeStoreError) as caught:
        summarize_usage(boom, 7)
    assert caught.value.reason == "storage_unavailable"
    assert caught.value.detail is None
    text = str(caught.value)
    assert "state.db" not in text and "SELECT" not in text and "sk-secret" not in text


def test_window_uses_started_at_local_day_and_exclusive_cutoff(tmp_path, monkeypatch):
    fixed = 1_800_000_000.0
    monkeypatch.setattr("hermes_state_usage_summary.time.time", lambda: fixed)
    cutoff = fixed - 7 * 86400
    inside = cutoff + 1
    db = _open(tmp_path, "window")
    try:
        _seed(db, "edge", "cli", "m", "p", cutoff, 9, 9, title="exact")
        _seed(db, "in", "cli", "grok", "xai", inside, 4, 6, cache=2, calls=3, title="窗内", messages=5)
        _seed(db, "old", "tui", "m", "p", cutoff - 10, 100, 100, title="旧")
        db._write_sql("UPDATE sessions SET last_activity_at=? WHERE id=?", (fixed, "old"))
        view = summarize_usage(db, 7)
    finally:
        db.close()
    assert view["days"] == 7
    assert view["totals"] == {"sessions": 1, "input": 4, "output": 6, "cacheRead": 2, "apiCalls": 3}
    assert view["daily"] == [{
        "day": datetime.fromtimestamp(inside).strftime("%Y-%m-%d"),
        "sessions": 1, "input": 4, "output": 6, "cacheRead": 2, "apiCalls": 3,
    }]
    assert view["top"] == [{
        "id": "in", "source": "cli", "model": "grok", "title": "窗内",
        "startedAt": inside, "totalTokens": 10, "apiCalls": 3, "messages": 5,
    }]
    assert [row["id"] for row in view["top"]] == ["in"]


def test_models_sources_aux_and_top_limit(tmp_path):
    started = datetime.now().astimezone().replace(hour=12, minute=0, second=0, microsecond=0).timestamp()
    db = _open(tmp_path, "models")
    try:
        _seed(db, "a", "cli", "grok", "xai", started, 10, 20, calls=2, title="A")
        _seed(db, "b", "tui", "claude", "anthropic", started, 3, 1, calls=1, title="B")
        _seed(db, "c", "", "", "", started, 1, 1, title=None)
        _seed(db, "out", "cli", "old", "old", started - 40 * 86400, 500, 500, title="窗外")
        for index in range(11):
            _seed(db, f"top{index}", "cli", "top", "p", started, 30 + index, 0, title=f"T{index}")
        _aux(db, "a", "vision", "vision-a", "aux", 8, 2, 2)
        _aux(db, "b", "vision", "vision-b", "aux", 1, 1, 1)
        _aux(db, "a", "compression", "comp", "aux", 5, 5, 1)
        db._write_sql(
            "INSERT INTO session_model_usage (session_id, model, billing_provider, billing_base_url, billing_mode, task, api_call_count, input_tokens, output_tokens) "
            "VALUES (?, 'hidden', 'secret-provider', '', '', '', 9, 1000, 1000)",
            ("a",),
        )
        view = summarize_usage(db, 30)
    finally:
        db.close()
    assert view["byModel"][0]["model"] == "top"
    models = {(row["model"], row["provider"]): (row["input"], row["output"], row["sessions"]) for row in view["byModel"]}
    assert models[("grok", "xai")] == (10, 20, 1)
    assert models[("claude", "anthropic")] == (3, 1, 1)
    assert models[("(未知)", "-")] == (1, 1, 1)
    assert ("old", "old") not in models
    assert all(row["input"] != 8 for row in view["byModel"] if row["model"] == "grok")
    sources = {row["source"]: row["sessions"] for row in view["bySource"]}
    assert sources["cli"] >= 1 and sources["tui"] == 1 and sources["(未知)"] == 1
    aux = {row["task"]: row for row in view["aux"]}
    assert set(aux) == {"vision", "compression"}
    assert aux["vision"]["sessions"] == 2
    assert aux["vision"]["input"] == 9 and aux["vision"]["apiCalls"] == 3
    assert aux["compression"] == {"task": "compression", "sessions": 1, "apiCalls": 1, "input": 5, "output": 5}
    assert len(view["top"]) == 10
    assert view["top"][0]["id"] == "top10"
    assert "top0" not in {row["id"] for row in view["top"]}
    assert view["daily"][0]["day"] == datetime.fromtimestamp(started).strftime("%Y-%m-%d")
    assert all(view["totals"][key] == sum(row[key] for row in view["daily"]) for key in view["totals"])


def test_profiles_do_not_share_rows_and_flush_timeout_keeps_committed_rows(tmp_path):
    started = datetime.now().astimezone().replace(hour=12, minute=0, second=0, microsecond=0).timestamp()
    left = _open(tmp_path, "profile-left")
    right = _open(tmp_path, "profile-right")
    try:
        _seed(left, "left", "cli", "left-model", "lp", started, 11, 1)
        _seed(right, "right", "tui", "right-model", "rp", started, 22, 2)
        _aux(left, "left", "vision", "v", "lp", 3, 0)
        left_view = summarize_usage(left, 7)
        right_view = summarize_usage(SimulatedFlushTimeout(right), 7)
        again = summarize_usage(left, 7)
    finally:
        left.close()
        right.close()
    assert left_view["totals"]["input"] == 11
    assert right_view["totals"]["input"] == 22
    assert {row["id"] for row in left_view["top"]} == {"left"}
    assert {row["id"] for row in right_view["top"]} == {"right"}
    assert left_view["aux"][0]["task"] == "vision"
    assert right_view["aux"] == []
    assert again["totals"] == left_view["totals"]
