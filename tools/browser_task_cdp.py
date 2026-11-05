"""Loopback CDP for targets a browser tool just created.

No default host, no desktop VNC, and no "current visible page". Every call
names the endpoint the tool already resolved. A missing id, a browser-id
mismatch, or a non-loopback URL fails closed.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from urllib.parse import quote, urlsplit

_LOOPBACK = frozenset({"127.0.0.1", "localhost"})
_MAX_BODY = 1_000_000
_TIMEOUT = 3.0


class CdpError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ARG002
        raise CdpError("cdp_redirect", "CDP endpoint redirected; refusing")


def http_base(cdp_url: str) -> str:
    """``http://127.0.0.1:port`` from a CDP websocket or HTTP URL. Nothing else."""
    raw = (cdp_url or "").strip()
    if not raw or "://" not in raw:
        raise CdpError("cdp_endpoint_missing", "browser endpoint is missing")
    parts = urlsplit(raw)
    if parts.scheme not in {"ws", "wss", "http", "https"}:
        raise CdpError("cdp_endpoint_invalid", "browser endpoint scheme is not CDP")
    if parts.hostname not in _LOOPBACK or parts.username or parts.password or parts.port is None:
        raise CdpError("cdp_endpoint_invalid", "browser endpoint is not a loopback CDP port")
    scheme = "https" if parts.scheme in {"wss", "https"} else "http"
    return f"{scheme}://{parts.hostname}:{parts.port}"


def _opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(_NoRedirect)


def _read(method: str, url: str) -> bytes:
    parts = urlsplit(url)
    if parts.hostname not in _LOOPBACK or parts.scheme not in {"http", "https"}:
        raise CdpError("cdp_endpoint_invalid", "refusing a non-loopback CDP request")
    request = urllib.request.Request(url, data=b"" if method == "PUT" else None, method=method)
    try:
        with _opener().open(request, timeout=_TIMEOUT) as response:
            body = response.read(_MAX_BODY + 1)
    except CdpError:
        raise
    except Exception as exc:
        raise CdpError("cdp_unavailable", f"CDP request failed: {type(exc).__name__}") from exc
    if len(body) > _MAX_BODY:
        raise CdpError("cdp_invalid", "CDP response is too large")
    return body


def _json(method: str, url: str):
    try:
        return json.loads(_read(method, url).decode("utf-8"))
    except CdpError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise CdpError("cdp_invalid", "CDP response is not JSON") from exc


def read_browser_id(base: str) -> str:
    version = _json("GET", base + "/json/version")
    if not isinstance(version, dict):
        raise CdpError("cdp_invalid", "browser version payload is not an object")
    path = urlsplit(str(version.get("webSocketDebuggerUrl") or "")).path
    if not path.startswith("/devtools/browser/"):
        raise CdpError("browser_id_missing", "browser identity is missing")
    return path


def create_page(base: str) -> str:
    """Create a blank page. Never selects an existing target."""
    url = base + "/json/new?" + quote("about:blank", safe=":")
    payload = None
    error: Exception | None = None
    for method in ("PUT", "GET"):
        try:
            payload = _json(method, url)
            break
        except CdpError as exc:
            error = exc
    if not isinstance(payload, dict):
        raise CdpError("target_create_failed", "browser did not create a target") from error
    target_id = payload.get("id") or payload.get("targetId")
    if not isinstance(target_id, str) or not target_id:
        raise CdpError("target_create_failed", "created target has no id")
    return target_id


def _pages(base: str) -> list[dict]:
    payload = _json("GET", base + "/json/list")
    if not isinstance(payload, list):
        raise CdpError("cdp_invalid", "target list is not a list")
    return [item for item in payload if isinstance(item, dict)]


def page_info(base: str, target_id: str, browser_id: str) -> dict | None:
    """The exact page, or None if that id is absent. Never substitutes another page."""
    if read_browser_id(base) != browser_id:
        raise CdpError("browser_replaced", "browser identity changed")
    return next((page for page in _pages(base) if page.get("type") == "page" and page.get("id") == target_id), None)


def close_exact(base: str, target_id: str, browser_id: str) -> str:
    """Close one registered page. Returns '' when verified gone, else an error.

    A browser-id mismatch does not call close. An id that is already absent is
    success. Any other page is ignored.
    """
    try:
        current = read_browser_id(base)
    except CdpError as exc:
        return f"{target_id} (browser identity unavailable)"
    if current != browser_id:
        return f"{target_id} (browser identity changed)"
    try:
        _read("GET", base + "/json/close/" + quote(target_id, safe=""))
    except CdpError:
        pass
    try:
        if page_info(base, target_id, browser_id) is None:
            return ""
    except CdpError as exc:
        return f"{target_id} ({exc.code})"
    return target_id


def page_ws(base: str, target_id: str, browser_id: str) -> tuple[str, str]:
    """Websocket and title for the exact page. Fails if the URL is not that page."""
    page = page_info(base, target_id, browser_id)
    if page is None:
        raise CdpError("target_closed", "registered target is not open")
    ws_url = str(page.get("webSocketDebuggerUrl") or "")
    endpoint, configured = urlsplit(ws_url), urlsplit(base)
    if (endpoint.scheme != "ws" or endpoint.hostname not in _LOOPBACK
            or endpoint.port != configured.port or endpoint.path != f"/devtools/page/{target_id}"
            or endpoint.query or endpoint.fragment or endpoint.username or endpoint.password):
        raise CdpError("target_invalid", "registered target debug address is invalid")
    return ws_url, str(page.get("title") or "")[:300]


class PageSocket:
    """Minimal loopback CDP websocket. The caller must already have validated ``ws_url``."""

    def __init__(self, ws_url: str, timeout: float = 3.0) -> None:
        import base64
        import secrets
        import socket

        parts = urlsplit(ws_url)
        if parts.scheme != "ws" or parts.hostname not in _LOOPBACK or parts.port is None:
            raise CdpError("target_invalid", "refusing a non-loopback page socket")
        self.sock = socket.create_connection((parts.hostname, parts.port), timeout=timeout)
        self.sock.settimeout(timeout)
        key = base64.b64encode(secrets.token_bytes(16)).decode()
        request = (
            f"GET {parts.path or '/'} HTTP/1.1\r\nHost: {parts.netloc}\r\nUpgrade: websocket\r\n"
            f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(request.encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise CdpError("cdp_handshake", "page socket closed during handshake")
            head += chunk
            if len(head) > 8192:
                raise CdpError("cdp_handshake", "page socket handshake is too large")
        if not head.startswith(b"HTTP/1.1 101"):
            raise CdpError("cdp_handshake", "page socket refused the CDP upgrade")
        self.buf = head.split(b"\r\n\r\n", 1)[1]
        self.next_id = 0

    def close(self) -> None:
        import contextlib

        with contextlib.suppress(OSError):
            self.sock.close()

    def _read(self, size: int) -> bytes:
        while len(self.buf) < size:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise CdpError("cdp_closed", "page socket closed")
            self.buf += chunk
        out, self.buf = self.buf[:size], self.buf[size:]
        return out

    def _send(self, opcode: int, payload: bytes) -> None:
        import secrets

        header = bytes([0x80 | opcode])
        size = len(payload)
        if size < 126:
            header += bytes([0x80 | size])
        elif size < 65536:
            header += bytes([0x80 | 126]) + size.to_bytes(2, "big")
        else:
            header += bytes([0x80 | 127]) + size.to_bytes(8, "big")
        mask = secrets.token_bytes(4)
        self.sock.sendall(header + mask + bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload)))

    def _recv(self) -> str:
        data = b""
        while True:
            first, second = self._read(2)
            opcode, size = first & 0x0F, second & 0x7F
            if size == 126:
                size = int.from_bytes(self._read(2), "big")
            elif size == 127:
                size = int.from_bytes(self._read(8), "big")
            if size > 4 * 1024 * 1024:
                raise CdpError("cdp_invalid", "CDP frame is too large")
            payload = self._read(size)
            if opcode == 0x8:
                raise CdpError("cdp_closed", "page socket closed")
            if opcode == 0x9:
                self._send(0xA, payload)
                continue
            if opcode in (0x0, 0x1):
                data += payload
                if first & 0x80:
                    return data.decode("utf-8", "replace")

    def call(self, method: str, params: dict | None = None) -> dict:
        self.next_id += 1
        call_id = self.next_id
        self._send(0x1, json.dumps({"id": call_id, "method": method, "params": params or {}}).encode())
        while True:
            message = json.loads(self._recv())
            if message.get("id") != call_id:
                continue
            if "error" in message:
                raise CdpError("cdp_error", f"browser rejected {method}")
            result = message.get("result") or {}
            if not isinstance(result, dict):
                raise CdpError("cdp_invalid", "CDP result is not an object")
            return result


def validate_cloud_ws(cdp_url: str) -> str:
    """A signed cloud websocket the backend already returned. Does not connect.

    Loopback and private hosts stay on the local CDP path. A static ``/json/version``
    discovery call is not a substitute for a websocket the provider already signed.
    """
    raw = (cdp_url or "").strip()
    parts = urlsplit(raw)
    if (parts.scheme != "wss" or not parts.hostname or parts.username or parts.password
            or len(raw) > 4000 or any(ch.isspace() for ch in raw)):
        raise CdpError("cdp_endpoint_invalid", "cloud endpoint is not a signed websocket")
    host = parts.hostname.lower().rstrip(".")
    if host in _LOOPBACK or host.endswith(".localhost") or host.endswith(".local"):
        raise CdpError("cdp_endpoint_invalid", "cloud endpoint must not be loopback")
    https = "https://" + parts.netloc + parts.path
    if parts.query:
        https += "?" + parts.query
    from tools.browser_url_policy import is_always_blocked_url, url_is_private
    if is_always_blocked_url(https) or url_is_private(https):
        raise CdpError("cdp_endpoint_invalid", "cloud endpoint failed the SSRF floor")
    return raw


def validate_cloud_browser_id(value: str) -> str:
    """``cloud:<provider session id>`` from the backend return. Not a scanned page."""
    from tools.browser_task_registry import valid_id
    if not isinstance(value, str) or not value.startswith("cloud:"):
        raise CdpError("browser_id_missing", "cloud browser identity was not in the backend return")
    try:
        valid_id(value[6:])
    except Exception as exc:
        raise CdpError("browser_id_missing", "cloud browser identity was not in the backend return") from exc
    return value


def capture_jpeg(ws_url: str) -> str:
    import base64
    import binascii

    socket = PageSocket(ws_url, timeout=3.0)
    try:
        frame = socket.call("Page.captureScreenshot", {
            "format": "jpeg", "quality": 65, "captureBeyondViewport": False, "fromSurface": True,
        })
    finally:
        socket.close()
    data = frame.get("data")
    if not isinstance(data, str) or len(data) > 3 * 1024 * 1024:
        raise CdpError("frame_invalid", "task frame is invalid")
    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise CdpError("frame_invalid", "task frame is invalid") from exc
    if not raw.startswith(b"\xff\xd8\xff") or not raw.endswith(b"\xff\xd9"):
        raise CdpError("frame_invalid", "task frame is not a jpeg")
    return data


def promote_provider_discovery(cdp_url: str) -> str:
    """Keep a provider HTTP discovery URL on the existing resolver. Does not add a protocol.

    Loopback stays on the local CDP path. A private or always-blocked host is refused
    before any request. A public HTTP URL may become the websocket ``resolve_cdp_override``
    already returns. Anything else is unresolved, not a visible-page fallback.
    """
    raw = (cdp_url or "").strip()
    if not raw or raw.startswith(("ws://", "wss://")):
        return raw
    parts = urlsplit(raw)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise CdpError("cdp_endpoint_invalid", "browser endpoint scheme is not CDP")
    host = parts.hostname.lower().rstrip(".")
    if host in _LOOPBACK:
        return raw
    from tools.browser_url_policy import is_always_blocked_url, url_is_private
    if is_always_blocked_url(raw) or url_is_private(raw):
        raise CdpError("endpoint_blocked", "provider discovery URL failed the existing SSRF floor")
    from tools.browser_session_cdp import resolve_cdp_override
    resolved = (resolve_cdp_override(raw) or "").strip()
    if resolved.startswith("wss://"):
        return validate_cloud_ws(resolved)
    if resolved.startswith(("ws://127.0.0.1:", "ws://localhost:", "http://127.0.0.1:", "http://localhost:")):
        return resolved
    raise CdpError("discovery_unresolved", "provider HTTP discovery did not yield a signed websocket; refusing a new protocol")


def _locator(directory: str, stem: str) -> tuple[str, str]:
    from pathlib import Path
    if not isinstance(directory, str) or not isinstance(stem, str):
        raise CdpError("daemon_locator_invalid", "daemon locator is missing")
    path = Path(directory)
    if not path.is_absolute() or ".." in path.parts or not stem.startswith("bu") or "/" in stem or "\\" in stem:
        raise CdpError("daemon_locator_invalid", "daemon locator is invalid")
    if not stem.replace("-", "").replace("_", "").isalnum():
        raise CdpError("daemon_locator_invalid", "daemon locator is invalid")
    return str(path), stem


def harness_exchange(directory: str, stem: str, payload: dict, timeout: float = 8.0) -> dict:
    """Raw daemon reply, including an error object. Does not open a CDP websocket."""
    import socket
    import sys
    from pathlib import Path

    directory, stem = _locator(directory, stem)
    token = None
    if sys.platform == "win32":
        port_file = Path(directory) / f"{stem}.port"
        try:
            ticket = json.loads(port_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CdpError("daemon_unavailable", "daemon port receipt is missing") from exc
        port = ticket.get("port") if isinstance(ticket, dict) else None
        token = ticket.get("token") if isinstance(ticket, dict) else None
        if type(port) is not int or not isinstance(token, str) or not token:
            raise CdpError("daemon_unavailable", "daemon port receipt is invalid")
        sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    else:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect(str(Path(directory) / f"{stem}.sock"))
        except OSError as exc:
            sock.close()
            raise CdpError("daemon_unavailable", "daemon socket is not connected") from exc
    try:
        from browser_harness import _ipc as ipc
        response = ipc.request(sock, token, payload)
    except Exception as exc:
        raise CdpError("daemon_unavailable", "daemon command failed") from exc
    finally:
        try:
            sock.close()
        except OSError:
            pass
    if not isinstance(response, dict):
        raise CdpError("daemon_invalid", "daemon response is not an object")
    return response


def harness_request(directory: str, stem: str, payload: dict, timeout: float = 8.0) -> dict:
    """One command on the browser-harness daemon socket. Does not open a CDP websocket."""
    response = harness_exchange(directory, stem, payload, timeout=timeout)
    error = response.get("error")
    if error:
        if error in _DAEMON_GRANT_ERRORS:
            raise CdpError(error, _DAEMON_GRANT_ERRORS[error])
        raise CdpError("daemon_error", "daemon rejected the command")
    return response


_DAEMON_GRANT_ERRORS = {
    "task_grant_required": "daemon task grant is required",
    "task_grant_mismatch": "daemon task grant does not match",
    "daemon_grant_unsupported": "daemon does not support task grants",
    "dialog_unscoped": "daemon dialog has no target, session, and frame",
    "dialog_unowned": "daemon dialog is not owned by this task",
    "browser_close_refused": "browser close was refused",
}


def _grant_ok(value: object) -> bool:
    """Same store rule as the registry. Does not generate a token."""
    if not isinstance(value, str) or not 16 <= len(value) <= 128 or not value.isascii():
        return False
    return all(ch.isalnum() or ch in "-_" for ch in value)


def _trusted_grant(target_id: str, expected_pid: int) -> str:
    from tools.browser_task_registry import RegistryError, harness_task_grant

    try:
        grant = harness_task_grant(target_id, expected_pid)
    except RegistryError as exc:
        if exc.code == "browser_replaced":
            raise CdpError("browser_replaced", "daemon identity changed") from None
        raise CdpError("task_grant_required", "daemon task grant is not registered for this target") from None
    if not _grant_ok(grant):
        raise CdpError("task_grant_required", "daemon task grant is not registered for this target")
    return grant


def _require_pid(directory: str, stem: str, expected_pid: int) -> int:
    if expected_pid and (type(expected_pid) is not int or not 0 < expected_pid < (1 << 31)):
        raise CdpError("browser_replaced", "daemon identity changed")
    report = harness_ping(directory, stem)
    if expected_pid and report["pid"] != expected_pid:
        raise CdpError("browser_replaced", "daemon identity changed")
    return expected_pid or report["pid"]


def ensure_harness_task_grants(directory: str, stem: str, expected_pid: int) -> None:
    """Refuse task-page creation unless this daemon reports the frozen capability.

    Vendor owns the ``meta=capabilities`` response. This client only accepts
    top-level ``task_grants``, ``dialog_identity``, and ``task_grant_version``.
    """
    if type(expected_pid) is not int or not 0 < expected_pid < (1 << 31):
        raise CdpError("browser_replaced", "daemon identity changed")
    report = harness_ping(directory, stem)
    if report["pid"] != expected_pid:
        raise CdpError("browser_replaced", "daemon identity changed")
    response = harness_exchange(directory, stem, {"meta": "capabilities"})
    version = response.get("task_grant_version") if isinstance(response, dict) else None
    if (not isinstance(response, dict) or response.get("error")
            or response.get("task_grants") is not True or response.get("dialog_identity") is not True
            or type(version) is not int or version != 1):
        raise CdpError("daemon_grant_unsupported", "daemon does not support task grants")


def _close_just_created(directory: str, stem: str, target_id: str) -> str:
    """Close only the target this create call just returned. No grant, no other page."""
    try:
        closed = harness_exchange(directory, stem, {
            "method": "Target.closeTarget",
            "params": {"targetId": target_id},
        })
    except CdpError:
        return "close rejected"
    result = closed.get("result") if isinstance(closed, dict) else None
    if not isinstance(closed, dict) or closed.get("error") or (isinstance(result, dict) and result.get("success") is False):
        return "close rejected"
    return ""


def create_harness_target(directory: str, stem: str, expected_pid: int, url: str = "about:blank") -> tuple[str, str]:
    """Create one task page and return ``(targetId, task_grant)``.

    Capability is checked before ``Target.createTarget``. The grant is the
    sibling of ``result``, never a client token and never a field inside ``result``.
    A create that returns no grant closes only that target and is unauthorized.
    """
    ensure_harness_task_grants(directory, stem, expected_pid)
    if not isinstance(url, str) or not url or any(ch.isspace() for ch in url):
        url = "about:blank"
    response = harness_exchange(directory, stem, {
        "method": "Target.createTarget",
        "params": {"url": url},
    })
    result = response.get("result") if isinstance(response.get("result"), dict) else {}
    target_id = result.get("targetId")
    if not isinstance(target_id, str) or not target_id or response.get("error"):
        if isinstance(target_id, str) and target_id:
            note = _close_just_created(directory, stem, target_id)
            message = "daemon did not create an authorized target"
            if note:
                message = f"{message}; {note}"
            raise CdpError("target_create_failed", message)
        raise CdpError("target_create_failed", "daemon did not create a target")
    if not _grant_ok(response.get("task_grant")):
        note = _close_just_created(directory, stem, target_id)
        message = "daemon did not authorize the created target"
        if note:
            message = f"{message}; {note}"
        raise CdpError("task_grant_required", message)
    return target_id, response["task_grant"]


def harness_pending_dialog(directory: str, stem: str, expected_pid: int) -> dict | None:
    """Daemon ``pending_dialog`` object, or None. Does not invent target, session, or frame."""
    ensure_harness_task_grants(directory, stem, expected_pid)
    response = harness_exchange(directory, stem, {"meta": "pending_dialog"})
    if not isinstance(response, dict):
        raise CdpError("daemon_invalid", "daemon response is not an object")
    error = response.get("error")
    if error:
        if error in _DAEMON_GRANT_ERRORS:
            raise CdpError(error, _DAEMON_GRANT_ERRORS[error])
        raise CdpError("daemon_error", "daemon rejected the command")
    dialog = response.get("dialog")
    if dialog is None:
        return None
    if not isinstance(dialog, dict):
        return None
    cleaned = {key: value for key, value in dialog.items() if key != "task_grant"}
    named = cleaned.get("targetId")
    if isinstance(named, str) and named:
        _trusted_grant(named, expected_pid)
    return cleaned


def harness_ping(directory: str, stem: str) -> dict:
    """Daemon self-report: pid and browser_kind. Not a page list and not an environment dump."""
    response = harness_request(directory, stem, {"meta": "ping"}, timeout=2.0)
    pid = response.get("pid")
    kind = response.get("browser_kind")
    if response.get("pong") is not True or type(pid) is not int or not 0 < pid < (1 << 31):
        raise CdpError("daemon_invalid", "daemon ping was not a connection receipt")
    if kind not in {"cloud", "cdp", "local"}:
        raise CdpError("daemon_invalid", "daemon did not report a connection kind")
    return {"pid": pid, "browser_kind": kind}


def _unwrap_panel_call(target_id, method, params, timeout):
    """Accept ``harness_call(*_harness_of(task), target_id, method, params)``.

    That splat is ``(directory, stem, pid, target_id, method, params)``. The
    declared order stays ``(directory, stem, target_id, method, params)``.
    """
    if type(target_id) is int and isinstance(method, str) and isinstance(params, str) and isinstance(timeout, dict):
        return method, params, timeout, 8.0, target_id
    return target_id, method, params, timeout, 0


def _harness_session(directory: str, stem: str, target_id: str, expected_pid: int, grant: str) -> str:
    if not isinstance(target_id, str) or not target_id:
        raise CdpError("target_invalid", "target identity is missing")
    if type(expected_pid) is not int or not 0 < expected_pid < (1 << 31):
        raise CdpError("browser_replaced", "daemon identity changed")
    if not _grant_ok(grant):
        raise CdpError("task_grant_required", "daemon task grant is not registered for this target")
    _require_pid(directory, stem, expected_pid)
    attached = harness_request(directory, stem, {
        "method": "Target.attachToTarget",
        "params": {"targetId": target_id, "flatten": True},
        "task_grant": grant,
    })
    session_id = (attached.get("result") or {}).get("sessionId")
    if not isinstance(session_id, str) or not session_id:
        raise CdpError("target_invalid", "daemon did not attach the registered target")
    return session_id


def harness_page_eval(directory: str, stem: str, target_id: str, expression: str, expected_pid: int,
                      timeout: float = 10.0) -> dict:
    """``Runtime.evaluate`` on one registered target. Does not open a CDP websocket.

    The daemon socket does not carry task ownership. Callers must already have
    matched this target to the task in the registry. A pid mismatch is a
    replaced daemon, not a cue to connect elsewhere.
    """
    report = harness_ping(directory, stem)
    if report["pid"] != expected_pid:
        raise CdpError("browser_replaced", "daemon identity changed")
    return harness_call(directory, stem, target_id, "Runtime.evaluate", {
        "expression": expression, "returnByValue": True, "awaitPromise": True, "userGesture": True,
    }, timeout=timeout, expected_pid=expected_pid)


def harness_call(directory: str, stem: str, target_id: str, method: str, params: dict | None = None,
                 timeout: float = 8.0, *, expected_pid: int = 0, session_id: str | None = None) -> dict:
    """Page CDP for one created target, through the daemon that already holds the socket.

    The registry grant is attached beside ``method``, not inside ``params``.
    ``session_id`` is used only when the caller already has the daemon's id;
    an empty explicit session is refused instead of attaching a different one.
    """
    target_id, method, params, timeout, splat_pid = _unwrap_panel_call(target_id, method, params, timeout)
    if splat_pid:
        expected_pid = splat_pid
    if method == "Target.createTarget":
        raise CdpError("task_grant_required", "Target.createTarget must use create_harness_target")
    if not isinstance(target_id, str) or not target_id or not isinstance(method, str) or not method:
        raise CdpError("target_invalid", "target identity is missing")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise CdpError("daemon_invalid", "daemon command is missing")
    pid = _require_pid(directory, stem, expected_pid)
    grant = _trusted_grant(target_id, pid)
    if session_id is not None:
        if not isinstance(session_id, str) or not session_id:
            raise CdpError("target_invalid", "session identity is missing")
        bound_session = session_id
    elif method.startswith("Target."):
        bound_session = ""
    else:
        bound_session = _harness_session(directory, stem, target_id, pid, grant)
    payload: dict = {"method": method, "params": params, "task_grant": grant}
    if bound_session:
        payload["session_id"] = bound_session
    result = harness_request(directory, stem, payload, timeout=timeout).get("result") or {}
    if not isinstance(result, dict):
        raise CdpError("cdp_invalid", "daemon CDP result is not an object")
    return result


def capture_harness_jpeg(directory: str, stem: str, target_id: str, expected_pid: int) -> tuple[str, str]:
    """JPEG and title for a registered target. Refuses a replaced daemon."""
    import base64
    import binascii

    if type(target_id) is int and isinstance(expected_pid, str):
        target_id, expected_pid = expected_pid, target_id
    if not isinstance(target_id, str) or type(expected_pid) is not int:
        raise CdpError("target_invalid", "target identity is missing")
    report = harness_ping(directory, stem)
    if report["pid"] != expected_pid:
        raise CdpError("browser_replaced", "daemon identity changed")
    frame = harness_call(directory, stem, target_id, "Page.captureScreenshot", {
        "format": "jpeg", "quality": 65, "captureBeyondViewport": False, "fromSurface": True,
    }, timeout=15.0, expected_pid=expected_pid)
    title_msg = harness_call(directory, stem, target_id, "Runtime.evaluate", {
        "expression": "document.title", "returnByValue": True,
    }, expected_pid=expected_pid)
    data = frame.get("data")
    title = ((title_msg.get("result") or {}).get("value") or "")
    if not isinstance(data, str) or len(data) > 3 * 1024 * 1024:
        raise CdpError("frame_invalid", "task frame is invalid")
    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise CdpError("frame_invalid", "task frame is invalid") from exc
    if not raw.startswith(b"\xff\xd8\xff") or not raw.endswith(b"\xff\xd9"):
        raise CdpError("frame_invalid", "task frame is not a jpeg")
    return data, str(title)[:300]


def close_harness_target(directory: str, stem: str, target_id: str, expected_pid: int) -> str:
    """Close one registered target on the daemon socket. Does not list pages."""
    try:
        report = harness_ping(directory, stem)
    except CdpError:
        return f"{target_id} (daemon unavailable)"
    if type(expected_pid) is not int or report["pid"] != expected_pid:
        return f"{target_id} (daemon identity changed)"
    try:
        grant = _trusted_grant(target_id, expected_pid)
    except CdpError:
        return f"{target_id} (close rejected)"
    try:
        closed = harness_exchange(directory, stem, {
            "method": "Target.closeTarget",
            "params": {"targetId": target_id},
            "task_grant": grant,
        })
        if closed.get("error") or (closed.get("result") or {}).get("success") is False:
            return f"{target_id} (close rejected)"
        # The patched daemon treats an ungranted getTargetInfo on a task target
        # as task_grant_required before CDP. That refusal is not proof the page
        # is gone. Send the same grant, and do not treat a grant error as closed.
        info = harness_exchange(directory, stem, {
            "method": "Target.getTargetInfo",
            "params": {"targetId": target_id},
            "task_grant": grant,
        })
    except CdpError as exc:
        return f"{target_id} ({exc.code})"
    error = info.get("error") if isinstance(info, dict) else "daemon_invalid"
    if error in {"task_grant_required", "task_grant_mismatch", "browser_close_refused"}:
        return f"{target_id} (close rejected)"
    if error:
        return ""
    return target_id
