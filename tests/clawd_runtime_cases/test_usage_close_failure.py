"""Usage handle close failures. Prepared only. Not a pass.

These call the real ``session_usage``. Database opens are fakes. This file
does not start HTTP, build, or import the sidecar.
"""

from __future__ import annotations

import logging
import traceback
from pathlib import Path
from types import SimpleNamespace

import pytest

from clawd_server.runtime_native import install, session_usage

_PATH = "/root/.hermes/state.db"
_SQL = "SELECT secret_sql"
_SECRET = "sk-close-secret"
_SUMMARY_SECRET = "sk-summary-secret"
_CANARIES = (_PATH, _SQL, _SECRET, _SUMMARY_SECRET)


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class _CloseFailure(OSError):
    """SIMULATED close failure. The message is a canary, not a real path."""


class _SummaryFailure(RuntimeError):
    """SIMULATED summary failure. The message is a canary, not a real query."""


class _InitFailure(RuntimeError):
    """SIMULATED SessionDB init failure. No handle is returned."""


class _FakeDB:
    def __init__(self, db_path=None, read_only=False, close_error=None, init_error=None) -> None:
        self.db_path = Path(db_path)
        self.read_only = read_only
        self.close_error = close_error
        self.closed = 0
        if init_error is not None:
            raise init_error

    def close(self) -> None:
        self.closed += 1
        if self.close_error is not None:
            raise self.close_error


def _mod(home: Path) -> SimpleNamespace:
    return SimpleNamespace(HERMES_HOME=home, ApiError=ApiError, _clawd_native=())


def _rendered(exc: BaseException) -> str:
    return "".join(traceback.format_exception(exc))


def _public_blob(exc: BaseException, records: list[logging.LogRecord]) -> str:
    notes = "\n".join(getattr(exc, "__notes__", ()) or ())
    logged = "\n".join((record.getMessage() + "\n" + (record.exc_text or "")) for record in records)
    return "\n".join((exc.message, str(exc), exc.code, notes, logged, _rendered(exc)))


def _assert_public_clean(exc: BaseException, records: list[logging.LogRecord]) -> None:
    blob = _public_blob(exc, records)
    for token in _CANARIES:
        assert token not in blob
    for record in records:
        assert record.exc_info is None
        assert record.name == "clawd.runtime"
        assert record.levelno == logging.ERROR


def _bind(monkeypatch, tmp_path: Path, *, init_error=None, close_error=None, summary=None):
    """Patch home lookup before any state import, then refuse later process-home calls."""
    process_home = tmp_path / "process-home-must-not-be-used"
    home_calls: list[str] = []

    def _benign_home() -> Path:
        home_calls.append("before-session-usage")
        return process_home

    monkeypatch.setattr("hermes_constants.get_hermes_home", _benign_home)
    opened: list[_FakeDB] = []

    def _factory(db_path=None, read_only=False):
        try:
            db = _FakeDB(db_path, read_only=read_only, close_error=close_error, init_error=init_error)
        except _InitFailure:
            opened.append(_FakeDB.__new__(_FakeDB))
            failed = opened[-1]
            failed.db_path = Path(db_path)
            failed.read_only = read_only
            failed.closed = 0
            failed.close_error = None
            raise
        opened.append(db)
        return db

    monkeypatch.setattr("hermes_state.SessionDB", _factory)
    seen: dict[str, object] = {}

    def _summarize(db, days):
        seen["db"] = db
        seen["days"] = days
        if isinstance(summary, BaseException):
            raise summary
        if callable(summary):
            return summary(db, days)
        return summary

    monkeypatch.setattr("hermes_state_usage_summary.summarize_usage", _summarize)

    def _explode_home() -> Path:
        home_calls.append("session_usage")
        raise AssertionError("process home guessed")

    monkeypatch.setattr("hermes_constants.get_hermes_home", _explode_home)
    home_calls.clear()
    return opened, seen, home_calls, process_home


def _close_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.name == "clawd.runtime"]


def test_close_success_returns_the_same_payload(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger="clawd.runtime")
    home = tmp_path / "bound-home"
    payload = {"days": 7, "totals": {"sessions": 3}, "marker": object()}
    opened, seen, home_calls, process_home = _bind(monkeypatch, tmp_path, summary=payload)
    result = session_usage(_mod(home), 7)
    assert result is payload
    assert seen["days"] == 7
    assert seen["db"] is opened[0]
    assert opened[0].read_only is True
    assert opened[0].db_path == home / "state.db"
    assert opened[0].db_path != process_home / "state.db"
    assert opened[0].closed == 1
    assert home_calls == []
    assert _close_records(caplog) == []


def test_summary_exception_keeps_suppressed_usage_chain(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger="clawd.runtime")
    home = tmp_path / "bound-home"
    summary = _SummaryFailure(f"summary {_SQL} {_SUMMARY_SECRET} {_PATH}")
    opened, _seen, home_calls, _process_home = _bind(monkeypatch, tmp_path, summary=summary)
    with pytest.raises(ApiError) as caught:
        session_usage(_mod(home), 30)
    exc = caught.value
    assert exc.status == 503 and exc.code == "usage_unavailable"
    assert exc.message == "用量暂不可用"
    assert exc.__cause__ is None and exc.__suppress_context__ is True
    assert exc.__context__ is summary
    assert not hasattr(exc, "usage_close_failures")
    assert opened[0].read_only is True
    assert opened[0].db_path == home / "state.db"
    assert opened[0].closed == 1
    assert home_calls == []
    assert _close_records(caplog) == []
    _assert_public_clean(exc, [])


def test_close_exception_is_503_and_does_not_return_payload(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger="clawd.runtime")
    home = tmp_path / "bound-home"
    payload = {"days": 7, "marker": "must-not-return"}
    close_error = _CloseFailure(f"close {_PATH} {_SQL} {_SECRET}")
    opened, seen, home_calls, _process_home = _bind(
        monkeypatch, tmp_path, close_error=close_error, summary=payload,
    )
    mod = _mod(home)
    with pytest.raises(ApiError) as caught:
        session_usage(mod, 90)
    exc = caught.value
    assert exc is not payload
    assert seen["db"] is opened[0]
    assert opened[0].closed == 1
    assert opened[0].read_only is True
    assert exc.status == 503 and exc.code == "usage_unavailable"
    assert exc.message == "用量暂不可用"
    assert exc.__cause__ is None and exc.__suppress_context__ is True
    assert exc.usage_close_failures == (close_error,)
    assert exc.__notes__ == ["usage_db_close_failed:_CloseFailure"]
    records = _close_records(caplog)
    assert [record.getMessage() for record in records] == ["usage_db_close_failed (_CloseFailure)"]
    assert home_calls == []
    _assert_public_clean(exc, records)

    caplog.clear()
    install(mod)
    with pytest.raises(ApiError) as installed:
        mod.get_usage(90)
    assert installed.value.status == 503 and installed.value.code == "usage_unavailable"
    assert installed.value.usage_close_failures[0] is close_error
    assert "session_usage" in mod._clawd_native
    assert home_calls == []


def test_generic_summary_and_close_keep_primary_and_close_evidence(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger="clawd.runtime")
    home = tmp_path / "bound-home"
    summary = _SummaryFailure(f"summary {_SQL} {_SUMMARY_SECRET} {_PATH}")
    close_error = _CloseFailure(f"close {_PATH} {_SQL} {_SECRET}")
    opened, _seen, home_calls, _process_home = _bind(
        monkeypatch, tmp_path, summary=summary, close_error=close_error,
    )
    with pytest.raises(ApiError) as caught:
        session_usage(_mod(home), 7)
    exc = caught.value
    assert type(exc) is ApiError
    assert exc is not summary and exc is not close_error
    assert exc.status == 503 and exc.code == "usage_unavailable"
    assert exc.message == "用量暂不可用"
    assert exc.__cause__ is None and exc.__suppress_context__ is True
    assert exc.__context__ is summary
    assert exc.__context__ is not close_error
    assert exc.usage_close_failures == (close_error,)
    assert "usage_db_close_failed:_CloseFailure" in exc.__notes__
    assert opened[0].closed == 1
    assert home_calls == []
    records = _close_records(caplog)
    assert [record.getMessage() for record in records] == ["usage_db_close_failed (_CloseFailure)"]
    _assert_public_clean(exc, records)


def test_api_error_and_close_keep_original_cause_chain(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger="clawd.runtime")
    home = tmp_path / "bound-home"
    cause = RuntimeError("summary-cause-marker")
    primary = ApiError(409, "session_busy", "会话正在运行")
    close_error = _CloseFailure(f"close {_PATH} {_SQL} {_SECRET}")

    def _raise_primary(_db, _days):
        raise primary from cause

    opened, _seen, home_calls, _process_home = _bind(
        monkeypatch, tmp_path, summary=_raise_primary, close_error=close_error,
    )
    with pytest.raises(ApiError) as caught:
        session_usage(_mod(home), 7)
    exc = caught.value
    assert exc is primary
    assert exc.status == 409 and exc.code == "session_busy"
    assert exc.message == "会话正在运行"
    assert exc.__cause__ is cause
    assert exc.__cause__ is not close_error
    assert exc.usage_close_failures == (close_error,)
    assert "usage_db_close_failed:_CloseFailure" in exc.__notes__
    assert opened[0].closed == 1
    assert home_calls == []
    rendered = _rendered(exc)
    assert "summary-cause-marker" in rendered
    assert _SECRET not in rendered and _PATH not in rendered and _SQL not in rendered
    records = _close_records(caplog)
    assert records and all(_SECRET not in record.getMessage() for record in records)
    _assert_public_clean(exc, records)


def test_db_init_failure_does_not_close(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger="clawd.runtime")
    home = tmp_path / "bound-home"
    init_error = _InitFailure(f"init {_PATH} {_SQL} {_SECRET}")
    opened, seen, home_calls, process_home = _bind(
        monkeypatch, tmp_path, init_error=init_error, summary={"must-not-run": True},
    )
    with pytest.raises(ApiError) as caught:
        session_usage(_mod(home), 7)
    exc = caught.value
    assert exc.status == 503 and exc.code == "usage_unavailable"
    assert exc.message == "用量暂不可用"
    assert exc.__cause__ is None and exc.__suppress_context__ is True
    assert exc.__context__ is init_error
    assert not hasattr(exc, "usage_close_failures")
    assert len(opened) == 1
    assert opened[0].closed == 0
    assert opened[0].read_only is True
    assert opened[0].db_path == home / "state.db"
    assert opened[0].db_path != process_home / "state.db"
    assert "db" not in seen
    assert home_calls == []
    assert _close_records(caplog) == []
    _assert_public_clean(exc, [])

    def _no_db(*_args, **_kwargs):
        raise AssertionError("db opened")

    monkeypatch.setattr("hermes_state.SessionDB", _no_db)
    mod = _mod(home)
    mod.HERMES_HOME = Path("relative-home")
    with pytest.raises(ApiError) as relative:
        session_usage(mod, 7)
    assert relative.value.status == 503 and relative.value.code == "usage_unavailable"
    mod.HERMES_HOME = None
    with pytest.raises(ApiError) as missing:
        session_usage(mod, 7)
    assert missing.value.status == 503 and missing.value.__cause__ is None
    assert home_calls == []
