"""Behavior tests for the clawd panel binding. They start a real API server."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PY = REPO / ".venv" / "bin" / "python"
ART = REPO / "artifacts" / "binding" / "pytest"
RUNTIME = REPO / ".dev-home" / "tools"


def _home(name: str) -> Path:
    path = ART / name
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path


def test_home_refuses_missing_and_production(monkeypatch):
    from clawd_server.home import HomeError, resolve_home

    monkeypatch.delenv("CLAWD_HOME", raising=False)
    monkeypatch.delenv("HERMES_HOME", raising=False)
    with pytest.raises(HomeError, match="CLAWD_HOME"):
        resolve_home(None)
    with pytest.raises(HomeError, match="production"):
        resolve_home("/root/.hermes")
    home = _home("split")
    monkeypatch.setenv("HERMES_HOME", "/tmp/other-home")
    with pytest.raises(HomeError, match="HERMES_HOME"):
        resolve_home(str(home))


def test_auth_hash_origin_and_store():
    from clawd_server.auth import add_user, credential_hash, hash_allowed, load_hashes, origin_allowed

    home = _home("auth")
    digest = credential_hash(" Ops ", "secret")
    assert digest == credential_hash("ops", "secret")
    add_user(home, "Ops", "secret")
    assert hash_allowed(digest, load_hashes(home))
    assert not hash_allowed("0" * 64, load_hashes(home))
    headers = {"Host": "127.0.0.1:9", "Origin": "http://evil.example"}
    assert origin_allowed("POST", headers, "127.0.0.1") is False
    assert origin_allowed("POST", {"Host": "127.0.0.1:9"}, "127.0.0.1") is True
    assert origin_allowed("POST", {"Host": "127.0.0.1:9"}, "10.1.1.1") is False
    assert origin_allowed("GET", {"Host": "127.0.0.1:9", "Origin": "http://evil.example"}, "10.1.1.1") is False
    assert origin_allowed("GET", {"Host": "127.0.0.1:9"}, "10.1.1.1") is True


def test_bind_disables_watcher_and_production_paths(monkeypatch):
    home = _home("bind")
    monkeypatch.setenv("CLAWD_HOME", str(home))
    monkeypatch.setenv("HERMES_HOME", str(home))
    from clawd_server.bind import bind, clawd_command, load_sidecar, prepare_env

    prepare_env(home, "http://127.0.0.1:9")
    mod = load_sidecar()
    command = clawd_command(home)
    bind(mod, home, command)
    assert mod.HERMES_BIN != "/usr/local/bin/hermes"
    assert "/usr/local/lib/hermes-agent" not in str(mod.HERMES_SRC)
    assert mod.FILE_ROOTS["uploads"]["path"] == home / "uploads"
    assert "/root/hermes-uploads" not in str(mod.FILE_ROOTS["uploads"]["path"])
    assert mod.RESTARTABLE == set()
    with pytest.raises(mod.ApiError) as restart:
        mod.restart_service("hermes-gateway")
    assert restart.value.status == 403
    with pytest.raises(RuntimeError, match="auto-continue"):
        mod._continue_watcher()
    assert mod._clawd_saved_continue_watcher is not None
    names = [thread.name for thread in threading.enumerate()]
    assert "auto-continue" not in names
    assert mod._clawd_native == ("config_set", "skills.disabled", "message_origins")
    assert mod.update_session_folders.__module__.endswith("hermes_panel_sidecar")
    created = mod.update_session_folders({"action": "create", "name": "旧文件夹"})
    assert created["folders"][0]["name"] == "旧文件夹"
    assert "projects" not in created

    def boom(*_args, **_kwargs):
        raise AssertionError("native path must not shell out")

    monkeypatch.setattr(mod.subprocess, "run", boom)
    with pytest.raises(mod.ApiError) as secret:
        mod.config_set("providers.api_key", "nope")
    assert secret.value.status == 403
    try:
        origins = mod.get_message_origins("api_missing", "1")
    except mod.ApiError as exc:
        assert exc.status == 503
    else:
        assert origins == {"items": {}}


def _env(home: Path) -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", "/usr/bin"),
        "HOME": str(home),
        "CLAWD_HOME": str(home),
        "HERMES_HOME": str(home),
        "HERMES_RUNTIME_DIR": str(RUNTIME),
        "PYTHONPATH": str(REPO),
        "LANG": "C.UTF-8",
        "CLAWD_PANEL_PASSWORD": "secret",
    }


def _request(url: str, method: str = "GET", body: bytes | None = None, headers: dict | None = None, timeout: float = 20):
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


@pytest.fixture(scope="module")
def panel():
    home = _home("server")
    env = _env(home)
    log_path = ART / "server.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [str(PY), "-m", "clawd_server", "user", "add", "--home", str(home), "--user", "ops"],
        env=env, stdout=log, stderr=subprocess.STDOUT, cwd=str(REPO),
    )
    assert proc.wait(timeout=60) == 0
    proc = subprocess.Popen(
        [str(PY), "-m", "clawd_server", "serve", "--home", str(home), "--host", "127.0.0.1", "--port", "0"],
        env=env, stdout=log, stderr=subprocess.STDOUT, cwd=str(REPO),
    )
    url = ""
    deadline = time.time() + 90
    while time.time() < deadline:
        if proc.poll() is not None:
            raise AssertionError(f"server exited {proc.returncode}; see {log_path}")
        text = log_path.read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            if line.startswith("CLAWD_PANEL_READY"):
                url = line.split("url=", 1)[1].strip()
                break
        if url:
            break
        time.sleep(0.2)
    if not url:
        proc.kill()
        raise AssertionError(f"server did not become ready; see {log_path}")
    digest = __import__("hashlib").sha256(b"hermes-panel:v2:ops\nsecret").hexdigest()
    yield {"url": url, "home": home, "cookie": f"hp_auth={digest}", "proc": proc, "log": log_path}
    proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
    log.close()


def test_server_auth_session_files_and_errors(panel):
    base = panel["url"]
    cookie = {"Cookie": panel["cookie"], "Host": base.split("://", 1)[1]}
    status, _, body = _request(base + "/panel-sidecar/health")
    assert status == 401
    status, _, body = _request(base + "/panel-sidecar/health", headers={**cookie, "Origin": "http://evil.example"})
    assert status == 403
    status, headers, body = _request(base + "/panel-auth/renew", "POST", b"", cookie)
    assert status == 204
    set_cookie = headers.get("Set-Cookie") or headers.get("set-cookie") or ""
    assert "HttpOnly" in set_cookie
    assert "Secure" not in set_cookie
    status, _, body = _request(base + "/clawd/ready")
    ready = json.loads(body)
    assert ready["legacyWatcher"] == "disabled"
    assert ready["continuation"] == "started"
    assert ready["continuationRunning"] is True
    assert ready["continuationModule"] == "present-not-started"
    status, _, body = _request(
        base + "/panel-api/api/sessions", "POST",
        json.dumps({"title": "clawd-bind"}).encode(),
        {**cookie, "Content-Type": "application/json"},
    )
    assert status in {200, 201}, body
    session = json.loads(body)["session"]["id"]
    assert session
    state = panel["home"] / "state.db"
    assert state.is_file()
    assert str(state.resolve()).startswith(str(panel["home"].resolve()))
    status, _, body = _request(base + "/panel-sidecar/sessions/folders", headers=cookie)
    assert status == 200
    assert "folders" in json.loads(body)
    status, _, body = _request(
        base + "/panel-sidecar/files/upload?root=uploads&name=note.txt", "POST", b"hello",
        {**cookie, "Content-Type": "application/octet-stream"},
    )
    assert status == 200, body
    uploaded = json.loads(body)
    assert uploaded["absPath"].startswith(str(panel["home"]))
    assert "/root/.hermes" not in uploaded["absPath"]
    status, _, body = _request(
        base + "/panel-sidecar/files/artifact?path=" + urllib.parse.quote("/etc/passwd"),
        headers=cookie,
    )
    assert status in {400, 403}
    status, _, body = _request(
        base + "/panel-sidecar/files/artifact?path=" + urllib.parse.quote("/root/.hermes/.env"),
        headers=cookie,
    )
    assert status in {400, 403}
    status, _, body = _request(
        base + "/panel-api/v1/runs/missing-run/approval", "POST",
        json.dumps({"request_id": "req", "execution_generation": 1, "choice": "deny"}).encode(),
        {**cookie, "Content-Type": "application/json"},
    )
    assert status in {404, 409, 400}, body
    assert b"request_id" not in body or status != 200
    status, _, body = _request(base + "/panel-api/v1/runs/missing-run/events?last_seq=0", headers={**cookie, "Accept": "text/event-stream"})
    assert status in {404, 400, 409}
    status, _, body = _request(base + "/panel-sidecar/services/hermes-gateway/restart", "POST", b"{}", {**cookie, "Content-Type": "application/json"})
    assert status == 403
    names = subprocess.check_output(["ps", "-T", "-p", str(panel["proc"].pid), "-o", "comm="], text=True)
    assert "auto-continue" not in names
