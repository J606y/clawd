"""Installed run-wait and same-home idempotency contracts.

Prepared only. Not a pass. These call the real projection and the real
``native.install`` replacement. Simulated fixtures are named and documented
as simulated. They do not start HTTP, do not scrape logs, and do not rewrite
the uninstalled sidecar log tests.
"""

from __future__ import annotations

import ast
import inspect
import re
import threading
import time
from pathlib import Path

import pytest

from clawd_server.native import install as native_install
from clawd_server.runtime_native import bind_run_store, store_for_db
from gateway.platforms.api_server_run_idempotency import RunIdempotencyStore
from gateway.platforms.clawd_runtime_routes import http_routes, project_run_wait, session_run_view
from hermes_constants import (
    get_hermes_home,
    reset_hermes_home_override,
    set_hermes_home_override,
)
from hermes_state import SessionDB
from hermes_state_runtime import RuntimeStoreError

_BANNED = (
    "/root/.hermes",
    "/usr/local/lib/hermes-agent",
    "/root/hermes-workspace/hermes-panel",
)
_SESSION = "sess01"
_OTHER = "sess02"
_TERMINAL = ("completed", "failed", "cancelled", "interrupted")
_LIVE = ("queued", "running", "waiting_for_approval", "stopping", "unknown")
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{5,79}$")

# Listed for a later isolated run. This module does not call them and they are
# not an end-to-end chat, panel, or browser proof.
REAL_HTTP_INTEGRATION_NOT_E2E = (
    {
        "entry": "GET /panel-core/run-wait",
        "panel_core_path": "/run-wait",
        "requires": "PanelServer.start then bind then native.install, isolated home, no production state",
        "not_e2e": "not a chat turn, not a browser, not the uninstalled log scraper",
    },
    {
        "entry": "GET /panel-core/sessions/run",
        "panel_core_path": "/sessions/run",
        "requires": "same installed panel-core process and isolated home",
        "not_e2e": "not proof that a gateway /v1/runs admission succeeded",
    },
    {
        "entry": "GET /panel-core/sessions/run/resolve",
        "panel_core_path": "/sessions/run/resolve",
        "requires": "same installed panel-core process and isolated home",
        "not_e2e": "not proof of cross-process replay",
    },
)


class SimulatedApiError(Exception):
    """SIMULATED panel-core error. Same constructor as the panel-core ApiError, not the panel-core class."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class SimulatedHandler:
    """SIMULATED handler class so admin route install can wrap dispatch. No socket."""

    def _route(self, method: str, path: str, query: dict[str, str]):
        raise AssertionError("simulated handler was dispatched")

    def _dispatch(self, method: str):
        raise AssertionError("simulated handler was dispatched")


class SimulatedBoundStore:
    """SIMULATED already-registered store. Not sqlite and not a second database."""

    def __init__(self, path: str) -> None:
        self._db_path = path
        self.durable = True


class SimulatedDiscoverFailure:
    """SIMULATED run index whose discovery raises."""

    def discover_for_session(self, session_id: str, **kwargs):
        raise RuntimeError("simulated discover failure")


class SimulatedNonDictRun:
    """SIMULATED run index that returns a non-dict row."""

    def discover_for_session(self, session_id: str, **kwargs):
        return ["simulated-not-a-dict"]


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


def _run_id(n: int) -> str:
    body = f"{n:032x}"
    assert len(body) == 32
    return "run_" + body


def _lease(db, session_id: str, *, busy: bool) -> None:
    if not busy:
        return
    now = time.time()
    db._write_sql(
        "INSERT INTO session_turn_leases (conversation_id, holder, acquired_at, expires_at) "
        "VALUES (?, ?, ?, ?)",
        (session_id, "unit-holder", now, now + 3600),
    )


def _reserve(store: RunIdempotencyStore, session_id: str, status: str, n: int) -> str:
    run_id = _run_id(n)
    store.reserve(
        "tenant",
        f"key-{n}",
        "fingerprint",
        run_id,
        {"session_id": session_id, "status": status, "run_id": run_id},
    )
    return run_id


def _bait_log(home: Path, session_id: str) -> Path:
    """Log text the uninstalled scraper would turn into retrying plus remainingSeconds."""
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - 1))
    line = (
        f"{stamp},000 WARNING [{session_id}] agent.conversation_loop: "
        "Retrying API call in 60.0s (attempt 2/3) model=fixture\n"
    )
    fail = (
        f"{stamp},000 WARNING [{session_id}] agent.conversation_loop: "
        "API call failed (attempt 2/3) error_type=RateLimitError model=fixture "
        "summary=HTTP 429: too many requests\n"
    )
    path = home / "logs" / "agent.log"
    path.parent.mkdir()
    path.write_text(fail + line, encoding="utf-8")
    (home / "agent.log").write_text(fail + line, encoding="utf-8")
    return path


def _forbid_planted_log_reads(paths: list[Path]):
    planted = {path.resolve() for path in paths}
    real_open = Path.open
    real_stat = Path.stat

    def open_guard(self, *args, **kwargs):
        if self.resolve() in planted:
            raise AssertionError(f"installed projection read a planted agent log: {self}")
        return real_open(self, *args, **kwargs)

    def stat_guard(self, *args, **kwargs):
        if self.resolve() in planted:
            raise AssertionError(f"installed projection stat a planted agent log: {self}")
        return real_stat(self, *args, **kwargs)

    return open_guard, stat_guard, real_open, real_stat


def _assert_durable_unknown(view: dict) -> None:
    assert view["state"] == "unknown"
    assert view["available"] is False
    assert view["source"] == "durable"
    assert "remainingSeconds" not in view
    assert view.get("state") != "idle"
    assert view.get("state") != "retrying"
    assert "httpStatus" not in view
    assert isinstance(view["observedAtMs"], int)


@pytest.fixture(autouse=True)
def _clear_run_store_registry():
    from clawd_server import runtime_native

    saved = dict(runtime_native._STORES_BY_HOME)
    runtime_native._STORES_BY_HOME.clear()
    try:
        yield
    finally:
        runtime_native._STORES_BY_HOME.clear()
        runtime_native._STORES_BY_HOME.update(saved)


def test_projection_signatures_are_keyword_stable():
    wait = inspect.signature(project_run_wait)
    assert list(wait.parameters) == ["db", "store", "session_id", "turn_started_at_ms"]
    view = inspect.signature(session_run_view)
    assert list(view.parameters)[:3] == ["db", "store", "session_id"]
    assert view.parameters["idempotency_key"].kind is inspect.Parameter.KEYWORD_ONLY
    assert view.parameters["scope"].kind is inspect.Parameter.KEYWORD_ONLY
    assert list(inspect.signature(store_for_db).parameters) == ["db"]
    assert list(inspect.signature(native_install).parameters) == ["mod"]
    assert list(inspect.signature(bind_run_store).parameters) == ["store"]
    discover = inspect.signature(RunIdempotencyStore.discover_for_session)
    assert discover.parameters["idempotency_key"].kind is inspect.Parameter.KEYWORD_ONLY
    assert discover.parameters["scope"].kind is inspect.Parameter.KEYWORD_ONLY


def test_gateway_runtime_table_has_no_run_wait_route():
    rows = http_routes(object())
    paths = [path for _method, path, _handler in rows]
    assert "/api/runtime/sessions/{session_id}/run" in paths
    assert all("run-wait" not in path and "run_wait" not in path for path in paths)


def _assert_module_does_not_call(*names: str) -> None:
    banned = set(names)
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[-1] not in banned
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                assert alias.name not in banned
        elif isinstance(node, ast.Call):
            func = node.func
            called = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
            assert called not in banned


def test_http_entries_are_listed_and_not_called_here():
    assert len(REAL_HTTP_INTEGRATION_NOT_E2E) == 3
    assert all(item["not_e2e"] and item["requires"] for item in REAL_HTTP_INTEGRATION_NOT_E2E)
    _assert_module_does_not_call("ThreadingHTTPServer", "urlopen", "urlretrieve")
    text = Path(__file__).read_text(encoding="utf-8")
    assert "panel-sidecar" not in text.split("REAL_HTTP_INTEGRATION_NOT_E2E", 1)[0]


def test_uninstalled_panel_core_log_case_keeps_original_semantics():
    root = Path(__file__).resolve().parents[2]
    wait = (root / "apps/panel/tests/test_run_wait.py").read_text(encoding="utf-8")
    assert "self.assertEqual(w['source'],'log')" in wait
    assert "self.assertEqual(w['state'],'retrying')" in wait
    assert "project_run_wait" not in wait
    assert "native.install" not in wait
    session = (root / "apps/panel/tests/test_session_run.py").read_text(encoding="utf-8")
    assert "native.install" not in session
    assert "project_run_wait" not in session


def test_missing_session_is_not_found_not_unknown(tmp_path):
    home = _home(tmp_path, "missing-session")
    with SessionDB(db_path=home / "state.db") as db:
        with pytest.raises(RuntimeStoreError) as caught:
            project_run_wait(db, None, _SESSION)
        assert caught.value.reason == "not_found"
        with pytest.raises(RuntimeStoreError) as run_caught:
            session_run_view(db, None, _SESSION)
        assert run_caught.value.reason == "not_found"


def test_missing_run_and_lease_stay_unknown_without_zero_seconds(tmp_path):
    home = _home(tmp_path, "missing-facts")
    bait = _bait_log(home, _SESSION)
    open_guard, stat_guard, real_open, real_stat = _forbid_planted_log_reads(
        [bait, home / "agent.log"]
    )
    with SessionDB(db_path=home / "state.db") as db:
        db.create_session(_SESSION, "api_server")
        Path.open = open_guard
        Path.stat = stat_guard
        try:
            view = project_run_wait(db, None, _SESSION, 1_700_000_000_000.0)
        finally:
            Path.open = real_open
            Path.stat = real_stat
    _assert_durable_unknown(view)
    assert view["busy"] is False
    assert view["turnStartedAtMs"] == 1_700_000_000_000.0
    assert bait.read_text(encoding="utf-8").startswith("20")


def test_lease_read_failure_stays_unknown_and_does_not_invent_idle(tmp_path, monkeypatch):
    """SIMULATED lease reader. The session row is real."""
    home = _home(tmp_path, "lease-fail")

    def boom(_db, _session_id):
        raise RuntimeError("simulated lease read failure")

    monkeypatch.setattr("hermes_state_lease_read.read_session_turn_lease", boom)
    with SessionDB(db_path=home / "state.db") as db:
        db.create_session(_SESSION, "api_server")
        view = project_run_wait(db, None, _SESSION)
        with pytest.raises(RuntimeError, match="simulated lease read failure"):
            session_run_view(db, None, _SESSION)
    _assert_durable_unknown(view)
    assert "busy" not in view


def test_non_bool_lease_stays_unknown(tmp_path, monkeypatch):
    """SIMULATED lease payload. Not a durable lease row."""
    home = _home(tmp_path, "bad-lease")

    def bad(_db, _session_id):
        return {"busy": "yes"}

    monkeypatch.setattr("hermes_state_lease_read.read_session_turn_lease", bad)
    with SessionDB(db_path=home / "state.db") as db:
        db.create_session(_SESSION, "api_server")
        view = project_run_wait(db, None, _SESSION)
    _assert_durable_unknown(view)
    assert "busy" not in view


def test_discover_failure_stays_unknown(tmp_path):
    """SIMULATED run index. Lease row is absent, so a swallowed error must not become idle."""
    home = _home(tmp_path, "discover-fail")
    with SessionDB(db_path=home / "state.db") as db:
        db.create_session(_SESSION, "api_server")
        view = project_run_wait(db, SimulatedDiscoverFailure(), _SESSION)
    _assert_durable_unknown(view)
    assert "busy" not in view


def test_non_dict_run_is_not_idle(tmp_path):
    """SIMULATED non-dict discovery result."""
    home = _home(tmp_path, "non-dict-run")
    with SessionDB(db_path=home / "state.db") as db:
        db.create_session(_SESSION, "api_server")
        view = project_run_wait(db, SimulatedNonDictRun(), _SESSION)
    _assert_durable_unknown(view)
    assert view["busy"] is False
    assert "runStatus" not in view


@pytest.mark.parametrize("status", _TERMINAL)
def test_terminal_and_idle_lease_is_the_only_available_state(tmp_path, status):
    home = _home(tmp_path, f"terminal-{status}")
    bait = _bait_log(home, _SESSION)
    store = RunIdempotencyStore(str(home / "runs_idempotency.db"))
    try:
        with SessionDB(db_path=home / "state.db") as db:
            db.create_session(_SESSION, "api_server")
            _reserve(store, _SESSION, status, 1)
            open_guard, stat_guard, real_open, real_stat = _forbid_planted_log_reads([bait])
            Path.open = open_guard
            Path.stat = stat_guard
            try:
                view = project_run_wait(db, store, _SESSION)
            finally:
                Path.open = real_open
                Path.stat = real_stat
    finally:
        store.close()
    assert view["state"] == "idle"
    assert view["available"] is True
    assert view["source"] == "durable"
    assert view["busy"] is False
    assert view["runStatus"] == status
    assert "remainingSeconds" not in view
    assert "httpStatus" not in view
    assert view.get("state") != "retrying"


@pytest.mark.parametrize("status", _TERMINAL)
def test_terminal_run_with_busy_lease_is_not_available(tmp_path, status):
    home = _home(tmp_path, f"busy-terminal-{status}")
    store = RunIdempotencyStore(str(home / "runs_idempotency.db"))
    try:
        with SessionDB(db_path=home / "state.db") as db:
            db.create_session(_SESSION, "api_server")
            _lease(db, _SESSION, busy=True)
            _reserve(store, _SESSION, status, 2)
            view = project_run_wait(db, store, _SESSION)
    finally:
        store.close()
    _assert_durable_unknown(view)
    assert view["busy"] is True
    assert view["runStatus"] == status


@pytest.mark.parametrize("status", _LIVE)
def test_live_run_status_stays_unknown_when_lease_is_idle(tmp_path, status):
    home = _home(tmp_path, f"live-{status}")
    store = RunIdempotencyStore(str(home / "runs_idempotency.db"))
    try:
        with SessionDB(db_path=home / "state.db") as db:
            db.create_session(_SESSION, "api_server")
            _reserve(store, _SESSION, status, 3)
            view = project_run_wait(db, store, _SESSION)
    finally:
        store.close()
    _assert_durable_unknown(view)
    assert view["busy"] is False
    assert view["runStatus"] == status


class SimulatedReturnedStatus:
    """SIMULATED discovery result. Bypasses store validation so an unlisted status is visible."""

    def __init__(self, status: str) -> None:
        self._status = status

    def discover_for_session(self, session_id: str, **kwargs):
        return {"status": self._status, "session_id": session_id}


def test_illegal_stored_status_stays_unknown(tmp_path):
    home = _home(tmp_path, "illegal-stored-status")
    store = RunIdempotencyStore(str(home / "runs_idempotency.db"))
    try:
        with SessionDB(db_path=home / "state.db") as db:
            db.create_session(_SESSION, "api_server")
            _reserve(store, _SESSION, "success", 4)
            view = project_run_wait(db, store, _SESSION)
    finally:
        store.close()
    _assert_durable_unknown(view)
    assert "busy" not in view
    assert "runStatus" not in view


def test_returned_unlisted_status_is_not_available(tmp_path):
    """SIMULATED run dict. A real store refuses this status before it can be projected."""
    home = _home(tmp_path, "unlisted-status")
    with SessionDB(db_path=home / "state.db") as db:
        db.create_session(_SESSION, "api_server")
        view = project_run_wait(db, SimulatedReturnedStatus("success"), _SESSION)
    _assert_durable_unknown(view)
    assert view["busy"] is False
    assert view["runStatus"] == "success"


def test_other_sessions_terminal_run_does_not_free_this_session(tmp_path):
    home = _home(tmp_path, "other-session")
    store = RunIdempotencyStore(str(home / "runs_idempotency.db"))
    try:
        with SessionDB(db_path=home / "state.db") as db:
            db.create_session(_SESSION, "api_server")
            db.create_session(_OTHER, "api_server")
            _reserve(store, _OTHER, "completed", 5)
            view = project_run_wait(db, store, _SESSION)
    finally:
        store.close()
    _assert_durable_unknown(view)
    assert "runStatus" not in view


def test_session_run_view_missing_store_with_key_is_unavailable_not_not_found(tmp_path):
    home = _home(tmp_path, "keyed-missing-store")
    with SessionDB(db_path=home / "state.db") as db:
        db.create_session(_SESSION, "api_server")
        for key in ("key123", ""):
            with pytest.raises(RuntimeStoreError) as caught:
                session_run_view(db, None, _SESSION, idempotency_key=key)
            assert caught.value.reason == "storage_unavailable"
            assert caught.value.reason != "not_found"
        view = session_run_view(db, None, _SESSION)
    assert view == {"run": None, "busy": False}
    assert not (home / "runs_idempotency.db").exists()


def test_session_run_view_reads_a_real_store_without_inventing_a_row(tmp_path):
    home = _home(tmp_path, "real-run-view")
    store = RunIdempotencyStore(str(home / "runs_idempotency.db"))
    try:
        with SessionDB(db_path=home / "state.db") as db:
            db.create_session(_SESSION, "api_server")
            _lease(db, _SESSION, busy=True)
            _reserve(store, _SESSION, "running", 6)
            found = session_run_view(
                db, store, _SESSION, idempotency_key="key-6", scope="tenant"
            )
            wrong_scope = session_run_view(
                db, store, _SESSION, idempotency_key="key-6", scope="other"
            )
            no_key = session_run_view(db, store, _SESSION)
    finally:
        store.close()
    assert found["busy"] is True
    assert found["resolution"] == {"idempotency_key": "key-6", "state": "found"}
    assert found["run"]["status"] == "running"
    assert found["run"]["session_id"] == _SESSION
    assert wrong_scope["resolution"]["state"] == "not_found"
    assert wrong_scope["run"] is None
    assert no_key["run"]["status"] == "running"
    assert "resolution" not in no_key


def test_missing_idempotency_file_does_not_construct_a_shadow_store(tmp_path, monkeypatch):
    """Real directory. SIMULATED constructor that fails if the missing-file path calls it."""
    home = _home(tmp_path, "no-shadow")
    calls: list[str] = []

    class Boom:
        def __init__(self, path: str) -> None:
            calls.append(path)
            raise AssertionError("missing file must not construct RunIdempotencyStore")

    monkeypatch.setattr(
        "gateway.platforms.api_server_run_idempotency.RunIdempotencyStore", Boom
    )
    with SessionDB(db_path=home / "state.db") as db:
        db.create_session(_SESSION, "api_server")
        assert store_for_db(db) is None
    assert calls == []
    assert not (home / "runs_idempotency.db").exists()
    assert not (home / "runs_idempotency.db-wal").exists()
    assert not (home / "runs_idempotency.db-shm").exists()


def test_same_home_registered_store_wins_and_still_does_not_create_a_file(tmp_path):
    """SIMULATED registry entry. The lookup and the missing-file rule are real."""
    home = _home(tmp_path, "registered")
    sentinel = SimulatedBoundStore(str(home / "runs_idempotency.db"))
    bind_run_store(sentinel)
    with SessionDB(db_path=home / "state.db") as db:
        assert store_for_db(db) is sentinel
    assert not (home / "runs_idempotency.db").exists()


def test_existing_same_home_file_is_opened_and_another_home_is_not_created(tmp_path):
    alpha = _home(tmp_path, "alpha")
    beta = _home(tmp_path, "beta")
    existing = RunIdempotencyStore(str(alpha / "runs_idempotency.db"))
    existing.close()
    with SessionDB(db_path=alpha / "state.db") as alpha_db, SessionDB(db_path=beta / "state.db") as beta_db:
        opened = store_for_db(alpha_db)
        try:
            assert opened is not None
            assert opened.durable is True
            assert store_for_db(alpha_db) is opened
            assert store_for_db(beta_db) is None
        finally:
            if opened is not None:
                opened.close()
    assert (alpha / "runs_idempotency.db").is_file()
    assert not (beta / "runs_idempotency.db").exists()


def test_non_durable_constructor_is_not_registered(tmp_path, monkeypatch):
    """SIMULATED constructor with durable false. The file already exists."""
    home = _home(tmp_path, "not-durable")
    path = home / "runs_idempotency.db"
    path.write_bytes(b"")

    class NonDurable:
        def __init__(self, db_path: str) -> None:
            self._db_path = db_path
            self.durable = False

    monkeypatch.setattr(
        "gateway.platforms.api_server_run_idempotency.RunIdempotencyStore", NonDurable
    )
    from clawd_server import runtime_native

    with SessionDB(db_path=home / "state.db") as db:
        assert store_for_db(db) is None
    assert str(home.resolve()) not in runtime_native._STORES_BY_HOME
    assert path.is_file()


def test_is_file_oserror_returns_none_without_creating(tmp_path, monkeypatch):
    """SIMULATED Path.is_file failure on the idempotency name only."""
    home = _home(tmp_path, "oserror")
    with SessionDB(db_path=home / "state.db") as db:
        real = Path.is_file

        def boom(self):
            if self.name == "runs_idempotency.db":
                raise OSError("simulated is_file failure")
            return real(self)

        monkeypatch.setattr(Path, "is_file", boom)
        assert store_for_db(db) is None
    assert not (home / "runs_idempotency.db").exists()


def _simulated_sidecar():
    """SIMULATED sidecar surface. Not the panel module and not a listening server."""
    mod = type("SimulatedSidecar", (), {})()
    mod.ApiError = SimulatedApiError
    mod.Handler = SimulatedHandler
    mod.SESSION_ID_RE = _SESSION_ID_RE
    mod._LIVE_LOCK = threading.Lock()
    mod._live_task_lease = {}
    mod._live = {}
    mod.LIVE_LEASE_S = 45.0
    mod._live_client_id = lambda: "simulated"
    mod._live_task_drop = lambda: None
    mod._live_task_expire = lambda: 0.0
    mod._live_expire = lambda: 0.0
    mod._live_task_check_renewal = lambda *args, **kwargs: None
    mod._live_task_require_owner = lambda *args, **kwargs: None
    mod._live_task_timeout = lambda *args, **kwargs: None
    mod._LIVE_CAPPED_CLIENTS = set()
    mod.redact = lambda text: text
    mod._LIVE_FRAME_SLOTS = {}

    def uninstalled_log_wait(*args, **kwargs):
        return {
            "state": "idle",
            "source": "log",
            "available": True,
            "remainingSeconds": 0,
        }

    mod.get_run_wait = uninstalled_log_wait
    mod.get_session_run = lambda *args, **kwargs: {"source": "log", "run": {"status": "idle"}}
    mod._uninstalled_log_wait = uninstalled_log_wait
    return mod


def _installed(tmp_path: Path, name: str):
    home = _home(tmp_path, name)
    mod = _simulated_sidecar()
    native_install(mod)
    assert mod._clawd_runtime_installed is True
    assert "run_wait" in mod._clawd_native
    assert "session_run" in mod._clawd_native
    assert mod.get_run_wait is not mod._uninstalled_log_wait
    return home, mod


def test_native_install_global_missing_facts_are_unknown_and_ignore_logs(tmp_path, monkeypatch):
    home, mod = _installed(tmp_path, "installed-unknown")
    bait = _bait_log(home, _SESSION)
    with SessionDB(db_path=home / "state.db") as db:
        db.create_session(_SESSION, "api_server")
    token = set_hermes_home_override(str(home))
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("CLAWD_HOME", str(home))
    open_guard, stat_guard, real_open, real_stat = _forbid_planted_log_reads(
        [bait, home / "agent.log"]
    )
    try:
        assert _refuse(get_hermes_home()) == home
        started = time.time() * 1000
        Path.open = open_guard
        Path.stat = stat_guard
        try:
            view = mod.get_run_wait(_SESSION, started)
        finally:
            Path.open = real_open
            Path.stat = real_stat
    finally:
        reset_hermes_home_override(token)
    _assert_durable_unknown(view)
    assert view["busy"] is False
    assert view["turnStartedAtMs"] == started
    assert not (home / "runs_idempotency.db").exists()


def test_native_install_global_terminal_idle_is_available_without_log_seconds(tmp_path, monkeypatch):
    home, mod = _installed(tmp_path, "installed-idle")
    bait = _bait_log(home, _SESSION)
    store = RunIdempotencyStore(str(home / "runs_idempotency.db"))
    try:
        with SessionDB(db_path=home / "state.db") as db:
            db.create_session(_SESSION, "api_server")
            _reserve(store, _SESSION, "completed", 7)
    finally:
        store.close()
    token = set_hermes_home_override(str(home))
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("CLAWD_HOME", str(home))
    open_guard, stat_guard, real_open, real_stat = _forbid_planted_log_reads([bait])
    try:
        Path.open = open_guard
        Path.stat = stat_guard
        try:
            view = mod.get_run_wait(_SESSION)
        finally:
            Path.open = real_open
            Path.stat = real_stat
    finally:
        reset_hermes_home_override(token)
    assert view["state"] == "idle"
    assert view["available"] is True
    assert view["source"] == "durable"
    assert view["busy"] is False
    assert view["runStatus"] == "completed"
    assert "remainingSeconds" not in view
    assert view.get("source") != "log"


def test_installed_global_missing_session_is_404_not_unknown(tmp_path, monkeypatch):
    home, mod = _installed(tmp_path, "installed-404")
    with SessionDB(db_path=home / "state.db"):
        pass
    token = set_hermes_home_override(str(home))
    monkeypatch.setenv("HERMES_HOME", str(home))
    try:
        with pytest.raises(SimulatedApiError) as caught:
            mod.get_run_wait(_SESSION)
    finally:
        reset_hermes_home_override(token)
    assert caught.value.status == 404
    assert caught.value.code == "session_not_found"


def test_installed_global_rejects_bad_session_and_turn_time(tmp_path):
    _home_unused, mod = _installed(tmp_path, "installed-400")
    del _home_unused
    with pytest.raises(SimulatedApiError) as bad_id:
        mod.get_run_wait("x")
    assert bad_id.value.status == 400
    assert bad_id.value.code == "invalid_session"
    for value in (float("nan"), float("inf"), float("-inf"), -1, 10**18):
        with pytest.raises(SimulatedApiError) as bad_time:
            mod.get_run_wait(_SESSION, value)
        assert bad_time.value.status == 400
        assert bad_time.value.code == "invalid_turn_start"


def test_installed_global_keyed_lookup_does_not_create_a_shadow_db(tmp_path, monkeypatch):
    home, mod = _installed(tmp_path, "installed-key")
    with SessionDB(db_path=home / "state.db") as db:
        db.create_session(_SESSION, "api_server")
    token = set_hermes_home_override(str(home))
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("CLAWD_HOME", str(home))
    try:
        for key in ("key123", ""):
            with pytest.raises(SimulatedApiError) as keyed:
                mod.get_session_run(_SESSION, idempotency_key=key)
            assert keyed.value.status == 503
            assert keyed.value.code == "runtime_unavailable"
        plain = mod.get_session_run(_SESSION)
    finally:
        reset_hermes_home_override(token)
    assert plain == {"run": None, "busy": False}
    assert not (home / "runs_idempotency.db").exists()
    assert not (home / "runs_idempotency.db-wal").exists()
