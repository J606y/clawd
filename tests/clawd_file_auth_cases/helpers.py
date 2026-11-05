"""Runtime helpers for clawd file and auth boundary cases.

Directories are created only when a test calls these functions. They never
resolve or open /root/.hermes, the official install, or the old panel data
roots. Import errors are failures, not skips.
"""

from __future__ import annotations

import copy
import io
import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

BANNED_PREFIXES = (
    "/root/.hermes",
    "/usr/local/lib/hermes-agent",
    "/usr/local/bin/hermes",
    "/root/hermes-uploads",
    "/root/hermes-workspace",
)
_ENV_KEYS = (
    "CLAWD_HOME",
    "HERMES_HOME",
    "HERMES_SRC",
    "HERMES_INSTALL",
    "HERMES_PY",
    "HERMES_BIN",
    "PANEL_LIVE_TASK_DB",
    "PANEL_LIVE_CDP",
    "PANEL_API_KEY_FILE",
    "PANEL_API_SERVER",
    "PANEL_SIDECAR_KEY_FILE",
)
_RESTORE_ATTRS = (
    "HERMES_HOME",
    "HERMES_BIN",
    "HERMES_SRC",
    "HERMES_INSTALL",
    "HERMES_PY",
    "LOG_DIR",
    "KEY_FILE",
    "LIVE_TASK_DB",
    "CONTINUE_KEY_FILE",
    "CONTINUE_API",
    "FILE_ROOTS",
    "DENY_PREFIXES",
    "LOG_JOURNAL",
    "SERVICES",
    "RESTARTABLE",
    "_service_states",
    "restart_service",
    "start_update",
    "get_update_plan",
    "get_update_status",
    "get_update_info",
    "_continue_watcher",
    "_continue_tick",
    "main",
    "yaml",
    "config_set",
    "_disabled_skills",
    "set_skill_enabled",
    "get_message_origins",
    "_clawd_native",
    "_clawd_saved_continue_watcher",
    "_clawd_saved_continue_tick",
    "_clawd_original_main",
    "_clawd_browser_native",
)


def refuse_banned(path: Path) -> None:
    text = str(path)
    for banned in BANNED_PREFIXES:
        if text == banned or text.startswith(banned + os.sep):
            raise AssertionError(f"refusing banned path {text}")


def assert_under(path: Path, root: Path) -> None:
    refuse_banned(path)
    resolved = path.resolve()
    base = root.resolve()
    refuse_banned(resolved)
    if resolved != base and base not in resolved.parents:
        raise AssertionError(f"{resolved} escaped {base}")


def assert_payload_absent(root: Path, payload: bytes) -> None:
    if not payload or not root.exists():
        return
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        kept = []
        for name in dirnames:
            child = Path(dirpath) / name
            if not child.is_symlink():
                kept.append(name)
        dirnames[:] = kept
        for name in filenames:
            path = Path(dirpath) / name
            if path.is_symlink() or not path.is_file():
                continue
            if payload in path.read_bytes():
                raise AssertionError(f"payload leaked to {path}")


def require_server():
    try:
        from clawd_server.server import PanelServer
    except ImportError as exc:
        raise AssertionError(
            f"blocker: cannot import clawd_server.server ({exc}); not skipped"
        ) from exc
    return PanelServer


def panel_request(
    method: str,
    path: str,
    headers: dict[str, str],
    *,
    scheme: str = "http",
    peer: str | None = None,
    match_info: dict[str, str] | None = None,
    body: bytes = b"",
):
    try:
        from aiohttp.test_utils import make_mocked_request
    except ImportError as exc:
        raise AssertionError(f"blocker: aiohttp.test_utils missing ({exc}); not skipped") from exc
    kwargs: dict[str, Any] = {"headers": headers, "payload": body}
    if scheme == "https":
        kwargs["sslcontext"] = object()
    if match_info is not None:
        kwargs["match_info"] = match_info
    try:
        request = make_mocked_request(method, path, **kwargs)
    except TypeError as exc:
        raise AssertionError(
            f"blocker: cannot build aiohttp request for scheme={scheme} ({exc}); not skipped"
        ) from exc
    if peer is not None:
        _force_peer(request, peer)
        from clawd_server.server import _peer

        seen = _peer(request)
        if seen != peer:
            raise AssertionError(
                f"blocker: request peer is {seen!r}, need {peer!r}; not skipped"
            )
    if request.scheme != scheme:
        raise AssertionError(
            f"blocker: request.scheme is {request.scheme!r}, need {scheme!r}; not skipped"
        )
    return request


def _force_peer(request, peer: str) -> None:
    transport = request.transport
    sslcontext = None
    if transport is not None and hasattr(transport, "get_extra_info"):
        try:
            sslcontext = transport.get_extra_info("sslcontext")
        except Exception:
            sslcontext = None

    def get_extra_info(key, default=None):
        if key == "peername":
            return (peer, 0)
        if key == "sslcontext":
            return sslcontext
        return default

    if transport is not None:
        transport.get_extra_info = get_extra_info
    for name, value in (("_remote", peer), ("_protocol_peername", (peer, 0))):
        try:
            setattr(request, name, value)
        except Exception:
            continue


def cookie_header(response) -> str:
    raw = response.headers.get("Set-Cookie", "")
    assert raw, "missing Set-Cookie"
    return raw


def make_handler(mod, *, key: str = "k", path: str = "/", headers: dict | None = None, body: bytes = b""):
    handler = object.__new__(mod.Handler)
    handler.key = key
    handler.path = path
    handler.headers = headers or {}
    handler.rfile = io.BytesIO(body)
    handler.wfile = io.BytesIO()
    handler.close_connection = False
    handler.request_version = "HTTP/1.1"
    handler.protocol_version = "HTTP/1.1"
    handler.requestline = f"GET {path} HTTP/1.1"
    return handler


def parse_http(raw: bytes) -> tuple[int, dict[str, str], bytes]:
    head, _, body = raw.partition(b"\r\n\r\n")
    if not head:
        raise AssertionError(f"handler wrote no HTTP response: {raw!r}")
    lines = head.split(b"\r\n")
    status = int(lines[0].split()[1])
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if b":" in line:
            name, value = line.split(b":", 1)
            headers[name.decode().lower()] = value.strip().decode()
    return status, headers, body


def dispatch(mod, method: str, path: str, *, key: str = "k", body: bytes = b"", headers: dict | None = None):
    merged = {"X-Panel-Key": key, "Content-Length": str(len(body))}
    if headers:
        merged.update(headers)
    handler = make_handler(mod, key=key, path=path, headers=merged, body=body)
    handler._dispatch(method)
    return parse_http(handler.wfile.getvalue())


def upload(mod, payload: bytes, name: str, *, root: str = "uploads", directory: str = ""):
    handler = make_handler(mod, headers={"Content-Length": str(len(payload))}, body=payload)
    query = {"root": root, "name": name}
    if directory:
        query["dir"] = directory
    return handler._upload(query)


def read_artifact(mod, absolute: str) -> bytes:
    handler = make_handler(mod)
    handler._artifact(absolute, False)
    _status, _headers, body = parse_http(handler.wfile.getvalue())
    return body


def error_code(body: bytes) -> str:
    payload = json.loads(body.decode())
    return payload["error"]["code"]


def _snapshot(mod) -> dict[str, Any]:
    saved: dict[str, Any] = {}
    for name in _RESTORE_ATTRS:
        if not hasattr(mod, name):
            continue
        value = getattr(mod, name)
        try:
            saved[name] = copy.deepcopy(value)
        except Exception:
            saved[name] = value
    saved["FILE_ROOTS"] = copy.deepcopy(dict(mod.FILE_ROOTS))
    return saved


def _restore(mod, saved: dict[str, Any]) -> None:
    for name, value in saved.items():
        setattr(mod, name, value)
    if "FILE_ROOTS" in saved:
        mod.FILE_ROOTS.clear()
        mod.FILE_ROOTS.update(saved["FILE_ROOTS"])


def _restore_env(saved: dict[str, str | None]) -> None:
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def assert_bound(mod, home: Path) -> None:
    root = home.resolve()
    refuse_banned(root)
    for spec in mod.FILE_ROOTS.values():
        path = Path(spec["path"])
        refuse_banned(path)
        resolved = path.resolve()
        refuse_banned(resolved)
        if resolved != root and root not in resolved.parents:
            raise AssertionError(f"file root {resolved} is outside {root}")
        if "/root/.hermes" in str(resolved) or "/usr/local/lib/hermes-agent" in str(resolved):
            raise AssertionError(f"file root points at production: {resolved}")


@contextmanager
def isolated_sidecar(tmp_path: Path, *, home: Path | None = None, bind_now: bool = True) -> Iterator[tuple[Path, Any]]:
    chosen = home if home is not None else tmp_path / "clawd-home"
    if not chosen.exists():
        chosen.mkdir(mode=0o700)
    refuse_banned(chosen)
    saved_env = {key: os.environ.get(key) for key in _ENV_KEYS}
    from clawd_server.bind import bind, clawd_command, load_sidecar, prepare_env

    prepare_env(chosen, "http://127.0.0.1:9")
    mod = load_sidecar()
    saved = _snapshot(mod)
    try:
        if bind_now:
            bind(mod, chosen, clawd_command(chosen))
            assert_bound(mod, chosen)
        yield chosen, mod
    finally:
        _restore(mod, saved)
        _restore_env(saved_env)
