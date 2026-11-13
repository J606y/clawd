"""Unified localhost server: panel-api, panel-sidecar, panel-auth, static files."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import threading
from pathlib import Path
from typing import Any

from aiohttp import ClientSession, ClientTimeout, web

from clawd_server import auth as panel_auth
from clawd_server.bind import bind, clawd_command, continuation_status, load_sidecar, prepare_env
from clawd_server.live_browser_routes import register_live_routes
from clawd_server.home import REPO_ROOT, assert_core_home, resolve_home

log = logging.getLogger("clawd.panel")
_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
    "transfer-encoding", "upgrade", "host", "content-length", "cookie", "authorization", "x-panel-key",
}
_PUBLIC_PREFIXES = ("/panel-auth/renew", "/clawd/ready")


def loopback_host(host: str) -> str:
    if host == "localhost":
        return "127.0.0.1"
    if host not in {"127.0.0.1", "::1"}:
        raise SystemExit(f"refusing non-loopback bind: {host}")
    return host


def _json_error(status: int, code: str, message: str) -> web.Response:
    return web.json_response({"error": {"code": code, "message": message}}, status=status)


def _event_stream(content_type: str) -> bool:
    """SSE media type only. Charset and other parameters do not qualify."""
    return content_type.split(";", 1)[0].strip().lower() == "text/event-stream"


def _peer(request: web.Request) -> str | None:
    if request.remote:
        return request.remote
    transport = request.transport
    if transport is None:
        return None
    peer = transport.get_extra_info("peername")
    return peer[0] if peer else None


_ARTIFACT_NAMES = ("uploads", "workspace", "images", "cron/output")


def _banned_artifact_home(text: str) -> bool:
    """True for a production Hermes home. String check only; does not open it."""
    normalized = text.rstrip("/")
    banned = ["/root/.hermes"]
    try:
        banned.append(str(Path.home() / ".hermes"))
    except (OSError, RuntimeError):
        pass
    return any(normalized == item or normalized.startswith(item + "/") for item in banned if item)


def _artifact_roots(home: Path) -> list[str]:
    """Prefixes under resolved CLAWD_HOME. Same JSON shape as before.

    Only the home symlink is resolved, so injected spelling matches upload
    ``absPath``. Child directories are not resolved: a swapped root symlink
    must not widen the allowlist. Unusable or production homes yield no
    prefixes rather than a production fallback.
    """
    try:
        raw = Path(home)
    except TypeError:
        return []
    raw_text = str(raw)
    if not raw_text or raw_text != raw_text.strip() or _banned_artifact_home(raw_text):
        return []
    expanded = raw.expanduser()
    if not expanded.is_absolute() or _banned_artifact_home(str(expanded)):
        return []
    try:
        resolved = expanded.resolve()
    except (OSError, RuntimeError, ValueError):
        return []
    if (
        not resolved.is_absolute()
        or resolved == Path(resolved.anchor)
        or _banned_artifact_home(str(resolved))
    ):
        return []
    resolved_text = str(resolved)
    prefixes: list[str] = []
    for name in _ARTIFACT_NAMES:
        prefix = str(resolved.joinpath(*Path(name).parts)) + "/"
        if (
            not prefix.startswith(resolved_text + "/")
            or not prefix.endswith("/")
            or ".." in Path(prefix).parts
            or _banned_artifact_home(prefix)
        ):
            return []
        prefixes.append(prefix)
    return prefixes


class PanelServer:
    def __init__(self, home: Path, static_dir: Path | None) -> None:
        self.home = home
        self.static_dir = static_dir
        self.api_key = ""
        self.sidecar_key = ""
        self.adapter: Any = None
        self.sidecar_httpd: Any = None
        self.live_supervisor: Any = None
        self.sidecar_mod: Any = None
        self.api_base = ""
        self.sidecar_base = ""
        self.client: ClientSession | None = None
        self.runner: web.AppRunner | None = None
        self.site: web.TCPSite | None = None
        self.port = 0

    async def start(self, host: str, port: int) -> None:
        host = loopback_host(host)
        self.home.joinpath("panel").mkdir(parents=True, mode=0o700, exist_ok=True)
        self.home.joinpath("logs").mkdir(parents=True, mode=0o700, exist_ok=True)
        self.api_key = secrets.token_hex(32)
        self.sidecar_key = secrets.token_hex(32)
        key_path = self.home / "panel" / "api-server.key"
        key_path.write_text(self.api_key + "\n", encoding="utf-8")
        os.chmod(key_path, 0o600)
        (self.home / "panel" / "sidecar.key").write_text(self.sidecar_key + "\n", encoding="utf-8")
        os.chmod(self.home / "panel" / "sidecar.key", 0o600)
        config = self.home / "config.yaml"
        if not config.exists():
            config.write_text("# clawd isolated home; no secrets\nagent: {}\n", encoding="utf-8")
        prepare_env(self.home, "http://127.0.0.1:9")
        assert_core_home(self.home)
        await self._start_api()
        state = self._start_continuation()
        prepare_env(self.home, self.api_base)
        command = clawd_command(self.home)
        os.environ["HERMES_BIN"] = command
        self.sidecar_mod = load_sidecar()
        bind(self.sidecar_mod, self.home, command)
        self.sidecar_mod.CONTINUE_API = self.api_base
        self._start_sidecar()
        # Core owns the live browser controller: adopt it when the systemd
        # unit already runs it, else spawn it as a core child (Chromium stays
        # a subprocess of the controller, never of this process). Idle
        # shutdown (10s) stays inside the runtime. Fail-open: a down live
        # browser must not stop the panel from starting.
        try:
            from clawd_server.live_browser_supervisor import LiveBrowserSupervisor
            self.live_supervisor = LiveBrowserSupervisor(self.home, REPO_ROOT)
            mode = await self.live_supervisor.start()
            log.info("live browser supervisor: %s", mode)
        except Exception as exc:
            log.warning("live browser supervisor unavailable: %s", exc)
            self.live_supervisor = None
        timeout = ClientTimeout(total=None, sock_connect=5, sock_read=None)
        self.client = ClientSession(timeout=timeout)
        # 502 MB admits 500 MB uploads with headroom; the gateway enforces
        # the 500 MB cap per route, this is only the outer proxy's ceiling.
        app = web.Application(client_max_size=502 * 1024 * 1024)
        app.router.add_get("/clawd/ready", self._ready)
        app.router.add_post("/panel-auth/renew", self._renew)
        app.router.add_route("*", "/panel-auth/logout", self._logout)
        from clawd_server.account_routes import register_account_routes

        register_account_routes(app, self)
        app.router.add_route("*", "/panel-api/{tail:.*}", self._proxy_api)
        app.router.add_route("*", "/panel-sidecar/{tail:.*}", self._proxy_sidecar)
        register_live_routes(app, self)
        app.router.add_get("/", self._index)
        app.router.add_get("/{name:.*}", self._static)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, host, port)
        await self.site.start()
        sockets = getattr(self.site._server, "sockets", None) or []
        self.port = sockets[0].getsockname()[1] if sockets else port
        log.info(
            "clawd panel listening on http://%s:%d continuation=%s module=%s",
            host, self.port, state, continuation_status(),
        )
        print(f"CLAWD_PANEL_READY url=http://{host}:{self.port}", flush=True)

    async def _start_api(self) -> None:
        from gateway.config import PlatformConfig
        from gateway.platforms.api_server import APIServerAdapter
        from tools.process_registry import process_registry

        last = "API server did not start"
        for _ in range(3):
            cfg = PlatformConfig(enabled=True, extra={"key": self.api_key, "host": "127.0.0.1", "port": 0})
            adapter = APIServerAdapter(cfg)
            # This adapter executes agent tools in this process. Bind the actual
            # registry, not a synthetic gateway_runner or a guessed empty view.
            adapter._background_process_registry = process_registry
            if not await adapter.connect():
                last = "APIServerAdapter.connect returned False"
                await adapter.disconnect()
                continue
            site = adapter._site
            sockets = getattr(getattr(site, "_server", None), "sockets", None) or []
            if not sockets:
                last = "API server bound no socket"
                await adapter.disconnect()
                continue
            port = sockets[0].getsockname()[1]
            self.adapter = adapter
            self.api_base = f"http://127.0.0.1:{port}"
            return
        raise RuntimeError(last)

    def _start_continuation(self) -> str:
        """Arm the API adapter's continuation service. Module import is not this start."""
        from gateway.clawd_continuation import arm_adapter, ready_fields

        if self.adapter is None:
            return "not-started"
        arm_adapter(self.adapter)
        live = ready_fields(self.adapter)
        if live["continuation"] != "started":
            log.error("clawd continuation not started: %s", live.get("continuationError"))
        return str(live["continuation"])

    async def _stop_continuation(self) -> None:
        from gateway.clawd_continuation import disarm_adapter, service_for

        adapter = self.adapter
        service = service_for(adapter) if adapter is not None else None
        if service is not None:
            await service.drain()
        if adapter is not None:
            disarm_adapter(adapter)

    def _start_sidecar(self) -> None:
        mod = self.sidecar_mod
        mod.Handler.key = self.sidecar_key
        httpd = mod.ThreadingHTTPServer(("127.0.0.1", 0), mod.Handler)
        httpd.daemon_threads = True
        thread = threading.Thread(target=httpd.serve_forever, name="clawd-sidecar", daemon=True)
        thread.start()
        self.sidecar_httpd = httpd
        self.sidecar_base = f"http://127.0.0.1:{httpd.server_address[1]}"

    async def close(self) -> None:
        if self.live_supervisor is not None:
            await self.live_supervisor.stop()
            self.live_supervisor = None
        await self._stop_continuation()
        if self.client is not None:
            await self.client.close()
        if self.runner is not None:
            await self.runner.cleanup()
        if self.sidecar_httpd is not None:
            self.sidecar_httpd.shutdown()
            self.sidecar_httpd.server_close()
        if self.adapter is not None:
            await self.adapter.disconnect()

    def _authed(self, request: web.Request) -> web.Response | None:
        if not panel_auth.origin_allowed(request.method, request.headers, _peer(request)):
            return _json_error(403, "origin_rejected", "来源不被允许")
        presented = panel_auth.cookie_value(request.headers.get("Cookie"))
        if not panel_auth.hash_allowed(presented, panel_auth.load_hashes(self.home)):
            return _json_error(401, "unauthorized", "unauthorized")
        return None

    async def _ready(self, _request: web.Request) -> web.Response:
        from gateway.clawd_continuation import ready_fields

        live = ready_fields(self.adapter)
        return web.json_response({
            "ok": True,
            "legacyWatcher": "disabled",
            "continuation": live["continuation"],
            "continuationModule": continuation_status(),
            "continuationRunning": live["continuationRunning"],
            "continuationError": live["continuationError"],
            "bind": "loopback",
        })

    async def _logout(self, request: web.Request) -> web.Response:
        if request.method != "POST":
            return web.Response(status=405, headers={"Allow": "POST"})
        if not panel_auth.origin_allowed(request.method, request.headers, _peer(request)):
            return _json_error(403, "origin_rejected", "来源不被允许")
        response = web.Response(status=204, headers={"Cache-Control": "no-store"})
        response.del_cookie(
            panel_auth.COOKIE, path="/", httponly=True, samesite="Strict",
            secure=panel_auth.request_is_https(request),
        )
        return response

    async def _renew(self, request: web.Request) -> web.Response:
        if not panel_auth.origin_allowed(request.method, request.headers, _peer(request)):
            return _json_error(403, "origin_rejected", "来源不被允许")
        presented = panel_auth.cookie_value(request.headers.get("Cookie"))
        if not panel_auth.hash_allowed(presented, panel_auth.load_hashes(self.home)):
            return _json_error(401, "unauthorized", "unauthorized")
        response = web.Response(status=204)
        response.set_cookie(
            panel_auth.COOKIE, presented, max_age=panel_auth.RENEW_MAX_AGE, httponly=True,
            samesite="Strict", secure=panel_auth.request_is_https(request), path="/",
        )
        return response

    async def _proxy_api(self, request: web.Request) -> web.StreamResponse:
        denied = self._authed(request)
        if denied:
            return denied
        return await self._proxy(request, self.api_base, {"Authorization": f"Bearer {self.api_key}"})

    async def _proxy_sidecar(self, request: web.Request) -> web.StreamResponse:
        denied = self._authed(request)
        if denied:
            return denied
        return await self._proxy(request, self.sidecar_base, {"X-Panel-Key": self.sidecar_key})

    async def _proxy(self, request: web.Request, base: str, inject: dict[str, str]) -> web.StreamResponse:
        tail = request.match_info.get("tail", "")
        url = f"{base}/{tail}"
        if request.query_string:
            url += "?" + request.query_string
        headers = {
            key: value for key, value in request.headers.items()
            if key.lower() not in _HOP
            and key.lower() not in {
                "host", "origin", "referer", "referrer", "cookie", "authorization",
                "x-panel-key", "sec-fetch-site", "sec-fetch-mode", "sec-fetch-dest",
            }
        }
        # The outer panel has authenticated the browser and checked its origin.
        # Internal loopback services receive only their own injected credentials.
        headers.update(inject)
        assert self.client is not None
        # 500 MB uploads must not be buffered whole in this process: stream
        # the request body for the upload route only; everything else keeps
        # the existing buffered path.
        stream_upload = tail == "api/uploads"
        try:
            if stream_upload:
                data = request.content.iter_chunked(65536)
                upstream = await self.client.request(
                    request.method, url, headers=headers, data=data, allow_redirects=False,
                )
            else:
                body = await request.read()
                upstream = await self.client.request(
                    request.method, url, headers=headers, data=body if body else None, allow_redirects=False,
                )
        except web.HTTPRequestEntityTooLarge:
            return _json_error(413, "too_large", "单个文件不能超过 500MB")
        except (OSError, TimeoutError) as exc:
            log.warning("upstream unavailable: %s", type(exc).__name__)
            return _json_error(503, "upstream_unavailable", "上游服务暂不可用")
        response = web.StreamResponse(status=upstream.status)
        for key, value in upstream.headers.items():
            if key.lower() in _HOP or key.lower() == "set-cookie":
                continue
            response.headers.add(key, value)
        sse = _event_stream(response.headers.get("Content-Type", ""))
        if sse:
            response.headers["Cache-Control"] = "no-cache"
            response.headers["X-Accel-Buffering"] = "no"
        await response.prepare(request)
        try:
            # SSE: yield bytes already received. Do not wait for 64KiB or upstream EOF.
            chunks = upstream.content.iter_any() if sse else upstream.content.iter_chunked(65536)
            async for chunk in chunks:
                await response.write(chunk)
        finally:
            await upstream.release()
        await response.write_eof()
        return response

    async def _index(self, request: web.Request) -> web.StreamResponse:
        return await self._serve_file(request, "index.html")

    async def _static(self, request: web.Request) -> web.StreamResponse:
        return await self._serve_file(request, request.match_info.get("name", ""))

    async def _serve_file(self, request: web.Request, name: str) -> web.StreamResponse:
        if self.static_dir is None or not self.static_dir.is_dir():
            return _json_error(503, "panel_not_built", "面板静态文件尚未构建")
        rel = name or "index.html"
        if rel.endswith("/"):
            rel += "index.html"
        target = (self.static_dir / rel).resolve()
        root = self.static_dir.resolve()
        if root != target and root not in target.parents:
            return _json_error(403, "denied", "路径不被允许")
        if not target.is_file():
            return _json_error(404, "not_found", "not found")
        if target.name == "index.html":
            text = target.read_text(encoding="utf-8")
            roots = json.dumps(_artifact_roots(self.home), ensure_ascii=False)
            needle = "</head>"
            inject = f'<script>window.__CLAWD_ARTIFACT_ROOTS__={roots};</script>'
            if needle in text and "__CLAWD_ARTIFACT_ROOTS__" not in text:
                text = text.replace(needle, inject + needle, 1)
            return web.Response(text=text, content_type="text/html", charset="utf-8")
        return web.FileResponse(target)


async def serve(home: Path, host: str, port: int, static_dir: Path | None) -> None:
    server = PanelServer(home, static_dir)
    await server.start(host, port)
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await server.close()


def run_serve(argv_home: str | None, host: str, port: int, static: str | None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    home = resolve_home(argv_home)
    from clawd_server.resources import ResourceError, panel_static_dir

    try:
        static_dir = Path(static) if static is not None else panel_static_dir()
    except ResourceError as exc:
        logging.getLogger("clawd.panel").error("%s", exc)
        return 2
    try:
        asyncio.run(serve(home, host, port, static_dir if static_dir.is_dir() else None))
    except KeyboardInterrupt:
        return 0
    return 0
