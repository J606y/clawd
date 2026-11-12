"""Panel log read. File sources stay. Journal is a host boundary.

A journal request fails closed. It is not turned into an empty line list.
"""

from __future__ import annotations

import errno
import os
import re
import stat
import sys
import time
from pathlib import Path
from typing import Any

from clawd_server.admin_common import fail, log, panel_home, redact_text, run

LOG_FILES = {
    "agent": "agent.log",
    "errors": "errors.log",
    "gateway": "gateway.log",
    "gui": "gui.log",
    "mcp": "mcp-stderr.log",
}
# Ids the sidecar still advertises as journal. Reading them is not this process.
LOG_JOURNAL = {
    "j-gateway": "hermes-gateway",
    "j-dashboard": "hermes-dashboard",
    "j-sidecar": "hermes-panel-sidecar",
}
LOG_LEVELS = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 4}
LOG_RECORD_RE = re.compile(
    r"^\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d(?:[,.]\d+)?\s+(DEBUG|INFO|WARNING|ERROR|CRITICAL)\s+(?:\[[^\]]*\]\s+)?(\S+?):"
)
LOG_COMPONENTS = {
    "gateway": ("gateway", "hermes_plugins", "plugins.platforms"),
    "agent": ("agent", "run_agent", "model_tools", "batch_runner"),
    "tools": ("tools",),
    "cli": ("hermes_cli", "cli"),
    "cron": ("cron",),
}
LOG_TAIL_BYTES = 6 * 1024 * 1024
_JOURNAL_MESSAGE = "系统服务日志属于宿主边界，不读取 systemd，也不返回空列表。"
_LOG_REFUSED = "日志路径是符号链接或已离开日志目录，已拒绝读取"
_LOG_REPLACED = "日志文件在打开后被替换，已拒绝读取"
_LOG_NOT_REGULAR = "日志不是日志目录内的普通文件，已拒绝读取"


def _nofollow_flag() -> int:
    flag = getattr(os, "O_NOFOLLOW", 0)
    if not flag:
        fail(409, "log_refused", "无法在不跟随符号链接的情况下读取日志")
    return flag


def _close_fds(fds: list[int]) -> bool:
    failed = False
    for fd in reversed(fds):
        try:
            os.close(fd)
        except OSError:
            failed = True
    return failed


def _contained_log_dir(mod: Any) -> tuple[Path, tuple[str, ...]]:
    """Lexical home containment. Does not resolve or follow a symlink."""
    home = panel_home(mod)
    if not isinstance(home, Path):
        home = Path(home)
    configured = getattr(mod, "LOG_DIR", None)
    path = home / "logs" if configured is None else Path(configured)
    if not path.is_absolute():
        path = home / path
    try:
        relative = path.relative_to(home)
    except ValueError:
        fail(409, "profile_scope_unsafe", "日志目录不在当前面板数据目录内，已拒绝")
    parts = relative.parts
    if relative.is_absolute() or not parts or any(part in {"", ".", ".."} for part in parts):
        fail(409, "profile_scope_unsafe", "日志目录不在当前面板数据目录内，已拒绝")
    return home, parts


def _log_dir(mod: Any) -> Path:
    home, parts = _contained_log_dir(mod)
    return home.joinpath(*parts)


def _tail_text(fd: int, max_bytes: int) -> list[str]:
    """Bounded tail of an already opened regular-file descriptor. Not a path open."""
    if type(fd) is not int or max_bytes < 1:
        fail(500, "log_read_failed", "读取日志失败：无法读取")
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            fail(409, "log_refused", _LOG_NOT_REGULAR)
        size = info.st_size
        start = size - max_bytes if size > max_bytes else 0
        os.lseek(fd, start, os.SEEK_SET)
        chunks: list[bytes] = []
        remaining = max_bytes
        while remaining > 0:
            blob = os.read(fd, min(remaining, 1024 * 1024))
            if not blob:
                break
            chunks.append(blob)
            remaining -= len(blob)
    except OSError as exc:
        fail(500, "log_read_failed", f"读取日志失败：{exc.strerror or '无法读取'}")
    lines = b"".join(chunks).decode("utf-8", errors="replace").splitlines()
    return lines[1:] if size > max_bytes and lines else lines


def _map_open_error(exc: OSError, *, missing_is_empty: bool) -> list[str]:
    if exc.errno == errno.ENOENT and missing_is_empty:
        return []
    if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
        fail(409, "log_refused", _LOG_REFUSED)
    fail(500, "log_read_failed", f"读取日志失败：{exc.strerror or '无法读取'}")


def _read_allowed_log(mod: Any, name: str, max_bytes: int) -> list[str]:
    """Open one allowlisted name through the verified log-directory fd.

    A missing log is an empty line list. A symlink, escape, or replacement is
    a refusal. An IO error is a distinct read failure. Never stat-then-open.
    """
    allowed = set(LOG_FILES.values())
    if name not in allowed or "/" in name or "\\" in name or "\x00" in name or name in {".", ".."}:
        fail(404, "unknown_source", "未知的日志来源")
    home, parts = _contained_log_dir(mod)
    # Keep the historical helper on the file-read path. Journal returns earlier.
    if _log_dir(mod) != home.joinpath(*parts):
        fail(409, "profile_scope_unsafe", "日志目录不在当前面板数据目录内，已拒绝")
    nofollow = _nofollow_flag()
    cloexec = getattr(os, "O_CLOEXEC", 0)
    dir_flags = os.O_RDONLY | os.O_DIRECTORY | nofollow | cloexec
    # Refuse a FIFO/device after fstat without blocking the event loop at open.
    file_flags = os.O_RDONLY | os.O_NONBLOCK | nofollow | cloexec
    fds: list[int] = []
    try:
        try:
            home_fd = os.open(home, os.O_RDONLY | os.O_DIRECTORY | cloexec)
        except OSError as exc:
            _map_open_error(exc, missing_is_empty=False)
        fds.append(home_fd)
        dir_fd = home_fd
        for part in parts:
            try:
                next_fd = os.open(part, dir_flags, dir_fd=dir_fd)
            except OSError as exc:
                return _map_open_error(exc, missing_is_empty=True)
            fds.append(next_fd)
            dir_fd = next_fd
        dir_info = os.fstat(dir_fd)
        if not stat.S_ISDIR(dir_info.st_mode):
            fail(409, "log_refused", _LOG_REFUSED)
        try:
            file_fd = os.open(name, file_flags, dir_fd=dir_fd)
        except OSError as exc:
            return _map_open_error(exc, missing_is_empty=True)
        fds.append(file_fd)
        file_info = os.fstat(file_fd)
        if (
            not stat.S_ISREG(file_info.st_mode)
            or stat.S_ISLNK(file_info.st_mode)
            or file_info.st_dev != dir_info.st_dev
        ):
            fail(409, "log_refused", _LOG_NOT_REGULAR)
        try:
            named = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
        except OSError as exc:
            if exc.errno in {errno.ELOOP, errno.ENOENT}:
                fail(409, "log_refused", _LOG_REPLACED)
            fail(500, "log_read_failed", f"读取日志失败：{exc.strerror or '无法读取'}")
        if (
            named.st_dev != file_info.st_dev
            or named.st_ino != file_info.st_ino
            or not stat.S_ISREG(named.st_mode)
        ):
            fail(409, "log_refused", _LOG_REPLACED)
        return _tail_text(file_fd, max_bytes)
    finally:
        close_failed = _close_fds(fds)
        pending = sys.exc_info()[1]
        if close_failed and pending is not None:
            log.warning("log descriptor close failed after %s", type(pending).__name__)
        elif close_failed:
            fail(500, "log_read_failed", "读取日志后无法关闭描述符")


def _filter_log(lines: list[str], level: str, component: str, q: str, limit: int) -> list[str]:
    min_level = LOG_LEVELS.get(level, -1)
    prefixes = LOG_COMPONENTS.get(component, ())
    if min_level < 0 and not prefixes and not q:
        return lines[-limit:]
    needle = q.lower()
    kept: list[str] = []
    keep = False
    for line in lines:
        match = LOG_RECORD_RE.match(line)
        if match:
            logger = match.group(2)
            keep = (min_level < 0 or LOG_LEVELS[match.group(1)] >= min_level) and (
                not prefixes or any(
                    logger == prefix or logger.startswith(prefix + ".") or logger.startswith(prefix + "_")
                    for prefix in prefixes
                )
            )
        if keep and (not needle or needle in line.lower()):
            kept.append(line)
    return kept[-limit:]


def _bounded_lines(value: Any) -> int:
    try:
        count = int(value)
    except (TypeError, ValueError):
        count = 200
    return max(20, min(500, count))


def get_logs(mod: Any, source: Any, lines: Any, level: Any, component: Any, q: Any) -> dict[str, Any]:
    """Read one file log. Journal ids raise 501 ``journal_host_boundary``.

    Signature is ``(mod, source, lines, level, component, q)`` so the route
    owner can pass the existing query. Callers must propagate the error.
    Replacing it with ``{"lines": []}`` would be a fake success.
    """

    def _read() -> dict[str, Any]:
        source_id = source if isinstance(source, str) else ""
        if source_id in LOG_JOURNAL:
            fail(501, "journal_host_boundary", _JOURNAL_MESSAGE)
        if source_id not in LOG_FILES:
            fail(404, "unknown_source", "未知的日志来源")
        count = _bounded_lines(lines)
        level_name = level.upper() if isinstance(level, str) else ""
        if level_name not in LOG_LEVELS:
            level_name = ""
        component_name = component if isinstance(component, str) and component in LOG_COMPONENTS else ""
        query = q[:200] if isinstance(q, str) else ""
        raw = _read_allowed_log(mod, LOG_FILES[source_id], LOG_TAIL_BYTES)
        if query:
            raw = [redact_text(mod, line[:2000]) or "" for line in raw]
        filtered = _filter_log(raw, level_name, component_name, query, count)
        return {
            "source": source_id,
            "lines": [redact_text(mod, line[:2000]) or "" for line in filtered],
            "at": time.time(),
        }

    return run(mod, _read)
