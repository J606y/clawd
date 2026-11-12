"""Protected /live routes for the shared clawd screen.

Static noVNC bytes never wake the display. Only an authenticated same-origin
WebSocket upgrade to /live/websockify connects to the fixed loopback controller.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import stat
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from aiohttp import ClientSession, ClientTimeout, ClientWSTimeout, DummyCookieJar, WSMsgType, web

log = logging.getLogger("clawd.panel.live")

PROTOCOL = "clawd-live-v1"
ISOLATED_CDP = "127.0.0.1:9"
FIXED_CDP = "127.0.0.1:39233"
CONTROLLER_HTTP = "http://127.0.0.1:39232"
HEALTH_URL = "http://127.0.0.1:39232/health"
WS_URL = "ws://127.0.0.1:39232/websockify"
CONFIG_NAME = "live-browser.json"
WAKE_DEADLINE_SECONDS = 45
HEALTH_DEADLINE_SECONDS = 5
MAX_WS_MSG = 16 * 1024 * 1024
MAX_STATIC_BYTES = 8 * 1024 * 1024
MAX_CONFIG_BYTES = 4096
HEARTBEAT_SECONDS = 20.0
_REQUIRED_CONFIG = frozenset({"protocol", "home", "controller", "cdp", "novnc_root"})
_BLOCKED_HEADERS = frozenset({
    "authorization",
    "cookie",
    "proxy-authorization",
    "x-panel-key",
})
_OPEN: set[tuple[Any, Any]] = set()

# Extensions present in the pinned noVNC 1.6 tree. Unknown types are not served.
_MIME: dict[str, tuple[str, str | None]] = {
    ".js": ("text/javascript", "utf-8"),
    ".mjs": ("text/javascript", "utf-8"),
    ".html": ("text/html", "utf-8"),
    ".css": ("text/css", "utf-8"),
    ".json": ("application/json", "utf-8"),
    ".svg": ("image/svg+xml", "utf-8"),
    ".txt": ("text/plain", "utf-8"),
    ".md": ("text/plain", "utf-8"),
    ".yml": ("text/plain", "utf-8"),
    ".yaml": ("text/plain", "utf-8"),
    ".po": ("text/plain", "utf-8"),
    ".pot": ("text/plain", "utf-8"),
    ".pl": ("text/plain", "utf-8"),
    ".sh": ("text/plain", "utf-8"),
    ".1": ("text/plain", "utf-8"),
    ".0": ("text/plain", "utf-8"),
    ".bsd-2-clause": ("text/plain", "utf-8"),
    ".bsd-3-clause": ("text/plain", "utf-8"),
    ".png": ("image/png", None),
    ".ico": ("image/x-icon", None),
    ".mp3": ("audio/mpeg", None),
    ".oga": ("audio/ogg", None),
    ".woff": ("font/woff", None),
    ".ttf": ("font/ttf", None),
    ".pdf": ("application/pdf", None),
}


def _endpoints_fixed() -> bool:
    return (
        CONTROLLER_HTTP == "http://127.0.0.1:39232"
        and FIXED_CDP == "127.0.0.1:39233"
        and HEALTH_URL == "http://127.0.0.1:39232/health"
        and WS_URL == "ws://127.0.0.1:39232/websockify"
        and ISOLATED_CDP == "127.0.0.1:9"
    )


def _outbound_headers() -> dict[str, str]:
    """Fixed loopback headers. Never copies the browser request."""
    raw = {"Host": "127.0.0.1:39232", "Accept": "application/json"}
    return {key: value for key, value in raw.items() if key.lower() not in _BLOCKED_HEADERS}


def _json_error(status: int, code: str, message: str) -> web.Response:
    return web.json_response(
        {"error": {"code": code, "message": message}},
        status=status,
        headers={"Cache-Control": "no-cache, no-store", "X-Content-Type-Options": "nosniff"},
    )


def _plain(status: int, text: str, extra: dict[str, str] | None = None) -> web.Response:
    headers = {"Cache-Control": "no-cache, no-store", "X-Content-Type-Options": "nosniff"}
    if extra:
        headers.update(extra)
    return web.Response(status=status, text=text, content_type="text/plain", charset="utf-8", headers=headers)


def _unauthorized() -> web.Response:
    return _json_error(401, "unauthorized", "unauthorized")


def _unavailable() -> web.Response:
    return _json_error(503, "live_unavailable", "实时画面暂不可用")


def _authorize(server: object, request: web.Request) -> web.Response | None:
    """None allows. Any denial, missing checker, or bad return fails closed."""
    checker = getattr(server, "_authed", None)
    if not callable(checker):
        return _unauthorized()
    try:
        denied = checker(request)
    except Exception:
        log.warning("live auth checker failed closed")
        return _unauthorized()
    if denied is None:
        return None
    if isinstance(denied, web.StreamResponse):
        return denied
    return _unauthorized()


def _header_list(headers: Any, name: str) -> list[str] | None:
    getall = getattr(headers, "getall", None)
    if not callable(getall):
        return None
    try:
        found = getall(name, [])
    except TypeError:
        try:
            found = getall(name)
        except KeyError:
            return []
        except Exception:
            return None
    except Exception:
        return None
    if found is None:
        return []
    if isinstance(found, str):
        found = [found]
    else:
        try:
            found = list(found)
        except TypeError:
            return None
    values: list[str] = []
    for item in found:
        if not isinstance(item, str):
            return None
        values.append(item)
    return values


def _peer(request: web.Request) -> str | None:
    remote = getattr(request, "remote", None)
    if isinstance(remote, str) and remote:
        return remote
    transport = getattr(request, "transport", None)
    if transport is None:
        return None
    try:
        peer = transport.get_extra_info("peername")
    except Exception:
        return None
    if not peer or not isinstance(peer[0], str):
        return None
    return peer[0]


def _loopback(peer: str | None) -> bool:
    if not isinstance(peer, str) or not peer or peer != peer.strip() or len(peer) > 128:
        return False
    text = peer[1:-1] if peer.startswith("[") and peer.endswith("]") else peer
    if text.startswith("::ffff:"):
        mapped = text[7:]
        try:
            if ipaddress.ip_address(mapped).version == 4:
                text = mapped
        except ValueError:
            return False
    try:
        return ipaddress.ip_address(text).is_loopback
    except ValueError:
        return False


def _public_scheme(request: web.Request) -> str | None:
    values = _header_list(request.headers, "X-Forwarded-Proto")
    if values is None or len(values) > 1:
        return None
    if values:
        raw = values[0]
        if not _loopback(_peer(request)) or raw not in {"http", "https"}:
            return None
        return raw
    scheme = getattr(request, "scheme", None)
    if isinstance(scheme, str) and scheme in {"http", "https"}:
        return scheme
    return None


def _split_host(value: str) -> tuple[str, int | None] | None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 512
        or value != value.strip()
        or not value.isascii()
        or any(ord(char) < 33 or ord(char) == 127 for char in value)
        or any(char in value for char in "@/\\?#")
    ):
        return None
    if value.startswith("["):
        end = value.find("]")
        if end <= 1 or value.count("[") != 1 or value.count("]") != 1:
            return None
        host = value[1:end].lower()
        rest = value[end + 1:]
        if "%" in host or not host:
            return None
        try:
            ipaddress.ip_address(host)
        except ValueError:
            return None
        if rest == "":
            return host, None
        if not rest.startswith(":") or not rest[1:].isdigit():
            return None
        port = int(rest[1:])
        if not 1 <= port <= 65535:
            return None
        return host, port
    if value.count(":") > 1 or "[" in value or "]" in value:
        return None
    host, sep, port_text = value.partition(":")
    if not host:
        return None
    host = host.lower()
    if not sep:
        return host, None
    if not port_text.isdigit():
        return None
    port = int(port_text)
    if not 1 <= port <= 65535:
        return None
    return host, port


def _origin_parts(value: str) -> tuple[str, str, int | None] | None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 2048
        or value != value.strip()
        or value.lower() == "null"
        or "," in value
        or not value.isascii()
    ):
        return None
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"}:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        return None
    if not parsed.hostname or not parsed.hostname.isascii():
        return None
    host = parsed.hostname.lower()
    return parsed.scheme, host, parsed.port


def _default_port(scheme: str, port: int | None) -> int:
    if port is None:
        return 443 if scheme == "https" else 80
    return port


def _origin_strict(request: web.Request) -> bool:
    """WebSocket same-origin. Do not use GET's omitted-Origin or omitted-port rules."""
    scheme = _public_scheme(request)
    if scheme not in {"http", "https"}:
        return False
    hosts = _header_list(request.headers, "Host")
    origins = _header_list(request.headers, "Origin")
    fetch = _header_list(request.headers, "Sec-Fetch-Site")
    if hosts is None or origins is None or fetch is None:
        return False
    if len(hosts) != 1 or len(origins) != 1 or len(fetch) > 1:
        return False
    if fetch and fetch[0] != "same-origin":
        return False
    origin = _origin_parts(origins[0])
    host = _split_host(hosts[0])
    if origin is None or host is None:
        return False
    origin_scheme, origin_host, origin_port = origin
    host_name, host_port = host
    if origin_scheme != scheme or origin_host != host_name:
        return False
    return _default_port(scheme, origin_port) == _default_port(scheme, host_port)


def _upgrade_websocket(request: web.Request) -> bool:
    upgrade = _header_list(request.headers, "Upgrade")
    connection = _header_list(request.headers, "Connection")
    if upgrade is None or connection is None or len(upgrade) != 1 or len(connection) != 1:
        return False
    if upgrade[0].lower() != "websocket":
        return False
    tokens = {part.strip().lower() for part in connection[0].split(",")}
    return "upgrade" in tokens


def _symlink_between(root: Path, path: Path) -> bool:
    try:
        logical_root = Path(os.path.abspath(root))
        logical = Path(os.path.abspath(path))
        rest = logical.relative_to(logical_root)
    except (OSError, ValueError):
        return True
    acc = logical_root
    for part in rest.parts:
        acc = acc / part
        try:
            if acc.is_symlink():
                return True
        except OSError:
            return True
    return False


def _real_novnc() -> Path | None:
    if not _endpoints_fixed():
        return None
    try:
        from clawd_server.resources import panel_root

        panel = panel_root()
        root = panel / "vendor" / "novnc"
        if root.is_symlink() or not root.is_dir() or _symlink_between(panel, root):
            return None
        resolved = root.resolve()
        panel_resolved = panel.resolve()
        if panel_resolved != resolved and panel_resolved not in resolved.parents:
            return None
        if resolved != (panel_resolved / "vendor" / "novnc"):
            return None
    except Exception:
        return None
    return resolved


def _read_config_bytes(home: Path) -> bytes | None:
    path = Path(home) / "panel" / CONFIG_NAME
    try:
        home_resolved = Path(home).resolve()
        if path.is_symlink() or _symlink_between(home_resolved, path):
            return None
        if home_resolved != path.resolve() and home_resolved not in path.resolve().parents:
            return None
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except (OSError, RuntimeError):
        return None
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_uid != os.geteuid()
            or info.st_size <= 0
            or info.st_size > MAX_CONFIG_BYTES
        ):
            return None
        data = os.read(fd, MAX_CONFIG_BYTES + 1)
    finally:
        os.close(fd)
    if len(data) > MAX_CONFIG_BYTES or data.startswith(b"\xef\xbb\xbf") or b"\x00" in data:
        return None
    return data


def _config_matches(home: Path) -> bool:
    if not _endpoints_fixed():
        return False
    try:
        current = Path(home).resolve()
    except (OSError, RuntimeError):
        return False
    expected = _real_novnc()
    raw = _read_config_bytes(home)
    if expected is None or raw is None:
        return False
    try:
        text = raw.decode("utf-8")

        def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
            obj: dict[str, Any] = {}
            for key, value in items:
                if key in obj:
                    raise ValueError("duplicate")
                obj[key] = value
            return obj

        parsed = json.loads(text, object_pairs_hook=_pairs)
    except (UnicodeError, ValueError, json.JSONDecodeError):
        return False
    if not isinstance(parsed, dict) or set(parsed) != _REQUIRED_CONFIG:
        return False
    protocol = parsed["protocol"]
    controller = parsed["controller"]
    cdp = parsed["cdp"]
    home_text = parsed["home"]
    novnc_text = parsed["novnc_root"]
    if (
        protocol != PROTOCOL
        or controller != CONTROLLER_HTTP
        or cdp != FIXED_CDP
        or not isinstance(home_text, str)
        or not isinstance(novnc_text, str)
    ):
        return False
    if not home_text.startswith("/") or not novnc_text.startswith("/") or "\x00" in home_text or "\x00" in novnc_text:
        return False
    try:
        home_path = Path(home_text)
        novnc_path = Path(novnc_text)
        if novnc_path.is_symlink():
            return False
        if home_path.resolve() != current or novnc_path.resolve() != expected:
            return False
    except OSError:
        return False
    return True


def panel_live_cdp(home: Path) -> str:
    """Non-waking CDP when the frozen config matches. Does not open a socket."""
    try:
        if _config_matches(home):
            return FIXED_CDP
    except Exception:
        log.warning("live config unusable; keeping isolated CDP")
    return ISOLATED_CDP


def live_config_enabled(home: Path) -> bool:
    try:
        return _config_matches(home)
    except Exception:
        return False


def live_routes_enabled(home: Path) -> bool:
    """Config match plus the CDP this process actually published."""
    try:
        return os.environ.get("PANEL_LIVE_CDP") == FIXED_CDP and _config_matches(home)
    except Exception:
        return False


def _server_home(server: object) -> Path | None:
    home = getattr(server, "home", None)
    if isinstance(home, Path):
        return home
    return None


def _safe_static(root: Path, name: str) -> tuple[bytes, str, str | None] | None:
    if not isinstance(name, str) or not name or len(name) > 512 or name.endswith("/"):
        return None
    if name.startswith("/") or "\\" in name or "\x00" in name or not name.isascii():
        return None
    parts = PurePosixPath(name).parts
    if not parts or any(part in {"", ".", ".."} or part.startswith(".") for part in parts):
        return None
    suffix = PurePosixPath(parts[-1]).suffix.lower()
    if suffix:
        mime = _MIME.get(suffix)
        if mime is None:
            return None
    else:
        mime = ("text/plain", "utf-8")
    current = root
    for part in parts:
        if part in {".", ".."} or "/" in part or "\\" in part:
            return None
        current = current / part
        try:
            if current.is_symlink():
                return None
        except OSError:
            return None
    try:
        resolved = current.resolve()
        if root != resolved and root not in resolved.parents:
            return None
        if not current.is_file():
            return None
        fd = os.open(current, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_STATIC_BYTES:
            return None
        data = os.read(fd, MAX_STATIC_BYTES + 1)
    finally:
        os.close(fd)
    if len(data) > MAX_STATIC_BYTES:
        return None
    return data, mime[0], mime[1]


async def handle_listing(server: object, request: web.Request) -> web.StreamResponse:
    denied = _authorize(server, request)
    if denied is not None:
        return denied
    return _plain(404, "not found\n")


async def handle_static(server: object, request: web.Request) -> web.StreamResponse:
    denied = _authorize(server, request)
    if denied is not None:
        return denied
    if request.method != "GET":
        return _plain(405, "method not allowed\n", {"Allow": "GET"})
    name = request.match_info.get("name", "")
    if name == "websockify":
        return await handle_websockify(server, request)
    home = _server_home(server)
    root = _real_novnc() if home is not None and live_routes_enabled(home) else None
    if home is None or root is None:
        return _plain(404, "not found\n")
    loaded = _safe_static(root, name)
    if loaded is None:
        escaped = isinstance(name, str) and (".." in PurePosixPath(name).parts or name.startswith("/") or "\\" in name)
        if escaped:
            return _json_error(403, "denied", "路径不被允许")
        return _plain(404, "not found\n")
    body, content_type, charset = loaded
    headers = {"Cache-Control": "no-cache, no-store", "X-Content-Type-Options": "nosniff"}
    if charset:
        return web.Response(body=body, content_type=content_type, charset=charset, headers=headers)
    return web.Response(body=body, content_type=content_type, headers=headers)


async def _health_matches(home: Path) -> bool:
    timeout = ClientTimeout(total=HEALTH_DEADLINE_SECONDS, sock_connect=2, sock_read=HEALTH_DEADLINE_SECONDS)
    try:
        async with ClientSession(timeout=timeout, cookie_jar=DummyCookieJar()) as session:
            async with session.get(
                HEALTH_URL,
                headers=_outbound_headers(),
                allow_redirects=False,
            ) as response:
                if response.status != 200:
                    return False
                raw = await response.content.read(MAX_CONFIG_BYTES + 1)
                if len(raw) > MAX_CONFIG_BYTES:
                    return False
                if await response.content.read(1):
                    return False
    except Exception:
        return False
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    if data.get("protocol") != PROTOCOL:
        return False
    if data.get("home") != str(Path(home).resolve()):
        return False
    return isinstance(data.get("running"), bool)


def _abort(ws: Any) -> None:
    if ws is None:
        return
    transport = None
    getter = getattr(ws, "get_extra_info", None)
    if callable(getter):
        try:
            transport = getter("transport")
        except Exception:
            transport = None
    if transport is None:
        transport = getattr(ws, "transport", None)
    if transport is None:
        return
    abort = getattr(transport, "abort", None)
    closer = getattr(transport, "close", None)
    try:
        if callable(abort):
            abort()
        elif callable(closer):
            closer()
    except Exception:
        return


async def _close_ws(ws: Any) -> None:
    if ws is None:
        return
    closer = getattr(ws, "close", None)
    try:
        if callable(closer) and not getattr(ws, "closed", False):
            await asyncio.wait_for(closer(), timeout=2)
    except Exception as exc:
        log.warning("live websocket close failed: %s", type(exc).__name__)
    _abort(ws)


async def _close_session(session: ClientSession | None) -> None:
    if session is None or session.closed:
        return
    try:
        await asyncio.wait_for(session.close(), timeout=2)
    except Exception as exc:
        log.warning("live session close failed: %s", type(exc).__name__)


async def _forward(src: Any, dst: Any) -> None:
    async for msg in src:
        if msg.type in {WSMsgType.PING, WSMsgType.PONG}:
            continue
        if msg.type == WSMsgType.BINARY:
            data = msg.data
            if isinstance(data, str):
                return
            await dst.send_bytes(bytes(data))
            continue
        if msg.type == WSMsgType.TEXT:
            if not isinstance(msg.data, str):
                return
            await dst.send_str(msg.data)
            continue
        return


async def _stop_pumps(tasks: list[asyncio.Task[Any]]) -> None:
    for task in tasks:
        if not task.done():
            task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def _cleanup(
    tasks: list[asyncio.Task[Any]],
    upstream: Any,
    session: ClientSession | None,
) -> None:
    try:
        await _stop_pumps(tasks)
        await _close_ws(upstream)
        await _close_session(session)
    finally:
        _OPEN.discard((upstream, session))
        _abort(upstream)


async def close_live_upstreams(_app: web.Application) -> None:
    items = list(_OPEN)
    _OPEN.clear()
    for upstream, session in items:
        _abort(upstream)
        await _close_session(session)


async def handle_websockify(server: object, request: web.Request) -> web.StreamResponse:
    denied = _authorize(server, request)
    if denied is not None:
        return denied
    if request.method != "GET":
        return _plain(405, "method not allowed\n", {"Allow": "GET"})
    if not _origin_strict(request):
        return _json_error(403, "origin_rejected", "来源不被允许")
    if not _upgrade_websocket(request):
        return _plain(426, "需要 WebSocket\n", {"Upgrade": "websocket"})
    home = _server_home(server)
    if home is None or not live_routes_enabled(home) or not _endpoints_fixed():
        return _unavailable()
    session: ClientSession | None = None
    upstream: Any = None
    client: web.WebSocketResponse | None = None
    tasks: list[asyncio.Task[Any]] = []
    try:
        if not await _health_matches(home):
            return _unavailable()
        session = ClientSession(
            timeout=ClientTimeout(total=None, sock_connect=WAKE_DEADLINE_SECONDS, sock_read=None),
            cookie_jar=DummyCookieJar(),
        )
        upstream = await asyncio.wait_for(
            session.ws_connect(
                WS_URL,
                headers=_outbound_headers(),
                timeout=ClientWSTimeout(ws_receive=None, ws_close=5.0),
                max_msg_size=MAX_WS_MSG,
                compress=0,
                autoping=True,
                heartbeat=HEARTBEAT_SECONDS,
                protocols=(),
            ),
            timeout=WAKE_DEADLINE_SECONDS,
        )
        _OPEN.add((upstream, session))
        client = web.WebSocketResponse(
            max_msg_size=MAX_WS_MSG,
            autoping=True,
            heartbeat=HEARTBEAT_SECONDS,
            compress=False,
        )
        await client.prepare(request)
        tasks.append(asyncio.create_task(_forward(client, upstream), name="clawd-live-c2u"))
        tasks.append(asyncio.create_task(_forward(upstream, client), name="clawd-live-u2c"))
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        return client
    except asyncio.CancelledError:
        raise
    except Exception:
        log.warning("live websockify unavailable")
        if client is not None and client.prepared:
            return client
        return _unavailable()
    finally:
        try:
            await asyncio.shield(_cleanup(tasks, upstream, session))
        except asyncio.CancelledError:
            _abort(upstream)
            raise
        except Exception:
            _abort(upstream)


def register_live_routes(app: web.Application, server: object) -> None:
    """Install /live before the static fallback. Does not connect or wake."""
    async def websockify(request: web.Request) -> web.StreamResponse:
        return await handle_websockify(server, request)

    async def listing(request: web.Request) -> web.StreamResponse:
        return await handle_listing(server, request)

    async def static(request: web.Request) -> web.StreamResponse:
        return await handle_static(server, request)

    app.router.add_route("*", "/live/websockify", websockify)
    app.router.add_route("*", "/live", listing)
    app.router.add_route("*", "/live/", listing)
    app.router.add_route("*", "/live/{name:.*}", static)
    app.on_shutdown.append(close_live_upstreams)
