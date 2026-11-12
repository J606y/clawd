"""On-demand clawd shared live browser.

Entry (does not run on import):

    python -m clawd_server.live_browser_runtime --home /root/.clawd

Controller is loopback only. Outer panel login protects the public socket.
This module does not attach to task, cloud, or unknown browsers, and it does
not implement the input lease.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import secrets
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from aiohttp import web

PROTOCOL = "clawd-live-v1"
HOST = "127.0.0.1"
CONTROLLER_PORT = 39232
CDP_PORT = 39233
CDP_PROXY_PORT = 39234
RFB_PORT = 15900
DISPLAY = 98
GEOMETRY = "1366x768x24"
IDLE_SECONDS = 10.0
WAKE_DEADLINE_SECONDS = 20.0
MAX_WS_CLIENTS = 4
MAX_CDP_CLIENTS = 8
MAX_WS_MESSAGE = 8 * 1024 * 1024
READ_CHUNK = 64 * 1024
WRITE_HIGH_WATER = 1024 * 1024
CHROMIUM_BACKOFF_CAP = 8.0
CHROMIUM_RECOVERY_LIMIT = 4
REBUILD_BACKOFF_CAP = 8.0
REAPER_BACKOFF_CAP = 8.0
PROFILE_MODE = 0o700
FILE_MODE = 0o600

BINARIES = {
    "Xvfb": "/usr/bin/Xvfb",
    "openbox": "/usr/bin/openbox",
    "x11vnc": "/usr/bin/x11vnc",
    "xauth": "/usr/bin/xauth",
    "wmctrl": "/usr/bin/wmctrl",
    "chromium": "/usr/bin/chromium",
}

_REWRITE_TO_BROWSER = (
    (b"127.0.0.1:39234", b"127.0.0.1:39233"),
    (b"localhost:39234", b"localhost:39233"),
    (b"[::1]:39234", b"[::1]:39233"),
)
_REWRITE_TO_PROXY = (
    (b"127.0.0.1:39233", b"127.0.0.1:39234"),
    (b"localhost:39233", b"localhost:39234"),
    (b"[::1]:39233", b"[::1]:39234"),
)

log = logging.getLogger("clawd.live_browser")

__all__ = [
    "CDP_PORT",
    "CDP_PROXY_PORT",
    "CONTROLLER_PORT",
    "DISPLAY",
    "PROTOCOL",
    "RFB_PORT",
    "main",
]


class _RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        lowered = message.lower()
        if "://" in message or "cookie" in lowered or "authorization" in lowered or "xauthority=" in lowered:
            record.msg = "redacted_log"
            record.args = ()
        return True


class LiveRuntimeError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _display_name() -> str:
    return f":{DISPLAY}"


def _lock_path() -> Path:
    return Path(f"/tmp/.X{DISPLAY}-lock")


def _socket_path() -> Path:
    return Path(f"/tmp/.X11-unix/X{DISPLAY}")


def _pgrp(pid: int) -> int | None:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    except OSError:
        return None
    end = raw.rfind(")")
    if end < 0:
        return None
    fields = raw[end + 2 :].split()
    if len(fields) < 3:
        return None
    try:
        return int(fields[2])
    except ValueError:
        return None


def _alive(pid: int | None) -> bool:
    if not pid or pid <= 1:
        return False
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
        fields = raw[raw.rfind(")") + 2:].split()
        if fields and fields[0] == "Z":
            return False
        os.kill(pid, 0)
    except (ProcessLookupError, FileNotFoundError):
        return False
    except PermissionError:
        return True
    return True


def _environ_has(pid: int, token: str) -> bool:
    try:
        raw = Path(f"/proc/{pid}/environ").read_bytes()
    except OSError:
        return False
    needle = f"CLAWD_LIVE_OWNER={token}".encode()
    return needle in raw.split(b"\0")


def _cmdline(pid: int) -> bytes:
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return b""


def _symlink_pid(path: Path) -> int | None:
    try:
        target = os.readlink(path)
    except OSError:
        return None
    tail = target.rsplit("-", 1)[-1]
    if not tail.isdigit():
        return None
    return int(tail)


def _port_busy(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((HOST, port))
        except OSError:
            return True
    return False


def _loopback(peer: Any) -> bool:
    if not peer:
        return False
    host = peer[0] if isinstance(peer, tuple) else str(peer)
    return host in {HOST, "::1"}


def _tune_socket(writer: asyncio.StreamWriter) -> None:
    sock = writer.get_extra_info("socket")
    if sock is None:
        return
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        if hasattr(socket, "TCP_KEEPIDLE"):
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 10)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 3)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
    except OSError as exc:
        log.warning("socket_tune_failed %s", type(exc).__name__)


def _atomic_write(path: Path, data: bytes, mode: int = FILE_MODE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, PROFILE_MODE)
    tmp = path.with_name(path.name + ".tmp-" + secrets.token_hex(8))
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        try:
            view = memoryview(data)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise OSError("short atomic write")
                view = view[written:]
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, path)
        os.chmod(path, mode)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            log.warning("temp_unlink_failed %s", type(exc).__name__)
            raise


def _rewrite(pending: bytes, chunk: bytes, pairs: tuple[tuple[bytes, bytes], ...]) -> tuple[bytes, bytes]:
    data = pending + chunk
    for old, new in pairs:
        if len(old) != len(new):
            raise LiveRuntimeError("rewrite_length")
        data = data.replace(old, new)
    hold = 0
    for old, _new in pairs:
        limit = min(len(old) - 1, len(data))
        for size in range(limit, 0, -1):
            if data.endswith(old[:size]):
                hold = max(hold, size)
                break
    if hold:
        return data[:-hold], data[-hold:]
    return data, b""


async def _wait_transport(transport: Any) -> None:
    if transport is None:
        return
    while not transport.is_closing() and transport.get_write_buffer_size() > WRITE_HIGH_WATER:
        await asyncio.sleep(0.01)


async def _close_writer(writer: asyncio.StreamWriter | None) -> None:
    if writer is None or writer.is_closing():
        return
    writer.close()
    try:
        await writer.wait_closed()
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log.warning("close_failed %s", type(exc).__name__)
        raise


async def _close_ws(ws: web.WebSocketResponse | None) -> None:
    if ws is None or not ws.prepared or ws.closed:
        return
    try:
        await ws.close()
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log.warning("ws_close_failed %s", type(exc).__name__)
        raise


class Generation:
    def __init__(self, home: Path, token: str) -> None:
        self.home = home
        self.token = token
        self.profile = home / "live-browser-profile"
        self.state_dir = home / "live-browser"
        self.xauthority = self.state_dir / "Xauthority"
        self.owner_path = self.state_dir / "owner.json"
        self.openbox_rc = self.state_dir / "openbox-rc.xml"
        self.watchdog_path = self.state_dir / "wm-watchdog.py"
        self.pgid: int | None = None
        self.procs: dict[str, subprocess.Popen[bytes]] = {}
        self.ready = False
        self.stopping = False
        self.chromium_restarts = 0
        self.chromium_failures = 0
        self.chromium_escalated = False
        self.chromium_recovering = False
        self.chromium_listening = False
        self.watchdog_recovering = False
        self.rebuilding = False
        self.stop_refused = False
        self.reclaim_pending = False
        self.display_owner_proven = False
        self.profile_owner_proven = False
        self.recovery_event = asyncio.Event()
        self.recovery_event.set()
        self.markers = {
            "Xvfb": f":{DISPLAY}".encode(),
            "openbox": b"openbox",
            "chromium": b"--remote-debugging-port=39233",
            "x11vnc": b"-rfbport",
            "watchdog": b"wm-watchdog.py",
        }

    def poll(self, name: str) -> int | None:
        proc = self.procs.get(name)
        if proc is None:
            return -1
        return proc.poll()

    def display_alive(self) -> bool:
        return self.poll("Xvfb") is None and self.poll("openbox") is None and self.poll("x11vnc") is None

    def stack_alive(self) -> bool:
        return self.display_alive() and self.poll("chromium") is None and self.poll("watchdog") is None

    def env(self) -> dict[str, str]:
        return {
            "HOME": os.environ.get("HOME", "/root"),
            "CLAWD_HOME": str(self.home),
            "DISPLAY": _display_name(),
            "XAUTHORITY": str(self.xauthority),
            "PATH": "/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "CLAWD_LIVE_OWNER": self.token,
        }

    def prove(self, pid: int, name: str) -> bool:
        if pid <= 1 or not _alive(pid):
            return False
        if not _environ_has(pid, self.token):
            return False
        if self.markers[name] not in _cmdline(pid):
            return False
        if self.pgid is None or _pgrp(pid) != self.pgid:
            return False
        return True

    def write_owner(self) -> None:
        payload = {
            "token": self.token,
            "pgid": self.pgid,
            "display": DISPLAY,
            "ready": self.ready,
            "pids": {name: proc.pid for name, proc in self.procs.items()},
        }
        _atomic_write(self.owner_path, json.dumps(payload).encode("ascii"), FILE_MODE)

    def spawn(self, name: str, argv: list[str], *, leader: bool = False) -> subprocess.Popen[bytes]:
        # All siblings must remain in the controller's session. A setsid()
        # leader cannot be joined by subsequently forked sibling processes.
        if not leader and (self.pgid is None or self.pgid <= 1):
            raise LiveRuntimeError("refusing_bad_pgid")
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=self.env(),
            cwd=str(self.state_dir),
            process_group=0 if leader else self.pgid,
            umask=0o077,
        )
        if leader:
            self.pgid = proc.pid
        self.procs[name] = proc
        self.write_owner()
        log.info("spawned %s pid=%s", name, proc.pid)
        return proc



class LiveRuntime:
    def __init__(self, home: Path) -> None:
        self.home = home
        self.wake_lock = asyncio.Lock()
        self.active_ws = 0
        self.active_cdp = 0
        self.last_activity = time.monotonic()
        self.last_error: str | None = None
        self.generation: Generation | None = None
        self.stopping = False
        self.rebuild_after = 0.0
        self.rebuild_failures = 0
        self.runner: web.AppRunner | None = None
        self.site: web.TCPSite | None = None
        self.cdp_server: asyncio.Server | None = None
        self.reaper_task: asyncio.Task[None] | None = None
        self.monitor_task: asyncio.Task[None] | None = None
        self._shutdown_started = False
        self.handlers: set[asyncio.Task[Any]] = set()
        self.handler_gen: dict[asyncio.Task[Any], Generation] = {}
        self.reaper_fault: str | None = None
        self._reaper_logged: set[str] = set()
        self.reaper_backoff = 0.0
        self._escalating: Generation | None = None

    def touch(self) -> None:
        self.last_activity = time.monotonic()

    def active_total(self) -> int:
        return self.active_ws + self.active_cdp

    def _browser_ready(self, gen: Generation | None) -> bool:
        # display_alive is only Xvfb/openbox/x11vnc. A live picture also needs this
        # generation's Chromium, watchdog, CDP listen flag, and ready bit.
        return bool(
            gen
            and gen.ready
            and not gen.stopping
            and not gen.reclaim_pending
            and not gen.stop_refused
            and gen.chromium_listening
            and gen.stack_alive()
        )

    def running(self) -> bool:
        return self._browser_ready(self.generation)

    def health_payload(self) -> dict[str, Any]:
        return {
            "protocol": PROTOCOL,
            "home": str(self.home),
            "running": self.running(),
            "error": self.last_error,
            "reaper": self.reaper_fault,
        }

    async def health(self, _request: web.Request) -> web.Response:
        return web.json_response(self.health_payload())

    async def websockify(self, request: web.Request) -> web.StreamResponse:
        if not _loopback(request.remote):
            return web.Response(status=403, text="not_loopback")
        ws = web.WebSocketResponse(
            heartbeat=30.0,
            protocols=("binary",),
            compress=False,
            max_msg_size=MAX_WS_MESSAGE,
            writer_limit=WRITE_HIGH_WATER,
            autoping=True,
            receive_timeout=None,
        )
        if not ws.can_prepare(request).ok:
            return web.Response(status=426, headers={"Upgrade": "websocket"}, text="websocket_required")
        handler = asyncio.current_task()
        if handler is not None:
            self.handlers.add(handler)
        self.active_ws += 1
        self.touch()
        prepared = False
        upstream: asyncio.StreamWriter | None = None
        try:
            if self.active_ws > MAX_WS_CLIENTS:
                self.last_error = "viewer_limit"
                return web.Response(status=503, text="viewer_limit")
            await self.ensure_worker(time.monotonic() + WAKE_DEADLINE_SECONDS)
            self._bind_handler(handler)
            _reader, upstream = await asyncio.wait_for(
                asyncio.open_connection(HOST, RFB_PORT),
                timeout=5,
            )
            _tune_socket(upstream)
            await ws.prepare(request)
            prepared = True
            await self._bridge_ws(ws, request.transport, _reader, upstream)
            upstream = None
        except asyncio.CancelledError:
            raise
        except LiveRuntimeError as exc:
            self.last_error = exc.code
            log.warning("websockify_failed %s", exc.code)
            if not prepared:
                return web.Response(status=503, text=exc.code)
        except Exception as exc:
            self.last_error = "bridge_failed"
            log.warning("websockify_failed %s", type(exc).__name__)
            if not prepared:
                return web.Response(status=503, text="bridge_failed")
        finally:
            self.active_ws = max(0, self.active_ws - 1)
            self.touch()
            try:
                await self._finish_edge(prepared, ws, upstream)
            finally:
                if handler is not None:
                    self.handlers.discard(handler)
                    self.handler_gen.pop(handler, None)
        return ws

    async def _finish_edge(
        self,
        prepared: bool,
        ws: web.WebSocketResponse | None,
        upstream: asyncio.StreamWriter | None,
    ) -> None:
        errors: list[BaseException] = []
        try:
            await _close_writer(upstream)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            errors.append(exc)
        if prepared:
            try:
                await _close_ws(ws)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                errors.append(exc)
        if errors:
            self.last_error = self.last_error or "close_failed"
            log.warning("edge_close_failed %s", type(errors[0]).__name__)
            if sys.exception() is None:
                raise errors[0]

    async def _handle_cdp(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        handler = asyncio.current_task()
        if handler is not None:
            self.handlers.add(handler)
        self.active_cdp += 1
        self.touch()
        upstream: asyncio.StreamWriter | None = None
        try:
            if not _loopback(writer.get_extra_info("peername")):
                self.last_error = "not_loopback"
                return
            if self.active_cdp > MAX_CDP_CLIENTS:
                self.last_error = "cdp_limit"
                return
            _tune_socket(writer)
            try:
                first = await asyncio.wait_for(reader.read(READ_CHUNK), timeout=10)
            except TimeoutError as exc:
                raise LiveRuntimeError("wake_timeout") from exc
            if not first:
                return
            await self.ensure_worker(time.monotonic() + WAKE_DEADLINE_SECONDS)
            self._bind_handler(handler)
            back_reader, upstream = await asyncio.wait_for(
                asyncio.open_connection(HOST, CDP_PORT),
                timeout=5,
            )
            _tune_socket(upstream)
            await self._bridge_tcp(reader, writer, back_reader, upstream, first)
            upstream = None
        except asyncio.CancelledError:
            raise
        except LiveRuntimeError as exc:
            self.last_error = exc.code
            log.warning("cdp_proxy_failed %s", exc.code)
        except Exception as exc:
            self.last_error = "bridge_failed"
            log.warning("cdp_proxy_failed %s", type(exc).__name__)
        finally:
            self.active_cdp = max(0, self.active_cdp - 1)
            self.touch()
            try:
                await _close_writer(upstream)
                await _close_writer(writer)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("cdp_close_failed %s", type(exc).__name__)
                self.last_error = self.last_error or "close_failed"
                if sys.exception() is None:
                    raise
            finally:
                if handler is not None:
                    self.handlers.discard(handler)
                    self.handler_gen.pop(handler, None)

    async def _bridge_ws(
        self,
        ws: web.WebSocketResponse,
        transport: Any,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        tasks = [
            asyncio.create_task(self._pump_ws_to_tcp(ws, writer), name="ws-to-rfb"),
            asyncio.create_task(self._pump_tcp_to_ws(reader, ws, transport), name="rfb-to-ws"),
        ]
        await self._join_pumps(tasks)
        await _close_writer(writer)

    async def _bridge_tcp(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        back_reader: asyncio.StreamReader,
        back_writer: asyncio.StreamWriter,
        initial: bytes,
    ) -> None:
        tasks = [
            asyncio.create_task(
                self._pump_tcp(client_reader, back_writer, _REWRITE_TO_BROWSER, initial),
                name="cdp-to-browser",
            ),
            asyncio.create_task(
                self._pump_tcp(back_reader, client_writer, _REWRITE_TO_PROXY, b""),
                name="browser-to-cdp",
            ),
        ]
        try:
            await self._join_pumps(tasks)
        finally:
            await _close_writer(back_writer)
            await _close_writer(client_writer)

    async def _join_pumps(self, tasks: list[asyncio.Task[None]]) -> None:
        try:
            _done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            results = await asyncio.gather(*tasks, return_exceptions=True)
        except asyncio.CancelledError:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        errors = [item for item in results if isinstance(item, Exception)]
        if errors:
            log.warning("pump_failed %s", type(errors[0]).__name__)
            raise LiveRuntimeError("bridge_failed") from errors[0]

    async def _pump_ws_to_tcp(self, ws: web.WebSocketResponse, writer: asyncio.StreamWriter) -> None:
        async for msg in ws:
            self.touch()
            if msg.type == web.WSMsgType.BINARY:
                payload = bytes(msg.data)
            elif msg.type == web.WSMsgType.TEXT:
                payload = msg.data.encode("latin-1", "replace") if isinstance(msg.data, str) else bytes(msg.data)
            elif msg.type in {web.WSMsgType.CLOSE, web.WSMsgType.CLOSING, web.WSMsgType.CLOSED}:
                break
            elif msg.type == web.WSMsgType.ERROR:
                raise LiveRuntimeError("bridge_failed")
            else:
                continue
            if len(payload) > MAX_WS_MESSAGE:
                raise LiveRuntimeError("message_too_large")
            await _wait_transport(writer.transport)
            writer.write(payload)
            await writer.drain()

    async def _pump_tcp_to_ws(
        self,
        reader: asyncio.StreamReader,
        ws: web.WebSocketResponse,
        transport: Any,
    ) -> None:
        while True:
            data = await reader.read(READ_CHUNK)
            if not data:
                break
            self.touch()
            await _wait_transport(transport)
            await ws.send_bytes(data)

    async def _pump_tcp(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        pairs: tuple[tuple[bytes, bytes], ...],
        initial: bytes,
    ) -> None:
        hold = b""
        pending = initial
        while True:
            if pending:
                chunk = pending
                pending = b""
            else:
                chunk = await reader.read(READ_CHUNK)
                if not chunk:
                    if hold:
                        await _wait_transport(writer.transport)
                        writer.write(hold)
                        await writer.drain()
                    break
            self.touch()
            out, hold = _rewrite(hold, chunk, pairs)
            if out:
                await _wait_transport(writer.transport)
                writer.write(out)
                await writer.drain()

    def _bind_handler(self, handler: asyncio.Task[Any] | None) -> None:
        gen = self.generation
        if handler is None or gen is None:
            return
        self.handler_gen[handler] = gen

    def _defer_recovery(self, gen: Generation) -> bool:
        if gen.chromium_recovering or gen.watchdog_recovering or gen.rebuilding or self._escalating is not None:
            return True
        if time.monotonic() < self.rebuild_after and not self._browser_ready(gen):
            return True
        if gen.stopping:
            return False
        return bool(
            gen.display_alive()
            and gen.ready
            and not gen.chromium_escalated
            and not self._browser_ready(gen)
        )

    async def _close_owned_viewers(self, gen: Generation) -> None:
        current = asyncio.current_task()
        targets = [
            task
            for task, owner in list(self.handler_gen.items())
            if owner is gen and task is not current and not task.done()
        ]
        for task in targets:
            task.cancel()
        # Shutdown joins handlers itself. Waiting here would deadlock that join.
        if targets and not self.stopping:
            await asyncio.gather(*targets, return_exceptions=True)

    def _drop_chromium_child(self, gen: Generation) -> None:
        proc = gen.procs.get("chromium")
        if proc is None or proc.poll() is not None:
            return
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                proc.send_signal(sig)
            except OSError as exc:
                log.warning("chromium_child_stop_failed %s", type(exc).__name__)
                return
            if sig == signal.SIGTERM:
                try:
                    proc.wait(timeout=0.5)
                    return
                except subprocess.TimeoutExpired:
                    continue
        try:
            proc.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            log.warning("chromium_child_stop_failed timeout")

    def _warn_once(self, code: str) -> None:
        if code in self._reaper_logged:
            return
        self._reaper_logged.add(code)
        log.warning("%s", code)

    def _observe_reaper(self, code: str) -> None:
        self.reaper_fault = code
        self.last_error = code
        self._warn_once(f"idle_reaper_fault {code}")

    def _arm_reclaim(self, gen: Generation) -> None:
        if gen.stop_refused:
            return
        gen.stopping = True
        gen.ready = False
        gen.reclaim_pending = True

    def _mark_reclaim(self, gen: Generation, code: str) -> None:
        if gen.stop_refused:
            self.last_error = "owner_unproven"
            return
        gen.stopping = True
        gen.ready = False
        gen.reclaim_pending = True
        self.last_error = code

    def _refuse_unproven(self, gen: Generation) -> None:
        # Hard refuse. Never clear this to let a wake or the reaper kill again.
        gen.stop_refused = True
        gen.reclaim_pending = False
        gen.stopping = True
        gen.ready = False
        self.last_error = "owner_unproven"

    def _bump_reaper_backoff(self) -> None:
        self.reaper_backoff = min(REAPER_BACKOFF_CAP, max(1.0, (self.reaper_backoff or 1.0) * 2))

    def _viewers_idle(self) -> bool:
        return self.active_total() == 0 and (time.monotonic() - self.last_activity) >= IDLE_SECONDS

    def _reclaim_due(self, gen: Generation | None) -> bool:
        return bool(gen is not None and gen.reclaim_pending and not gen.stop_refused)

    def _hold_reclaim(self, gen: Generation | None, code: str) -> None:
        if gen is None:
            return
        if code == "owner_unproven":
            self._refuse_unproven(gen)
            return
        self._mark_reclaim(gen, code)


    def _observe_joined(self, tasks: list[asyncio.Task[Any]], results: list[Any]) -> None:
        for task, result in zip(tasks, results):
            if result is None or isinstance(result, asyncio.CancelledError):
                continue
            if not isinstance(result, Exception):
                continue
            log.warning("background_task_failed name=%s error=%s", task.get_name(), type(result).__name__)
            if task is self.reaper_task:
                self.reaper_fault = self.reaper_fault or type(result).__name__

    async def ensure_worker(self, deadline: float) -> None:
        if self.stopping:
            raise LiveRuntimeError("runtime_stopping")
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LiveRuntimeError("wake_timeout")
            try:
                await asyncio.wait_for(self.wake_lock.acquire(), timeout=remaining)
            except TimeoutError as exc:
                raise LiveRuntimeError("wake_timeout") from exc
            wait_event: asyncio.Event | None = None
            wait_slice = 0.0
            try:
                if self.stopping:
                    raise LiveRuntimeError("runtime_stopping")
                gen = self.generation
                if gen is not None and gen.stop_refused:
                    self.last_error = "owner_unproven"
                    raise LiveRuntimeError("owner_unproven")
                if self._browser_ready(gen):
                    return
                if gen is not None and gen.reclaim_pending:
                    await self._stop_locked(gen, "unhealthy")
                    if self.generation is not None:
                        raise LiveRuntimeError(self.last_error or "stop_failed")
                    gen = None
                if gen is not None and self._defer_recovery(gen):
                    if (
                        gen.chromium_recovering
                        or gen.watchdog_recovering
                        or gen.rebuilding
                        or self._escalating is not None
                    ):
                        wait_event = gen.recovery_event
                    else:
                        wait_slice = min(0.5, remaining)
                elif gen is None and time.monotonic() < self.rebuild_after:
                    wait_slice = min(self.rebuild_after - time.monotonic(), remaining)
                else:
                    if gen is not None:
                        await self._stop_locked(gen, "unhealthy")
                    await self._start_locked(deadline)
                    if not self._browser_ready(self.generation):
                        raise LiveRuntimeError(self.last_error or "worker_exited")
                    return
            except asyncio.CancelledError:
                current = self.generation
                if current is not None and not current.ready and not current.stop_refused:
                    try:
                        await self._stop_locked(current, "cancelled")
                    except asyncio.CancelledError:
                        if not current.stop_refused:
                            try:
                                self._signal_group(current, signal.SIGKILL)
                            except LiveRuntimeError as exc:
                                if exc.code == "owner_unproven":
                                    self._refuse_unproven(current)
                                else:
                                    self._mark_reclaim(current, exc.code)
                        raise
                raise
            finally:
                self.wake_lock.release()
            if wait_event is not None and not wait_event.is_set():
                try:
                    await asyncio.wait_for(wait_event.wait(), timeout=max(0.05, deadline - time.monotonic()))
                except TimeoutError as exc:
                    raise LiveRuntimeError("wake_timeout") from exc
                continue
            if wait_slice > 0 or wait_event is not None:
                await asyncio.sleep(min(wait_slice or 0.5, max(0.05, deadline - time.monotonic())))
                continue
            await asyncio.sleep(0.05)

    async def _start_locked(self, deadline: float) -> None:
        existing = self.generation
        if existing is not None:
            if existing.stop_refused:
                self.last_error = "owner_unproven"
                raise LiveRuntimeError("owner_unproven")
            code = self.last_error if self.last_error in {"stop_failed", "cleanup_failed"} else "stop_failed"
            self._mark_reclaim(existing, code)
            raise LiveRuntimeError(code)
        self._require_binaries()
        gen = Generation(self.home, secrets.token_hex(16))
        self.generation = gen
        try:
            self._prepare_dirs(gen)
            self._reject_unknown_occupancy()
            self._write_authority(gen)
            self._write_openbox(gen)
            self._write_watchdog(gen)
            self._reject_profile_lock(gen, None)
            self._nudge_preferences(gen)
            gen.spawn("Xvfb", self._xvfb_argv(gen), leader=True)
            await self._wait_path(gen, _socket_path(), deadline)
            gen.spawn("openbox", [BINARIES["openbox"], "--sm-disable", "--config-file", str(gen.openbox_rc)])
            await self._wait_alive(gen, "openbox", deadline)
            self._reject_profile_lock(gen, None)
            gen.spawn("chromium", self._chromium_argv(gen))
            gen.spawn("x11vnc", self._x11vnc_argv(gen))
            gen.spawn("watchdog", [sys.executable, str(gen.watchdog_path)])
            await self._wait_port(gen, "x11vnc", RFB_PORT, deadline)
            await self._wait_port(gen, "chromium", CDP_PORT, deadline)
            gen.chromium_listening = True
            gen.ready = True
            gen.write_owner()
            self.last_error = None
            self.rebuild_failures = 0
            log.info("worker_ready display=%s", DISPLAY)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            try:
                await self._stop_locked(gen, "start_failed")
            except Exception as stop_exc:
                self._warn_once(f"stop_during_start_failed {type(stop_exc).__name__}")
                raise stop_exc from exc
            raise

    def _require_binaries(self) -> None:
        missing = [name for name, path in BINARIES.items() if not os.access(path, os.X_OK)]
        if missing:
            self.last_error = "missing_binary"
            log.warning("missing_binary %s", ",".join(missing))
            raise LiveRuntimeError("missing_binary")

    def _prepare_dirs(self, gen: Generation) -> None:
        for path in (gen.profile, gen.state_dir):
            if path.is_symlink():
                raise LiveRuntimeError("profile_symlink")
            path.mkdir(parents=True, exist_ok=True)
            os.chmod(path, PROFILE_MODE)

    def _reject_unknown_occupancy(self) -> None:
        if _lock_path().exists() or _socket_path().exists():
            self.last_error = "display_occupied"
            log.warning("display_occupied %s", DISPLAY)
            raise LiveRuntimeError("display_occupied")
        for port in (CDP_PORT, RFB_PORT):
            if _port_busy(port):
                self.last_error = "port_occupied"
                log.warning("port_occupied %s", port)
                raise LiveRuntimeError("port_occupied")

    def _write_authority(self, gen: Generation) -> None:
        cookie = secrets.token_hex(16)
        source = gen.state_dir / "xauthority.source"
        try:
            _atomic_write(source, f"add {_display_name()} . {cookie}\n".encode("ascii"), FILE_MODE)
            completed = subprocess.run(
                [BINARIES["xauth"], "-f", str(gen.xauthority), "source", str(source)],
                check=False,
                timeout=5,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=gen.env(),
            )
            if completed.returncode != 0:
                raise LiveRuntimeError("authority_failed")
            os.chmod(gen.xauthority, FILE_MODE)
        except LiveRuntimeError:
            raise
        except Exception as exc:
            log.warning("authority_failed %s", type(exc).__name__)
            raise LiveRuntimeError("authority_failed") from exc
        finally:
            cookie = ""
            try:
                source.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                log.warning("authority_source_unlink_failed %s", type(exc).__name__)
                raise

    def _write_openbox(self, gen: Generation) -> None:
        text = """<?xml version="1.0" encoding="UTF-8"?>
<openbox_config xmlns="http://openbox.org/3.4/rc">
  <applications>
    <application class="*" name="*" type="*">
      <decor>no</decor>
      <maximized>yes</maximized>
      <focus>yes</focus>
    </application>
  </applications>
</openbox_config>
"""
        _atomic_write(gen.openbox_rc, text.encode("ascii"), FILE_MODE)

    def _write_watchdog(self, gen: Generation) -> None:
        script = f"""import subprocess, time, sys
WMCTRL = {BINARIES["wmctrl"]!r}
while True:
    try:
        listed = subprocess.run([WMCTRL, "-l"], capture_output=True, timeout=2)
        for line in listed.stdout.splitlines():
            parts = line.split()
            if not parts or not parts[0].startswith(b"0x"):
                continue
            wid = parts[0].decode("ascii", "ignore")
            subprocess.run([WMCTRL, "-i", "-r", wid, "-b", "remove,hidden,shaded"], timeout=2)
            subprocess.run([WMCTRL, "-i", "-r", wid, "-b", "add,maximized_vert,maximized_horz"], timeout=2)
            subprocess.run([WMCTRL, "-i", "-a", wid], timeout=2)
    except Exception as exc:
        sys.stderr.write("watchdog_error %s\\n" % type(exc).__name__)
    time.sleep(1)
"""
        _atomic_write(gen.watchdog_path, script.encode("ascii"), PROFILE_MODE)

    def _nudge_preferences(self, gen: Generation) -> None:
        pref = gen.profile / "Default" / "Preferences"
        pref.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(pref.parent, PROFILE_MODE)
        data: dict[str, Any] = {}
        if pref.exists():
            if pref.is_symlink():
                log.warning("preferences_unreadable")
                return
            try:
                loaded = json.loads(pref.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                log.warning("preferences_unreadable %s", type(exc).__name__)
                return
            if not isinstance(loaded, dict):
                log.warning("preferences_unreadable")
                return
            data = loaded
        browser = data.get("browser")
        if not isinstance(browser, dict):
            browser = {}
            data["browser"] = browser
        browser["custom_chrome_frame"] = False
        profile = data.get("profile")
        if not isinstance(profile, dict):
            profile = {}
            data["profile"] = profile
        profile["exit_type"] = "Normal"
        profile["exited_cleanly"] = True
        _atomic_write(pref, json.dumps(data, ensure_ascii=False).encode("utf-8"), FILE_MODE)

    def _xvfb_argv(self, gen: Generation) -> list[str]:
        return [
            BINARIES["Xvfb"],
            _display_name(),
            "-screen",
            "0",
            GEOMETRY,
            "-listen",
            "unix",
            "-nolisten",
            "tcp",
            "-nolisten",
            "inet6",
            "-auth",
            str(gen.xauthority),
            "+extension",
            "GLX",
            "+render",
            "-noreset",
        ]

    def _chromium_argv(self, gen: Generation) -> list[str]:
        return [
            BINARIES["chromium"],
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--ozone-platform=x11",
            "--force-device-scale-factor=1",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-session-crashed-bubble",
            "--disable-background-networking",
            "--disable-component-update",
            "--disable-sync",
            "--disable-breakpad",
            "--disk-cache-size=20971520",
            "--media-cache-size=20971520",
            "--lang=zh-CN",
            "--remote-debugging-address=127.0.0.1",
            "--remote-debugging-port=39233",
            f"--user-data-dir={gen.profile}",
            "--window-position=0,0",
            "--window-size=1366,768",
            "--start-maximized",
        ]

    def _x11vnc_argv(self, gen: Generation) -> list[str]:
        return [
            BINARIES["x11vnc"],
            "-display",
            _display_name(),
            "-auth",
            str(gen.xauthority),
            "-forever",
            "-shared",
            "-nopw",
            "-rfbport",
            str(RFB_PORT),
            "-localhost",
            "-xkb",
            "-noxdamage",
            "-nolookup",
            "-quiet",
        ]

    async def _wait_path(self, gen: Generation, path: Path, deadline: float) -> None:
        while time.monotonic() < deadline:
            if gen.poll("Xvfb") is not None:
                raise LiveRuntimeError("worker_exited")
            if path.exists():
                return
            await asyncio.sleep(0.1)
        raise LiveRuntimeError("wake_timeout")

    async def _wait_alive(self, gen: Generation, name: str, deadline: float) -> None:
        await asyncio.sleep(0.2)
        if time.monotonic() >= deadline or gen.poll(name) is not None:
            raise LiveRuntimeError("worker_exited" if gen.poll(name) is not None else "wake_timeout")

    async def _wait_port(self, gen: Generation, name: str, port: int, deadline: float) -> None:
        while time.monotonic() < deadline:
            if gen.poll(name) is not None:
                raise LiveRuntimeError("worker_exited")
            if _port_busy(port):
                return
            await asyncio.sleep(0.1)
        raise LiveRuntimeError("wake_timeout")

    def _reject_profile_lock(self, gen: Generation, dead_pid: int | None) -> None:
        lock = gen.profile / "SingletonLock"
        if not lock.exists() and not lock.is_symlink():
            return
        if not lock.is_symlink():
            raise LiveRuntimeError("profile_lock_held")
        pid = _symlink_pid(lock)
        if dead_pid is None or pid != dead_pid or _alive(pid):
            raise LiveRuntimeError("profile_lock_held")
        for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
            path = gen.profile / name
            try:
                path.unlink()
            except FileNotFoundError:
                continue
            except OSError as exc:
                log.warning("lock_unlink_failed %s", type(exc).__name__)
                raise LiveRuntimeError("profile_lock_held") from exc

    def _signal_group(self, gen: Generation, sig: int) -> None:
        pgid = gen.pgid
        if pgid is None or pgid <= 1:
            return
        proven = False
        for name, proc in gen.procs.items():
            if gen.prove(proc.pid, name):
                proven = True
                break
        if not proven:
            if any(_alive(proc.pid) for proc in gen.procs.values()):
                self.last_error = "owner_unproven"
                self._warn_once("owner_unproven")
                raise LiveRuntimeError("owner_unproven")
            return
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            return
        except Exception as exc:
            self._warn_once(f"signal_failed {type(exc).__name__}")
            raise LiveRuntimeError("stop_failed") from exc

    async def _stop_locked(self, gen: Generation, reason: str) -> None:
        # Same generation stays reclaim_pending until kill and proven cleanup finish.
        # owner_unproven never signals again and is not cleared to recover.
        if gen.stop_refused:
            self.last_error = "owner_unproven"
            raise LiveRuntimeError("owner_unproven")
        self._arm_reclaim(gen)
        log.info("stop_worker reason=%s", reason)
        try:
            if any(_alive(proc.pid) for proc in gen.procs.values()):
                self._signal_group(gen, signal.SIGTERM)
                await self._wait_group(gen, 3)
                if any(_alive(proc.pid) for proc in gen.procs.values()):
                    self._signal_group(gen, signal.SIGKILL)
                    await self._wait_group(gen, 2)
        except asyncio.CancelledError:
            if not gen.stop_refused:
                try:
                    self._signal_group(gen, signal.SIGKILL)
                except LiveRuntimeError as exc:
                    if exc.code == "owner_unproven":
                        self._refuse_unproven(gen)
                    else:
                        self._mark_reclaim(gen, exc.code)
            raise
        except LiveRuntimeError as exc:
            if exc.code == "owner_unproven":
                self._refuse_unproven(gen)
            else:
                self._mark_reclaim(gen, exc.code)
            raise
        if gen.stop_refused:
            self.last_error = "owner_unproven"
            raise LiveRuntimeError("owner_unproven")
        if any(_alive(proc.pid) for proc in gen.procs.values()):
            self._mark_reclaim(gen, "stop_failed")
            raise LiveRuntimeError("stop_failed")
        try:
            self._clear_owned_display(gen, strict=True)
            chromium = gen.procs.get("chromium")
            if chromium is not None:
                gen.profile_owner_proven = self._clear_singleton(
                    gen.profile,
                    chromium.pid,
                    gen.profile_owner_proven,
                    gen,
                )
            try:
                gen.owner_path.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                self._warn_once(f"owner_unlink_failed {type(exc).__name__}")
                raise
        except FileNotFoundError:
            pass
        except LiveRuntimeError as exc:
            self._mark_reclaim(gen, exc.code)
            raise
        except OSError as exc:
            self._warn_once(f"reclaim_cleanup_failed {type(exc).__name__}")
            self._mark_reclaim(gen, "cleanup_failed")
            raise LiveRuntimeError("cleanup_failed") from exc
        gen.reclaim_pending = False
        if self.generation is gen:
            self.generation = None
            self.reaper_fault = None
            self._reaper_logged.clear()
            self.reaper_backoff = 0.0
            if reason == "idle":
                self.last_error = None
            elif reason not in {"shutdown", "start_failed", "cancelled"} and self.last_error is None:
                self.last_error = "worker_exited"


    async def _wait_group(self, gen: Generation, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for proc in gen.procs.values():
                proc.poll()  # Reap our children; zombies must not block idle shutdown.
            if not any(_alive(proc.pid) for proc in gen.procs.values()):
                return
            await asyncio.sleep(0.1)

    def _clear_owned_display(self, gen: Generation, *, strict: bool = False) -> None:
        xvfb = gen.procs.get("Xvfb")
        if xvfb is None or _alive(xvfb.pid):
            return
        lock = _lock_path()
        recorded: int | None = None
        if lock.exists():
            try:
                text = lock.read_text(encoding="ascii", errors="replace")
                recorded = int(text.strip().split()[0])
            except FileNotFoundError:
                recorded = None
            except OSError as exc:
                self._warn_once(f"display_lock_unreadable {type(exc).__name__}")
                if strict:
                    raise
                return
            except (ValueError, IndexError) as exc:
                self._warn_once(f"display_lock_unreadable {type(exc).__name__}")
                return
            if recorded is None:
                if not gen.display_owner_proven:
                    return
            elif recorded != xvfb.pid:
                self._warn_once("display_lock_not_ours")
                return
            else:
                gen.display_owner_proven = True
                try:
                    lock.unlink()
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    self._warn_once(f"display_lock_unlink_failed {type(exc).__name__}")
                    raise
        elif not gen.display_owner_proven:
            return
        sock = _socket_path()
        if not sock.exists() and not sock.is_socket():
            return
        try:
            sock.unlink()
        except FileNotFoundError:
            return
        except OSError as exc:
            self._warn_once(f"display_socket_unlink_failed {type(exc).__name__}")
            raise

    def _clear_singleton(self, profile: Path, pid: int | None, proven: bool = False, owner: Generation | None = None) -> bool:
        if pid is None or _alive(pid):
            return proven
        lock = profile / "SingletonLock"
        if lock.is_symlink():
            if _symlink_pid(lock) != pid:
                self._warn_once("profile_lock_not_ours")
                return proven
            proven = True
        elif not proven:
            return False
        if owner is not None:
            owner.profile_owner_proven = True
        for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
            path = profile / name
            if name == "SingletonLock" and path.is_symlink() and _symlink_pid(path) != pid:
                self._warn_once("profile_lock_not_ours")
                return proven
            if name == "SingletonLock" and path.exists() and not path.is_symlink():
                self._warn_once("profile_lock_not_ours")
                return proven
            try:
                if path.is_symlink() or path.exists():
                    path.unlink()
            except FileNotFoundError:
                continue
            except OSError as exc:
                self._warn_once(f"lock_unlink_failed {type(exc).__name__}")
                raise
        return proven


    def _read_owner(self, path: Path) -> dict[str, Any] | None:
        if not path.exists() or path.is_symlink():
            return None
        try:
            data = json.loads(path.read_text(encoding="ascii"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("owner_unreadable %s", type(exc).__name__)
            self.last_error = "owner_unproven"
            return None
        if not isinstance(data, dict):
            self.last_error = "owner_unproven"
            return None
        return data

    async def reconcile_previous(self) -> None:
        owner = self.home / "live-browser" / "owner.json"
        record = self._read_owner(owner)
        if not record:
            return
        token = record.get("token")
        pgid = record.get("pgid")
        pids = record.get("pids")
        if not isinstance(token, str) or not isinstance(pgid, int) or not isinstance(pids, dict):
            self.last_error = "owner_unproven"
            return
        gen = Generation(self.home, token)
        gen.pgid = pgid

        class _Pid:
            def __init__(self, pid: int) -> None:
                self.pid = pid

            def poll(self) -> None:
                return None

        unproven = False
        for name, pid in pids.items():
            if name not in gen.markers or not isinstance(pid, int) or not _alive(pid):
                continue
            if gen.prove(pid, name):
                gen.procs[name] = _Pid(pid)  # type: ignore[assignment]
            else:
                unproven = True
        if unproven:
            self.last_error = "owner_unproven"
            log.warning("previous_owner_unproven")
            return
        if gen.procs:
            try:
                self._signal_group(gen, signal.SIGTERM)
                await self._wait_group(gen, 3)
                if any(_alive(proc.pid) for proc in gen.procs.values()):
                    self._signal_group(gen, signal.SIGKILL)
                    await self._wait_group(gen, 2)
            except LiveRuntimeError as exc:
                self.last_error = exc.code
                log.warning("reconcile_failed %s", exc.code)
                return
            except asyncio.CancelledError:
                self._signal_group(gen, signal.SIGKILL)
                raise
            if any(_alive(proc.pid) for proc in gen.procs.values()):
                self.last_error = "stop_failed"
                return
        xvfb_pid = pids.get("Xvfb")
        if isinstance(xvfb_pid, int) and not _alive(xvfb_pid):
            gen.procs["Xvfb"] = _Pid(xvfb_pid)  # type: ignore[assignment]
            self._clear_owned_display(gen)
        chromium_pid = pids.get("chromium")
        if isinstance(chromium_pid, int):
            self._clear_singleton(gen.profile, chromium_pid)
        try:
            owner.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            log.warning("owner_unlink_failed %s", type(exc).__name__)
            raise

    async def idle_reaper(self) -> None:
        while not self.stopping:
            gen = self.generation
            idle = self._viewers_idle()
            delay = 1.0
            if gen is not None and gen.stop_refused:
                delay = REAPER_BACKOFF_CAP
            elif self._reclaim_due(gen) and idle and self.reaper_backoff > 0:
                delay = self.reaper_backoff
            elif (
                self.reaper_fault
                and self.reaper_backoff > 0
                and gen is not None
                and gen.ready
                and not gen.stopping
                and not gen.reclaim_pending
                and not gen.stop_refused
                and idle
            ):
                delay = self.reaper_backoff
            await asyncio.sleep(delay)
            if self.stopping:
                return
            gen = self.generation
            if gen is None or gen.stop_refused:
                continue
            # ready/stopping are cleared at the start of stop. A pending reclaim
            # of this same generation must still be retried.
            if not self._reclaim_due(gen) and (not gen.ready or gen.stopping):
                continue
            if not self._viewers_idle():
                continue
            target = gen
            stopped = False
            try:
                async with self.wake_lock:
                    gen = self.generation
                    target = gen
                    if gen is None or gen.stop_refused:
                        continue
                    if not self._reclaim_due(gen) and (not gen.ready or gen.stopping):
                        continue
                    if self._viewers_idle():
                        await self._stop_locked(gen, "idle")
                        stopped = True
                if stopped:
                    self.reaper_fault = None
                    self._reaper_logged.clear()
                    self.reaper_backoff = 0.0
            except asyncio.CancelledError:
                raise
            except LiveRuntimeError as exc:
                self._observe_reaper(exc.code)
                self._hold_reclaim(target, exc.code)
                if exc.code != "owner_unproven":
                    self._bump_reaper_backoff()
            except Exception as exc:
                self._observe_reaper(type(exc).__name__)
                self._hold_reclaim(target, type(exc).__name__)
                self._bump_reaper_backoff()

    async def monitor(self) -> None:
        while not self.stopping:
            await asyncio.sleep(0.5)
            gen = self.generation
            if gen is None or gen.stopping or gen.reclaim_pending or gen.stop_refused or not gen.ready:
                continue
            if gen.poll("Xvfb") is not None or gen.poll("openbox") is not None or gen.poll("x11vnc") is not None:
                await self._rebuild(gen, "critical_exit")
                continue
            if gen.poll("watchdog") is not None and not gen.watchdog_recovering and not gen.rebuilding and self._escalating is None:
                log.warning("watchdog_exited code=%s", gen.poll("watchdog"))
                await self._restart_watchdog(gen)
            if gen is not self.generation:
                continue
            gen = self.generation
            if gen is None or gen.stopping or gen.reclaim_pending or gen.stop_refused or not gen.ready or gen.chromium_escalated:
                continue
            if gen.poll("chromium") is not None and not gen.chromium_recovering:
                log.warning("chromium_exited code=%s", gen.poll("chromium"))
                await self._restart_chromium(gen)

    async def _rebuild(self, gen: Generation, reason: str) -> None:
        log.warning("worker_exit %s", reason)
        self.last_error = "worker_exited"
        demand = self.active_total() > 0
        try:
            async with self.wake_lock:
                if self.generation is gen:
                    try:
                        await self._stop_locked(gen, reason)
                    except asyncio.CancelledError:
                        raise
                    except LiveRuntimeError as exc:
                        if exc.code == "owner_unproven":
                            self._refuse_unproven(gen)
                        else:
                            self._mark_reclaim(gen, exc.code)
                        self._warn_once(f"rebuild_stop_refused {exc.code}")
                        return
                    if self.generation is gen:
                        self._mark_reclaim(gen, self.last_error or "stop_failed")
                        self._warn_once("rebuild_stop_incomplete")
                        return
                elif self.generation is not None:
                    # Another owner already replaced this generation. Do not orphan it.
                    log.info("rebuild_skipped_foreign_generation")
                    return
                if not demand or self.stopping or time.monotonic() < self.rebuild_after:
                    return
                try:
                    await self._start_locked(time.monotonic() + WAKE_DEADLINE_SECONDS)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.rebuild_failures += 1
                    delay = min(REBUILD_BACKOFF_CAP, 2 ** min(self.rebuild_failures, 3))
                    self.rebuild_after = time.monotonic() + delay
                    code = exc.code if isinstance(exc, LiveRuntimeError) else "worker_exited"
                    self.last_error = code
                    log.warning("rebuild_failed %s", code)
        finally:
            await self._close_owned_viewers(gen)

    async def _restart_chromium(self, gen: Generation) -> None:
        if self.active_total() == 0 or gen.chromium_escalated:
            return
        claimed = False
        failed_code: str | None = None
        escalate = False
        dead_pid: int | None = None
        async with self.wake_lock:
            if (
                self.generation is not gen
                or gen.stopping
                or gen.reclaim_pending
                or gen.stop_refused
                or gen.chromium_escalated
                or gen.chromium_recovering
                or self._escalating is not None
                or self.active_total() == 0
                or gen.poll("chromium") is None
            ):
                return
            gen.chromium_recovering = True
            gen.chromium_listening = False
            gen.recovery_event.clear()
            claimed = True
            delay = min(CHROMIUM_BACKOFF_CAP, 2 ** min(gen.chromium_restarts, 3))
            gen.chromium_restarts += 1
            proc = gen.procs.get("chromium")
            dead_pid = proc.pid if proc is not None else None
        try:
            await asyncio.sleep(delay)
            async with self.wake_lock:
                if self.generation is not gen or gen.stopping or gen.reclaim_pending or gen.stop_refused or self.active_total() == 0:
                    return
                if gen.poll("chromium") is not None:
                    try:
                        self._reject_profile_lock(gen, dead_pid)
                        gen.spawn("chromium", self._chromium_argv(gen))
                    except asyncio.CancelledError:
                        raise
                    except LiveRuntimeError as exc:
                        failed_code = exc.code
                        self.last_error = exc.code
                        log.warning("chromium_restart_failed %s", exc.code)
                    except Exception as exc:
                        failed_code = "worker_exited"
                        self.last_error = "worker_exited"
                        log.warning("chromium_restart_failed %s", type(exc).__name__)
            if failed_code is None and self.generation is gen and not gen.stopping and self.active_total() > 0:
                try:
                    await self._wait_port(gen, "chromium", CDP_PORT, time.monotonic() + 8)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    failed_code = exc.code if isinstance(exc, LiveRuntimeError) else "worker_exited"
                    self.last_error = failed_code
                    log.warning("chromium_restart_failed %s", failed_code)
                    self._drop_chromium_child(gen)
            drop_child = False
            if (
                failed_code is None
                and self.generation is gen
                and gen.poll("chromium") is None
                and _port_busy(CDP_PORT)
            ):
                async with self.wake_lock:
                    if self.generation is gen and not gen.stopping and gen.poll("chromium") is None and _port_busy(CDP_PORT):
                        gen.chromium_listening = True
                        gen.chromium_failures = 0
                    elif self.generation is gen:
                        failed_code = "worker_exited"
                        self.last_error = failed_code
                        drop_child = True
            elif (
                failed_code is None
                and self.generation is gen
                and not gen.stopping
                and self.active_total() > 0
                and (gen.poll("chromium") is not None or not _port_busy(CDP_PORT))
            ):
                failed_code = "worker_exited"
                self.last_error = failed_code
                drop_child = True
            if drop_child:
                self._drop_chromium_child(gen)
            if failed_code is not None:
                gen.chromium_failures += 1
                escalate = gen.chromium_failures >= CHROMIUM_RECOVERY_LIMIT and not gen.chromium_escalated
        finally:
            if claimed:
                async with self.wake_lock:
                    gen.chromium_recovering = False
                    if escalate:
                        gen.chromium_escalated = True
                        self._escalating = gen
                    else:
                        gen.recovery_event.set()
        if not escalate:
            return
        try:
            await self._rebuild(gen, "chromium_restart_failed")
        except asyncio.CancelledError:
            raise
        except LiveRuntimeError as exc:
            self.last_error = exc.code
            log.warning("chromium_escalate_failed %s", exc.code)
            if exc.code == "owner_unproven":
                self._refuse_unproven(gen)
        finally:
            async with self.wake_lock:
                if self._escalating is gen:
                    self._escalating = None
            gen.recovery_event.set()

    async def _restart_watchdog(self, gen: Generation) -> None:
        claimed = False
        failed = False
        async with self.wake_lock:
            if (
                self.generation is not gen
                or gen.stopping
                or gen.reclaim_pending
                or gen.stop_refused
                or gen.watchdog_recovering
                or gen.rebuilding
                or self._escalating is not None
                or gen.poll("watchdog") is None
            ):
                return
            gen.watchdog_recovering = True
            gen.recovery_event.clear()
            claimed = True
        try:
            await asyncio.sleep(1)
            async with self.wake_lock:
                if self.generation is not gen or gen.stopping or gen.reclaim_pending or gen.stop_refused:
                    return
                if gen.poll("watchdog") is None:
                    return
                try:
                    gen.spawn("watchdog", [sys.executable, str(gen.watchdog_path)])
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    failed = True
                    self.last_error = "worker_exited"
                    log.warning("watchdog_restart_failed %s", type(exc).__name__)
        finally:
            if claimed:
                async with self.wake_lock:
                    gen.watchdog_recovering = False
                    if failed:
                        gen.rebuilding = True
                    else:
                        gen.recovery_event.set()
        if not failed:
            return
        try:
            await self._rebuild(gen, "watchdog_restart_failed")
        finally:
            gen.rebuilding = False
            gen.recovery_event.set()

    async def bind(self) -> None:
        app = web.Application(client_max_size=16 * 1024)
        app.router.add_get("/health", self.health)
        app.router.add_get("/websockify", self.websockify)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        self.site = web.TCPSite(
            self.runner,
            HOST,
            CONTROLLER_PORT,
            reuse_address=True,
            reuse_port=False,
        )
        try:
            await self.site.start()
        except OSError as exc:
            await self.runner.cleanup()
            self.last_error = "port_occupied"
            log.warning("port_occupied %s", CONTROLLER_PORT)
            raise LiveRuntimeError("port_occupied") from exc
        try:
            self.cdp_server = await asyncio.start_server(
                self._handle_cdp,
                HOST,
                CDP_PROXY_PORT,
                reuse_address=True,
            )
        except OSError as exc:
            await self.runner.cleanup()
            self.last_error = "port_occupied"
            log.warning("port_occupied %s", CDP_PROXY_PORT)
            raise LiveRuntimeError("port_occupied") from exc
        log.info("listening controller=%s cdp_proxy=%s", CONTROLLER_PORT, CDP_PROXY_PORT)

    async def serve(self) -> None:
        await self.reconcile_previous()
        await self.bind()
        self.reaper_task = asyncio.create_task(self.idle_reaper(), name="idle-reaper")
        self.monitor_task = asyncio.create_task(self.monitor(), name="worker-monitor")
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        await stop.wait()
        await self.shutdown()

    async def shutdown(self) -> None:
        if self._shutdown_started:
            return
        self._shutdown_started = True
        self.stopping = True
        current = asyncio.current_task()
        background = [
            task for task in (self.reaper_task, self.monitor_task)
            if task is not None and task is not current
        ]
        for task in background:
            if not task.done():
                task.cancel()
        handlers = tuple(task for task in self.handlers if task is not current)
        for handler in handlers:
            if not handler.done():
                handler.cancel()
        joined = background + list(handlers)
        if joined:
            results = await asyncio.gather(*joined, return_exceptions=True)
            self._observe_joined(joined, results)
        if self.cdp_server is not None:
            self.cdp_server.close()
            await self.cdp_server.wait_closed()
        if self.site is not None:
            await self.site.stop()
        late = tuple(task for task in self.handlers if task is not current and not task.done())
        for handler in late:
            handler.cancel()
        if late:
            await asyncio.gather(*late, return_exceptions=True)
        async with self.wake_lock:
            gen = self.generation
            if gen is not None:
                try:
                    await self._stop_locked(gen, "shutdown")
                except asyncio.CancelledError:
                    raise
                except LiveRuntimeError as exc:
                    self.last_error = exc.code
                    self._warn_once(f"shutdown_{exc.code}")
                    # owner_unproven is a safe refuse: do not signal again or
                    # wait on this task. Other stop failures still propagate.
                    if exc.code != "owner_unproven":
                        raise
        if self.runner is not None:
            await self.runner.cleanup()


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )
    log.addFilter(_RedactFilter())


def main(argv: list[str] | None = None) -> int:
    _configure_logging()
    parser = argparse.ArgumentParser(prog="clawd-live-browser")
    parser.add_argument("--home", required=True)
    args = parser.parse_args(argv)
    from clawd_server.home import resolve_home

    home = resolve_home(args.home)
    runtime = LiveRuntime(home)
    try:
        asyncio.run(runtime.serve())
    except KeyboardInterrupt:
        return 0
    except LiveRuntimeError as exc:
        log.warning("runtime_failed %s", exc.code)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
