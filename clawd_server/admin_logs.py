"""Panel log read. File sources stay. Journal is a host boundary.

A journal request fails closed. It is not turned into an empty line list.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from clawd_server.admin_common import fail, panel_home, redact_text, run

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


def _tail_text(path: Path, max_bytes: int) -> list[str]:
    size = path.stat().st_size
    with path.open("rb") as handle:
        handle.seek(max(0, size - max_bytes))
        data = handle.read()
    lines = data.decode("utf-8", errors="replace").splitlines()
    return lines[1:] if size > max_bytes and lines else lines


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


def _log_dir(mod: Any) -> Path:
    home = panel_home(mod)
    configured = getattr(mod, "LOG_DIR", None)
    if configured is None:
        return home / "logs"
    path = Path(configured)
    try:
        path.resolve().relative_to(home.resolve())
    except ValueError:
        fail(409, "profile_scope_unsafe", "日志目录不在当前面板数据目录内，已拒绝")
    return path


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
        path = _log_dir(mod) / LOG_FILES[source_id]
        try:
            raw = _tail_text(path, LOG_TAIL_BYTES) if path.is_file() else []
        except OSError as exc:
            fail(500, "log_read_failed", f"读取日志失败：{exc.strerror or '无法读取'}")
        if query:
            raw = [redact_text(mod, line[:2000]) or "" for line in raw]
        filtered = _filter_log(raw, level_name, component_name, query, count)
        return {
            "source": source_id,
            "lines": [redact_text(mod, line[:2000]) or "" for line in filtered],
            "at": time.time(),
        }

    return run(mod, _read)
