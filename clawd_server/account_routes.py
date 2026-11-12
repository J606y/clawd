"""Change the signed-in panel account. Does not define a new login protocol.

The presented ``hp_auth`` cookie is the digest from ``panel_auth.credential_hash``.
The signed-in row is the unique store row whose hash matches that cookie; the
request username is only the desired new name.

The store is read and written with ``json.loads`` / ``json.dumps``. Other
accounts and unknown fields are kept by value. Whitespace and the literal
bytes of auth.json are not preserved.

The parent directory and ``auth.json.lock`` are both locked with non-blocking
``flock``, and every file operation uses that directory fd. ``auth.add_user``
does not take either lock. The pre-replace fingerprint check only narrows
that race; it is not an atomic compare-and-swap against the old CLI.

Replacing the digest makes this cookie fail the next hash check. It does not
close existing WebSockets, and choosing the same password again can recreate
a digest.

Cookie deletion calls ``panel_auth.request_is_https`` once and
``response.del_cookie`` once. This module does not read forwarding headers
and does not emit a second Secure or non-Secure cookie.
"""

from __future__ import annotations

import errno
import fcntl
import hashlib
import hmac
import json
import logging
import os
import secrets
import stat
import threading
import time
import unicodedata
from collections import deque
from pathlib import Path
from typing import Any

from aiohttp import web

from clawd_server import auth as panel_auth

log = logging.getLogger("clawd.panel.account")

_MAX_BODY = 16 * 1024
_MAX_STORE = 256 * 1024
_MAX_DEPTH = 32
_RATE_LIMIT = 5
_RATE_WINDOW = 60.0
_RATE_MAX_KEYS = 512
_FIELDS = frozenset({"username", "currentPassword", "newPassword"})
_LOCK = threading.Lock()
_FAILS: dict[str, deque[float]] = {}
_RATE_SECRET = secrets.token_bytes(32)
_OPEN_AT = os.O_CLOEXEC | os.O_NOFOLLOW


class _Fail(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _Busy(_Fail):
    pass


def _error(status: int, code: str, message: str, **headers: str) -> web.Response:
    response = web.json_response(
        {"error": {"code": code, "message": message}},
        status=status,
        headers={"Cache-Control": "no-store"},
    )
    for key, value in headers.items():
        response.headers[key.replace("_", "-")] = value
    return response


def _peer(request: web.Request) -> str | None:
    if request.remote:
        return request.remote
    transport = request.transport
    if transport is None:
        return None
    try:
        peer = transport.get_extra_info("peername")
    except Exception:
        return None
    if not peer or not isinstance(peer[0], str):
        return None
    return peer[0]


def _gate(owner: Any, request: web.Request) -> web.Response | None:
    checker = getattr(owner, "_authed", None)
    if not callable(checker):
        log.error("account auth gate missing")
        return _error(401, "unauthorized", "未授权")
    try:
        denied = checker(request)
    except Exception as exc:
        log.error("account auth gate failed: %s", type(exc).__name__)
        return _error(401, "unauthorized", "未授权")
    if denied is not None:
        if isinstance(denied, web.StreamResponse):
            try:
                denied.headers["Cache-Control"] = "no-store"
            except Exception:
                log.error("account auth denial could not be marked no-store")
            return denied
        log.error("account auth gate returned %s", type(denied).__name__)
        return _error(401, "unauthorized", "未授权")
    try:
        allowed = panel_auth.origin_allowed(request.method, request.headers, _peer(request))
    except Exception as exc:
        log.error("account origin check failed: %s", type(exc).__name__)
        return _error(403, "origin_rejected", "来源不被允许")
    if allowed is not True:
        return _error(403, "origin_rejected", "来源不被允许")
    return None


def _presented(request: web.Request) -> str | None:
    try:
        value = panel_auth.cookie_value(request.headers.get("Cookie"))
    except Exception:
        return None
    if not isinstance(value, str) or panel_auth.HASH_RE.fullmatch(value) is None:
        return None
    return value


def _content_type_ok(request: web.Request) -> bool:
    try:
        values = request.headers.getall("Content-Type", [])
    except Exception:
        return False
    if len(values) != 1 or not isinstance(values[0], str):
        return False
    value = values[0]
    if len(value) > 200 or any(ord(char) < 32 or ord(char) == 127 for char in value):
        return False
    parts = [part.strip() for part in value.split(";")]
    if not parts or parts[0].lower() != "application/json":
        return False
    if len(parts) == 1:
        return True
    if len(parts) != 2:
        return False
    key, sep, raw = parts[1].partition("=")
    if not sep or key.strip().lower() != "charset":
        return False
    return raw.strip().strip('"').strip("'").lower() in {"utf-8", "utf8"}


async def _read_body(request: web.Request) -> bytes:
    """Business cap only. aiohttp owns Content-Length and chunked framing."""
    if request.content_length is not None and request.content_length > _MAX_BODY:
        raise _Fail("size")
    data = bytearray()
    try:
        while True:
            chunk = await request.content.read(min(8192, _MAX_BODY + 1 - len(data)))
            if not isinstance(chunk, (bytes, bytearray)):
                raise _Fail("body")
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > _MAX_BODY:
                raise _Fail("size")
    except _Fail:
        raise
    except Exception as exc:
        log.error("account body read failed: %s", type(exc).__name__)
        raise _Fail("body") from None
    return bytes(data)


def _no_dupes(pairs: list[tuple[Any, Any]]) -> dict[str, Any]:
    obj: dict[str, Any] = {}
    for key, value in pairs:
        if not isinstance(key, str) or key in obj:
            raise ValueError("duplicate")
        obj[key] = value
    return obj


def _lone_surrogate(value: str) -> bool:
    return any(0xD800 <= ord(char) <= 0xDFFF for char in value)


def _bounded(value: Any, depth: int = 0) -> None:
    if depth > _MAX_DEPTH:
        raise _Fail("parse")
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or _lone_surrogate(key):
                raise _Fail("parse")
            _bounded(item, depth + 1)
        return
    if isinstance(value, list):
        for item in value:
            _bounded(item, depth + 1)
        return
    if isinstance(value, str):
        if _lone_surrogate(value):
            raise _Fail("parse")
        return
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, float) and value == value and value not in (float("inf"), float("-inf")):
        return
    raise _Fail("parse")


def _loads(data: bytes) -> Any:
    if len(data) > _MAX_STORE or data.startswith((b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff")):
        raise _Fail("parse")
    try:
        parsed = json.loads(data.decode("utf-8"), object_pairs_hook=_no_dupes)
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError, RecursionError):
        raise _Fail("parse") from None
    _bounded(parsed)
    return parsed


def _username(value: Any) -> str:
    if not isinstance(value, str) or _lone_surrogate(value):
        raise _Fail("username")
    name = value.strip().lower()
    if not 1 <= len(name) <= 64 or _lone_surrogate(name):
        raise _Fail("username")
    for char in name:
        if char.isspace() or ord(char) == 127 or unicodedata.category(char) == "Cc":
            raise _Fail("username")
    return name


def _passwords(current: Any, new: Any) -> tuple[str, str]:
    if (
        not isinstance(current, str)
        or not isinstance(new, str)
        or _lone_surrogate(current)
        or _lone_surrogate(new)
        or "\x00" in new
        or not 1 <= len(current) <= 1024
        or len(new) > 1024
        or (new and not 12 <= len(new) <= 1024)
    ):
        raise _Fail("password")
    return current, new


def _body(payload: bytes) -> tuple[str, str, str]:
    if len(payload) > _MAX_BODY:
        raise _Fail("size")
    try:
        parsed = _loads(payload)
    except _Fail:
        raise _Fail("json") from None
    if not isinstance(parsed, dict) or set(parsed) != _FIELDS:
        raise _Fail("fields")
    return (
        _username(parsed["username"]),
        *_passwords(parsed["currentPassword"], parsed["newPassword"]),
    )


def _rate_key(cookie: str) -> str:
    return hmac.new(_RATE_SECRET, cookie.encode("ascii"), hashlib.sha256).hexdigest()


def _prune(now: float) -> None:
    stale = [key for key, stamps in _FAILS.items() if not stamps or now - stamps[-1] >= _RATE_WINDOW]
    for key in stale:
        _FAILS.pop(key, None)


def _retry_after(stamps: deque[float], now: float) -> int:
    if not stamps:
        return 1
    return max(1, int(_RATE_WINDOW - (now - stamps[0]) + 0.999))


def _rate_blocked(cookie: str) -> int | None:
    now = time.monotonic()
    key = _rate_key(cookie)
    with _LOCK:
        _prune(now)
        stamps = _FAILS.get(key)
        if stamps is None and len(_FAILS) >= _RATE_MAX_KEYS:
            return 60
        if not stamps:
            return None
        while stamps and now - stamps[0] >= _RATE_WINDOW:
            stamps.popleft()
        if not stamps:
            _FAILS.pop(key, None)
            return None
        if len(stamps) >= _RATE_LIMIT:
            return _retry_after(stamps, now)
        return None


def _rate_note(cookie: str) -> None:
    now = time.monotonic()
    key = _rate_key(cookie)
    with _LOCK:
        _prune(now)
        if key not in _FAILS and len(_FAILS) >= _RATE_MAX_KEYS:
            return
        stamps = _FAILS.setdefault(key, deque())
        while stamps and now - stamps[0] >= _RATE_WINDOW:
            stamps.popleft()
        if len(stamps) < _RATE_LIMIT:
            stamps.append(now)


def _rate_clear(cookie: str) -> None:
    with _LOCK:
        _FAILS.pop(_rate_key(cookie), None)


def _reject_hermes(path: Path) -> None:
    text = str(path)
    if text == "/root/.hermes" or text.startswith("/root/.hermes/"):
        raise _Fail("home")


def _store_path(owner: Any) -> Path:
    home = owner.home
    raw = home if isinstance(home, str) else str(home)
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        raise _Fail("home")
    if isinstance(home, str):
        home = Path(home)
    if not isinstance(home, Path) or not home.is_absolute() or any(part == ".." for part in home.parts):
        raise _Fail("home")
    _reject_hermes(home)
    found = panel_auth.auth_path(home)
    if not isinstance(found, Path):
        found = Path(str(found))
    prefix = str(home).rstrip("/") + "/"
    if (
        not found.is_absolute()
        or found.name != "auth.json"
        or any(part == ".." for part in found.parts)
        or not str(found).startswith(prefix)
    ):
        raise _Fail("home")
    _reject_hermes(found)
    return found


def _leaf(name: str) -> str:
    if not name or name in {".", ".."} or "/" in name or "\x00" in name:
        raise _Fail("io")
    return name


def _close_fd(fd: int | None, label: str) -> None:
    if fd is None:
        return
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            log.error("account %s unlock failed", label)
    finally:
        try:
            os.close(fd)
        except OSError:
            log.error("account %s close failed", label)


def _open_at(dir_fd: int, name: str, flags: int, mode: int = 0o600) -> int:
    try:
        return os.open(_leaf(name), flags | _OPEN_AT, mode, dir_fd=dir_fd)
    except OSError as exc:
        raise _Fail("symlink" if exc.errno == errno.ELOOP else "io") from None


def _fingerprint(info: os.stat_result) -> tuple[int, int, int, int]:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


def _read_named(dir_fd: int, name: str) -> tuple[bytes, tuple[int, int, int, int]]:
    try:
        info = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except OSError as exc:
        raise _Fail("missing" if exc.errno == errno.ENOENT else "io") from None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_STORE:
        raise _Fail("symlink" if not stat.S_ISREG(info.st_mode) else "size")
    fd = _open_at(dir_fd, name, os.O_RDONLY | os.O_NONBLOCK)
    try:
        current = os.fstat(fd)
        if (
            not stat.S_ISREG(current.st_mode)
            or (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino)
            or current.st_size > _MAX_STORE
        ):
            raise _Fail("changed")
        data = os.read(fd, _MAX_STORE + 1)
    except _Fail:
        raise
    except OSError:
        raise _Fail("io") from None
    finally:
        try:
            os.close(fd)
        except OSError:
            log.error("account read close failed")
    if len(data) > _MAX_STORE or len(data) != current.st_size:
        raise _Fail("size" if len(data) > _MAX_STORE else "changed")
    return data, _fingerprint(current)


def _open_lock(dir_fd: int) -> int:
    fd = _open_at(dir_fd, "auth.json.lock", os.O_RDWR | os.O_CREAT | os.O_NONBLOCK)
    try:
        os.fchmod(fd, 0o600)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise _Fail("symlink")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise _Busy("busy") from None
    except OSError as exc:
        os.close(fd)
        if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
            raise _Busy("busy") from None
        raise _Fail("lock") from None
    except _Fail:
        os.close(fd)
        raise
    return fd


class _Locked:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.dir_fd: int | None = None
        self.lock_fd: int | None = None

    def __enter__(self) -> "_Locked":
        parent = self.path.parent
        try:
            info = os.lstat(parent)
        except OSError as exc:
            raise _Fail("missing" if exc.errno == errno.ENOENT else "io") from None
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise _Fail("symlink")
        try:
            dir_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | _OPEN_AT)
        except OSError as exc:
            raise _Fail("symlink" if exc.errno == errno.ELOOP else "io") from None
        self.dir_fd = dir_fd
        try:
            current = os.fstat(dir_fd)
            if not stat.S_ISDIR(current.st_mode) or (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                raise _Fail("symlink")
            try:
                fcntl.flock(dir_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise _Busy("busy") from None
            except OSError as exc:
                if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                    raise _Busy("busy") from None
                raise _Fail("lock") from None
            self.lock_fd = _open_lock(dir_fd)
        except Exception:
            self._release()
            raise
        return self

    def __exit__(self, *args: object) -> None:
        self._release()

    def _release(self) -> None:
        lock_fd = self.lock_fd
        dir_fd = self.dir_fd
        try:
            _close_fd(lock_fd, "lock")
        finally:
            self.lock_fd = None
            try:
                _close_fd(dir_fd, "dir")
            finally:
                self.dir_fd = None


def _rows(users: list[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen_user: set[str] = set()
    seen_norm: set[str] = set()
    seen_hash: set[str] = set()
    for item in users:
        if not isinstance(item, dict):
            raise _Fail("parse")
        user = item.get("user")
        digest = item.get("hash")
        if not isinstance(user, str) or not isinstance(digest, str) or not user or _lone_surrogate(user):
            raise _Fail("parse")
        if panel_auth.HASH_RE.fullmatch(digest) is None:
            raise _Fail("parse")
        norm = user.strip().lower()
        if not norm or user in seen_user or norm in seen_norm or digest in seen_hash:
            raise _Fail("ambiguous")
        seen_user.add(user)
        seen_norm.add(norm)
        seen_hash.add(digest)
        rows.append(item)
    return rows


def _load_store(data: bytes) -> dict[str, Any]:
    parsed = _loads(data)
    if not isinstance(parsed, dict) or not isinstance(parsed.get("users"), list):
        raise _Fail("parse")
    _rows(parsed["users"])
    return parsed


def _match(rows: list[dict[str, Any]], cookie: str) -> dict[str, Any] | None:
    found = [row for row in rows if hmac.compare_digest(row["hash"], cookie)]
    if len(found) > 1:
        raise _Fail("ambiguous")
    return found[0] if found else None


def _digest(user: str, password: str, *, check: bool) -> str:
    try:
        digest = panel_auth.credential_hash(user, password)
    except Exception as exc:
        log.error("account credential %s failed: %s", "check" if check else "derive", type(exc).__name__)
        raise _Fail("hash") from None
    if not isinstance(digest, str) or panel_auth.HASH_RE.fullmatch(digest) is None:
        raise _Fail("hash")
    return digest


def _prepare(store: dict[str, Any], cookie: str, name: str, current: str, new: str) -> bytes:
    rows = _rows(store["users"])
    row = _match(rows, cookie)
    if row is None:
        raise _Fail("expired")
    if not hmac.compare_digest(_digest(row["user"], current, check=True), cookie):
        raise _Fail("password")
    digest = _digest(name, new if new else current, check=False)
    if name == row["user"].strip().lower() and hmac.compare_digest(digest, cookie):
        raise _Fail("unchanged")
    for other in rows:
        if other is row:
            continue
        if other["user"] == name or other["user"].strip().lower() == name:
            raise _Fail("taken")
        if hmac.compare_digest(other["hash"], digest):
            raise _Fail("conflict")
    others = [(item["user"], item["hash"]) for item in rows if item is not row]
    row["user"] = name
    row["hash"] = digest
    if [(item["user"], item["hash"]) for item in rows if item is not row] != others:
        raise _Fail("parse")
    try:
        payload = json.dumps(store, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError):
        raise _Fail("parse") from None
    if len(payload) > _MAX_STORE or _load_store(payload) != store:
        raise _Fail("size" if len(payload) > _MAX_STORE else "parse")
    return payload


def _write_all(fd: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        wrote = os.write(fd, view)
        if wrote <= 0:
            raise _Fail("write")
        view = view[wrote:]


def _create_temp(dir_fd: int) -> tuple[int, str]:
    last = "write"
    for _ in range(2):
        name = ".auth-" + secrets.token_hex(16) + ".tmp"
        try:
            fd = _open_at(dir_fd, name, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        except _Fail as exc:
            last = exc.reason
            continue
        try:
            os.fchmod(fd, 0o600)
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise _Fail("symlink")
            return fd, name
        except Exception:
            try:
                os.close(fd)
            except OSError:
                log.error("account temp close failed")
            try:
                os.unlink(name, dir_fd=dir_fd)
            except OSError:
                log.error("account temp cleanup failed")
            raise
    raise _Fail(last)


def _commit(
    dir_fd: int,
    name: str,
    original: bytes,
    fingerprint: tuple[int, int, int, int],
    payload: bytes,
) -> bool:
    """Return directory-sync status. Raise only when replace has not happened."""
    again, again_fp = _read_named(dir_fd, name)
    if again != original or again_fp != fingerprint:
        raise _Fail("changed")
    tmp_fd: int | None = None
    tmp_name: str | None = None
    committed = False
    try:
        tmp_fd, tmp_name = _create_temp(dir_fd)
        _write_all(tmp_fd, payload)
        os.fsync(tmp_fd)
        latest, latest_fp = _read_named(dir_fd, name)
        if latest != original or latest_fp != fingerprint:
            raise _Fail("changed")
        os.rename(tmp_name, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        committed = True
        dir_ok = True
        try:
            os.fsync(dir_fd)
        except OSError:
            dir_ok = False
            log.error("account change committed; directory sync failed")
        try:
            confirmed, _fp = _read_named(dir_fd, name)
            if confirmed != payload:
                log.error("account change committed; post-commit bytes differ")
        except Exception as exc:
            log.error("account change committed; post-commit read failed: %s", type(exc).__name__)
        return dir_ok
    except _Fail:
        if committed:
            log.error("account change committed; post-commit store error")
            return False
        raise
    except OSError as exc:
        if committed:
            log.error("account change committed; post-commit io failed: %s", type(exc).__name__)
            return False
        log.error("account change not committed: %s", type(exc).__name__)
        raise _Fail("write") from None
    finally:
        if tmp_fd is not None:
            try:
                os.close(tmp_fd)
            except OSError:
                log.error("account temp close failed")
        if tmp_name is not None and not committed:
            try:
                os.unlink(tmp_name, dir_fd=dir_fd)
            except FileNotFoundError:
                pass
            except OSError:
                log.error("account temp cleanup failed")


def _locked_username(owner: Any, cookie: str) -> str | None:
    path = _store_path(owner)
    with _Locked(path) as locked:
        dir_fd = locked.dir_fd
        if dir_fd is None:
            raise _Fail("lock")
        data, _fp = _read_named(dir_fd, path.name)
        row = _match(_rows(_load_store(data)["users"]), cookie)
        return None if row is None else row["user"]


def _locked_update(owner: Any, cookie: str, name: str, current: str, new: str) -> None:
    path = _store_path(owner)
    with _Locked(path) as locked:
        dir_fd = locked.dir_fd
        if dir_fd is None:
            raise _Fail("lock")
        data, fingerprint = _read_named(dir_fd, path.name)
        payload = _prepare(_load_store(data), cookie, name, current, new)
        synced = _commit(dir_fd, path.name, data, fingerprint, payload)
    try:
        log.info("account change committed dir_sync=%s", "ok" if synced else "failed")
    except Exception:
        # Already committed. A log failure must not become an uncommitted error.
        return


def _store_error(exc: _Fail) -> web.Response:
    log.error("account store closed: %s", exc.reason)
    return _error(503, "store_unavailable", "账户存储暂不可用")


def _client_error(reason: str) -> web.Response:
    table = {
        "size": (400, "payload_too_large", "请求体过大"),
        "body": (400, "invalid_json", "请求格式无效"),
        "json": (400, "invalid_json", "请求格式无效"),
        "fields": (400, "invalid_body", "请求字段无效"),
        "username": (400, "invalid_username", "用户名不符合要求"),
        "password": (400, "invalid_password", "密码不符合要求"),
    }
    status, code, message = table.get(reason, (400, "invalid_body", "请求字段无效"))
    return _error(status, code, message)


def _clear_cookie(response: web.Response, request: web.Request) -> None:
    https = panel_auth.request_is_https(request)
    if https is not True and https is not False:
        raise RuntimeError("request_is_https")
    response.del_cookie(
        panel_auth.COOKIE,
        path="/",
        secure=https,
        httponly=True,
        samesite="Strict",
    )


async def _get(owner: Any, request: web.Request) -> web.Response:
    denied = _gate(owner, request)
    if denied is not None:
        return denied
    cookie = _presented(request)
    if cookie is None:
        return _error(401, "unauthorized", "登录已失效，请重新登录")
    try:
        username = _locked_username(owner, cookie)
    except _Busy:
        log.error("account store closed: busy")
        return _error(503, "store_unavailable", "账户存储暂不可用")
    except _Fail as exc:
        return _store_error(exc)
    if username is None:
        return _error(401, "unauthorized", "登录已失效，请重新登录")
    return web.json_response({"username": username}, headers={"Cache-Control": "no-store"})


async def _put(owner: Any, request: web.Request) -> web.Response:
    denied = _gate(owner, request)
    if denied is not None:
        return denied
    cookie = _presented(request)
    if cookie is None:
        return _error(401, "unauthorized", "登录已失效，请重新登录")
    if not _content_type_ok(request):
        return _error(400, "invalid_content_type", "请求类型不正确")
    try:
        name, current, new = _body(await _read_body(request))
    except _Fail as exc:
        return _client_error(exc.reason)
    retry = _rate_blocked(cookie)
    if retry is not None:
        return _error(429, "rate_limited", "尝试次数过多，请稍后再试", Retry_After=str(retry))
    try:
        _locked_update(owner, cookie, name, current, new)
    except _Busy:
        log.error("account store closed: busy")
        return _error(503, "store_unavailable", "账户存储暂不可用")
    except _Fail as exc:
        if exc.reason == "expired":
            return _error(401, "unauthorized", "登录已失效，请重新登录")
        if exc.reason == "password":
            _rate_note(cookie)
            return _error(403, "bad_password", "当前密码不正确")
        if exc.reason == "unchanged":
            _rate_clear(cookie)
            return _error(400, "unchanged", "账户信息没有变化")
        if exc.reason == "taken":
            _rate_clear(cookie)
            return _error(409, "username_taken", "用户名已被占用")
        return _store_error(exc)
    except Exception as exc:
        log.error("account update failed: %s", type(exc).__name__)
        return _error(503, "store_unavailable", "账户存储暂不可用")
    try:
        _rate_clear(cookie)
    except Exception as exc:
        log.error("account rate clear failed after commit: %s", type(exc).__name__)
    response = web.json_response(
        {"ok": True, "reauthenticate": True},
        status=200,
        headers={"Cache-Control": "no-store"},
    )
    try:
        _clear_cookie(response, request)
    except Exception as exc:
        log.error("account cookie clear failed after commit: %s", type(exc).__name__)
    return response


def _method_not_allowed() -> web.Response:
    return web.Response(status=405, headers={"Allow": "GET, PUT", "Cache-Control": "no-store"})


async def _dispatch(owner: Any, request: web.Request) -> web.StreamResponse:
    try:
        if request.method == "GET":
            return await _get(owner, request)
        if request.method == "PUT":
            return await _put(owner, request)
        return _method_not_allowed()
    except Exception as exc:
        log.error("account request failed: %s", type(exc).__name__)
        return _error(503, "store_unavailable", "账户存储暂不可用")


def register_account_routes(app: Any, owner: Any) -> None:
    async def account_endpoint(request: web.Request) -> web.StreamResponse:
        return await _dispatch(owner, request)

    app.router.add_route("*", "/panel-auth/account", account_endpoint)
