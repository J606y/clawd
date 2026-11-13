#!/usr/bin/env python3
"""Isolated clawd panel acceptance. External model, MCP, and browser gaps stay blocked."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PY = REPO / ".venv" / "bin" / "python"
ART = REPO / "artifacts" / "binding"
HOME = ART / "home"
LOG = ART / "verify.log"
REPORT = ART / "report.json"
RUNTIME = REPO / ".dev-home" / "tools"
USER = "ops"
PASSWORD = "verify-secret"


def log(text: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(text.rstrip() + "\n")
    print(text, flush=True)


def request(url: str, method: str = "GET", body: bytes | None = None, headers: dict | None = None, timeout: float = 30):
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


class Server:
    def __init__(self, env: dict[str, str]) -> None:
        self.env = env
        self.proc: subprocess.Popen[str] | None = None
        self.log = open(ART / "server.log", "a", encoding="utf-8")
        self.url = ""

    def start(self) -> str:
        self.log.flush()
        start_pos = (ART / "server.log").stat().st_size if (ART / "server.log").exists() else 0
        self.proc = subprocess.Popen(
            [str(PY), "-m", "clawd_server", "serve", "--home", str(HOME), "--host", "127.0.0.1", "--port", "0"],
            env=self.env, cwd=str(REPO), stdout=self.log, stderr=subprocess.STDOUT,
        )
        deadline = time.time() + 90
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"server exit {self.proc.returncode}; see {ART / 'server.log'}")
            self.log.flush()
            text = (ART / "server.log").read_text(encoding="utf-8", errors="replace")[start_pos:]
            for line in text.splitlines():
                if line.startswith("CLAWD_PANEL_READY"):
                    self.url = line.split("url=", 1)[1].strip()
                    return self.url
            time.sleep(0.2)
        raise RuntimeError(f"server not ready; see {ART / 'server.log'}")

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None

    def close(self) -> None:
        self.stop()
        self.log.close()


def main() -> int:
    ART.mkdir(parents=True, exist_ok=True)
    if LOG.exists():
        LOG.unlink()
    if (ART / "server.log").exists():
        (ART / "server.log").unlink()
    if HOME.exists():
        shutil.rmtree(HOME)
    HOME.mkdir(parents=True)
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin"),
        "HOME": str(HOME),
        "CLAWD_HOME": str(HOME),
        "HERMES_HOME": str(HOME),
        "HERMES_RUNTIME_DIR": str(RUNTIME),
        "PYTHONPATH": str(REPO),
        "LANG": "C.UTF-8",
        "CLAWD_PANEL_PASSWORD": PASSWORD,
    }
    results: dict[str, dict[str, str]] = {}

    def record(name: str, status: str, detail: str) -> None:
        results[name] = {"status": status, "detail": detail}
        log(f"{status.upper()} {name}: {detail}")

    add = subprocess.run(
        [str(PY), "-m", "clawd_server", "user", "add", "--home", str(HOME), "--user", USER],
        env=env, cwd=str(REPO), capture_output=True, text=True, timeout=60, check=False,
    )
    log(add.stdout + add.stderr)
    if add.returncode != 0:
        record("user", "fail", (add.stderr or add.stdout)[-400:])
        REPORT.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return 1
    record("user", "pass", "isolated auth user added")

    server = Server(env)
    try:
        url = server.start()
        record("start", "pass", url)
        host = url.split("://", 1)[1]
        digest = hashlib.sha256(f"hermes-panel:v2:{USER}\n{PASSWORD}".encode()).hexdigest()
        cookie = {"Cookie": f"hp_auth={digest}", "Host": host}
        json_headers = {**cookie, "Content-Type": "application/json"}

        status, _, _ = request(url + "/panel-api/health")
        record("auth-reject", "pass" if status == 401 else "fail", f"HTTP {status}")
        status, headers, _ = request(url + "/panel-auth/renew", "POST", b"", cookie)
        set_cookie = headers.get("Set-Cookie") or headers.get("set-cookie") or ""
        record("renew", "pass" if status == 204 and "HttpOnly" in set_cookie else "fail", f"HTTP {status}")
        status, _, body = request(url + "/clawd/ready")
        ready = json.loads(body)
        record("watcher-disabled", "pass" if ready.get("legacyWatcher") == "disabled" else "fail", json.dumps(ready, ensure_ascii=False))
        record(
            "continuation-lifecycle",
            "pass" if ready.get("continuation") == "started" and ready.get("continuationRunning") is True else "fail",
            f"service status={ready.get('continuation')}; running={ready.get('continuationRunning')}",
        )
        record(
            "continuation-core",
            "blocked",
            "Lifecycle checked separately; this probe has not exercised a delegated completion model turn",
        )

        status, _, body = request(url + "/panel-api/api/sessions", "POST", json.dumps({"title": "verify"}).encode(), json_headers)
        session_id = ""
        if status not in {200, 201}:
            record("session", "fail", body.decode()[:400])
        else:
            session_id = json.loads(body)["session"]["id"]
            record("session", "pass", session_id)
        if session_id:
            status, _, _ = request(url + f"/panel-api/api/sessions/{urllib.parse.quote(session_id)}", headers=cookie)
            record("session-get", "pass" if status == 200 else "fail", f"HTTP {status}")

        status, _, body = request(
            url + "/panel-core/files/upload?root=uploads&name=verify.txt", "POST", b"ok",
            {**cookie, "Content-Type": "application/octet-stream"},
        )
        if status != 200:
            record("upload", "fail", body.decode()[:300])
        else:
            uploaded = json.loads(body)
            record("upload", "pass" if str(uploaded.get("absPath", "")).startswith(str(HOME)) else "fail", uploaded.get("absPath", ""))
        denied, _, _ = request(url + "/panel-core/files/artifact?path=" + urllib.parse.quote("/etc/passwd"), headers=cookie)
        record("path-reject", "pass" if denied in {400, 403} else "fail", f"HTTP {denied}")
        status, _, _ = request(url + "/panel-core/services/nginx/restart", "POST", b"{}", json_headers)
        record("no-system-restart", "pass" if status == 403 else "fail", f"HTTP {status}")

        status, _, body = request(url + "/panel-api/api/jobs", headers=cookie)
        record("jobs-list", "pass" if status == 200 else "fail", f"HTTP {status} {body[:160]!r}")
        status, _, body = request(url + "/panel-core/skills", headers=cookie, timeout=40)
        record("skills", "pass" if status == 200 else "fail", f"HTTP {status} {body[:160]!r}")
        status, _, body = request(url + "/panel-core/config", headers=cookie, timeout=20)
        if status == 503 and b"yaml_parser_unavailable" in body:
            record("config-read", "blocked", "prepared venv has no PyYAML; redaction refuses to return raw config")
        else:
            record("config-read", "pass" if status == 200 else "fail", f"HTTP {status} {body[:160]!r}")
        status, _, body = request(url + "/panel-core/subagents?limit=5", headers=cookie)
        record("subagents", "pass" if status == 200 else "fail", f"HTTP {status}")
        status, _, body = request(url + "/panel-core/live/state", headers=cookie, timeout=8)
        if status != 200:
            record("live-browser", "blocked", f"HTTP {status}; clawd has no browser daemon")
        else:
            state = json.loads(body)
            record("live-browser", "pass" if state.get("running") else "blocked", "no isolated browser daemon" if not state.get("running") else "running")
        status, _, body = request(url + "/panel-core/mcp", headers=cookie, timeout=40)
        record("mcp", "blocked" if status != 200 or body == b'{"data": []}' else "pass", f"HTTP {status} {body[:180]!r}; no live MCP session")

        if session_id:
            run_body = json.dumps({"input": "ping", "session_id": session_id}).encode()
            status, _, body = request(
                url + "/panel-api/v1/runs", "POST", run_body,
                {**json_headers, "Idempotency-Key": "verify-once"}, timeout=40,
            )
            if status not in {200, 201, 202}:
                record("model-run", "blocked", f"HTTP {status} {body[:240]!r}")
            else:
                run_id = ""
                try:
                    run_id = json.loads(body).get("run_id", "")
                except json.JSONDecodeError:
                    run_id = ""
                detail = body[:240].decode(errors="replace")
                if run_id:
                    time.sleep(1)
                    polled, _, polled_body = request(url + f"/panel-api/v1/runs/{urllib.parse.quote(run_id)}", headers=cookie)
                    detail = f"accepted {run_id}; poll HTTP {polled} {polled_body[:240]!r}"
                record("model-run", "blocked", detail + "; no approved provider completed a turn")
            status, _, body = request(url + "/panel-api/v1/runs/missing-run/events?last_seq=0", headers={**cookie, "Accept": "text/event-stream"})
            record("events-missing", "pass" if status in {400, 404, 409} else "fail", f"HTTP {status}")
            status, _, body = request(
                url + "/panel-api/v1/runs/missing-run/approval", "POST",
                json.dumps({"request_id": "req", "execution_generation": 1, "choice": "deny"}).encode(),
                json_headers,
            )
            record("approval-missing", "pass" if status in {400, 404, 409} else "fail", f"HTTP {status} {body[:160]!r}")

        names = subprocess.check_output(["ps", "-T", "-p", str(server.proc.pid), "-o", "comm="], text=True)
        record("no-continue-thread", "pass" if "auto-continue" not in names else "fail", names.replace("\n", " ")[:200])

        server.stop()
        if session_id:
            url = server.start()
            host = url.split("://", 1)[1]
            cookie["Host"] = host
            status, _, body = request(url + f"/panel-api/api/sessions/{urllib.parse.quote(session_id)}", headers=cookie)
            record("restart-session", "pass" if status == 200 and session_id in body.decode() else "fail", f"HTTP {status}")
        index = REPO / "apps" / "panel" / "dist" / "index.html"
        if not index.is_file():
            record("panel-static", "blocked", "Build artifact apps/panel/dist/index.html is absent")
        else:
            status, _, body = request(url + "/", headers=cookie)
            expected = index.read_text(encoding="utf-8")
            roots = json.dumps([str(HOME / name) + "/" for name in ("uploads", "workspace", "images", "cron/output")], ensure_ascii=False)
            injection = f'<script>window.__CLAWD_ARTIFACT_ROOTS__={roots};</script>'
            if "</head>" in expected and "__CLAWD_ARTIFACT_ROOTS__" not in expected:
                expected = expected.replace("</head>", injection + "</head>", 1)
            record("panel-static", "pass" if status == 200 and body == expected.encode("utf-8") else "fail",
                   f"HTTP {status}; built index and isolated artifact-root injection verified")
    except Exception as exc:
        record("harness", "fail", f"{type(exc).__name__}: {exc}")
    finally:
        server.close()
    REPORT.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    failed = [name for name, item in results.items() if item["status"] == "fail"]
    blocked = [name for name, item in results.items() if item["status"] == "blocked"]
    log(f"report {REPORT} failed={failed or 'none'} blocked={blocked or 'none'}")
    return verification_exit_code(results)


def verification_exit_code(results: dict) -> int:
    """Zero means every reported acceptance check passed, not merely no failures."""
    states = {item.get("status") for item in results.values()}
    if "fail" in states:
        return 1
    if not states or states != {"pass"}:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
