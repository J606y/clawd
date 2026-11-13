"""Usage route, install, and page-channel contracts. Prepared only. Not a pass.

HTTP is not started. Database failures are simulated or confined to tmp
SessionDB handles. This file does not import the sidecar module.
"""

from __future__ import annotations

import ast
import asyncio
import json
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from multidict import MultiDict, MultiDictProxy

from clawd_server.runtime_native import install
from gateway.platforms.clawd_runtime_routes import handle_usage, http_routes, parse_usage_days
from hermes_state import SessionDB

_REPO = Path(__file__).resolve().parents[2]
_BANNED = ("/root/.hermes", "/usr/local/lib/hermes-agent")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class _Opened:
    def __init__(self) -> None:
        self.paths: list[tuple[Path, bool]] = []
        self.closed: list[Path] = []


class _FakeDB:
    def __init__(self, db_path=None, read_only=False, opened: _Opened | None = None) -> None:
        self.db_path = Path(db_path)
        self.read_only = read_only
        if opened is not None:
            opened.paths.append((self.db_path, read_only))
            self._opened = opened

    def close(self) -> None:
        self._opened.closed.append(self.db_path)

    def flush_for_read(self) -> bool:
        return True

    def _read_ctx(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args) -> bool:
        return False

    def execute(self, sql, params):
        assert "?" in sql and "started_at > ?" in sql
        assert len(params) == 1 and type(params[0]) is float
        return ()


def _request(*days: str):
    return SimpleNamespace(query=MultiDictProxy(MultiDict([("days", value) for value in days])))


def _body(response) -> dict:
    return json.loads(response.body.decode())


def _function(path: Path, name: str) -> ast.AST:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} missing from {path}")


def _calls(node: ast.AST) -> set[str]:
    found = set()
    for item in ast.walk(node):
        if isinstance(item, ast.Call):
            func = item.func
            if isinstance(func, ast.Name):
                found.add(func.id)
            elif isinstance(func, ast.Attribute):
                found.add(func.attr)
    return found


def test_route_is_registered_before_profile_catchall():
    rows = http_routes(object())
    assert ("GET", "/api/runtime/usage") in [(method, path) for method, path, _handler in rows]
    server = (_REPO / "gateway/platforms/api_server.py").read_text(encoding="utf-8")
    table = server.find("for method, path, handler in self._http_route_table()")
    catch = server.find('"/p/{profile}/{tail:.*}"')
    assert 0 < table < catch
    assert server.find("routes.extend(_clawd_runtime_http_routes(self))") > 0


def test_handlers_do_not_open_sqlite_or_guess_process_home():
    summary = ast.parse((_REPO / "hermes_state_usage_summary.py").read_text(encoding="utf-8"))
    route = _function(_REPO / "gateway/platforms/clawd_runtime_routes.py", "handle_usage")
    native = _function(_REPO / "clawd_server/runtime_native.py", "session_usage")
    for node in (summary, route, native):
        names = {item.id for item in ast.walk(node) if isinstance(item, ast.Name)}
        attrs = {item.attr for item in ast.walk(node) if isinstance(item, ast.Attribute)}
        assert "get_hermes_home" not in names | attrs
        assert "connect" not in _calls(node)
    assert "sqlite3" not in {item.id for item in ast.walk(summary) if isinstance(item, ast.Name)}
    route_names = {item.id for item in ast.walk(route) if isinstance(item, ast.Name)}
    assert {"_auth", "_db", "summarize_usage"} <= route_names | _calls(route)
    assert "HERMES_HOME" in {item.attr for item in ast.walk(native) if isinstance(item, ast.Attribute)}


def test_days_rejects_missing_illegal_boolean_and_duplicates():
    assert parse_usage_days(_request("7")) == 7
    assert parse_usage_days(_request("30")) == 30
    assert parse_usage_days(_request("90")) == 90
    for values, code in (
        ((), "invalid_days"),
        (("1",), "invalid_days"),
        (("07",), "invalid_days"),
        (("7.0",), "invalid_days"),
        (("true",), "invalid_days"),
        (("false",), "invalid_days"),
        (("True",), "invalid_days"),
        ((" 7",), "invalid_days"),
        (("7", "7"), "ambiguous_days"),
        (("7", "30"), "ambiguous_days"),
        (("true", "false"), "ambiguous_days"),
    ):
        response = parse_usage_days(_request(*values))
        assert response.status == 400
        body = _body(response)
        assert body["error"]["code"] == code
        assert "state.db" not in body["error"]["message"]
        assert "SELECT" not in body["error"]["message"]


def test_auth_and_bad_days_do_not_touch_the_adapter_db():
    class Adapter:
        def __init__(self, deny: bool) -> None:
            self.deny = deny

        def _check_auth(self, request):
            if self.deny:
                from aiohttp import web
                return web.json_response({"error": {"code": "unauthorized"}}, status=401)
            return None

        async def _ensure_session_db_async(self):
            raise AssertionError("adapter db opened")

    denied = asyncio.run(handle_usage(Adapter(True), _request("true")))
    assert denied.status == 401
    rejected = asyncio.run(handle_usage(Adapter(False), _request("true", "false")))
    assert rejected.status == 400 and _body(rejected)["error"]["code"] == "ambiguous_days"


def test_database_error_is_503_and_does_not_leak(tmp_path):
    secret = "unable to open /root/.hermes/state.db SELECT sk-secret"

    class Boom:
        def _check_auth(self, request):
            return None

        async def _ensure_session_db_async(self):
            return self

        def flush_for_read(self) -> bool:
            return True

        def _read_ctx(self):
            raise sqlite3.OperationalError(secret)

        def close(self) -> None:
            raise AssertionError("adapter db closed")

    response = asyncio.run(handle_usage(Boom(), _request("30")))
    assert response.status == 503
    body = _body(response)
    assert body["error"]["code"] == "usage_unavailable"
    text = response.body.decode()
    assert "sk-secret" not in text and "/root/.hermes" not in text and "SELECT" not in text
    assert "totals" not in body

    class Missing:
        def _check_auth(self, request):
            return None

    missing = asyncio.run(handle_usage(Missing(), _request("7")))
    assert missing.status == 503
    assert _body(missing)["error"]["code"] == "session_db_unavailable"


def test_route_reads_only_the_adapter_db(tmp_path):
    started = time.time() - 3600
    left = SessionDB(db_path=tmp_path / "left" / "state.db")
    right = SessionDB(db_path=tmp_path / "right" / "state.db")
    try:
        left.create_session("left", "cli", model="left-model")
        right.create_session("right", "tui", model="right-model")
        left._write_sql("UPDATE sessions SET started_at=?, input_tokens=11, output_tokens=1 WHERE id=?", (started, "left"))
        right._write_sql("UPDATE sessions SET started_at=?, input_tokens=22, output_tokens=2 WHERE id=?", (started, "right"))

        class Adapter:
            def __init__(self, db) -> None:
                self.db = db

            def _check_auth(self, request):
                return None

            async def _ensure_session_db_async(self):
                return self.db

        response = asyncio.run(handle_usage(Adapter(left), _request("90")))
        assert response.status == 200
        body = _body(response)
        assert body["totals"]["input"] == 11
        assert [row["id"] for row in body["top"]] == ["left"]
        again = left.get_session("left")
        assert again is not None
    finally:
        left.close()
        right.close()


def test_install_replaces_get_usage_without_private_sqlite(tmp_path, monkeypatch):
    opened = _Opened()

    def _fake_db(*args, **kwargs):
        return _FakeDB(*args, opened=opened, **kwargs)

    def _guessed_home():
        raise AssertionError("process home guessed")

    monkeypatch.setattr("hermes_state.SessionDB", _fake_db)
    monkeypatch.setattr(sqlite3, "connect", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("private sqlite")))
    monkeypatch.setattr("hermes_constants.get_hermes_home", _guessed_home)
    mod = SimpleNamespace(HERMES_HOME=tmp_path, ApiError=ApiError, _clawd_native=())
    install(mod)
    assert "session_usage" in mod._clawd_native
    view = mod.get_usage(7)
    assert view["days"] == 7 and view["totals"]["sessions"] == 0 and view["daily"] == []
    assert opened.paths == [(tmp_path / "state.db", True)]
    assert opened.closed == [tmp_path / "state.db"]
    with pytest.raises(ApiError) as bad:
        mod.get_usage(True)
    assert bad.value.status == 400 and bad.value.code == "invalid_days"
    assert opened.paths == [(tmp_path / "state.db", True)]


def test_unbound_or_production_home_does_not_open_or_leak(monkeypatch):
    def _no_db(*args, **kwargs):
        raise AssertionError("db opened")

    monkeypatch.setattr("hermes_state.SessionDB", _no_db)
    monkeypatch.setattr(Path, "resolve", lambda self, *args, **kwargs: self)
    mod = SimpleNamespace(HERMES_HOME=Path("/root/.hermes"), ApiError=ApiError)
    install(mod)
    with pytest.raises(ApiError) as caught:
        mod.get_usage(30)
    assert caught.value.status == 503 and caught.value.code == "usage_unavailable"
    assert caught.value.message == "用量暂不可用"
    assert "/root/.hermes" not in caught.value.message and "state.db" not in caught.value.message
    mod.HERMES_HOME = Path("relative-home")
    with pytest.raises(ApiError) as relative:
        mod.get_usage(30)
    assert relative.value.status == 503
    mod.HERMES_HOME = None
    with pytest.raises(ApiError) as missing:
        mod.get_usage(30)
    assert missing.value.status == 503


def test_page_uses_the_runtime_channel_and_legacy_function_remains():
    sidecar = (_REPO / "apps/panel/src/react/lib/sidecar.ts").read_text(encoding="utf-8")
    usage = next(line for line in sidecar.splitlines() if "usage:" in line and "request<Usage>" in line)
    assert "api/runtime/usage?" in usage
    assert "BASE" not in usage
    assert "usage?days" not in usage
    assert "panel-sidecar" not in usage
    queries = (_REPO / "apps/panel/src/react/lib/queries.ts").read_text(encoding="utf-8")
    assert "sidecar.usage(days)" in queries
    page = (_REPO / "apps/panel/src/react/pages/settings/usage-tab.tsx").read_text(encoding="utf-8")
    assert "useUsage(days, visible)" in page
    legacy = (_REPO / "clawd_server/panel_core.py").read_text(encoding="utf-8")
    assert "def get_usage(days: int)" in legacy
    assert "sqlite3.connect" in legacy
    tsconfig = json.loads((_REPO / "apps/panel/tsconfig.json").read_text(encoding="utf-8"))
    assert tsconfig["include"] == ["src/react"]


def test_root_module_is_covered_by_existing_discovery():
    pyproject = (_REPO / "pyproject.toml").read_text(encoding="utf-8")
    assert "py-modules =" not in pyproject
    setup = (_REPO / "setup.py").read_text(encoding="utf-8")
    assert "def _root_py_modules" in setup
    assert 'name.endswith(".py")' in setup
    assert "setup.py" in setup
    assert (_REPO / "hermes_state_usage_summary.py").is_file()
    nix = (_REPO / "nix/lib.nix").read_text(encoding="utf-8")
    assert "hermes_state_usage_summary.py" not in nix
    for banned in _BANNED:
        assert banned not in str(_REPO / "hermes_state_usage_summary.py")
