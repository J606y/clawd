#!/usr/bin/env python3
"""面板核心逻辑：补齐 api_server 没有的能力（主机状态、技能、配置、文件、活动、生图）。

前身是 apps/panel/sidecar/hermes_panel_sidecar.py。HTTP 形态已收进核心：
本模块不再开监听、不再起独立 HTTP 服务线程，`register_panel_core_routes`
把同一批路由挂到面板统一进程的 aiohttp 应用上（前缀 /panel-core/），鉴权
由面板的 cookie + Origin 检查（PanelServer._authed）负责。

`Handler` 是普通分发类（不再继承标准库的 HTTP 处理基类），只做一件事：
把一次请求（method/path/headers/body）按旧 sidecar 的路由表分发给同名函数，
把状态行 + 头 + 体写进 `self.wfile`。单元测试与文件鉴权用例继续用它；
生产经由 aiohttp 处理函数调用它（JSON 路由），文件下载/预览另走流式路径。

设计约束（沿用 sidecar 时期）：
- 核心逻辑坚持仅用标准库且绝不 import Hermes 内部模块——Hermes 频繁自动更新，内部 API 随时会变。
  配置读写一律走核心配置服务；技能直接读 SKILL.md。
  对于配置展示脱敏，引入锁定版本的 PyYAML 依赖；若环境缺少 yaml 模块，则显式报错不可用，不破坏进程启动。
- 所有外部命令带超时；任何异常都返回结构化 JSON 错误，不吞异常。
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import errno
import fcntl
import hashlib
import hmac
import io
import select
import signal
import json
import math
import logging
import mimetypes
import os
import re
import secrets
import shutil
import sqlite3
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unicodedata
from aiohttp import web
from pathlib import Path
from typing import Any, BinaryIO, Iterator
from urllib.parse import parse_qs, quote, unquote, urlsplit

try:
    import yaml
except ImportError:
    try:
        from clawd_server import yaml_adapter as yaml
    except ImportError:
        yaml = None  # type: ignore[assignment]

KEY_FILE = Path(os.environ.get("PANEL_SIDECAR_KEY_FILE", "/etc/hermes-panel/sidecar.key"))
HERMES_HOME = Path(os.environ.get("HERMES_HOME", "/root/.hermes"))
HERMES_BIN = os.environ.get("HERMES_BIN", "/usr/local/bin/hermes")
CLI_TIMEOUT_S = 30
MAX_BODY_BYTES = 64 * 1024
MAX_SKILL_BODY_BYTES = 320 * 1024  # 技能全文保存（内容上限 256KB，留出 JSON 转义余量）

# 状态页展示的服务（顺序即展示顺序）。
SERVICES = [
    ("hermes-gateway", "clawd 网关"),
    ("hermes-live-browser", "实时画面"),
    ("hermes-panel-sidecar", "面板辅助服务"),
    ("nginx", "Nginx"),
    ("x-ui", "x-ui 代理"),
    ("moss-agent", "Moss 监控"),
    ("ssh", "SSH"),
]
# 面板允许重启的服务：重启 sidecar 自身或 ssh/moss 会切断面板或运维通道，不开放。旧面板 hermes-dashboard 已于 2026-10-07 移除（日志来源 j-dashboard 保留供查历史）。
RESTARTABLE = {"hermes-gateway", "hermes-live-browser"}

# 配置展示时遮蔽的键（命中键名即遮蔽值）。.env 永不读取。
# 密钥类名字：只看每一段点分名的"结尾单词"。`threshold_tokens` 这类带 tokens（复数）的是数量设置，不是密钥。
SECRET_NAME_RE = re.compile(r"(?:^|[_-])(?:api[_-]?keys?|keys?|secrets?|passw(?:or)?ds?|private[_-]?keys?|cookies?|authorization|token|hash(?:es)?|salts?|credentials?)$", re.I)


def _is_secret_name(name: str) -> bool:
    return bool(SECRET_NAME_RE.search(name))


def _is_secret_key(key: str) -> bool:
    return any(_is_secret_name(seg) for seg in key.split("."))
CONFIG_KEY_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-]{0,127}$")
SKILL_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-]{0,127}$")

# 文件：只开放这几个根（用户决定：上传目录 + Hermes 工作产物，不做全盘浏览）。
# 注意网关工作目录是 ~/.hermes（.env / config.yaml / state.db 所在），绝不能作为根。
FILE_ROOTS: dict[str, dict[str, Any]] = {
    "uploads": {"path": Path("/root/hermes-uploads"), "label": "上传", "writable": True, "create": True},
    "workspace": {"path": Path("/root/hermes-workspace"), "label": "工作区", "writable": True, "create": True},
    "cron": {"path": HERMES_HOME / "cron" / "output", "label": "定时任务输出", "writable": False, "create": False},
    "images": {"path": HERMES_HOME / "images", "label": "图片", "writable": False, "create": False},
}
# 无论根怎么配，这些名字一律拒绝（纵深防御）。
DENY_NAME_RE = re.compile(r"(^\.env(\..*)?$|^config\.ya?ml$|\.db(-wal|-shm|-journal)?$|\.key$|\.pem$|^id_[a-z0-9_]+$|^authorized_keys$|^auth\.json$)", re.I)
# 仅用于 /files/artifact（助手消息里的文件引用）：比 DENY_NAME_RE 更严，与前端 artifacts.ts 的 SECRET 保持一致。
ARTIFACT_SECRET_RE = re.compile(r"^(?:node_modules|credentials?|secrets?|tokens?)$|\.(?:p12|pfx)$", re.I)
DENY_PREFIXES = (Path("/etc"), Path("/root/.ssh"), HERMES_HOME / ".env")
# 只用于工作区 /files 浏览。不并入 DENY_NAME_RE：那条也约束 artifact、上传文件名和删除。
_WORKSPACE_BROWSE_DENY_PARTS = frozenset({".git", "hermes-panel-state"})
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
UPLOAD_NAME_RE = re.compile(r'[\x00-\x1f/\\:*?"<>|]')

log = logging.getLogger("hermes-panel-sidecar")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


# ---------------------------------------------------------------- 会话文件夹（独立面板数据库）


@contextlib.contextmanager
def _folder_store() -> Iterator[sqlite3.Connection]:
    """Serialize validation/write/snapshot across threads and sidecar processes.

    Commit before the caller receives the snapshot; any query/commit failure is
    visible as 503, never an empty replacement snapshot. Always close handles.
    """
    path = HERMES_HOME / "panel_folders.db"
    try:
        try:  # 与 panel_auto_continue.db 一致：库文件只给 sidecar 所属用户读写。
            os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
        except FileExistsError:
            pass
        with contextlib.closing(sqlite3.connect(path, timeout=3)) as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("CREATE TABLE IF NOT EXISTS folders (id TEXT PRIMARY KEY, name TEXT NOT NULL, name_key TEXT NOT NULL UNIQUE, collapsed INTEGER NOT NULL DEFAULT 0)")
                conn.execute("CREATE TABLE IF NOT EXISTS assignments (session_id TEXT PRIMARY KEY, folder_id TEXT NOT NULL REFERENCES folders(id) ON DELETE CASCADE)")
                yield conn
    except (sqlite3.Error, OSError) as exc:
        raise ApiError(503, "folder_store_unavailable", "会话文件夹存储暂不可用，请稍后重试") from exc


def _folder_snapshot(conn: sqlite3.Connection) -> dict[str, Any]:
    return {
        "folders": [{"id": row[0], "name": row[1], "collapsed": bool(row[2])}
                    for row in conn.execute("SELECT id, name, collapsed FROM folders ORDER BY rowid")],
        "assignments": dict(conn.execute("SELECT session_id, folder_id FROM assignments")),
    }


def _folder_name_char_ok(c: str) -> bool:
    """控制字符、孤立代理，以及零宽/双向覆盖/BOM 等不可见格式字符（Unicode Cf）都不能进名称：
    否则能建出看起来是空名、或与另一个文件夹看起来同名的文件夹。"""
    return not (ord(c) < 32 or 127 <= ord(c) <= 159 or 0xD800 <= ord(c) <= 0xDFFF or unicodedata.category(c) == "Cf")


def _require_folder(conn: sqlite3.Connection, folder_id: str | None) -> None:
    if folder_id is not None and conn.execute("SELECT 1 FROM folders WHERE id = ?", (folder_id,)).fetchone() is None:
        raise ApiError(404, "folder_not_found", "文件夹不存在")


def update_session_folders(body: dict[str, Any]) -> dict[str, Any]:
    """Apply one single-level folder operation without modifying native state."""
    action = body.get("action")
    fields = {"create": {"action", "name"}, "rename": {"action", "id", "name"},
              "collapse": {"action", "id", "collapsed"}, "delete": {"action", "id"},
              "move": {"action", "sessionId", "folderId"}}
    if not isinstance(action, str) or action not in fields or set(body) != fields[action]:
        raise ApiError(400, "invalid_body", "文件夹操作或字段不合法")
    for key in ("id", "sessionId", "folderId"):
        if key in body and not (key == "folderId" and body[key] is None):
            value = body[key]
            if not isinstance(value, str) or not value or any(ord(c) < 32 or 127 <= ord(c) <= 159 or 0xD800 <= ord(c) <= 0xDFFF for c in value):
                raise ApiError(400, "invalid_body", f"{key} 必须为非空字符串")
    if body.get("action") == "delete":
        with _folder_store() as conn:
            _require_folder(conn, body["id"])
            conn.execute("DELETE FROM folders WHERE id = ?", (body.get("id"),))
            return _folder_snapshot(conn)
    if body.get("action") == "move":
        session_id = body.get("sessionId")
        if body.get("folderId") is None:
            # 移出不需要会话还在：已删除/被清理的会话也要能移出，否则归档关系永远残留。
            with _folder_store() as conn:
                conn.execute("DELETE FROM assignments WHERE session_id = ?", (session_id,))
                return _folder_snapshot(conn)
        try:
            with contextlib.closing(sqlite3.connect((HERMES_HOME / "state.db").resolve().as_uri() + "?mode=ro", uri=True, timeout=3)) as state:
                state.execute("PRAGMA query_only = ON")
                if state.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone() is None:
                    raise ApiError(404, "session_not_found", "会话不存在")
        except (sqlite3.Error, OSError) as exc:
            raise ApiError(503, "session_state_unavailable", "会话数据库暂不可用，请稍后重试") from exc
        with _folder_store() as conn:
            _require_folder(conn, body["folderId"])
            if body.get("folderId") is None:
                conn.execute("DELETE FROM assignments WHERE session_id = ?", (session_id,))
            else:
                conn.execute("INSERT INTO assignments (session_id, folder_id) VALUES (?, ?) ON CONFLICT(session_id) DO UPDATE SET folder_id = excluded.folder_id", (session_id, body["folderId"]))
            return _folder_snapshot(conn)
    if body.get("action") == "collapse":
        if not isinstance(body.get("collapsed"), bool):
            raise ApiError(400, "invalid_body", "collapsed 必须为布尔值")
        with _folder_store() as conn:
            _require_folder(conn, body["id"])
            conn.execute("UPDATE folders SET collapsed = ? WHERE id = ?", (body["collapsed"], body.get("id")))
            return _folder_snapshot(conn)
    name = body.get("name")
    if (not isinstance(name, str) or not all(_folder_name_char_ok(c) for c in name)
            or not 1 <= len(name.strip()) <= 40):
        raise ApiError(400, "invalid_folder_name", "文件夹名称须为 1–40 个字符，且不能包含控制字符或不可见字符")
    name = name.strip()
    with _folder_store() as conn:
        folder_id = body.get("id") if body.get("action") == "rename" else secrets.token_hex(16)
        if action == "rename":
            _require_folder(conn, folder_id)
        if conn.execute("SELECT 1 FROM folders WHERE name_key = ? AND id != ?", (name.casefold(), folder_id)).fetchone():
            raise ApiError(409, "folder_name_conflict", "同名文件夹已存在")
        if body.get("action") == "rename":
            conn.execute("UPDATE folders SET name = ?, name_key = ? WHERE id = ?", (name, name.casefold(), folder_id))
        else:
            conn.execute("INSERT INTO folders (id, name, name_key) VALUES (?, ?, ?)",
                         (folder_id, name, name.casefold()))
        return _folder_snapshot(conn)


def get_session_folders(include_sessions: bool = False) -> dict[str, Any]:
    with _folder_store() as conn:
        snapshot = _folder_snapshot(conn)
    if include_sessions:
        # 只读已归属会话的摘要。不把查询失败变成空文件夹快照，也不写会话库。
        snapshot["sessions"] = _assigned_session_summaries(list(snapshot["assignments"]))
    return snapshot


def _folder_session_summary(row: sqlite3.Row) -> dict[str, Any] | None:
    cols = set(row.keys())
    session_id = row["id"] if "id" in cols else None
    if not isinstance(session_id, str) or not session_id:
        return None
    source = row["source"] if "source" in cols and isinstance(row["source"], str) and row["source"] else "api_server"
    started = row["started_at"] if "started_at" in cols else 0
    if type(started) not in (int, float):
        started = 0
    last_active = row["last_activity_at"] if "last_activity_at" in cols else None
    if type(last_active) not in (int, float):
        last_active = None
    title = row["title"] if "title" in cols and (row["title"] is None or isinstance(row["title"], str)) else None
    model = row["model"] if "model" in cols and (row["model"] is None or isinstance(row["model"], str)) else None
    message_count = row["message_count"] if "message_count" in cols else 0
    if type(message_count) is not int:
        message_count = 0
    return {
        "id": session_id,
        "source": source,
        "title": title,
        "model": model,
        "started_at": started,
        "last_active": last_active,
        "message_count": message_count,
        "preview": None,
        "pinned": bool(row["pinned"]) if "pinned" in cols and row["pinned"] is not None else False,
        "archived": bool(row["archived"]) if "archived" in cols and row["archived"] is not None else False,
    }


def _assigned_session_summaries(session_ids: list[str]) -> list[dict[str, Any]]:
    ids = [session_id for session_id in session_ids if isinstance(session_id, str) and session_id]
    if not ids:
        return []
    path = HERMES_HOME / "state.db"
    try:
        if not path.is_file():
            return []
        with _session_run_db(path) as conn:
            conn.row_factory = sqlite3.Row
            found: list[dict[str, Any]] = []
            for start in range(0, len(ids), 200):
                chunk = ids[start:start + 200]
                placeholders = ",".join("?" for _ in chunk)
                rows = conn.execute(f"SELECT * FROM sessions WHERE id IN ({placeholders})", chunk).fetchall()
                for row in rows:
                    item = _folder_session_summary(row)
                    if item is not None:
                        found.append(item)
            return found
    except (sqlite3.Error, OSError, ValueError, TypeError):
        return []


# ---------------------------------------------------------------- 主机状态


def _cpu_times() -> tuple[int, int]:
    with open("/proc/stat", encoding="ascii") as f:
        parts = [int(x) for x in f.readline().split()[1:]]
    idle = parts[3] + (parts[4] if len(parts) > 4 else 0)
    return sum(parts), idle


def _cpu_percent(sample_s: float = 0.25) -> float:
    t1, i1 = _cpu_times()
    time.sleep(sample_s)
    t2, i2 = _cpu_times()
    dt = t2 - t1
    return round(100.0 * (1 - (i2 - i1) / dt), 1) if dt > 0 else 0.0


def _meminfo() -> dict[str, int]:
    out: dict[str, int] = {}
    with open("/proc/meminfo", encoding="ascii") as f:
        for line in f:
            k, v = line.split(":", 1)
            out[k] = int(v.split()[0]) * 1024
    return out


def _anon_memory(unit: str) -> int | None:
    """进程自身占用（cgroup memory.stat 的 anon）。systemd 的 MemoryCurrent 含可随时回收的文件缓存，会夸大占用。"""
    try:
        with open(f"/sys/fs/cgroup/system.slice/{unit}.service/memory.stat", encoding="ascii") as f:
            for line in f:
                if line.startswith("anon "):
                    return int(line.split()[1])
    except OSError:
        return None
    return None


def _service_states() -> list[dict[str, Any]]:
    names = [n for n, _ in SERVICES]
    proc = subprocess.run(
        ["systemctl", "show", *[f"{n}.service" for n in names], "-p", "Id,LoadState,ActiveState,SubState,MemoryMax,ActiveEnterTimestampMonotonic"],
        capture_output=True, text=True, timeout=10, check=False,
    )
    blocks: dict[str, dict[str, str]] = {}
    for block in proc.stdout.strip().split("\n\n"):
        kv = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
        if "Id" in kv:
            blocks[kv["Id"].removesuffix(".service")] = kv

    def num(v: str | None) -> int | None:
        return int(v) if v and v.isdigit() else None

    if not blocks:
        raise ApiError(503, "service_status_unavailable", "无法读取系统服务状态，未当作已停止")
    uptime_s = time.clock_gettime(time.CLOCK_MONOTONIC)
    result = []
    for name, label in SERVICES:
        kv = blocks.get(name, {})
        since = num(kv.get("ActiveEnterTimestampMonotonic"))
        result.append({
            "name": name,
            "label": label,
            "load": kv.get("LoadState", "unknown"),
            "active": kv.get("ActiveState", "unknown"),
            "sub": kv.get("SubState", ""),
            "memory": _anon_memory(name) if kv.get("ActiveState") == "active" else None,
            "memoryMax": num(kv.get("MemoryMax")),
            "runningForS": round(uptime_s - since / 1e6) if since else None,
            "restartable": name in RESTARTABLE,
        })
    return result


def get_status() -> dict[str, Any]:
    mem = _meminfo()
    disk = shutil.disk_usage("/")
    with open("/proc/loadavg", encoding="ascii") as f:
        load = [float(x) for x in f.read().split()[:3]]
    with open("/proc/uptime", encoding="ascii") as f:
        uptime = float(f.read().split()[0])
    return {
        "host": {
            "cpuPercent": _cpu_percent(),
            "cpuCount": os.cpu_count() or 1,
            "load": load,
            "uptimeS": round(uptime),
            "mem": {"total": mem["MemTotal"], "available": mem["MemAvailable"]},
            "swap": {"total": mem.get("SwapTotal", 0), "free": mem.get("SwapFree", 0)},
            "disk": {"total": disk.total, "used": disk.used, "free": disk.free},
        },
        "services": _service_states(),
        "sampledAt": time.time(),
    }


def _active_turn_count() -> int:
    try:
        db_path = HERMES_HOME / "state.db"
        if not db_path.exists():
            return 0
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2)
        try:
            conn.execute("PRAGMA query_only = 1")
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "session_turn_leases" not in tables:
                return 0
            row = conn.execute("SELECT count(*) FROM session_turn_leases WHERE expires_at > ?", (time.time(),)).fetchone()
            return int(row[0]) if row and row[0] is not None else 0
        finally:
            conn.close()
    except (sqlite3.Error, OSError, ValueError, TypeError) as exc:
        log.warning("restart activity query unavailable: %s", type(exc).__name__)
        raise ApiError(503, "activity_state_unavailable", "活动回合状态暂不可用；请稍后重试，或确认风险后强制重启") from exc


def restart_service(name: str, force: bool = False) -> dict[str, Any]:
    if name not in RESTARTABLE:
        raise ApiError(403, "not_restartable", f"不允许从面板重启 {name}")
    if name == "hermes-gateway" and not force:
        active_turns = _active_turn_count()
        if active_turns > 0:
            raise ApiError(409, "active_runs_exist", f"当前有 {active_turns} 个正在执行的活动回合，重启将导致其中断")
    try:
        proc = subprocess.run(["systemctl", "restart", f"{name}.service"], capture_output=True, text=True, timeout=120, check=False)
    except subprocess.TimeoutExpired as exc:
        raise ApiError(504, "restart_timeout", f"重启服务 {name} 超时（已等待 120 秒，可能正在排空活动任务）") from exc
    if proc.returncode != 0:
        raise ApiError(500, "restart_failed", (proc.stderr or proc.stdout).strip()[-500:] or "重启失败")
    log.info("restarted %s via panel (force=%s)", name, force)
    return {"ok": True, "name": name}


# ---------------------------------------------------------------- Hermes CLI


# 每个 hermes 进程约 70MB（Python + 依赖）。同时只放 3 个，否则会撑爆本服务的内存上限而被限速。
_CLI_SLOTS = threading.BoundedSemaphore(3)
_READ_ONLY_CLI = {("config", "get"), ("plugins", "list"), ("profile", "list"), ("profile", "show")}
# 配置读取结果缓存：以 config.yaml 的 (mtime, size) 为准——文件没变就一直命中，面板内外任何写入都会让它失效；
# 再加一个兜底的最长存活时间。每次读都要起一个约 1 秒的 hermes 进程，设置/模型/MCP 页一次加载会连续读多个键。
CFG_CACHE_MAX_AGE_S = 900.0
_CFG_CACHE: dict[str, tuple[Any, float, Any]] = {}  # key -> (stamp, stored_at, value)
_CFG_CACHE_LOCK = threading.Lock()


def _cfg_stamp() -> tuple[int, int] | None:
    try:
        st = (HERMES_HOME / "config.yaml").stat()
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


def _cfg_cache_get(key: str) -> tuple[bool, Any]:
    with _CFG_CACHE_LOCK:
        hit = _CFG_CACHE.get(key)
    if hit and hit[0] is not None and hit[0] == _cfg_stamp() and time.monotonic() - hit[1] < CFG_CACHE_MAX_AGE_S:
        return True, hit[2]
    return False, None


def _cfg_cache_put(key: str, stamp: tuple[int, int] | None, value: Any) -> None:
    with _CFG_CACHE_LOCK:
        _CFG_CACHE[key] = (stamp, time.monotonic(), value)


def _clear_caches() -> None:
    with _CFG_CACHE_LOCK:
        _CFG_CACHE.clear()
    _PLUGIN_CACHE.update(at=0.0, data=None)
    _PROFILE_CACHE.update(at=0.0, stamp=None, data=None)


def _hermes(*args: str, timeout: int = CLI_TIMEOUT_S) -> subprocess.CompletedProcess[str]:
    mutating = tuple(args[:2]) not in _READ_ONLY_CLI
    with _CLI_SLOTS:
        try:
            return subprocess.run(
                [HERMES_BIN, *args], capture_output=True, text=True, timeout=timeout, check=False,
                stdin=subprocess.DEVNULL, env={**os.environ, "NO_COLOR": "1", "TERM": "dumb"},
            )
        except subprocess.TimeoutExpired as exc:
            raise ApiError(504, "cli_timeout", f"clawd {' '.join(args[:2])} 超时") from exc
        finally:
            if mutating:
                _clear_caches()


def _clean(out: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", out).strip()


def config_get(key: str) -> str | None:
    hit, cached = _cfg_cache_get(f"s:{key}")
    if hit:
        return cached
    stamp = _cfg_stamp()  # 先取戳再读：读的过程中文件被改，下次比对就会失配而重读
    proc = _hermes("config", "get", key)
    out = _clean(proc.stdout)
    # 未设置的键：hermes 打印提示并以非 0 退出，这不是错误。
    if "Config key not set" in out or "Config key not set" in proc.stderr:
        _cfg_cache_put(f"s:{key}", stamp, None)
        return None
    if proc.returncode != 0:
        raise ApiError(500, "config_get_failed", _clean(proc.stderr) or out or "读取配置失败")
    _cfg_cache_put(f"s:{key}", stamp, out)
    return out


def config_set(key: str, value: str) -> dict[str, Any]:
    if not CONFIG_KEY_RE.match(key):
        raise ApiError(400, "invalid_key", "配置项名称不合法")
    if _is_secret_key(key):
        raise ApiError(403, "secret_key", "密钥类配置不允许在面板里修改")
    before = config_get(key)
    proc = _hermes("config", "set", key, value)
    out = _clean(proc.stdout + "\n" + proc.stderr)
    if proc.returncode != 0:
        raise ApiError(400, "config_set_failed", out or "写入配置失败")
    # Hermes 对未知的顶层键只警告（⚠）仍然写入（实测）。面板不允许写未知键：立即复原。
    if "⚠" in out:
        undo = _hermes("config", "unset", key) if before is None else _hermes("config", "set", key, before)
        if undo.returncode != 0:
            log.error("failed to roll back unknown key %s: %s", key, _clean(undo.stdout + undo.stderr))
            raise ApiError(500, "config_rollback_failed", f"clawd 不认识 {key}，且自动撤回失败，请手工检查 config.yaml")
        raise ApiError(400, "unknown_key", f"clawd 不认识配置项 {key}，未写入")
    log.info("config set %s via panel", key)
    return {"ok": True, "key": key, "value": config_get(key), "output": redact(out)}


def _clean_yaml_key(raw: str) -> str:
    """清理并标准化 YAML 映射键名。"""
    k = raw.strip()
    if (k.startswith('"') and k.endswith('"')) or (k.startswith("'") and k.endswith("'")):
        k = k[1:-1].strip()
    if "\\" in k:
        try:
            k = re.sub(r"\\x([0-9a-fA-F]{2})", lambda m: chr(int(m.group(1), 16)), k)
            k = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), k)
        except Exception as exc:
            log.warning("YAML 键转义失败，保留原键：%s", type(exc).__name__)
    return k


_ALLOWED_YAML_TAGS = {
    "tag:yaml.org,2002:null",
    "tag:yaml.org,2002:bool",
    "tag:yaml.org,2002:int",
    "tag:yaml.org,2002:float",
    "tag:yaml.org,2002:binary",
    "tag:yaml.org,2002:timestamp",
    "tag:yaml.org,2002:omap",
    "tag:yaml.org,2002:pairs",
    "tag:yaml.org,2002:set",
    "tag:yaml.org,2002:str",
    "tag:yaml.org,2002:seq",
    "tag:yaml.org,2002:map",
}


def _validate_safe_yaml_ast(node: Any, *, max_nodes: int = 5000, max_depth: int = 30) -> None:
    """遍历校验 YAML AST：深度限制、节点总数限制、防循环别名、禁止恶意自定义tag、复杂键及重复键。"""
    node_count = 0
    visiting_ids: set[int] = set()

    def _walk(curr: Any, depth: int) -> None:
        nonlocal node_count
        if depth > max_depth:
            raise ValueError("YAML 嵌套层级超出安全上限")
        node_count += 1
        if node_count > max_nodes:
            raise ValueError("YAML 节点数量超出安全上限")

        curr_id = id(curr)
        if curr_id in visiting_ids:
            raise ValueError("检测到 YAML 递归或循环别名引用，拒绝解析")
        visiting_ids.add(curr_id)

        try:
            tag = getattr(curr, "tag", None)
            if tag not in _ALLOWED_YAML_TAGS:
                raise ValueError(f"禁止不受支持或自定义的 YAML 标签：{tag}")

            if isinstance(curr, yaml.MappingNode):
                seen_keys: set[str] = set()
                for k_node, v_node in curr.value:
                    if not isinstance(k_node, yaml.ScalarNode):
                        raise ValueError("禁止使用复杂类型（列表或映射）作为键名")
                    cleaned_k = _clean_yaml_key(k_node.value)
                    if cleaned_k in seen_keys:
                        raise ValueError(f"YAML 包含重复键名：{cleaned_k}")
                    seen_keys.add(cleaned_k)
                    _walk(k_node, depth + 1)
                    _walk(v_node, depth + 1)
            elif isinstance(curr, yaml.SequenceNode):
                for item in curr.value:
                    _walk(item, depth + 1)
        finally:
            visiting_ids.remove(curr_id)

    _walk(node, 0)


def _redact_yaml_tree(root_node: Any) -> Any:
    """在 AST 层级定位所有敏感键及其关联节点（含别名/共享引用），将对应值节点替换为脱敏标量。"""
    secret_node_ids: set[int] = set()
    visiting_ids: set[int] = set()

    def _scan(curr: Any, is_sec: bool) -> None:
        if curr is None:
            return
        curr_id = id(curr)
        if is_sec:
            secret_node_ids.add(curr_id)
            if curr_id in visiting_ids:
                return
            visiting_ids.add(curr_id)
            try:
                if isinstance(curr, yaml.MappingNode):
                    for k_node, v_node in curr.value:
                        secret_node_ids.add(id(k_node))
                        _scan(v_node, True)
                elif isinstance(curr, yaml.SequenceNode):
                    for item in curr.value:
                        _scan(item, True)
            finally:
                visiting_ids.remove(curr_id)
            return

        if curr_id in visiting_ids:
            return
        visiting_ids.add(curr_id)
        try:
            if isinstance(curr, yaml.MappingNode):
                for k_node, v_node in curr.value:
                    k_str = k_node.value if isinstance(k_node, yaml.ScalarNode) and isinstance(k_node.value, str) else ""
                    sec = _is_secret_key(_clean_yaml_key(k_str))
                    _scan(v_node, sec)
            elif isinstance(curr, yaml.SequenceNode):
                for item in curr.value:
                    _scan(item, False)
        finally:
            visiting_ids.remove(curr_id)

    _scan(root_node, False)

    rebuilt_cache: dict[int, Any] = {}

    def _rebuild(curr: Any) -> Any:
        curr_id = id(curr)
        if curr_id in secret_node_ids:
            return yaml.ScalarNode(tag="tag:yaml.org,2002:str", value="<已隐藏>")
        if curr_id in rebuilt_cache:
            return rebuilt_cache[curr_id]

        if isinstance(curr, yaml.MappingNode):
            new_pairs: list[tuple[Any, Any]] = []
            new_map = yaml.MappingNode(tag=curr.tag, value=new_pairs, flow_style=curr.flow_style)
            rebuilt_cache[curr_id] = new_map
            for k_node, v_node in curr.value:
                new_pairs.append((_rebuild(k_node), _rebuild(v_node)))
            return new_map
        elif isinstance(curr, yaml.SequenceNode):
            new_items: list[Any] = []
            new_seq = yaml.SequenceNode(tag=curr.tag, value=new_items, flow_style=curr.flow_style)
            rebuilt_cache[curr_id] = new_seq
            for item in curr.value:
                new_items.append(_rebuild(item))
            return new_seq
        return curr

    return _rebuild(root_node)


def _redact_yaml(text: str) -> str:
    """使用锁定版 PyYAML 安全解析并脱敏 YAML 配置。
    绝不返回原文，解析失败或缺依赖时抛出固定结构化 ApiError。
    """
    if yaml is None:
        raise ApiError(503, "yaml_parser_unavailable", "YAML 解析组件未安装，无法安全脱敏配置")

    max_input_bytes = 1_048_576
    if len(text) > max_input_bytes or len(text.encode("utf-8")) > max_input_bytes:
        raise ApiError(500, "config_parse_failed", "配置内容超过安全大小上限，未返回原文")
    if not text.strip():
        return text

    try:
        docs = list(yaml.compose_all(text, Loader=yaml.SafeLoader))
    except Exception as exc:
        log.warning("YAML 安全解析失败（已阻断返回原文）：%s", type(exc).__name__)
        raise ApiError(500, "config_parse_failed", "配置内容不是合法的 YAML 格式，未返回原文") from None

    if len(docs) > 1:
        raise ApiError(500, "config_parse_failed", "配置包含多文档（Multi-document YAML），未返回原文")

    if not docs or docs[0] is None:
        return ""

    doc = docs[0]
    try:
        _validate_safe_yaml_ast(doc)
    except Exception as exc:
        log.warning("YAML AST 安全校验未通过（已阻断返回原文）：%s", type(exc).__name__)
        raise ApiError(500, "config_parse_failed", "配置结构未通过安全约束校验，未返回原文") from None

    try:
        redacted_doc = _redact_yaml_tree(doc)
        _validate_safe_yaml_ast(redacted_doc)
        serialized = yaml.serialize(redacted_doc, allow_unicode=True)
        if not serialized.endswith("\n"):
            serialized += "\n"
        return serialized
    except Exception as exc:
        log.warning("YAML 序列化脱敏失败（已阻断返回原文）：%s", type(exc).__name__)
        raise ApiError(500, "config_parse_failed", "配置序列化失败，未返回原文") from None


def get_config() -> dict[str, Any]:
    path = HERMES_HOME / "config.yaml"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ApiError(500, "config_read_failed", f"读取 {path} 失败：{exc.strerror}") from exc
    return {"path": str(path), "yaml": _redact_yaml(text), "modifiedAt": path.stat().st_mtime}


# ---------------------------------------------------------------- 技能


def _front_matter(text: str) -> dict[str, str]:
    """只取顶层标量字段（name/description/version 等）；嵌套块忽略。"""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    fields: dict[str, str] = {}
    for line in text[3:end].splitlines():
        m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if m and m.group(2) and not m.group(2).startswith(("|", ">")):
            fields[m.group(1)] = m.group(2).strip().strip("'\"")
    return fields


def _disabled_skills() -> set[str]:
    raw = config_get("skills.disabled")
    if not raw or raw in ("[]", "null"):
        return set()
    if raw.startswith("["):
        try:
            return {str(x) for x in json.loads(raw)}
        except json.JSONDecodeError:
            pass
    return {line.lstrip("- ").strip().strip("'\"") for line in raw.splitlines() if line.strip().startswith("-")}


def _skill_files() -> list[Path]:
    root = HERMES_HOME / "skills"
    return sorted(p for p in root.rglob("SKILL.md") if ".hub" not in p.parts) if root.is_dir() else []


def list_skills() -> dict[str, Any]:
    root = HERMES_HOME / "skills"
    disabled = _disabled_skills()
    skills = []
    for path in _skill_files():
        rel = path.parent.relative_to(root)
        try:
            meta = _front_matter(path.read_text(encoding="utf-8", errors="replace")[:8192])
        except OSError:
            continue
        name = meta.get("name") or rel.name
        skills.append({
            "name": name,
            "category": rel.parts[0] if len(rel.parts) > 1 else "",
            "description": meta.get("description", ""),
            "version": meta.get("version", ""),
            "path": str(rel),
            "enabled": name not in disabled,
            "modifiedAt": path.stat().st_mtime,
        })
    return {"data": skills, "disabled": sorted(disabled)}


SKILL_REL_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-]*(?:/[A-Za-z0-9_][A-Za-z0-9_.\-]*){0,4}$")
SKILL_MAX_BYTES = 256 * 1024
SKILL_BACKUP_KEEP = 20
# 备份放在 skills/ 之外：放在里面会被 Hermes 当成新技能加载。
SKILL_BACKUP_DIR = HERMES_HOME / "skill-backups"
SKILL_BACKUP_ID_RE = re.compile(r"^\d{8}-\d{6}(?:-\d+)?\.md$")


def _skill_file(rel: str) -> Path:
    """技能目录（相对 skills/）→ 其 SKILL.md 的真实路径；只允许已存在的、非 .hub 的技能。"""
    if not isinstance(rel, str) or not SKILL_REL_RE.match(rel) or ".." in rel.split("/") or ".hub" in rel.split("/"):
        raise ApiError(400, "invalid_skill_path", "技能路径不合法")
    root = (HERMES_HOME / "skills").resolve()
    path = (root / rel / "SKILL.md").resolve()
    if root not in path.parents or not path.is_file():
        raise ApiError(404, "skill_not_found", "技能不存在")
    return path


def get_skill(rel: str) -> dict[str, Any]:
    path = _skill_file(rel)
    return {"path": rel, "content": path.read_text(encoding="utf-8", errors="replace"), "modifiedAt": path.stat().st_mtime}


def _backup_dir(rel: str) -> Path:
    return SKILL_BACKUP_DIR / rel.replace("/", "__")


def save_skill(rel: str, content: Any, base_modified_at: Any) -> dict[str, Any]:
    """保存技能全文。保存前备份旧版；文件在打开之后被改过则拒绝（防止盖掉 Hermes 自己的修改）；技能名不允许被改。"""
    path = _skill_file(rel)
    if not isinstance(content, str) or "\x00" in content:
        raise ApiError(400, "invalid_content", "内容必须是文本")
    data = content.replace("\r\n", "\n")
    if not data.endswith("\n"):
        data += "\n"
    raw = data.encode("utf-8")
    if len(raw) > SKILL_MAX_BYTES:
        raise ApiError(413, "too_large", f"技能文件不能超过 {SKILL_MAX_BYTES // 1024}KB")
    if not isinstance(base_modified_at, (int, float)) or isinstance(base_modified_at, bool):
        raise ApiError(400, "invalid_body", "缺少 baseModifiedAt")
    new_meta = _front_matter(data)
    if not data.startswith("---") or data.find("\n---", 3) == -1 or not new_meta.get("name"):
        raise ApiError(400, "invalid_front_matter", "文件开头必须保留以 --- 包裹的头信息，且包含 name")
    with _WRITE_LOCK:
        st = path.stat()
        if abs(st.st_mtime - float(base_modified_at)) > 0.001:
            raise ApiError(409, "modified_elsewhere", "这个技能在你打开之后被修改过（可能是 clawd 自己改的），请重新载入后再改")
        old = path.read_text(encoding="utf-8", errors="replace")
        old_name = _front_matter(old).get("name")
        if old_name and new_meta.get("name") != old_name:
            raise ApiError(400, "name_changed", f"不允许在这里修改技能名（当前为 {old_name}）")
        if old.replace("\r\n", "\n") == data:
            return {"ok": True, "unchanged": True, "modifiedAt": st.st_mtime}
        bdir = _backup_dir(rel)
        bdir.mkdir(mode=0o700, parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = bdir / f"{stamp}.md"
        n = 1
        while backup.exists():
            backup = bdir / f"{stamp}-{n}.md"
            n += 1
        shutil.copy2(path, backup)
        for stale in sorted(bdir.glob("*.md"))[:-SKILL_BACKUP_KEEP]:
            stale.unlink(missing_ok=True)
        tmp = path.with_name(f".SKILL.md.panel-{os.getpid()}")
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, st.st_mode & 0o777)
            with os.fdopen(fd, "wb") as f:
                f.write(raw)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
        new_mtime = path.stat().st_mtime
    log.info("skill %s edited via panel (%d bytes, backup %s)", rel, len(raw), backup.name)
    return {"ok": True, "unchanged": False, "modifiedAt": new_mtime, "backup": backup.name}


def list_skill_backups(rel: str) -> dict[str, Any]:
    _skill_file(rel)
    bdir = _backup_dir(rel)
    items = []
    for f in sorted(bdir.glob("*.md"), reverse=True) if bdir.is_dir() else []:
        if SKILL_BACKUP_ID_RE.match(f.name):
            st = f.stat()
            items.append({"id": f.name, "at": st.st_mtime, "size": st.st_size})
    return {"data": items}


def get_skill_backup(rel: str, backup_id: str) -> dict[str, Any]:
    _skill_file(rel)
    if not SKILL_BACKUP_ID_RE.match(backup_id):
        raise ApiError(400, "invalid_backup", "备份编号不合法")
    f = _backup_dir(rel) / backup_id
    if not f.is_file():
        raise ApiError(404, "backup_not_found", "备份不存在")
    return {"id": backup_id, "content": f.read_text(encoding="utf-8", errors="replace")}


def set_skill_enabled(name: str, enabled: bool) -> dict[str, Any]:
    if not SKILL_NAME_RE.match(name):
        raise ApiError(400, "invalid_skill", "技能名不合法")
    known = {s["name"] for s in list_skills()["data"]}
    if name not in known:
        raise ApiError(404, "skill_not_found", "技能不存在")
    disabled = _disabled_skills()
    disabled = disabled - {name} if enabled else disabled | {name}
    if disabled:
        proc = _hermes("config", "set", "skills.disabled", json.dumps(sorted(disabled)))
    else:
        proc = _hermes("config", "unset", "skills.disabled")
    if proc.returncode != 0:
        raise ApiError(500, "skill_toggle_failed", _clean(proc.stderr + proc.stdout) or "切换失败")
    actual = _disabled_skills()
    if (name in actual) == enabled:
        raise ApiError(500, "skill_toggle_unverified", "已提交但回读结果不一致，请刷新确认")
    log.info("skill %s %s via panel", name, "enabled" if enabled else "disabled")
    return {"ok": True, "name": name, "enabled": enabled}


# ---------------------------------------------------------------- 文件

# CLAWD_HOME 已设置时，默认的 /root/hermes-uploads 与 /root/hermes-workspace 不能再当兜底根。
_PRODUCTION_TEXT_MARKERS = ("/root/.hermes", "/usr/local/lib/hermes-agent", "/usr/local/bin/hermes")
_ROOT_INJECT_SUFFIX = {
    "uploads": "uploads/",
    "workspace": "workspace/",
    "images": "images/",
    "cron": "cron/output/",
}


def _clawd_home_raw() -> str:
    return os.environ.get("CLAWD_HOME", "").strip()


def _segment_denied(name: str) -> bool:
    """点文件、拒绝表、助手凭据容器、密钥名。threshold_tokens 不是密钥。"""
    if not name or name in {".", ".."} or name.startswith("."):
        return True
    return bool(DENY_NAME_RE.search(name) or ARTIFACT_SECRET_RE.search(name) or _is_secret_name(name))


def _secret_segment(name: str) -> bool:
    """密钥文件名。不含普通点文件：工作区浏览仍要列出 .gitignore。"""
    return bool(name) and bool(DENY_NAME_RE.search(name) or _is_secret_name(name))


def _home_rel_denied(root_id: str, parts: tuple[str, ...] | list[str]) -> bool:
    """home 模式下的相对路径策略。`..` 留给下载的 invalid_path，不在这里改成 denied。"""
    named = [part for part in parts if part not in {".", ".."}]
    if any(_secret_segment(part) for part in named):
        return True
    if root_id == "workspace" and any(part in _WORKSPACE_BROWSE_DENY_PARTS for part in parts):
        return True
    if root_id != "workspace" and any(_segment_denied(part) for part in named):
        return True
    return False


def _link_text_denied(root_id: str, link: str) -> bool:
    """只读链接文本，不打开目标。"""
    parts = Path(link).parts
    if root_id == "workspace" and any(part in _WORKSPACE_BROWSE_DENY_PARTS for part in parts):
        return True
    return any(_secret_segment(part) for part in parts if part not in {".", ".."})


def _browse_parts(rel: str) -> list[str]:
    """列表用的词法段。允许 `..`，拒绝绝对路径和控制字符。"""
    if (rel.startswith(("/", "\\")) or "\\" in rel or "\x00" in rel
            or any(ord(c) < 32 or ord(c) == 127 for c in rel)):
        raise ApiError(400, "invalid_path", "路径不合法")
    return [part for part in rel.split("/") if part and part != "."]


def _normalize_inside(parts: list[str]) -> list[str]:
    normalized: list[str] = []
    for part in parts:
        if part == "..":
            if not normalized:
                raise ApiError(403, "outside_root", "不允许访问该路径")
            normalized.pop()
            continue
        normalized.append(part)
    return normalized


def _require_nofollow() -> int:
    flag = getattr(os, "O_NOFOLLOW", 0)
    if not flag:
        raise ApiError(500, "nofollow_unavailable", "当前系统没有 O_NOFOLLOW，拒绝打开附件")
    return flag


def _production_text(text: str) -> bool:
    return any(text == marker or text.startswith(marker + "/") for marker in _PRODUCTION_TEXT_MARKERS)


def _legacy_file_root(path: Path) -> bool:
    text = str(path)
    if text in {"/root/hermes-uploads", "/root/hermes-workspace", "/root/.hermes"}:
        return True
    return text.startswith("/root/hermes-uploads/") or text.startswith("/root/.hermes/")


def _close_fd(fd: int | None) -> None:
    if fd is None or fd < 0:
        return
    try:
        os.close(fd)
    except OSError:
        pass


def _proc_fd_path(fd: int) -> Path:
    try:
        target = os.readlink(f"/proc/self/fd/{fd}")
    except OSError as exc:
        raise ApiError(500, "fd_identity_unavailable", "无法证明已打开目录的身份，拒绝继续") from exc
    if not target.startswith("/") or target.startswith("/proc/") or " (deleted)" in target:
        raise ApiError(500, "fd_identity_unavailable", "无法证明已打开目录的身份，拒绝继续")
    if _production_text(target) or _legacy_file_root(Path(target)):
        raise ApiError(403, "denied", "该文件不允许通过面板访问")
    return Path(target)


def _home_path() -> Path:
    raw = _clawd_home_raw()
    if not raw:
        raise ApiError(500, "home_unavailable", "CLAWD_HOME 未设置，拒绝使用生产文件根")
    home = Path(raw)
    if not home.is_absolute() or home == Path("/") or any(part in {"", ".", ".."} for part in home.parts):
        raise ApiError(403, "invalid_home", "CLAWD_HOME 不是可用的绝对路径")
    if _production_text(raw) or _legacy_file_root(home):
        raise ApiError(403, "denied", "该文件不允许通过面板访问")
    return home


def _open_dir_at(parent_fd: int, name: str, *, missing: str) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | _require_nofollow() | os.O_CLOEXEC
    try:
        return os.open(name, flags, dir_fd=parent_fd)
    except OSError as exc:
        if exc.errno == errno.ENOENT:
            raise ApiError(404, missing, "目录不存在") from exc
        raise ApiError(403, "unsafe_path", "不允许访问该路径") from exc


def _open_home_fd(home: Path) -> int:
    """打开当前 home。只有 home 自己的最后一段可以是符号链接，其余段 O_NOFOLLOW。"""
    nofollow = _require_nofollow()
    flags = os.O_RDONLY | os.O_DIRECTORY | nofollow | os.O_CLOEXEC
    follow = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
    try:
        fd = os.open("/", flags)
    except OSError as exc:
        raise ApiError(500, "home_unavailable", "无法打开路径根，拒绝继续") from exc
    parts = home.parts[1:]
    try:
        for index, part in enumerate(parts):
            leaf = index == len(parts) - 1
            try:
                child = os.open(part, flags, dir_fd=fd)
            except OSError as exc:
                if not leaf or exc.errno not in {errno.ELOOP, errno.EPERM, errno.ENOTDIR}:
                    if exc.errno == errno.ENOENT:
                        raise ApiError(404, "root_missing", "CLAWD_HOME 不存在") from exc
                    raise ApiError(403, "unsafe_path", "不允许访问该路径") from exc
                try:
                    child = os.open(part, follow, dir_fd=fd)
                except OSError as follow_exc:
                    raise ApiError(403, "unsafe_path", "不允许访问该路径") from follow_exc
            _close_fd(fd)
            fd = child
        identity = _proc_fd_path(fd)
        if _production_text(str(identity)):
            raise ApiError(403, "denied", "该文件不允许通过面板访问")
        return fd
    except Exception:
        _close_fd(fd)
        raise


def _walk_from(start_fd: int, parts: list[str], *, create: bool, missing: str) -> int:
    current = os.dup(start_fd)
    try:
        for part in parts:
            if part in {"", ".", ".."} or _segment_denied(part):
                raise ApiError(403, "denied", "该文件不允许通过面板访问")
            try:
                nxt = _open_dir_at(current, part, missing=missing)
            except ApiError as exc:
                if not create or exc.status != 404:
                    raise
                try:
                    os.mkdir(part, 0o700, dir_fd=current)
                except FileExistsError:
                    pass
                nxt = _open_dir_at(current, part, missing=missing)
            _close_fd(current)
            current = nxt
        return current
    except Exception:
        _close_fd(current)
        raise


def _open_root_fd(root_id: str) -> tuple[int, Path, dict[str, Any]]:
    """每次打开/上传重新验证根。根目录换成越出 home 的符号链接时 O_NOFOLLOW 拒绝。"""
    spec = FILE_ROOTS.get(root_id)
    if spec is None:
        raise ApiError(404, "unknown_root", "未知的目录")
    home = _home_path()
    configured = Path(spec["path"])
    if _legacy_file_root(configured) or _production_text(str(configured)):
        raise ApiError(403, "legacy_root", "CLAWD_HOME 已设置，拒绝生产文件根")
    try:
        relative = configured.relative_to(home)
    except ValueError as exc:
        raise ApiError(403, "outside_root", "文件根不在当前 CLAWD_HOME 内") from exc
    if not relative.parts or any(part in {"", ".", ".."} or part.startswith(".") for part in relative.parts):
        raise ApiError(403, "outside_root", "文件根不在当前 CLAWD_HOME 内")
    home_fd = _open_home_fd(home)
    root_fd = None
    try:
        root_fd = _walk_from(home_fd, list(relative.parts), create=bool(spec.get("create")), missing="root_missing")
        identity = _proc_fd_path(root_fd)
        home_identity = _proc_fd_path(home_fd)
        if identity != home_identity and home_identity not in identity.parents:
            raise ApiError(403, "outside_root", "文件根不在当前 CLAWD_HOME 内")
        if _production_text(str(identity)) or _legacy_file_root(identity):
            raise ApiError(403, "denied", "该文件不允许通过面板访问")
        return root_fd, configured, spec
    except Exception:
        _close_fd(root_fd)
        raise
    finally:
        _close_fd(home_fd)


def _injected_prefix(home: Path, root_id: str) -> str:
    """跟 clawd_server.server._artifact_roots 的活拼写对齐，不在这里改注入 schema。"""
    suffix = _ROOT_INJECT_SUFFIX.get(root_id)
    if not suffix:
        raise ApiError(500, "artifact_roots_unavailable", "未知文件根，拒绝返回成功")
    try:
        from clawd_server.server import _artifact_roots
        roots = _artifact_roots(home)
    except Exception as exc:
        raise ApiError(500, "artifact_roots_unavailable", "无法读取附件根注入，拒绝返回成功") from exc
    if not isinstance(roots, list):
        raise ApiError(500, "artifact_roots_unavailable", "附件根注入不可用，拒绝返回成功")
    matches = [root for root in roots if isinstance(root, str) and root.endswith("/" + suffix)]
    if len(matches) != 1 or not matches[0].startswith("/") or ".." in matches[0]:
        raise ApiError(500, "artifact_roots_unavailable", "附件根注入与当前根不一致，拒绝返回成功")
    return matches[0]


def _abs_for_injection(home: Path, root_id: str, relative: str, file_fd: int) -> str:
    prefix = _injected_prefix(home, root_id)
    if not prefix.endswith("/"):
        raise ApiError(500, "artifact_roots_unavailable", "附件根注入缺少路径分隔，拒绝返回成功")
    abs_path = prefix + relative
    try:
        st = os.stat(abs_path, follow_symlinks=True)
        opened = os.fstat(file_fd)
    except OSError as exc:
        raise ApiError(500, "artifact_root_mismatch", "上传路径与已打开文件不一致，拒绝返回成功") from exc
    if st.st_dev != opened.st_dev or st.st_ino != opened.st_ino or not stat.S_ISREG(opened.st_mode):
        raise ApiError(500, "artifact_root_mismatch", "上传路径与已打开文件不一致，拒绝返回成功")
    if _production_text(abs_path):
        raise ApiError(403, "denied", "该文件不允许通过面板访问")
    return abs_path


def _resolved_inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _match_home_artifact(absolute: str) -> tuple[str, str]:
    """解析后的 absPath 必须落在当前 home 的解析根内。打开仍走根 fd，不按解析字符串再开一次。"""
    target = Path(absolute)
    if any(target == item or item in target.parents for item in DENY_PREFIXES) or _production_text(absolute):
        raise ApiError(403, "denied", "该文件不允许通过面板访问")
    home = _home_path()
    try:
        resolved_home = home.resolve()
        resolved_target = target.resolve()
    except (OSError, RuntimeError) as exc:
        raise ApiError(403, "unsafe_path", "不允许访问该路径") from exc
    if _production_text(str(resolved_home)) or _production_text(str(resolved_target)):
        raise ApiError(403, "denied", "该文件不允许通过面板访问")
    if not _resolved_inside(resolved_target, resolved_home):
        raise ApiError(403, "outside_root", "不允许访问该路径")
    for root_id, spec in FILE_ROOTS.items():
        configured = Path(spec["path"])
        if _legacy_file_root(configured) or _production_text(str(configured)):
            continue
        try:
            configured.relative_to(home)
        except ValueError:
            continue
        try:
            at_root = target.relative_to(configured)
        except ValueError:
            at_root = None
        if at_root is not None and not at_root.parts:
            raise ApiError(404, "not_found", "文件不存在")
        lexical = None
        try:
            rel = target.relative_to(configured)
            if rel.parts and all(part not in {".", ".."} for part in rel.parts):
                lexical = rel
        except ValueError:
            lexical = None
        try:
            resolved_root = configured.resolve()
        except (OSError, RuntimeError):
            continue
        if not _resolved_inside(resolved_root, resolved_home) or _production_text(str(resolved_root)):
            continue
        try:
            resolved_rel = resolved_target.relative_to(resolved_root)
        except ValueError:
            continue
        if not resolved_rel.parts or any(part in {".", ".."} for part in resolved_rel.parts):
            continue
        chosen = lexical if lexical is not None else resolved_rel
        if any(_segment_denied(part) for part in chosen.parts):
            raise ApiError(403, "denied", "该文件不允许通过面板访问")
        return root_id, chosen.as_posix()
    raise ApiError(403, "outside_root", "不允许访问该路径")


def _reject_raw_upload_name(raw: str) -> str:
    if not isinstance(raw, str) or "\x00" in raw or "/" in raw or "\\" in raw:
        raise ApiError(400, "invalid_name", "文件名不合法")
    visible = raw.strip()
    if not visible or visible.startswith(".") or _segment_denied(visible):
        raise ApiError(400, "invalid_name", "文件名不合法")
    cleaned = UPLOAD_NAME_RE.sub("_", raw).strip(" .")[:180]
    if not cleaned or cleaned.startswith(".") or _segment_denied(cleaned) or DENY_NAME_RE.search(cleaned):
        raise ApiError(400, "invalid_name", "文件名不合法")
    return cleaned


def _root(root_id: str) -> tuple[Path, dict[str, Any]]:
    spec = FILE_ROOTS.get(root_id)
    if spec is None:
        raise ApiError(404, "unknown_root", "未知的目录")
    if _clawd_home_raw():
        fd, _configured, checked = _open_root_fd(root_id)
        try:
            return _proc_fd_path(fd), checked
        finally:
            _close_fd(fd)
    base: Path = spec["path"]
    if spec["create"]:
        base.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not base.is_dir():
        raise ApiError(404, "root_missing", f"{spec['label']} 目录不存在")
    return base.resolve(), spec


def _safe_path(root_id: str, rel: str) -> tuple[Path, Path, dict[str, Any]]:
    """rel → 根内的绝对路径。resolve() 之后校验仍在根内（挡 ../、编码变体与符号链接逃逸），并套用黑名单。"""
    base, spec = _root(root_id)
    if "\x00" in rel or rel.startswith(("/", "\\")):
        raise ApiError(400, "invalid_path", "路径不合法")
    if _clawd_home_raw() and _home_rel_denied(root_id, Path(rel).parts):
        raise ApiError(403, "denied", "该文件不允许通过面板访问")
    target = (base / rel).resolve()
    if target != base and base not in target.parents:
        raise ApiError(403, "outside_root", "不允许访问该路径")
    if any(target == p or p in target.parents for p in DENY_PREFIXES) or any(DENY_NAME_RE.search(part) for part in target.relative_to(base).parts):
        raise ApiError(403, "denied", "该文件不允许通过面板访问")
    return base, target, spec


def _workspace_browse_denied(root_id: str, rel: str, base: Path, resolved: Path) -> bool:
    """工作区浏览是否点到 .git 或 hermes-panel-state（含 .. 解析和符号链接落点）。"""
    if root_id != "workspace":
        return False
    if any(part in _WORKSPACE_BROWSE_DENY_PARTS for part in Path(rel).parts):
        return True
    try:
        relative = resolved.relative_to(base)
    except ValueError:
        return False
    return any(part in _WORKSPACE_BROWSE_DENY_PARTS for part in relative.parts)


def _reject_workspace_browse(root_id: str, rel: str) -> None:
    """在 /files 下载打开字节前拒绝。不放进 _open_download，避免改变 artifact。"""
    if root_id != "workspace":
        return
    try:
        base, resolved, _spec = _safe_path(root_id, rel)
    except RuntimeError as exc:  # 与 _open_download 一样，符号链接环失败即关闭。
        raise ApiError(403, "unsafe_path", "不允许访问该路径") from exc
    if _workspace_browse_denied(root_id, rel, base, resolved):
        raise ApiError(403, "denied", "该文件不允许通过面板访问")


@contextlib.contextmanager
def _open_download_home(root_id: str, rel: str) -> Iterator[tuple[BinaryIO, os.stat_result, Path]]:
    """从已验证的根 fd 用 openat/O_NOFOLLOW 打开。不在 resolve 之后再按不可信词法路径打开。"""
    if (rel.startswith(("/", "\\")) or "\\" in rel
            or any(ord(c) < 32 or ord(c) == 127 for c in rel)
            or ".." in rel.split("/")):
        raise ApiError(400, "invalid_path", "路径不合法")
    parts = [part for part in rel.split("/") if part]
    if _home_rel_denied(root_id, parts):
        raise ApiError(403, "denied", "该文件不允许通过面板访问")
    try:
        _base, _resolved, spec = _safe_path(root_id, rel)
    except RuntimeError as exc:
        raise ApiError(403, "unsafe_path", "不允许访问该路径") from exc
    if not parts:
        raise ApiError(404, "not_found", "文件不存在")
    root_fd, _configured, spec = _open_root_fd(root_id)
    dir_fd = file_fd = None
    try:
        dir_fd = _walk_from(root_fd, parts[:-1], create=False, missing="not_found") if len(parts) > 1 else os.dup(root_fd)
        flags = os.O_RDONLY | _require_nofollow() | os.O_NONBLOCK | os.O_CLOEXEC
        try:
            file_fd = os.open(parts[-1], flags, dir_fd=dir_fd)
        except OSError as exc:
            if exc.errno == errno.ENOENT:
                raise ApiError(404, "not_found", "文件不存在") from exc
            raise ApiError(403, "unsafe_file", "无法安全打开该文件") from exc
        info = os.fstat(file_fd)
        if stat.S_ISDIR(info.st_mode):
            raise ApiError(404, "not_found", "文件不存在")
        if not stat.S_ISREG(info.st_mode):
            raise ApiError(403, "unsafe_file", "只能下载普通文件")
        identity = _proc_fd_path(file_fd)
        if _production_text(str(identity)):
            raise ApiError(403, "denied", "该文件不允许通过面板访问")
        lexical = Path(spec["path"]) / rel
        with os.fdopen(file_fd, "rb") as stream:
            file_fd = None
            yield stream, info, lexical
    finally:
        _close_fd(file_fd)
        _close_fd(dir_fd)
        _close_fd(root_fd)


@contextlib.contextmanager
def _open_download(root_id: str, rel: str) -> Iterator[tuple[BinaryIO, os.stat_result, Path]]:
    """Keep validation and bytes tied to a no-follow descriptor, not a second path lookup."""
    if _clawd_home_raw():
        with _open_download_home(root_id, rel) as opened:
            yield opened
        return
    if (rel.startswith(("/", "\\")) or "\\" in rel
            or any(ord(c) < 32 or ord(c) == 127 for c in rel)
            or ".." in rel.split("/")):
        raise ApiError(400, "invalid_path", "路径不合法")
    directory_fd = file_fd = None
    try:
        try:
            _base, resolved, spec = _safe_path(root_id, rel)
            lexical = spec["path"] / rel
            for target in (lexical, resolved):
                if (any(target == p or p in target.parents for p in DENY_PREFIXES)
                        or any(DENY_NAME_RE.search(part) for part in target.parts)):
                    raise ApiError(403, "denied", "该文件不允许通过面板访问")
            # Walk the configured root's ancestors too; never silently trust a symlinked root.
            directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
            directory_fd = os.open("/", directory_flags)
            for part in lexical.parts[1:-1]:
                child_fd = os.open(part, directory_flags, dir_fd=directory_fd)
                os.close(directory_fd)
                directory_fd = child_fd
            file_fd = os.open(lexical.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                              dir_fd=directory_fd)
            info = os.fstat(file_fd)
            if stat.S_ISDIR(info.st_mode):
                raise ApiError(404, "not_found", "文件不存在")
            if not stat.S_ISREG(info.st_mode):
                raise ApiError(403, "unsafe_file", "只能下载普通文件")
        except RuntimeError as exc:  # Path.resolve detects a symlink loop.
            raise ApiError(403, "unsafe_path", "不允许访问该路径") from exc
        except OSError as exc:
            if exc.errno == errno.ENOENT:
                raise ApiError(404, "not_found", "文件不存在") from exc
            raise ApiError(403, "unsafe_file", "无法安全打开该文件") from exc
        finally:
            if directory_fd is not None:
                os.close(directory_fd)
        with os.fdopen(file_fd, "rb") as stream:
            file_fd = None  # fdopen owns the descriptor from here.
            yield stream, info, lexical
    finally:
        if file_fd is not None:
            os.close(file_fd)


def _raster_mime(name: str, header: bytes) -> str | None:
    """Only explicitly supported raster suffixes with matching magic may render inline."""
    suffix = Path(name).suffix.lower()
    if suffix == ".png" and header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if suffix in (".jpg", ".jpeg") and header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if suffix == ".gif" and header[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if (suffix == ".webp" and header[:4] == b"RIFF" and header[8:12] == b"WEBP"
            and header[12:16] in (b"VP8 ", b"VP8L", b"VP8X")):
        return "image/webp"
    if suffix == ".bmp" and len(header) >= 14 and header.startswith(b"BM"):
        return "image/bmp"
    return None


def _list_names(dir_fd: int) -> list[str]:
    """列出已打开目录 fd 的名字。失败即关闭，不退回词法路径。"""
    dup = os.dup(dir_fd)
    try:
        try:
            return os.listdir(f"/proc/self/fd/{dup}")
        except OSError as exc:
            raise ApiError(500, "fd_identity_unavailable", "无法证明已打开目录的身份，拒绝继续") from exc
    finally:
        os.close(dup)


def _dir_abs_for_injection(home: Path, root_id: str, relative: str, dir_fd: int) -> str:
    prefix = _injected_prefix(home, root_id)
    abs_path = prefix.rstrip("/") if not relative else prefix + relative
    if _production_text(abs_path) or ".." in Path(abs_path).parts:
        raise ApiError(403, "denied", "该文件不允许通过面板访问")
    try:
        st = os.stat(abs_path, follow_symlinks=False)
        opened = os.fstat(dir_fd)
    except OSError as exc:
        raise ApiError(500, "artifact_root_mismatch", "列表路径与已打开目录不一致，拒绝返回成功") from exc
    if stat.S_ISLNK(st.st_mode) or st.st_dev != opened.st_dev or st.st_ino != opened.st_ino or not stat.S_ISDIR(opened.st_mode):
        raise ApiError(500, "artifact_root_mismatch", "列表路径与已打开目录不一致，拒绝返回成功")
    return abs_path


def _split_rel(rel: str) -> list[str]:
    if (rel.startswith(("/", "\\")) or "\\" in rel or "\x00" in rel
            or any(ord(c) < 32 or ord(c) == 127 for c in rel)
            or ".." in rel.split("/")):
        raise ApiError(400, "invalid_path", "路径不合法")
    return [part for part in rel.split("/") if part]


def _configured_listing_path(spec: dict[str, Any], parts: list[str], dir_fd: int) -> str:
    """返回已打开目录的配置路径。inode 必须与 fd 一致，不把注入拼写换掉 absPath。"""
    configured = Path(spec["path"]).joinpath(*parts) if parts else Path(spec["path"])
    try:
        st = os.stat(configured, follow_symlinks=False)
        opened = os.fstat(dir_fd)
    except OSError as exc:
        raise ApiError(500, "artifact_root_mismatch", "列表路径与已打开目录不一致，拒绝返回成功") from exc
    if stat.S_ISLNK(st.st_mode) or (st.st_dev, st.st_ino) != (opened.st_dev, opened.st_ino) or not stat.S_ISDIR(opened.st_mode):
        raise ApiError(500, "artifact_root_mismatch", "列表路径与已打开目录不一致，拒绝返回成功")
    text = str(configured)
    if _production_text(text) or ".." in configured.parts:
        raise ApiError(403, "denied", "该文件不允许通过面板访问")
    return text


def _list_dir_home(root_id: str, rel: str) -> dict[str, Any]:
    raw_parts = _browse_parts(rel)
    if _home_rel_denied(root_id, raw_parts):
        raise ApiError(403, "denied", "该文件不允许通过面板访问")
    parts = _normalize_inside(raw_parts)
    root_fd = dir_fd = parent_fd = None
    try:
        root_fd, _configured, spec = _open_root_fd(root_id)
        if parts:
            parent_fd = _walk_from(root_fd, parts[:-1], create=False, missing="not_a_directory") if len(parts) > 1 else os.dup(root_fd)
            try:
                st = os.lstat(parts[-1], dir_fd=parent_fd)
            except OSError as exc:
                if exc.errno == errno.ENOENT:
                    raise ApiError(404, "not_a_directory", "目录不存在") from exc
                raise ApiError(403, "unsafe_path", "不允许访问该路径") from exc
            if stat.S_ISLNK(st.st_mode):
                try:
                    link = os.readlink(parts[-1], dir_fd=parent_fd)
                except OSError as exc:
                    raise ApiError(403, "unsafe_path", "不允许访问该路径") from exc
                if _link_text_denied(root_id, link):
                    raise ApiError(403, "denied", "该文件不允许通过面板访问")
                raise ApiError(404, "not_a_directory", "目录不存在")
            if not stat.S_ISDIR(st.st_mode):
                raise ApiError(404, "not_a_directory", "目录不存在")
            dir_fd = _open_dir_at(parent_fd, parts[-1], missing="not_a_directory")
        else:
            dir_fd = os.dup(root_fd)
        if not stat.S_ISDIR(os.fstat(dir_fd).st_mode):
            raise ApiError(404, "not_a_directory", "目录不存在")
        identity = _proc_fd_path(dir_fd)
        if _production_text(str(identity)) or _legacy_file_root(identity):
            raise ApiError(403, "denied", "该文件不允许通过面板访问")
        names = _list_names(dir_fd)
        entries = []
        for name in names:
            if name in {".", ".."} or _secret_segment(name):
                continue
            if root_id == "workspace" and name in _WORKSPACE_BROWSE_DENY_PARTS:
                continue
            if root_id != "workspace" and _segment_denied(name):
                continue
            try:
                child = os.lstat(name, dir_fd=dir_fd)
            except OSError as exc:
                if exc.errno in {errno.ENOENT, errno.ENOTDIR}:
                    continue
                raise ApiError(500, "list_failed", "目录无法安全列出") from exc
            if stat.S_ISLNK(child.st_mode):
                try:
                    link = os.readlink(name, dir_fd=dir_fd)
                except OSError:
                    continue
                if _link_text_denied(root_id, link):
                    continue
                entries.append({"name": name, "dir": False, "size": None, "modifiedAt": child.st_mtime})
                continue
            is_dir = stat.S_ISDIR(child.st_mode)
            entries.append({
                "name": name,
                "dir": is_dir,
                "size": None if is_dir else child.st_size,
                "modifiedAt": child.st_mtime,
            })
        entries.sort(key=lambda e: (not e["dir"], -e["modifiedAt"]))
        relative = "/".join(parts)
        return {
            "root": root_id,
            "path": relative,
            "absPath": _configured_listing_path(spec, parts, dir_fd),
            "writable": spec["writable"],
            "entries": entries,
        }
    finally:
        _close_fd(dir_fd)
        _close_fd(parent_fd)
        _close_fd(root_fd)


def _list_roots_legacy() -> dict[str, Any]:
    out = []
    for rid, spec in FILE_ROOTS.items():
        p: Path = spec["path"]
        if spec["create"] or p.is_dir():
            out.append({"id": rid, "label": spec["label"], "path": str(p), "writable": spec["writable"]})
    return {"data": out}


def _list_roots_home() -> dict[str, Any]:
    home = _home_path()
    out = []
    rejected = False
    for rid in FILE_ROOTS:
        fd = None
        try:
            fd, _configured, checked = _open_root_fd(rid)
            path = _injected_prefix(home, rid).rstrip("/")
            identity = _proc_fd_path(fd)
            st = os.stat(path, follow_symlinks=False)
            opened = os.fstat(fd)
            if stat.S_ISLNK(st.st_mode) or (st.st_dev, st.st_ino) != (opened.st_dev, opened.st_ino):
                raise ApiError(500, "artifact_root_mismatch", "文件根与已打开目录不一致，拒绝返回成功")
            if _production_text(path) or _production_text(str(identity)) or _legacy_file_root(identity):
                raise ApiError(403, "denied", "该文件不允许通过面板访问")
            out.append({"id": rid, "label": checked["label"], "path": path, "writable": checked["writable"]})
        except ApiError as exc:
            if exc.status == 404:
                continue
            if exc.code in {"legacy_root", "outside_root", "denied"}:
                rejected = True
                continue
            raise
        finally:
            _close_fd(fd)
    if not out and rejected:
        raise ApiError(403, "legacy_root", "CLAWD_HOME 已设置，拒绝生产文件根")
    return {"data": out}


def list_roots() -> dict[str, Any]:
    if _clawd_home_raw():
        return _list_roots_home()
    return _list_roots_legacy()


def _list_dir_legacy(root_id: str, rel: str) -> dict[str, Any]:
    base, target, spec = _safe_path(root_id, rel)
    if _workspace_browse_denied(root_id, rel, base, target):
        raise ApiError(403, "denied", "该文件不允许通过面板访问")
    if not target.is_dir():
        raise ApiError(404, "not_a_directory", "目录不存在")
    entries = []
    for child in target.iterdir():
        if DENY_NAME_RE.search(child.name):
            continue
        try:
            st = child.stat()
            resolved = child.resolve()
        except OSError:
            continue
        if resolved != base and base not in resolved.parents:
            continue  # 指向根外的符号链接不展示
        if _workspace_browse_denied(root_id, child.name, base, resolved):
            continue
        entries.append({
            "name": child.name,
            "dir": child.is_dir(),
            "size": st.st_size if not child.is_dir() else None,
            "modifiedAt": st.st_mtime,
        })
    entries.sort(key=lambda e: (not e["dir"], -e["modifiedAt"]))
    return {"root": root_id, "path": str(target.relative_to(base)) if target != base else "", "absPath": str(target), "writable": spec["writable"], "entries": entries}


def list_dir(root_id: str, rel: str) -> dict[str, Any]:
    if _clawd_home_raw():
        return _list_dir_home(root_id, rel)
    return _list_dir_legacy(root_id, rel)


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    for i in range(1, 1000):
        cand = path.with_name(f"{stem} ({i}){suffix}")
        if not cand.exists():
            return cand
    raise ApiError(409, "name_exhausted", "同名文件过多")


def _delete_file_home(root_id: str, rel: str) -> dict[str, Any]:
    spec = FILE_ROOTS.get(root_id)
    if spec is None:
        raise ApiError(404, "unknown_root", "未知的目录")
    if not spec["writable"]:
        raise ApiError(403, "read_only", f"{spec['label']} 是只读目录")
    parts = _split_rel(rel)
    if not parts or any(_segment_denied(part) for part in parts):
        raise ApiError(400, "not_a_file", "只能删除文件")
    if root_id == "workspace" and any(part in _WORKSPACE_BROWSE_DENY_PARTS for part in parts):
        raise ApiError(403, "denied", "该文件不允许通过面板访问")
    root_fd = dir_fd = None
    try:
        root_fd, _configured, checked = _open_root_fd(root_id)
        if not checked["writable"]:
            raise ApiError(403, "read_only", f"{checked['label']} 是只读目录")
        try:
            dir_fd = _walk_from(root_fd, parts[:-1], create=False, missing="not_a_file") if len(parts) > 1 else os.dup(root_fd)
        except ApiError as exc:
            if exc.status == 404:
                raise ApiError(400, "not_a_file", "只能删除文件") from exc
            raise
        try:
            info = os.lstat(parts[-1], dir_fd=dir_fd)
        except OSError as exc:
            if exc.errno == errno.ENOENT:
                raise ApiError(400, "not_a_file", "只能删除文件") from exc
            raise ApiError(403, "unsafe_file", "无法安全打开该文件") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise ApiError(400, "not_a_file", "只能删除文件")
        try:
            os.unlink(parts[-1], dir_fd=dir_fd)
        except OSError as exc:
            raise ApiError(403, "unsafe_file", "无法安全打开该文件") from exc
        log.info("deleted %s/%s via panel", root_id, "/".join(parts))
        return {"ok": True}
    finally:
        _close_fd(dir_fd)
        _close_fd(root_fd)


def delete_file(root_id: str, rel: str) -> dict[str, Any]:
    if _clawd_home_raw():
        return _delete_file_home(root_id, rel)
    base, target, spec = _safe_path(root_id, rel)
    _reject_workspace_browse(root_id, rel)
    if not spec["writable"]:
        raise ApiError(403, "read_only", f"{spec['label']} 是只读目录")
    if target == base or not target.is_file():
        raise ApiError(400, "not_a_file", "只能删除文件")
    target.unlink()
    log.info("deleted %s via panel", target)
    return {"ok": True}


# ---------------------------------------------------------------- 独立消息编辑副本（原会话只读）


def _edit_summary(conn: sqlite3.Connection, session_id: str) -> dict[str, Any]:
    session = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    rows = conn.execute("SELECT * FROM messages WHERE session_id = ? ORDER BY id", (session_id,)).fetchall()
    preview = next((r["content"] for r in reversed(rows) if r["role"] == "user"), None)
    return {"session": {
        "id": session["id"], "source": session["source"], "title": session["title"],
        "model": session["model"], "started_at": session["started_at"],
        "last_active": rows[-1]["timestamp"] if rows else None,
        "message_count": len(rows), "preview": preview,
        "pinned": False, "archived": False,
    }}


def _edit_insert(conn: sqlite3.Connection, table: str, values: dict[str, Any]) -> None:
    # Column names come only from our constants or SQLite schema, never the payload.
    columns = ",".join('"' + key.replace('"', '""') + '"' for key in values)
    conn.execute(f'INSERT INTO "{table}" ({columns}) VALUES ({",".join("?" for _ in values)})',
                 tuple(values.values()))


def _edit_plain_boundary(row: sqlite3.Row) -> bool:
    content = row["content"]
    if row["role"] != "user" or not isinstance(content, str):
        return False
    lower = content.lower()
    if any(marker in lower for marker in ("--- 附件：", "[附件已上传：", "[screenshot",
            "[out-of-band user message", "[steer", "<user_steering", "[user steering")):
        return False
    try:
        if isinstance(json.loads(content), (dict, list)):
            return False
    except ValueError:
        pass
    return (not any(row[key] is not None and row[key] != "" for key in
                    ("api_content", "display_metadata", "tool_calls", "tool_call_id"))
            and row["display_kind"] in (None, "", "text", "message", "user"))


def _edit_has_compression(config: Any) -> bool:
    pending = [config]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            if any("compress" in key.lower() or "compact" in key.lower() for key in item):
                return True
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    return False


def _edit_history_guard(conn: sqlite3.Connection, source: sqlite3.Row, rows: list[sqlite3.Row]) -> None:
    try:
        config = json.loads(source["model_config"] or "{}")
    except (ValueError, TypeError, RecursionError) as exc:
        raise ApiError(409, "unsupported_edit_history", "原会话配置不能安全解析") from exc
    reason = (source["end_reason"] or "").lower()
    if (not isinstance(config, dict) or _edit_has_compression(config)
            or source["parent_session_id"] is not None or "compress" in reason or "compact" in reason
            or conn.execute("SELECT 1 FROM sessions WHERE parent_session_id = ? LIMIT 1", (source["id"],)).fetchone()
            or any(r["active"] != 1 or r["compacted"] not in (None, 0) or r["_compressed_summary"] for r in rows)):
        raise ApiError(409, "unsupported_edit_history", "压缩或继承会话暂不支持编辑")
    now = time.time()
    for table, column in (("compression_locks", "session_id"), ("session_turn_leases", "conversation_id")):
        locks = conn.execute(f'SELECT expires_at FROM "{table}" WHERE "{column}" = ?', (source["id"],)).fetchall()
        if any(type(r["expires_at"]) not in (int, float) or not r["expires_at"] <= now for r in locks):
            raise ApiError(409, "session_busy", "会话正在运行或压缩，请稍后重试")


def _edit_prefix_guard(prefix: list[sqlite3.Row]) -> int:
    pending: set[str] = set()
    seen: set[str] = set()
    count = 0
    for row in prefix:
        if any(isinstance(row[key], str) and "[screenshot" in row[key].lower()
               for key in ("content", "api_content")):
            raise ApiError(409, "unsupported_edit_history", "历史截图缺少可恢复图像，不支持编辑")
        try:
            calls = json.loads(row["tool_calls"] or "[]")
        except (ValueError, TypeError, RecursionError) as exc:
            raise ApiError(409, "incomplete_tool_history", "工具调用记录不能安全解析") from exc
        if not isinstance(calls, list) or (calls and row["role"] != "assistant"):
            raise ApiError(409, "incomplete_tool_history", "工具调用记录不完整")
        for call in calls:
            call_id = call.get("id") if isinstance(call, dict) else None
            if not isinstance(call_id, str) or not call_id or call_id in seen:
                raise ApiError(409, "incomplete_tool_history", "工具调用编号缺失或重复")
            seen.add(call_id)
            pending.add(call_id)
            count += 1
        result_id = row["tool_call_id"]
        if row["role"] == "tool":
            if not isinstance(result_id, str) or result_id not in pending or calls:
                raise ApiError(409, "incomplete_tool_history", "存在孤立工具结果")
            pending.remove(result_id)
        elif result_id or (row["role"] == "user" and pending):
            raise ApiError(409, "incomplete_tool_history", "工具执行中插入的用户消息不能作为完整历史")
    if pending:
        raise ApiError(409, "incomplete_tool_history", "截断位置之前仍有未完成的工具调用")
    return count


def _edit_schema(conn: sqlite3.Connection) -> dict[str, dict[str, sqlite3.Row]]:
    required = {
        "sessions": {"id", "source", "model", "model_config", "parent_session_id", "started_at",
                     "ended_at", "end_reason", "message_count", "tool_call_count", "title",
                     "system_prompt", "system_prompt_hash", "profile_name", "cwd", "transport_profile", "tool_names"},
        "messages": {"id", "session_id", "role", "content", "timestamp", "tool_calls", "tool_call_id",
                     "active", "compacted", "_compressed_summary", "api_content", "display_kind",
                     "display_metadata", "display_order"},
        "compression_locks": {"session_id", "expires_at"},
        "session_turn_leases": {"conversation_id", "expires_at"},
    }
    schema = {}
    for table, columns in required.items():
        kind = conn.execute("SELECT type FROM sqlite_master WHERE name = ?", (table,)).fetchone()
        fields = {r["name"]: r for r in conn.execute(f'PRAGMA table_info("{table}")')}
        if kind is None or kind["type"] != "table" or not columns <= fields.keys():
            raise ApiError(409, "unsupported_edit_schema", "数据库结构不支持安全编辑")
        schema[table] = fields
    if (schema["messages"]["id"]["type"].upper() != "INTEGER" or schema["messages"]["id"]["pk"] != 1
            or schema["sessions"]["id"]["type"].upper() != "TEXT" or schema["sessions"]["id"]["pk"] != 1
            or any(sum(bool(r["pk"]) for r in schema[table].values()) != 1 for table in ("sessions", "messages"))):
        raise ApiError(409, "unsupported_edit_schema", "消息与会话主键结构不兼容")
    supported = required["sessions"] | {"created_source", "pinned", "archived"}
    if any(r["notnull"] and r["dflt_value"] is None and key not in supported
           for key, r in schema["sessions"].items()):
        raise ApiError(409, "unsupported_edit_schema", "数据库有未知必填会话字段")
    return schema


def _persist_edit_branch_project(db_path: Path, parent_id: str, child_id: str) -> None:
    """Copy the source session's project binding before the edit copy commits.

    The sidecar does not reimplement ``project_sessions``. A missing helper or a
    failed write aborts the copy; the caller rolls ``state.db`` back. This is
    not a cross-database transaction. Profile home is the state file's parent,
    not the import-time ``HERMES_HOME``.
    """
    import sys

    try:
        from clawd_projects.inheritance import (
            ProjectInheritanceError,
            inherit_session_project,
            state_db_profile_home,
        )
    except ImportError:
        root = Path(__file__).resolve().parents[3]
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        try:
            from clawd_projects.inheritance import (
                ProjectInheritanceError,
                inherit_session_project,
                state_db_profile_home,
            )
        except ImportError as exc:
            raise ApiError(503, "project_inheritance_unavailable", "无法加载项目归属，已拒绝未绑定副本") from exc
    try:
        inherit_session_project(parent_id, child_id, hermes_home=state_db_profile_home(db_path))
    except ProjectInheritanceError as exc:
        raise ApiError(503, "project_inheritance_failed", "项目归属未能在副本提交前写入，已放弃未绑定副本") from exc


def create_edit_branch(db_path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    """Copy raw history strictly before the selected user row; never end the source."""
    required = {"sessionId", "fromEnd", "expectedContent", "expectedTimestamp", "requestId", "model", "provider"}
    if not isinstance(payload, dict) or set(payload) != required:
        raise ApiError(400, "invalid_edit_request", "需要完整的编辑请求字段")
    for key, limit in (("sessionId", 256), ("model", 512), ("provider", 128)):
        value = payload[key]
        if not isinstance(value, str) or not value.strip() or len(value) > limit or "\x00" in value:
            raise ApiError(400, "invalid_edit_request", f"{key} 不合法")
    stamp = payload["expectedTimestamp"]
    if (type(payload["fromEnd"]) is not int or payload["fromEnd"] < 1
            or not isinstance(payload["expectedContent"], str)
            or type(stamp) not in (int, float) or not -float("inf") < stamp < float("inf")
            or not isinstance(payload["requestId"], str)
            or re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", payload["requestId"]) is None):
        raise ApiError(400, "invalid_edit_request", "编辑位置、时间、文本或 UUID 不合法")
    conn = None
    try:
        conn = sqlite3.connect(db_path.resolve().as_uri() + "?mode=rw", uri=True, timeout=3)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN IMMEDIATE")
        _edit_schema(conn)
        session_id = "api_edit_" + payload["requestId"]
        existing = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if existing is not None:
            try:
                metadata = json.loads(existing["model_config"] or "{}").get("_panel_edit_branch")
            except (ValueError, TypeError, AttributeError):
                metadata = None
            if (metadata != {"version": 1, "request": payload}
                    or existing["source"] != "api_server"
                    or existing["model"] != payload["model"]
                    or existing["parent_session_id"] is not None):
                raise ApiError(409, "edit_request_conflict", "请求编号已被其他编辑占用")
            result = _edit_summary(conn, session_id)
            conn.commit()
            return result
        source = conn.execute("SELECT * FROM sessions WHERE id = ?", (payload["sessionId"],)).fetchone()
        if source is None:
            raise ApiError(404, "session_not_found", "原会话不存在")
        total = conn.execute("SELECT count(*) FROM messages WHERE session_id = ?", (source["id"],)).fetchone()[0]
        if total - payload["fromEnd"] > 10000:
            raise ApiError(413, "edit_prefix_too_large", "编辑历史超过 10000 条消息")
        if total > 10001:
            raise ApiError(413, "edit_history_too_large", "会话消息过多，超过安全编辑上限")
        rows = []
        prefix_bytes = 0
        history_bytes = 0
        for index, row in enumerate(conn.execute("SELECT * FROM messages WHERE session_id = ? ORDER BY id", (source["id"],))):
            row_bytes = sum(len(value.encode("utf-8")) if isinstance(value, str)
                            else len(value) if isinstance(value, bytes)
                            else len(str(value)) if value is not None else 0 for value in row)
            if index < total - payload["fromEnd"]:
                prefix_bytes += row_bytes
                if prefix_bytes > 8 * 1024 * 1024:
                    raise ApiError(413, "edit_prefix_too_large", "编辑历史超过 8 MiB")
            history_bytes += row_bytes
            if history_bytes > 8 * 1024 * 1024:
                raise ApiError(413, "edit_history_too_large", "会话历史超过 8 MiB 安全编辑上限")
            rows.append(row)
        _edit_history_guard(conn, source, rows)
        if payload["fromEnd"] > len(rows):
            raise ApiError(409, "edit_boundary_changed", "消息位置已变化，请刷新")
        boundary = rows[-payload["fromEnd"]]
        if boundary["content"] != payload["expectedContent"] or boundary["timestamp"] != payload["expectedTimestamp"]:
            raise ApiError(409, "edit_boundary_changed", "消息内容或时间已变化，请刷新")
        if sum(r["content"] == payload["expectedContent"] and r["timestamp"] == payload["expectedTimestamp"] for r in rows) != 1:
            raise ApiError(409, "ambiguous_edit_boundary", "存在同文同时间消息，不能安全定位")
        stamps = [r["timestamp"] for r in rows]
        orders = [r["display_order"] for r in rows]
        # 面板按 display_order 展示，编辑位置（fromEnd）也是按它数的：每条都有且严格递增，就与 id 顺序一致。
        # 时间戳会在个别位置倒退（工具结果、插话等），线上约三成真实会话如此，不能据此拒绝；
        # 只有没有 display_order 时才退回要求时间戳不倒退。
        ordered = all(type(order) in (int, float) for order in orders) and all(a < b for a, b in zip(orders, orders[1:]))
        if (any(type(stamp) not in (int, float) or not -float("inf") < stamp < float("inf") for stamp in stamps)
                or (any(order is not None for order in orders) and not ordered)
                or (not ordered and any(a > b for a, b in zip(stamps, stamps[1:])))):
            raise ApiError(409, "unsupported_message_order", "展示顺序与原始消息顺序不一致")
        if not _edit_plain_boundary(boundary):
            raise ApiError(409, "unsafe_edit_boundary", "只能编辑无附件的普通用户文字消息")
        prefix = rows[:-payload["fromEnd"]]
        tool_call_count = _edit_prefix_guard(prefix)
        values = {"id": session_id, "source": "api_server", "model": payload["model"],
                  "model_config": json.dumps({"_panel_edit_branch": {"version": 1, "request": payload}}, ensure_ascii=False),
                  "started_at": time.time(), "ended_at": None, "end_reason": None,
                  "parent_session_id": None, "message_count": len(prefix),
                  "tool_call_count": tool_call_count,
                  "title": f'{source["title"] or "新对话"} · 编辑副本 {payload["requestId"][:8]}'}
        for key in ("archived", "pinned"):
            if key in source.keys():
                values[key] = 0
        for key in ("system_prompt", "system_prompt_hash", "profile_name", "cwd", "transport_profile", "tool_names", "created_source"):
            if key in source.keys():
                values[key] = "api_server" if key == "created_source" else source[key]
        _edit_insert(conn, "sessions", values)
        for row in prefix:
            copied = {key: row[key] for key in row.keys() if key not in ("id", "session_id")}
            _edit_insert(conn, "messages", {"session_id": session_id, **copied})
        _persist_edit_branch_project(db_path, payload["sessionId"], session_id)
        result = _edit_summary(conn, session_id)
        conn.commit()
        return result
    except sqlite3.Error as exc:
        if conn is not None:
            conn.rollback()
        if conn is None:
            raise ApiError(503, "edit_database_unavailable", "会话数据库不可用") from exc
        if isinstance(exc, sqlite3.IntegrityError):
            raise ApiError(409, "edit_write_failed", "副本写入被数据库约束拒绝") from exc
        if "locked" in str(exc).lower() or "busy" in str(exc).lower():
            raise ApiError(409, "session_busy", "会话数据库忙，请稍后重试") from exc
        raise ApiError(500, "edit_database_error", "副本数据库操作失败") from exc
    except Exception:
        if conn is not None:
            conn.rollback()
        raise
    finally:
        if conn is not None:
            conn.close()


# ---------------------------------------------------------------- 实时画面：接管与窗口尺寸（Chromium CDP）

# hermes-live-browser 的 Chromium 直接在 9223 开 CDP；9222 是会唤醒整套浏览器栈的代理，这里只连 9223，
# 浏览器休眠时如实返回 running=false，不替用户唤醒。
# 接管只是一份租约（面板每 15s 续一次）：用来标记「用户在操作」并为文字输入把关，断线 45s 后自动失效。
LIVE_CDP = os.environ.get("PANEL_LIVE_CDP", "127.0.0.1:9223")
LIVE_LEASE_S = 45
MAX_LIVE_TEXT = 2000
_LIVE_LOCK = threading.Lock()
_live: dict[str, Any] = {"until": 0.0}
_live_task_lease: dict[str, Any] = {"until": 0.0}
_TASK_KEYS: dict[str, tuple[str, int]] = {
    "Enter": ("Enter", 13),
    "Backspace": ("Backspace", 8),
    "Tab": ("Tab", 9),
    "Escape": ("Escape", 27),
}


class _CdpSocket:
    """最小 WebSocket 客户端（RFC 6455，仅文本帧），只用于本机 CDP，避免引入第三方库。"""

    def __init__(self, ws_url: str, timeout: float = 5.0) -> None:
        import base64
        import socket

        parts = urlsplit(ws_url)
        if parts.hostname not in ("127.0.0.1", "localhost") or parts.scheme != "ws":
            raise ApiError(502, "cdp_bad_url", "浏览器调试地址异常")
        self.sock = socket.create_connection((parts.hostname, parts.port or 80), timeout=timeout)
        key = base64.b64encode(secrets.token_bytes(16)).decode()
        req = (
            f"GET {parts.path or '/'} HTTP/1.1\r\nHost: {parts.netloc}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(req.encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ApiError(502, "cdp_handshake", "浏览器调试连接被拒绝")
            head += chunk
        if not head.startswith(b"HTTP/1.1 101"):
            raise ApiError(502, "cdp_handshake", "浏览器调试连接被拒绝")
        self.buf = head.split(b"\r\n\r\n", 1)[1]
        self.next_id = 0

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self.sock.close()

    def _read(self, n: int) -> bytes:
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ApiError(502, "cdp_closed", "浏览器调试连接中断")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _send(self, opcode: int, payload: bytes) -> None:
        header = bytes([0x80 | opcode])
        n = len(payload)
        if n < 126:
            header += bytes([0x80 | n])
        elif n < 65536:
            header += bytes([0x80 | 126]) + n.to_bytes(2, "big")
        else:
            header += bytes([0x80 | 127]) + n.to_bytes(8, "big")
        mask = secrets.token_bytes(4)
        self.sock.sendall(header + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def _recv_message(self) -> str:
        data = b""
        while True:
            b1, b2 = self._read(2)
            opcode, n = b1 & 0x0F, b2 & 0x7F
            if n == 126:
                n = int.from_bytes(self._read(2), "big")
            elif n == 127:
                n = int.from_bytes(self._read(8), "big")
            payload = self._read(n)
            if opcode == 0x8:
                raise ApiError(502, "cdp_closed", "浏览器调试连接中断")
            if opcode == 0x9:
                self._send(0xA, payload)
                continue
            if opcode in (0x0, 0x1):
                data += payload
                if b1 & 0x80:
                    return data.decode("utf-8", "replace")

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.next_id += 1
        mid = self.next_id
        self._send(0x1, json.dumps({"id": mid, "method": method, "params": params or {}}).encode())
        while True:
            msg = json.loads(self._recv_message())
            if msg.get("id") != mid:
                continue  # 事件或别的回复
            if "error" in msg:
                raise ApiError(502, "cdp_error", f"浏览器拒绝了 {method}：{(msg['error'] or {}).get('message', '')}"[:300])
            return msg.get("result") or {}


def _cdp_http(path: str) -> Any:
    """CDP 的 HTTP 端点；浏览器休眠（端口未监听）时返回 None。"""
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://{LIVE_CDP}{path}", timeout=2) as resp:  # noqa: S310 本机固定地址
            return json.loads(resp.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError):
        return None


def _page_eval(page: dict[str, Any], expression: str) -> Any:
    ws = _CdpSocket(page["webSocketDebuggerUrl"])
    try:
        return (ws.call("Runtime.evaluate", {"expression": expression, "returnByValue": True}).get("result") or {}).get("value")
    finally:
        ws.close()


def _live_page(require_visible: bool = False) -> dict[str, Any] | None:
    """Read-only metadata may fall back; input requires one verified visible page."""
    targets = _cdp_http("/json/list")
    if not isinstance(targets, list):
        return None
    pages = [t for t in targets if isinstance(t, dict) and t.get("type") == "page" and t.get("webSocketDebuggerUrl")
             and not str(t.get("url", "")).startswith("devtools://")]
    visible = []
    for page in pages:
        try:
            if _page_eval(page, "document.visibilityState") == "visible":
                visible.append(page)
        except (ApiError, OSError, ValueError):
            continue
    if require_visible:
        return visible[0] if len(visible) == 1 else None
    return (visible or pages or [None])[0]


LIVE_TASK_DB = Path(os.environ.get("PANEL_LIVE_TASK_DB", "/root/hermes-workspace/hermes-panel-state/live-tasks.sqlite3"))
_LIVE_FRAME_SLOTS = threading.BoundedSemaphore(2)


def _live_task_store():
    if not LIVE_TASK_DB.exists():
        return None
    try:
        try:
            if __package__:
                from .live_task_store import Store
            else:
                from live_task_store import Store
        except ImportError:
            # Legacy deployers install only this sidecar file. The approved
            # plugin bundle carries the same module without changing deploy.sh.
            import importlib.util
            module_path = Path(os.environ.get('PANEL_LIVE_STORE_MODULE',
                               str(HERMES_HOME / 'plugins/browser-window-guard/live_task_store.py')))
            spec = importlib.util.spec_from_file_location('panel_live_task_store', module_path)
            if spec is None or spec.loader is None:
                raise ImportError('Installed live task store is unavailable')
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            Store = module.Store
        return Store(LIVE_TASK_DB, read_only=True)
    except (ImportError, OSError, sqlite3.Error) as exc:
        raise ApiError(503, "live_registry_unavailable", "任务窗口登记服务不可用") from exc


def _task_view(task: dict[str, Any] | None, session_id: str, client_id: str | None = None) -> dict[str, Any]:
    task_id = str((task or {}).get("taskId") or "")
    status = "ended" if task and task.get("status") in ("ended", "closing") else "waiting"
    with _LIVE_LOCK:
        t_rem = _live_task_expire()
        g_rem = _live_expire()
        if status == "ended" and t_rem > 0 and _live_task_lease.get("sessionId") == session_id and (not task_id or _live_task_lease.get("taskId") == task_id):
            _live_task_drop()
            _live_task_lease["until"] = 0.0
            t_rem = 0.0
        same_task = bool(t_rem > 0 and _live_task_lease.get("sessionId") == session_id and task_id and _live_task_lease.get("taskId") == task_id)
        controlled_by_you = bool(same_task and client_id and _live_task_lease.get("clientId") == client_id)
        taken_over = bool(same_task or (g_rem > 0 and (not client_id or _live.get("clientId") != client_id)))
        lease_rem = t_rem if same_task else 0.0
    return {"taskId": task_id, "sessionId": session_id,
            "status": status,
            "targetId": None, "title": "", "image": None,
            "cleanupError": str((task or {}).get("cleanupError") or "")[:300],
            "takenOver": taken_over, "controlledByYou": controlled_by_you,
            "leaseRemainingSeconds": lease_rem}


def live_task(session_id: str, task_id: str = "", client_id: str | None = None) -> dict[str, Any]:
    """Task-only JPEG spectator. Unknown ownership never falls back to VNC/focus."""
    if (not isinstance(session_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:@-]{1,200}", session_id)
            or not isinstance(task_id, str) or task_id and not re.fullmatch(r"[A-Za-z0-9_.:@-]{1,200}", task_id)):
        raise ApiError(400, "invalid_live_task", "任务标识不合法")
    if client_id is not None:
        client_id = _live_client_id(client_id)
    store = _live_task_store()
    try:
        task = store.latest(session_id, task_id) if store else None
        if task and (task.get("sessionId") != session_id or task_id and task.get("taskId") != task_id):
            raise ApiError(409, "live_task_mismatch", "任务窗口归属不匹配")
        if not task_id and task and task.get("status") in ("ended", "closing"):
            task = None  # A fresh viewer must not bind the previous ended turn.
        result = _task_view(task, session_id, client_id)
        if not task or task.get("status") != "active" or not task.get("targetId"):
            return result
        if not _LIVE_FRAME_SLOTS.acquire(blocking=False):
            raise ApiError(503, "live_frame_busy", "画面服务繁忙，请稍后重试")
        try:
            return _live_task_frame(store, task, session_id, result, client_id)
        finally:
            _LIVE_FRAME_SLOTS.release()
    except (OSError, sqlite3.Error) as exc:
        raise ApiError(503, "live_registry_unavailable", "任务窗口登记服务不可用") from exc


def _live_task_frame(store: Any, task: dict[str, Any], session_id: str, result: dict[str, Any], client_id: str | None = None) -> dict[str, Any]:
    import base64
    import binascii
    version = _cdp_http("/json/version")
    if not isinstance(version, dict):
        raise ApiError(502, "live_browser_unavailable", "任务浏览器暂不可用")
    browser_id = urlsplit(str(version.get("webSocketDebuggerUrl") or "")).path
    if not browser_id or browser_id != task.get("browserId"):
        raise ApiError(409, "live_browser_replaced", "任务浏览器已变化，不显示其他窗口")
    target_id = task["targetId"]
    if not store.owns(session_id, task["taskId"], target_id, browser_id):
        raise ApiError(409, "live_target_unowned", "目标页面不属于本任务")
    targets = _cdp_http("/json/list")
    if not isinstance(targets, list):
        raise ApiError(502, "live_browser_unavailable", "任务浏览器暂不可用")
    page = next((p for p in targets if isinstance(p, dict) and p.get("type") == "page" and p.get("id") == target_id), None)
    if page is None:
        return result
    ws_url = str(page.get("webSocketDebuggerUrl") or "")
    endpoint, configured = urlsplit(ws_url), urlsplit("http://" + LIVE_CDP)
    if (endpoint.scheme != "ws" or endpoint.hostname not in ("localhost", "127.0.0.1")
            or endpoint.port != (configured.port or 80) or endpoint.path != f"/devtools/page/{target_id}"
            or endpoint.query or endpoint.fragment or endpoint.username or endpoint.password):
        raise ApiError(502, "live_target_invalid", "任务页面调试地址异常")
    try:
        ws = _CdpSocket(ws_url, timeout=3.0)
        try:
            frame = ws.call("Page.captureScreenshot", {"format": "jpeg", "quality": 65, "captureBeyondViewport": False, "fromSurface": True})
        finally:
            ws.close()
    except (OSError, ValueError) as exc:
        raise ApiError(502, "live_frame_unavailable", "任务画面暂不可用") from exc
    data = frame.get("data")
    if not isinstance(data, str) or len(data) > 3 * 1024 * 1024:
        raise ApiError(502, "live_frame_invalid", "任务画面数据异常")
    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ApiError(502, "live_frame_invalid", "任务画面数据异常") from exc
    if not raw.startswith(b"\xff\xd8\xff") or not raw.endswith(b"\xff\xd9"):
        raise ApiError(502, "live_frame_invalid", "任务画面格式异常")
    current = store.latest(session_id, task["taskId"])
    if (not current or current.get("status") != "active" or current.get("taskId") != task["taskId"]
            or current.get("targetId") != target_id or current.get("browserId") != browser_id):
        return _task_view(current, session_id, client_id)
    if not store.owns(session_id, task["taskId"], target_id, browser_id):
        raise ApiError(409, "live_target_unowned", "任务页面归属已变化")
    refreshed = _task_view(current, session_id, client_id)
    result.update(refreshed, status="active", targetId=target_id,
                  title=redact(str(page.get("title") or "")[:300]),
                  image="data:image/jpeg;base64," + data)
    return result


# 仅向可验证的网页编辑焦点插入；地址栏/不可编辑焦点不插入，跨域 iframe 不确定时明确报错。
_EDITABLE_FOCUS_JS = """(() => {
  function editable(doc, depth = 0) {
    if (depth > 32) return "uncertain";
    if (!doc.hasFocus() || doc.visibilityState !== "visible") return false;
    let el = doc.activeElement;
    while (el && el.shadowRoot && el.shadowRoot.activeElement) el = el.shadowRoot.activeElement;
    if (!el || el.disabled || el.readOnly || (el.matches && el.matches(":disabled, [inert], [inert] *"))) return false;
    if (el.tagName === "IFRAME") {
      try {
        const child = el.contentDocument;
        return child ? editable(child, depth + 1) : "uncertain";
      } catch (_) { return "uncertain"; }
    }
    return !!(el.isContentEditable || el.tagName === "TEXTAREA" ||
      (el.tagName === "INPUT" && ["text","search","url","tel","email","password","number"].includes(el.type)));
  }
  return editable(document);
})()"""


def _live_client_id(value: Any) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9_-]{16,96}", value) is None:
        raise ApiError(400, "invalid_client", "控制器 id 需为 16–96 个 ASCII 字母、数字、下划线或连字符")
    return value


def _live_expire() -> float:
    """Caller holds _LIVE_LOCK. Monotonic time avoids wall-clock lease jumps."""
    remaining = max(0.0, _live["until"] - time.monotonic())
    if remaining == 0:
        _live.clear()
        _live["until"] = 0.0
    return remaining


_LIVE_CAPPED_CLIENTS: dict[str, None] = {}


def _live_task_drop() -> None:
    """Caller holds _LIVE_LOCK. Closing the descriptor wakes the native guard."""
    gate = _live_task_lease.get('gate')
    if _live_task_lease.get('hardUntil', float('inf')) <= time.monotonic():
        owner = _live_task_lease.get('clientId')
        if owner:
            _LIVE_CAPPED_CLIENTS[owner] = None
            if len(_LIVE_CAPPED_CLIENTS) > 256:
                del _LIVE_CAPPED_CLIENTS[next(iter(_LIVE_CAPPED_CLIENTS))]
    timer = _live_task_lease.get('timer')
    if timer is not None:
        timer.cancel()
    if gate is not None:
        gate.close()
    _live_task_lease.clear()
    _live_task_lease['until'] = 0.0


def _live_task_timeout(gate: Any, deadline: float) -> None:
    with _LIVE_LOCK:
        # Old canceled timers must never close a renewed/new controller's gate.
        if _live_task_lease.get('gate') is gate and _live_task_lease.get('until') == deadline:
            _live_task_drop()


def _live_task_open_gate() -> Any:
    import fcntl
    path = LIVE_TASK_DB.parent / 'browser-exec.lock'
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    gate = os.fdopen(fd, 'w')
    try:
        fcntl.flock(gate, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return gate
    except BlockingIOError:
        gate.close()
        return None
    except BaseException:
        gate.close()
        raise


def _live_task_expire() -> float:
    """Caller holds _LIVE_LOCK."""
    remaining = max(0.0, _live_task_lease["until"] - time.monotonic())
    if remaining == 0:
        _live_task_drop()
        _live_task_lease["until"] = 0.0
    return remaining


def _live_require_owner(client_id: str) -> None:
    if _live_task_expire() and _live_task_lease.get("clientId") != client_id:
        raise ApiError(409, "live_owned_elsewhere", "控制权已由另一个窗口持有")
    if not _live_expire():
        raise ApiError(409, "not_taken_over", "请先取得控制权")
    if _live.get("clientId") != client_id:
        raise ApiError(409, "live_owned_elsewhere", "控制权已由另一个窗口持有")


def live_state(client_id: str | None = None) -> dict[str, Any]:
    if client_id is not None:
        client_id = _live_client_id(client_id)
    page = _live_page()
    with _LIVE_LOCK:
        remaining = _live_expire()
        t_remaining = _live_task_expire()
        task_other = t_remaining > 0 and _live_task_lease.get("clientId") != client_id
        state: dict[str, Any] = {
            "running": page is not None, "takenOver": remaining > 0 or task_other,
            "controlledByYou": remaining > 0 and client_id == _live.get("clientId"),
            "leaseRemainingSeconds": remaining,
        }
        if remaining and _live.get("sessionId"):
            state["ownerSessionId"] = _live["sessionId"]
        elif task_other and _live_task_lease.get("sessionId"):
            state["ownerSessionId"] = _live_task_lease["sessionId"]
    if page is not None:
        state.update(url=redact(str(page.get("url", ""))[:2000]),
                     title=redact(str(page.get("title", ""))[:300]))
    return state


def _live_check_renewal(client_id: str, session_id: str) -> None:
    """Caller holds _LIVE_LOCK. A live lease may only be renewed by its owner for the same session."""
    if _live_task_expire():
        raise ApiError(409, "live_owned_elsewhere", "控制权已由另一个窗口持有")
    if _live_expire():
        _live_require_owner(client_id)
        if _live.get("sessionId", "") != session_id:
            raise ApiError(409, "live_session_changed", "续租不能更换会话，请先交还控制权")


def live_takeover(body: dict[str, Any]) -> dict[str, Any]:
    """面板输入租约；不暂停原生 agent，也不是全局浏览器互斥锁。
    会话库与 CDP 的慢查询在锁外完成，提交前在锁内复核租约，避免拖住 /live/state 与心跳。"""
    client_id = _live_client_id(body.get("clientId"))
    session_id = body.get("sessionId", "")
    if not isinstance(session_id, str) or (session_id and SESSION_ID_RE.fullmatch(session_id) is None):
        raise ApiError(400, "invalid_session", "会话 id 不合法")
    with _LIVE_LOCK:
        _live_check_renewal(client_id, session_id)
    if session_id:
        try:
            discovery = get_session_run(session_id)
            if (not isinstance(discovery, dict) or type(discovery.get("busy")) is not bool
                    or "run" not in discovery):
                raise ValueError("invalid session discovery")
            run = discovery["run"]
            if run is not None and (not isinstance(run, dict) or run.get("status") not in
                                    _SESSION_RUN_ACTIVE | _SESSION_RUN_TERMINAL):
                raise ValueError("invalid session run status")
        except (ApiError, OSError, ValueError, TypeError) as exc:
            raise ApiError(503, "session_run_unavailable", "无法确认会话任务状态，暂不能接管") from exc
        if discovery["busy"] or (run is not None and run["status"] in _SESSION_RUN_ACTIVE):
            raise ApiError(409, "live_session_busy", "所选会话仍在执行任务，请先停止或等待完成")
    if _live_page() is None:
        raise ApiError(409, "browser_asleep", "服务器上的浏览器未运行")
    with _LIVE_LOCK:
        _live_check_renewal(client_id, session_id)  # 慢查询期间别的窗口可能已取得租约
        if _live_task_expire() and _live_task_lease.get("clientId") == client_id:
            _live_task_drop()
            _live_task_lease["until"] = 0.0
        _live.update(until=time.monotonic() + LIVE_LEASE_S, clientId=client_id, sessionId=session_id)
    return {"ok": True, "leaseSeconds": LIVE_LEASE_S}


def _live_task_verify_target(session_id: str, task_id: str, target_id: str) -> tuple[Any, dict[str, Any], str, str]:
    store = _live_task_store()
    if store is None:
        raise ApiError(409, "live_task_missing", "当前任务尚未登记浏览器页面")
    try:
        task = store.latest(session_id, task_id)
        if not task or task.get("sessionId") != session_id or task.get("taskId") != task_id:
            raise ApiError(409, "live_task_mismatch", "任务窗口归属不匹配")
        if task.get("status") != "active" or not task.get("targetId"):
            raise ApiError(409, "live_task_ended", "任务已结束或页面已关闭，不能接管或输入")
        if task.get("targetId") != target_id:
            raise ApiError(409, "live_target_changed", "任务当前目标页面已变化，请等待画面刷新后重试")
        version = _cdp_http("/json/version")
        if not isinstance(version, dict):
            raise ApiError(502, "live_browser_unavailable", "任务浏览器暂不可用")
        browser_id = urlsplit(str(version.get("webSocketDebuggerUrl") or "")).path
        if not browser_id or browser_id != task.get("browserId"):
            raise ApiError(409, "live_browser_replaced", "任务浏览器已变化，不显示或控制其他窗口")
        if not store.owns(session_id, task_id, target_id, browser_id):
            raise ApiError(409, "live_target_unowned", "目标页面不属于本任务")
        targets = _cdp_http("/json/list")
        if not isinstance(targets, list):
            raise ApiError(502, "live_browser_unavailable", "任务浏览器暂不可用")
        page = next((p for p in targets if isinstance(p, dict) and p.get("type") == "page" and p.get("id") == target_id), None)
        if page is None:
            raise ApiError(409, "live_target_closed", "目标页面已关闭")
        ws_url = str(page.get("webSocketDebuggerUrl") or "")
        endpoint, configured = urlsplit(ws_url), urlsplit("http://" + LIVE_CDP)
        if (endpoint.scheme != "ws" or endpoint.hostname not in ("localhost", "127.0.0.1")
                or endpoint.port != (configured.port or 80) or endpoint.path != f"/devtools/page/{target_id}"
                or endpoint.query or endpoint.fragment or endpoint.username or endpoint.password):
            raise ApiError(502, "live_target_invalid", "任务页面调试地址异常")
        return store, task, browser_id, ws_url
    except (OSError, sqlite3.Error) as exc:
        raise ApiError(503, "live_registry_unavailable", "任务窗口登记服务不可用") from exc


def _live_task_check_renewal(client_id: str, session_id: str, task_id: str, target_id: str, browser_id: str = "") -> None:
    """Caller holds _LIVE_LOCK."""
    if _live_expire():
        raise ApiError(409, "live_owned_elsewhere", "全局画面正由另一个窗口控制，无法同时接管任务页面")
    remaining = _live_task_expire()
    if client_id in _LIVE_CAPPED_CLIENTS:
        raise ApiError(409, 'live_control_limit', '本次接管已满五分钟，已交还浏览器；再次接管需重新点击')
    if remaining:
        if _live_task_lease.get("clientId") != client_id:
            raise ApiError(409, "live_owned_elsewhere", "该任务页面或另一个任务正由其他窗口控制")
        if (_live_task_lease.get("sessionId") != session_id
                or _live_task_lease.get("taskId") != task_id
                or _live_task_lease.get("targetId") != target_id
                or (browser_id and _live_task_lease.get("browserId") and _live_task_lease.get("browserId") != browser_id)):
            raise ApiError(409, "live_task_lease_changed", "续租不能更换任务或目标页，请先交还控制权")


def _live_task_require_owner(client_id: str, session_id: str, task_id: str, target_id: str, browser_id: str) -> None:
    """Caller holds _LIVE_LOCK."""
    if _live_expire() and _live.get("clientId") != client_id:
        raise ApiError(409, "live_owned_elsewhere", "控制权已由另一个窗口持有")
    if not _live_task_expire():
        raise ApiError(409, "not_taken_over", "请先取得当前任务页面的控制权")
    gate = _live_task_lease.get('gate')
    if gate is None or gate.closed:
        _live_task_drop()
        raise ApiError(409, 'live_gate_lost', '浏览器暂停锁已失效，已拒绝输入')
    if _live_task_lease.get("clientId") != client_id:
        raise ApiError(409, "live_owned_elsewhere", "控制权已由另一个窗口持有")
    if (_live_task_lease.get("sessionId") != session_id
            or _live_task_lease.get("taskId") != task_id
            or _live_task_lease.get("targetId") != target_id
            or _live_task_lease.get("browserId") != browser_id):
        raise ApiError(409, "live_task_lease_mismatch", "控制租约与当前任务目标页不匹配")


def live_task_control(body: dict[str, Any]) -> dict[str, Any]:
    """Task-scoped control lease (takeover / release). Never stops a running turn."""
    if not isinstance(body, dict):
        raise ApiError(400, "invalid_body", "请求体需为 JSON 对象")
    action = body.get("action")
    client_id = _live_client_id(body.get("clientId"))
    if action == "release":
        with _LIVE_LOCK:
            if _live_task_expire():
                if _live_task_lease.get("clientId") != client_id:
                    raise ApiError(409, "live_owned_elsewhere", "控制权已由另一个窗口持有")
                _live_task_drop()
                _live_task_lease["until"] = 0.0
        return {"ok": True, "controlledByYou": False}
    if action != "takeover":
        raise ApiError(400, "invalid_action", "action 必须为 takeover 或 release")
    session_id = body.get("sessionId", "")
    task_id = body.get("taskId", "")
    target_id = body.get("targetId", "")
    if (not isinstance(session_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:@-]{1,200}", session_id)
            or not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:@-]{1,200}", task_id)
            or not isinstance(target_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:@-]{1,200}", target_id)):
        raise ApiError(400, "invalid_live_task", "任务、会话或目标页标识不合法")
    with _LIVE_LOCK:
        _live_task_check_renewal(client_id, session_id, task_id, target_id)
    store, _task, browser_id, _ws_url = _live_task_verify_target(session_id, task_id, target_id)
    if store.control_ready(session_id, task_id) is not True:
        raise ApiError(409, 'live_guard_upgrade_required', '该回合尚未启用安全接管协议，请在插件更新后的新任务中接管')
    try:
        current = store.latest(session_id, task_id)
        if (not current or current.get("status") != "active" or current.get("targetId") != target_id
                or current.get("browserId") != browser_id or not store.owns(session_id, task_id, target_id, browser_id)):
            raise ApiError(409, "live_task_ended", "任务已结束或目标页面归属已变化，不能接管")
    except (OSError, sqlite3.Error) as exc:
        raise ApiError(503, "live_registry_unavailable", "任务窗口登记服务不可用") from exc
    with _LIVE_LOCK:
        _live_task_check_renewal(client_id, session_id, task_id, target_id, browser_id)
        gate = _live_task_lease.get('gate')
        if gate is None:
            try:
                gate = _live_task_open_gate()
            except OSError as exc:
                raise ApiError(503, 'live_gate_unavailable', '浏览器执行锁不可用，未授予控制权') from exc
            if gate is None:
                return {'ok': True, 'pending': True, 'controlledByYou': False,
                        'taskId': task_id, 'targetId': target_id}
            _live_task_lease['gate'] = gate
            _live_task_lease['hardUntil'] = time.monotonic() + 300.0
        # Cleanup may have won the gate immediately before us; verify under it.
        try:
            current = store.latest(session_id, task_id)
            if (not current or current.get('status') != 'active' or current.get('targetId') != target_id
                    or current.get('browserId') != browser_id):
                raise ApiError(409, 'live_task_ended', '任务已结束，未授予控制权')
        except BaseException:
            _live_task_drop()
            raise
        previous_timer = _live_task_lease.get('timer')
        if previous_timer is not None:
            previous_timer.cancel()
        until = min(time.monotonic() + LIVE_LEASE_S, _live_task_lease['hardUntil'])
        if until <= time.monotonic():
            _live_task_drop()
            raise ApiError(409, 'live_control_limit', '本次接管已满五分钟，已交还浏览器')
        if _live_expire() and _live.get("clientId") == client_id:
            _live.clear()
            _live["until"] = 0.0
        _live_task_lease.update(
            until=until,
            clientId=client_id,
            sessionId=session_id,
            taskId=task_id,
            targetId=target_id,
            browserId=browser_id,
        )
        timer = threading.Timer(max(.001, until - time.monotonic()), _live_task_timeout, (gate, until))
        timer.daemon = True
        _live_task_lease['timer'] = timer
        timer.start()
    return {
        "ok": True,
        "takenOver": True,
        "controlledByYou": True,
        "leaseSeconds": LIVE_LEASE_S,
        "leaseRemainingSeconds": float(LIVE_LEASE_S),
        "taskId": task_id,
        "targetId": target_id,
    }


def _reverify_task_before_cdp(store: Any, client_id: str, session_id: str, task_id: str, target_id: str, browser_id: str) -> None:
    try:
        current = store.latest(session_id, task_id)
        if (not current or current.get("status") != "active" or current.get("targetId") != target_id
                or current.get("browserId") != browser_id or not store.owns(session_id, task_id, target_id, browser_id)):
            with _LIVE_LOCK:
                if _live_task_expire() and _live_task_lease.get("sessionId") == session_id and _live_task_lease.get("taskId") == task_id:
                    _live_task_drop()
                    _live_task_lease["until"] = 0.0
            raise ApiError(409, "live_task_ended", "任务已结束或页面归属已变化，已拒绝输入")
    except (OSError, sqlite3.Error) as exc:
        raise ApiError(503, "live_registry_unavailable", "任务窗口登记服务不可用") from exc


def live_task_input(body: dict[str, Any]) -> dict[str, Any]:
    """Task-scoped CDP input: click, scroll, text, and safe keys on the exact owned target only."""
    if not isinstance(body, dict):
        raise ApiError(400, "invalid_body", "请求体需为 JSON 对象")
    client_id = _live_client_id(body.get("clientId"))
    session_id = body.get("sessionId", "")
    task_id = body.get("taskId", "")
    target_id = body.get("targetId", "")
    kind = body.get("kind", "")
    if (not isinstance(session_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:@-]{1,200}", session_id)
            or not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:@-]{1,200}", task_id)
            or not isinstance(target_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:@-]{1,200}", target_id)):
        raise ApiError(400, "invalid_live_task", "任务、会话或目标页标识不合法")
    if kind not in ("click", "scroll", "text", "key"):
        raise ApiError(400, "invalid_input_kind", "输入类型不合法")
    x_ratio = y_ratio = 0.0
    delta_x = delta_y = 0.0
    text = ""
    key_name = ""
    if kind in ("click", "scroll"):
        xr, yr = body.get("xRatio"), body.get("yRatio")
        if (type(xr) not in (int, float) or type(yr) not in (int, float)
                or not (0.0 <= float(xr) <= 1.0 and 0.0 <= float(yr) <= 1.0)):
            raise ApiError(400, "invalid_coordinates", "坐标比例需在 0 到 1 之间")
        x_ratio, y_ratio = float(xr), float(yr)
        if kind == "scroll":
            dx, dy = body.get("deltaX", 0), body.get("deltaY", 0)
            if (type(dx) not in (int, float) or type(dy) not in (int, float)
                    or not (-4000.0 <= float(dx) <= 4000.0 and -4000.0 <= float(dy) <= 4000.0)):
                raise ApiError(400, "invalid_scroll", "滚动增量超出范围")
            delta_x, delta_y = float(dx), float(dy)
    elif kind == "text":
        raw_text = body.get("text")
        if not isinstance(raw_text, str) or not raw_text or len(raw_text) > MAX_LIVE_TEXT:
            raise ApiError(400, "invalid_text", f"文字需为 1–{MAX_LIVE_TEXT} 个字符")
        text = raw_text
    elif kind == "key":
        raw_key = body.get("key")
        if not isinstance(raw_key, str) or raw_key not in _TASK_KEYS:
            raise ApiError(400, "invalid_key", "仅支持 Enter、Backspace、Tab、Escape")
        key_name = raw_key

    store, _task, browser_id, ws_url = _live_task_verify_target(session_id, task_id, target_id)
    with _LIVE_LOCK:
        _live_task_require_owner(client_id, session_id, task_id, target_id, browser_id)

    ws = _CdpSocket(ws_url, timeout=3.0)
    try:
        if kind in ("click", "scroll"):
            metrics = ws.call("Page.getLayoutMetrics")
            vp = metrics.get("cssVisualViewport") or metrics.get("visualViewport") or metrics.get("layoutViewport") or {}
            vw = float(vp.get("clientWidth") or vp.get("pageX") or 1280.0)
            vh = float(vp.get("clientHeight") or vp.get("pageY") or 720.0)
            if vw <= 0 or vh <= 0:
                vw, vh = 1280.0, 720.0
            x = round(max(0.0, min(vw - 1.0, x_ratio * vw)), 2)
            y = round(max(0.0, min(vh - 1.0, y_ratio * vh)), 2)
            _reverify_task_before_cdp(store, client_id, session_id, task_id, target_id, browser_id)
            with _LIVE_LOCK:
                _live_task_require_owner(client_id, session_id, task_id, target_id, browser_id)
                if kind == "click":
                    ws.call("Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1})
                    ws.call("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1})
                else:
                    ws.call("Input.dispatchMouseEvent", {"type": "mouseWheel", "x": x, "y": y, "deltaX": delta_x, "deltaY": delta_y})
            return {"ok": True, "x": x, "y": y}
        if kind == "text":
            _reverify_task_before_cdp(store, client_id, session_id, task_id, target_id, browser_id)
            with _LIVE_LOCK:
                _live_task_require_owner(client_id, session_id, task_id, target_id, browser_id)
                ws.call("Input.insertText", {"text": text})
            return {"ok": True, "inserted": True}
        code, vk = _TASK_KEYS[key_name]
        _reverify_task_before_cdp(store, client_id, session_id, task_id, target_id, browser_id)
        with _LIVE_LOCK:
            _live_task_require_owner(client_id, session_id, task_id, target_id, browser_id)
            down_params: dict[str, Any] = {"type": "keyDown", "key": key_name, "code": code, "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk}
            if key_name == "Enter":
                down_params["text"] = "\r"
                down_params["unmodifiedText"] = "\r"
            ws.call("Input.dispatchKeyEvent", down_params)
            ws.call("Input.dispatchKeyEvent", {"type": "keyUp", "key": key_name, "code": code, "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk})
        return {"ok": True, "key": key_name}
    finally:
        ws.close()


def live_release(body: dict[str, Any]) -> dict[str, Any]:
    with _LIVE_LOCK:
        client_id = _live_client_id(body.get("clientId"))
        if _live_expire() and _live.get("clientId") != client_id:
            raise ApiError(409, "live_owned_elsewhere", "控制权已由另一个窗口持有")
        _live.clear()
        _live["until"] = 0.0
    return {"ok": True}


def live_type(body: dict[str, Any]) -> dict[str, Any]:
    """仅向当前可验证的网页编辑焦点插入；不改焦点，不发送 Enter/提交。
    页面发现与焦点探测在锁外；锁内只做最终的租约复核 + 一次 insertText。"""
    client_id = _live_client_id(body.get("clientId"))
    text = body.get("text")
    if not isinstance(text, str) or not text or len(text) > MAX_LIVE_TEXT:
        raise ApiError(400, "invalid_text", f"文字需为 1–{MAX_LIVE_TEXT} 个字符")
    with _LIVE_LOCK:
        _live_require_owner(client_id)
    page = _live_page(require_visible=True)
    if page is None:
        raise ApiError(409, "visible_page_unavailable", "无法确认当前可见的浏览器标签页，未输入文字")
    ws = _CdpSocket(page["webSocketDebuggerUrl"])
    try:
        focused = (ws.call("Runtime.evaluate", {"expression": _EDITABLE_FOCUS_JS, "returnByValue": True}).get("result") or {}).get("value")
        if focused == "uncertain":
            raise ApiError(409, "focused_frame_unavailable", "无法确认跨域 iframe 中的编辑焦点，未输入文字")
        if focused is not True:
            return {"ok": True, "inserted": False}
        # 探测可能耗时数秒：插入前在锁内复核租约，且复核与插入之间不放锁，交还不会插队。
        with _LIVE_LOCK:
            _live_require_owner(client_id)
            ws.call("Input.insertText", {"text": text})
    finally:
        ws.close()
    return {"ok": True, "inserted": True}


# ---------------------------------------------------------------- 回合旁白（state.db 只读）

# Codex 通道（codex_responses）的旁白存在 messages.codex_message_items 里 phase=commentary 的条目，
# 同时被并进 reasoning；api_server 的历史接口不返回 codex_message_items，面板无从区分。
# 这里按 Hermes agent/history_commentary.py 的 _phase_message_items 同样规则取出，供面板放回正文。
MAX_COMMENTARY_IDS = 1000
_THINK_BLOCK_RE = re.compile(r"<(think|thinking|reasoning)>.*?</\1>", re.IGNORECASE | re.DOTALL)


def _commentary_items(raw: Any) -> list[str]:
    try:
        items = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return []
    if not isinstance(items, list):
        return []
    out: list[str] = []
    for item in items:
        if not isinstance(item, dict) or item.get("type") != "message" or item.get("role") != "assistant":
            continue
        phase, content = item.get("phase"), item.get("content")
        if not isinstance(phase, str) or phase.strip().lower() != "commentary" or not isinstance(content, list):
            continue
        text = "".join(
            p["text"] for p in content
            if isinstance(p, dict) and p.get("type") == "output_text" and isinstance(p.get("text"), str) and p["text"].strip()
        ).strip()
        visible = _THINK_BLOCK_RE.sub("", text).strip()
        if visible:
            out.append(redact(visible))
    return out


def get_message_origins(session_id: str, raw_ids: str) -> dict[str, Any]:
    """只读核对 API 接收来源，不返回接收载荷、原文或身份凭据。"""
    if not isinstance(session_id, str) or not session_id or len(session_id) > 512:
        raise ApiError(400, "invalid_session", "需要有效会话 ID")
    if not isinstance(raw_ids, str) or len(raw_ids) > 4096:
        raise ApiError(400, "invalid_ids", "消息 ID 列表过长")
    parts = raw_ids.split(",") if raw_ids else []
    if len(parts) > 200 or any(re.fullmatch(r"[1-9][0-9]{0,18}", value) is None or int(value) > 9223372036854775807 for value in parts):
        raise ApiError(400, "invalid_ids", "需要不超过 200 个正整数消息 ID")
    ids = list(dict.fromkeys(int(value) for value in parts))
    if not ids:
        return {"items": {}}
    try:
        with contextlib.closing(sqlite3.connect((HERMES_HOME / "state.db").resolve().as_uri() + "?mode=ro", uri=True, timeout=3)) as conn:
            conn.execute("PRAGMA query_only = ON")
            message_columns = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
            admission_columns = {row[1] for row in conn.execute("PRAGMA table_info(session_admissions)")}
            if not {"id", "session_id", "role", "platform_message_id"} <= message_columns or not {"admission_id", "target_session_id", "principal_id", "intent", "payload_json"} <= admission_columns:
                return {"items": {}}
            marks = ",".join("?" for _ in ids)
            rows = conn.execute(
                "SELECT m.id, s.intent, CASE WHEN json_valid(s.payload_json) THEN json_type(s.payload_json, '$.api_turn_v1') ELSE NULL END "
                "FROM messages m JOIN session_admissions s ON s.admission_id = m.platform_message_id AND s.target_session_id = m.session_id "
                f"WHERE m.session_id = ? AND m.role = 'user' AND m.id IN ({marks}) "
                "AND s.principal_id = 'api' AND s.intent IN ('queue', 'steer')",
                [session_id, *ids],
            ).fetchall()
    except (sqlite3.Error, OSError) as exc:
        raise ApiError(503, "message_origin_unavailable", "消息来源暂不可用") from exc
    return {"items": {str(mid): "api_" + intent for mid, intent, payload_type in rows if payload_type == "object"}}


def get_commentary(body: dict[str, Any]) -> dict[str, Any]:
    """按消息 id 批量取旁白：{"items": {"<id>": ["…", …]}}，没有旁白的 id 不出现。"""
    ids = body.get("ids")
    if not isinstance(ids, list) or len(ids) > MAX_COMMENTARY_IDS or not all(isinstance(i, int) and not isinstance(i, bool) and i > 0 for i in ids):
        raise ApiError(400, "invalid_ids", f"ids 需为不超过 {MAX_COMMENTARY_IDS} 个的正整数")
    if not ids:
        return {"items": {}}
    conn = sqlite3.connect(f"file:{HERMES_HOME / 'state.db'}?mode=ro", uri=True, timeout=5)
    try:
        conn.execute("PRAGMA query_only = 1")
        marks = ",".join("?" * len(ids))
        rows = conn.execute(
            f"SELECT id, codex_message_items FROM messages WHERE id IN ({marks}) AND role = 'assistant' "
            "AND codex_message_items IS NOT NULL AND codex_message_items != ''",
            list(dict.fromkeys(ids)),
        ).fetchall()
    finally:
        conn.close()
    items: dict[str, list[str]] = {}
    for mid, raw in rows:
        texts = _commentary_items(raw)
        if texts:
            items[str(mid)] = texts
    return {"items": items}


# ---------------------------------------------------------------- 活动（state.db 只读）


def get_activity(limit: int) -> dict[str, Any]:
    """最近的工具调用：从 messages 末尾按 id 倒序扫（主键，避免全表排序），连同对应结果判断成败。"""
    db = HERMES_HOME / "state.db"
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT m.id, m.session_id, m.tool_calls, m.timestamp, s.title, s.source "
            "FROM messages m LEFT JOIN sessions s ON s.id = m.session_id "
            "WHERE m.role = 'assistant' AND m.tool_calls IS NOT NULL AND m.tool_calls != '' AND m.tool_calls != '[]' "
            "ORDER BY m.id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        calls: list[dict[str, Any]] = []
        for r in rows:
            try:
                parsed = json.loads(r["tool_calls"])
            except (TypeError, json.JSONDecodeError):
                continue
            for c in parsed if isinstance(parsed, list) else []:
                fn = c.get("function") or {}
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                calls.append({
                    "callId": c.get("id"),
                    "tool": fn.get("name") or "tool",
                    "args": args if isinstance(args, dict) else {},
                    "sessionId": r["session_id"],
                    "title": r["title"],
                    "source": r["source"],
                    "timestamp": r["timestamp"],
                })
        ids = [c["callId"] for c in calls if c["callId"]]
        results: dict[str, str] = {}
        if ids:
            marks = ",".join("?" * len(ids))
            for r in conn.execute(f"SELECT tool_call_id, substr(content, 1, 600) AS c FROM messages WHERE role = 'tool' AND tool_call_id IN ({marks})", ids):
                results[r["tool_call_id"]] = r["c"] or ""
    finally:
        conn.close()

    for c in calls:
        # 进行中（pending）的调用也必须先过白名单、截断与脱敏：命令里可能夹带 token。
        c["args"] = _safe_args(c["args"])
        raw = results.get(c.pop("callId") or "", None)
        if raw is None:
            c["status"] = "pending"
            continue
        failed = False
        try:
            obj = json.loads(raw)
            if isinstance(obj, dict):
                failed = bool(obj.get("error")) or (isinstance(obj.get("exit_code"), int) and obj["exit_code"] != 0) or obj.get("success") is False
        except json.JSONDecodeError:
            failed = raw.lstrip().lower().startswith(("error", "traceback"))
        c["status"] = "failed" if failed else "done"
    return {"data": calls}


# ---------------------------------------------------------------- 第 8 段：共用工具

# 串行化所有会改配置/订阅的 CLI 调用（Hermes 自己会重写 config.yaml，并发写会互相覆盖）。
_WRITE_LOCK = threading.Lock()
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,63}$")

_REDACTIONS: list[tuple[re.Pattern[str], str]] = [
    # 边界只认 ASCII 字母数字：\b 把中文也当单词字符，"密钥mak_xxx" 这种紧挨着中文的密钥会漏掉。
    (re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_\-]{8,}"), "sk-***"),
    (re.compile(r"(?i)(?<![A-Za-z0-9])bearer\s+[A-Za-z0-9._~+/=\-]{6,}"), "Bearer ***"),
    (re.compile(r"""(?i)(?<![A-Za-z0-9])(api[_-]?key|access[_-]?token|token|secret|passw(?:or)?d|authorization|x-gitlab-token)(["']?\s*[:=]\s*)(?:(?:bearer|basic)\s+)?(?:"[^"]*"|'[^']*'|\S+)"""), r"\1\2***"),
    (re.compile(r"(?i)([?&](?:key|token|api_key|access_token|secret)=)[^&\s]+"), r"\1***"),
    (re.compile(r"(?<=://)[^/\s:@]*:[^@\s/]+(?=@)"), "***"),  # https://用户:密码@主机 里的凭据
    # 带前缀的长令牌：mak_…、ghp_…、xoxb_… 一类（前缀 2–6 个字母 + 下划线 + 24 位以上字母数字）。
    (re.compile(r"(?<![A-Za-z0-9])[A-Za-z]{2,6}_[A-Za-z0-9]{24,}(?![A-Za-z0-9])"), "***"),
    (re.compile(r"(?<![0-9A-Fa-f])[0-9a-fA-F]{32,}(?![0-9A-Fa-f])"), "***"),
]


def redact(text: str) -> str:
    """输出前遮蔽密钥样式字符串（日志、CLI 输出、命令行参数都过这一道）。"""
    for pattern, repl in _REDACTIONS:
        text = pattern.sub(repl, text)
    return text


# 工具调用参数只回传能说明"在做什么"的短字段，避免把大段代码/内容整块送到浏览器。
TOOL_ARG_KEYS = ("command", "path", "file_path", "query", "url", "pattern", "goal", "task", "name", "code")


def _safe_args(args: Any) -> dict[str, Any]:
    """白名单字段 → 先脱敏再截断到 200 字（先截断会把密钥切成脱敏规则认不出的残段）。"""
    if not isinstance(args, dict):
        return {}
    return {k: (redact(v)[:200] if isinstance(v, str) else v) for k, v in args.items() if k in TOOL_ARG_KEYS}


def config_get_json(key: str) -> Any:
    """`hermes config get --json <键>`：未设置返回 None。"""
    hit, cached = _cfg_cache_get(f"j:{key}")
    if hit:
        return cached
    stamp = _cfg_stamp()
    proc = _hermes("config", "get", "--json", key)
    out = _clean(proc.stdout)
    if "Config key not set" in out or "Config key not set" in proc.stderr:
        _cfg_cache_put(f"j:{key}", stamp, None)
        return None
    if proc.returncode != 0:
        raise ApiError(500, "config_get_failed", redact(_clean(proc.stderr) or out or "读取配置失败"))
    try:
        value = json.loads(out)
    except json.JSONDecodeError as exc:
        raise ApiError(500, "config_parse_failed", "clawd 返回的配置不是 JSON") from exc
    _cfg_cache_put(f"j:{key}", stamp, value)
    return value


def _require_name(name: Any, what: str) -> str:
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise ApiError(400, "invalid_name", f"{what}名称不合法（只允许字母、数字、下划线、连字符）")
    return name


def _cli_failure(proc: subprocess.CompletedProcess[str], fallback: str) -> ApiError:
    return ApiError(400, "cli_failed", redact(_clean(proc.stderr + "\n" + proc.stdout))[-500:] or fallback)


# ---------------------------------------------------------------- 用量统计（state.db 只读）

USAGE_DAYS = (7, 30, 90)


def get_usage(days: int) -> dict[str, Any]:
    if days not in USAGE_DAYS:
        raise ApiError(400, "invalid_days", f"days 只能是 {'/'.join(map(str, USAGE_DAYS))}")
    cutoff = time.time() - days * 86400
    conn = sqlite3.connect(f"file:{HERMES_HOME / 'state.db'}?mode=ro", uri=True, timeout=5)
    try:
        conn.execute("PRAGMA query_only = 1")
        conn.row_factory = sqlite3.Row

        def rows(sql: str, *params: Any) -> list[dict[str, Any]]:
            return [dict(r) for r in conn.execute(sql, params)]

        daily = rows(
            "SELECT date(started_at,'unixepoch','localtime') AS day, COUNT(*) AS sessions, "
            "COALESCE(SUM(input_tokens),0) AS input, COALESCE(SUM(output_tokens),0) AS output, "
            "COALESCE(SUM(cache_read_tokens),0) AS cacheRead, COALESCE(SUM(api_call_count),0) AS apiCalls "
            "FROM sessions WHERE started_at > ? GROUP BY day ORDER BY day", cutoff)
        by_model = rows(
            "SELECT COALESCE(NULLIF(model,''),'(未知)') AS model, COALESCE(NULLIF(billing_provider,''),'-') AS provider, "
            "COUNT(*) AS sessions, COALESCE(SUM(input_tokens),0) AS input, COALESCE(SUM(output_tokens),0) AS output, "
            "COALESCE(SUM(api_call_count),0) AS apiCalls "
            "FROM sessions WHERE started_at > ? GROUP BY 1,2 ORDER BY input+output DESC LIMIT 50", cutoff)
        by_source = rows(
            "SELECT COALESCE(NULLIF(source,''),'(未知)') AS source, COUNT(*) AS sessions, "
            "COALESCE(SUM(input_tokens),0) AS input, COALESCE(SUM(output_tokens),0) AS output "
            "FROM sessions WHERE started_at > ? GROUP BY 1 ORDER BY input+output DESC", cutoff)
        top = rows(
            "SELECT id, source, model, title, started_at AS startedAt, "
            "COALESCE(input_tokens,0)+COALESCE(output_tokens,0) AS totalTokens, "
            "COALESCE(api_call_count,0) AS apiCalls, COALESCE(message_count,0) AS messages "
            "FROM sessions WHERE started_at > ? ORDER BY totalTokens DESC LIMIT 10", cutoff)
        aux = rows(
            "SELECT u.task AS task, COUNT(DISTINCT u.session_id) AS sessions, COALESCE(SUM(u.api_call_count),0) AS apiCalls, "
            "COALESCE(SUM(u.input_tokens),0) AS input, COALESCE(SUM(u.output_tokens),0) AS output "
            "FROM session_model_usage u JOIN sessions s ON s.id = u.session_id "
            "WHERE s.started_at > ? AND u.task != '' GROUP BY u.task ORDER BY input+output DESC", cutoff)
    except sqlite3.Error as exc:
        raise ApiError(500, "usage_query_failed", f"读取用量失败：{exc}") from exc
    finally:
        conn.close()
    totals = {
        "sessions": sum(d["sessions"] for d in daily),
        "input": sum(d["input"] for d in daily),
        "output": sum(d["output"] for d in daily),
        "cacheRead": sum(d["cacheRead"] for d in daily),
        "apiCalls": sum(d["apiCalls"] for d in daily),
    }
    return {"days": days, "totals": totals, "daily": daily, "byModel": by_model, "bySource": by_source, "top": top, "aux": aux}


# ---------------------------------------------------------------- 日志

LOG_DIR = HERMES_HOME / "logs"
LOG_FILES = {"agent": "agent.log", "errors": "errors.log", "gateway": "gateway.log", "gui": "gui.log", "mcp": "mcp-stderr.log"}
LOG_JOURNAL = {"j-gateway": "hermes-gateway", "j-dashboard": "hermes-dashboard", "j-sidecar": "hermes-panel-sidecar"}
LOG_LABELS = {
    "agent": "Agent", "errors": "错误", "gateway": "网关", "gui": "旧面板", "mcp": "MCP 服务",
    "j-gateway": "网关 journal", "j-dashboard": "旧面板 journal", "j-sidecar": "辅助服务 journal",
}
LOG_LEVELS = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 4}
LOG_RECORD_RE = re.compile(r"^\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d(?:[,.]\d+)?\s+(DEBUG|INFO|WARNING|ERROR|CRITICAL)\s+(?:\[[^\]]*\]\s+)?(\S+?):")
LOG_COMPONENTS = {
    "gateway": ("gateway", "hermes_plugins", "plugins.platforms"),
    "agent": ("agent", "run_agent", "model_tools", "batch_runner"),
    "tools": ("tools",),
    "cli": ("hermes_cli", "cli"),
    "cron": ("cron",),
}
LOG_TAIL_BYTES = 6 * 1024 * 1024


def _tail_text(path: Path, max_bytes: int) -> list[str]:
    size = path.stat().st_size
    with path.open("rb") as f:
        f.seek(max(0, size - max_bytes))
        data = f.read()
    lines = data.decode("utf-8", errors="replace").splitlines()
    return lines[1:] if size > max_bytes and lines else lines


def _journal_lines(unit: str, n: int) -> list[str]:
    proc = subprocess.run(
        ["journalctl", "-u", f"{unit}.service", "-n", str(n), "--no-pager", "-o", "json"],
        capture_output=True, text=True, timeout=15, check=False,
    )
    if proc.returncode != 0:
        raise ApiError(500, "journal_failed", redact(proc.stderr.strip())[-300:] or "读取 journal 失败")
    out: list[str] = []
    for raw in proc.stdout.splitlines():
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            continue
        msg = entry.get("MESSAGE")
        if isinstance(msg, list):  # journald 对含不可打印字符的消息给字节数组
            msg = bytes(msg).decode("utf-8", errors="replace")
        msg = str(msg or "")
        if LOG_RECORD_RE.match(msg):
            out.append(msg)
        else:
            ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(entry.get("__REALTIME_TIMESTAMP", "0")) / 1e6))
            out.append(f"{ts} {msg}")
    return out


def _filter_log(lines: list[str], level: str, component: str, q: str, limit: int) -> list[str]:
    min_level = LOG_LEVELS.get(level, -1)
    prefixes = LOG_COMPONENTS.get(component, ())
    if min_level < 0 and not prefixes and not q:
        return lines[-limit:]
    needle = q.lower()
    kept: list[str] = []
    keep = False
    for line in lines:
        m = LOG_RECORD_RE.match(line)
        if m:  # 新记录：重新判定；无时间戳的续行（traceback 等）跟随上一条记录
            logger = m.group(2)
            keep = (min_level < 0 or LOG_LEVELS[m.group(1)] >= min_level) and (
                not prefixes or any(logger == p or logger.startswith(p + ".") or logger.startswith(p + "_") for p in prefixes))
        if keep and (not needle or needle in line.lower()):
            kept.append(line)
    return kept[-limit:]


def list_log_sources() -> dict[str, Any]:
    data = [{"id": sid, "label": LOG_LABELS[sid], "kind": "file", "available": (LOG_DIR / fn).is_file()} for sid, fn in LOG_FILES.items()]
    data += [{"id": sid, "label": LOG_LABELS[sid], "kind": "journal", "available": True} for sid in LOG_JOURNAL]
    return {"data": data, "components": list(LOG_COMPONENTS), "levels": list(LOG_LEVELS)}


def get_logs(source: str, lines: int, level: str, component: str, q: str) -> dict[str, Any]:
    lines = max(20, min(500, lines))
    level = level.upper()
    if level not in LOG_LEVELS:
        level = ""
    if component not in LOG_COMPONENTS:
        component = ""
    q = q[:200]
    filtering = bool(level or component or q)
    if source in LOG_FILES:
        path = LOG_DIR / LOG_FILES[source]
        try:
            raw = _tail_text(path, LOG_TAIL_BYTES) if path.is_file() else []
        except OSError as exc:
            raise ApiError(500, "log_read_failed", f"读取日志失败：{exc.strerror}") from exc
    elif source in LOG_JOURNAL:
        raw = _journal_lines(LOG_JOURNAL[source], 3000 if filtering else lines)
    else:
        raise ApiError(404, "unknown_source", "未知的日志来源")
    if q:
        # 搜索必须在脱敏后的文本上匹配：否则可以用 q=sk-a、sk-ab… 逐字符试探，从"有没有命中"反推出被遮蔽的密钥。
        raw = [redact(line[:2000]) for line in raw]
    return {"source": source, "lines": [redact(line[:2000]) for line in _filter_log(raw, level, component, q, lines)], "at": time.time()}


# ---------------------------------------------------------------- 模型与提供方（读写都走 hermes config，白名单键）

MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/\-]{0,127}$")


def _providers() -> dict[str, Any]:
    raw = config_get_json("providers")
    return raw if isinstance(raw, dict) else {}


def get_models() -> dict[str, Any]:
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:  # 三个键并行读，各起一个 hermes 进程
        f_model, f_providers, f_catalog = (pool.submit(config_get_json, k) for k in ("model", "providers", "model_catalog"))
        raw_model, raw_providers, catalog = f_model.result(), f_providers.result(), f_catalog.result()
    model = raw_model if isinstance(raw_model, dict) else {"default": raw_model} if isinstance(raw_model, str) else {}
    current = {k: model.get(k) for k in ("provider", "default", "base_url", "api_mode") if isinstance(model.get(k), (str, type(None)))}
    base_url_val = current.get("base_url")
    if isinstance(base_url_val, str):
        current["base_url"] = redact(base_url_val)
    providers = []
    for name, spec in (raw_providers if isinstance(raw_providers, dict) else {}).items():
        if not isinstance(spec, dict):
            continue
        models = spec.get("models")
        providers.append({
            "id": name,
            "name": spec.get("name") if isinstance(spec.get("name"), str) else name,
            "baseUrl": redact(spec["base_url"]) if isinstance(spec.get("base_url"), str) else None,
            "apiMode": spec.get("api_mode") if isinstance(spec.get("api_mode"), str) else None,
            "models": [m for m in models if isinstance(m, str)] if isinstance(models, list) else [],
            "discoverModels": bool(spec.get("discover_models")),
            "editable": bool(NAME_RE.match(name)),
        })
    excluded = catalog.get("excluded_providers") if isinstance(catalog, dict) else None
    return {"current": current, "providers": providers, "excludedProviders": [p for p in excluded if isinstance(p, str)] if isinstance(excluded, list) else []}


def _hermes_set(key: str, value: str) -> None:
    proc = _hermes("config", "set", key, value)
    if proc.returncode != 0 or "⚠" in proc.stdout + proc.stderr:
        raise _cli_failure(proc, "写入配置失败")


def _provider_for_edit(name: Any) -> str:
    if not isinstance(name, str) or name not in _providers():
        raise ApiError(404, "provider_not_found", "提供方不存在")
    if not NAME_RE.match(name):  # 含点号等的键路径需转义，面板不支持
        raise ApiError(400, "provider_unsupported", "该提供方名称含特殊字符，请在服务器上修改")
    return name


def set_default_model(model: Any, provider: Any) -> dict[str, Any]:
    if not isinstance(model, str) or not MODEL_ID_RE.match(model):
        raise ApiError(400, "invalid_model", "模型名不合法")
    with _WRITE_LOCK:
        if provider is not None:
            provider = _provider_for_edit(provider)
            if get_models()["current"].get("provider") != provider:
                _hermes_set("model.provider", provider)
        _hermes_set("model.default", model)
        after = get_models()["current"]
    if after.get("default") != model:
        raise ApiError(500, "model_unverified", "已提交但回读结果不一致，请刷新确认")
    log.info("default model -> %s (%s) via panel", model, after.get("provider"))
    return {"ok": True, "current": after, "note": "只对新会话生效"}


def set_provider_models(provider: Any, models: Any) -> dict[str, Any]:
    if not isinstance(models, list) or not 0 < len(models) <= 100 or not all(isinstance(m, str) and MODEL_ID_RE.match(m) for m in models):
        raise ApiError(400, "invalid_models", "模型列表需为 1–100 个合法模型名")
    with _WRITE_LOCK:
        name = _provider_for_edit(provider)
        _hermes_set(f"providers.{name}.models", json.dumps(models))
        after = next((p for p in get_models()["providers"] if p["id"] == name), None)
    if not after or after["models"] != models:
        raise ApiError(500, "models_unverified", "已提交但回读结果不一致，请刷新确认")
    log.info("provider %s models updated via panel", name)
    return {"ok": True, "provider": after}


def set_provider_discover(provider: Any, discover: Any) -> dict[str, Any]:
    if not isinstance(discover, bool):
        raise ApiError(400, "invalid_body", "需要布尔 discover")
    with _WRITE_LOCK:
        name = _provider_for_edit(provider)
        _hermes_set(f"providers.{name}.discover_models", "true" if discover else "false")
        after = next((p for p in get_models()["providers"] if p["id"] == name), None)
    if not after or after["discoverModels"] != discover:
        raise ApiError(500, "discover_unverified", "已提交但回读结果不一致，请刷新确认")
    return {"ok": True, "provider": after}


# ---------------------------------------------------------------- 定时任务默认模型（cron.model / cron.model_provider）
# Hermes 解析定时任务模型的优先级（cron/scheduler.py `_load_cron_job_config` / `_resolve_job_runtime`）：
#   任务自己固定的 model/provider  >  cron.model + cron.model_provider（所有未固定任务的默认）  >  主模型 model.default。
# 每次触发都重新读 config.yaml，所以改完下一次触发就生效。备用链是另一回事：全局 fallback_providers，本面板不碰。

def _cron_jobs_view() -> list[dict[str, Any]]:
    """只读解析 cron/jobs.json，只回传展示需要的字段（不含提示词、投递目标等）。"""
    try:
        raw = json.loads((HERMES_HOME / "cron" / "jobs.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    jobs = raw.get("jobs", raw) if isinstance(raw, dict) else raw
    if isinstance(jobs, dict):
        jobs = list(jobs.values())
    out: list[dict[str, Any]] = []
    for j in jobs if isinstance(jobs, list) else []:
        if not isinstance(j, dict) or not isinstance(j.get("id"), str):
            continue
        model = j.get("model") if isinstance(j.get("model"), str) and j["model"].strip() else None
        provider = j.get("provider") if isinstance(j.get("provider"), str) and j["provider"].strip() else None
        out.append({
            "id": j["id"], "name": redact(str(j.get("name") or j["id"]))[:80],
            "enabled": j.get("enabled", True) is not False, "model": model, "provider": provider,
        })
    return out


def get_cron_model() -> dict[str, Any]:
    cron = config_get_json("cron")
    cron = cron if isinstance(cron, dict) else {}
    def text(key: str) -> str | None:
        v = cron.get(key)
        return v.strip() if isinstance(v, str) and v.strip() else None
    main = get_models()["current"]
    return {
        "cronModel": text("model"), "cronProvider": text("model_provider"),
        "main": {"provider": main.get("provider"), "model": main.get("default")},
        "jobs": _cron_jobs_view(),
    }


def _hermes_unset(key: str) -> None:
    proc = _hermes("config", "unset", key)
    out = _clean(proc.stdout + "\n" + proc.stderr)
    # 本来就没设置过不算失败（清除是幂等的）。
    if proc.returncode != 0 and "not set" not in out.lower():
        raise _cli_failure(proc, "清除配置失败")


def set_cron_model(model: Any, provider: Any) -> dict[str, Any]:
    """设置（或清除）所有未固定模型的定时任务的默认模型。model 为空 = 清除，回到跟随主模型。"""
    clear = model is None or model == ""
    if not clear:
        if not isinstance(model, str) or not MODEL_ID_RE.match(model):
            raise ApiError(400, "invalid_model", "模型名不合法")
        provider = _provider_for_edit(provider)  # 必须是已配置的提供方，名字也要安全
    with _WRITE_LOCK:
        if clear:
            _hermes_unset("cron.model")
            _hermes_unset("cron.model_provider")
        else:
            # 先写提供方再写模型：调度器每次触发都重读配置，两次写之间恰好触发的话，
            # "新提供方 + 旧默认(跟随主模型)"比"旧提供方 + 新模型"更不容易出错（提供方只是路由）。
            _hermes_set("cron.model_provider", provider)
            _hermes_set("cron.model", model)
        after = get_cron_model()
    want_model = None if clear else model
    want_provider = None if clear else provider
    if after["cronModel"] != want_model or after["cronProvider"] != want_provider:
        raise ApiError(500, "cron_model_unverified", "已提交但回读结果不一致，请刷新确认")
    log.info("cron default model -> %s (%s) via panel", want_model, want_provider)
    return {"ok": True, **after, "note": "下一次定时任务触发时生效；已固定模型的任务不受影响"}


def set_excluded_providers(items: Any) -> dict[str, Any]:
    if not isinstance(items, list) or len(items) > 100 or not all(isinstance(p, str) and MODEL_ID_RE.match(p) and "." not in p for p in items):
        raise ApiError(400, "invalid_providers", "需要提供方名称列表")
    with _WRITE_LOCK:
        if items:
            _hermes_set("model_catalog.excluded_providers", json.dumps(items))
        else:
            proc = _hermes("config", "unset", "model_catalog.excluded_providers")
            if proc.returncode != 0:
                raise _cli_failure(proc, "清除失败")
        after = get_models()["excludedProviders"]
    if sorted(after) != sorted(items):
        raise ApiError(500, "excluded_unverified", "已提交但回读结果不一致，请刷新确认")
    return {"ok": True, "excludedProviders": after}


# ---------------------------------------------------------------- MCP

MCP_ALLOWED_FIELDS = ("command", "url", "enabled", "tools", "auth")


def _mcp_servers() -> dict[str, Any]:
    raw = config_get_json("mcp_servers")
    return raw if isinstance(raw, dict) else {}


def _mcp_view(name: str, spec: Any) -> dict[str, Any]:
    spec = spec if isinstance(spec, dict) else {}
    tools = spec.get("tools")
    args = spec.get("args")
    return {
        "name": name,
        "transport": "http" if spec.get("url") else "stdio",
        "command": redact(str(spec["command"])) if spec.get("command") else None,
        "args": [redact(str(a)) for a in args][:20] if isinstance(args, list) else [],
        "url": redact(str(spec["url"])) if spec.get("url") else None,
        "auth": spec.get("auth") if isinstance(spec.get("auth"), str) else None,
        "enabled": spec.get("enabled") is not False,
        "toolAllowlist": [str(t) for t in tools][:200] if isinstance(tools, list) else None,
        # 只给键名，不给值：env/headers 的值通常就是密钥。
        "envKeys": sorted(spec["env"]) if isinstance(spec.get("env"), dict) else [],
        "headerKeys": sorted(spec["headers"]) if isinstance(spec.get("headers"), dict) else [],
        "editable": spec.get("source") != "plugin",
    }


def list_mcp() -> dict[str, Any]:
    return {"data": [_mcp_view(n, s) for n, s in sorted(_mcp_servers().items())]}


def _mcp_name(name: Any) -> str:
    _require_name(name, "MCP 服务")
    if name not in _mcp_servers():
        raise ApiError(404, "mcp_not_found", "MCP 服务不存在")
    return name


def set_mcp_enabled(name: Any, enabled: Any) -> dict[str, Any]:
    if not isinstance(enabled, bool):
        raise ApiError(400, "invalid_body", "需要布尔 enabled")
    with _WRITE_LOCK:
        name = _mcp_name(name)
        _hermes_set(f"mcp_servers.{name}.enabled", "true" if enabled else "false")
        after = _mcp_servers().get(name)
    if not isinstance(after, dict) or (after.get("enabled") is not False) != enabled:
        raise ApiError(500, "mcp_toggle_unverified", "已提交但回读结果不一致，请刷新确认")
    log.info("mcp %s %s via panel", name, "enabled" if enabled else "disabled")
    return {"ok": True, "name": name, "enabled": enabled, "note": "新会话或网关重启后生效"}


def remove_mcp(name: Any) -> dict[str, Any]:
    with _WRITE_LOCK:
        name = _mcp_name(name)
        proc = _hermes("mcp", "remove", name)
        if proc.returncode != 0:
            raise _cli_failure(proc, "删除失败")
        still = name in _mcp_servers()
    if still:
        raise ApiError(500, "mcp_remove_unverified", "已提交但服务仍在配置里，请刷新确认")
    log.info("mcp %s removed via panel", name)
    return {"ok": True, "name": name}


def test_mcp(name: Any) -> dict[str, Any]:
    name = _mcp_name(name)
    proc = _hermes("mcp", "test", name, timeout=60)
    return {"ok": proc.returncode == 0, "output": redact(_clean(proc.stdout + "\n" + proc.stderr))[-3000:]}


# ---------------------------------------------------------------- 插件

_PLUGIN_CACHE: dict[str, Any] = {"at": 0.0, "stamp": None, "data": None}
PLUGIN_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,79}$")


def list_plugins(force: bool = False) -> dict[str, Any]:
    # `hermes plugins list --json` 约 1.6s：config.yaml 没变就缓存 300 秒；写操作后强制刷新。
    if force or _PLUGIN_CACHE["data"] is None or time.time() - _PLUGIN_CACHE["at"] > 300 or _PLUGIN_CACHE.get("stamp") != _cfg_stamp():
        stamp = _cfg_stamp()
        proc = _hermes("plugins", "list", "--json", timeout=60)
        if proc.returncode != 0:
            raise _cli_failure(proc, "读取插件列表失败")
        try:
            items = json.loads(_clean(proc.stdout))
        except json.JSONDecodeError as exc:
            raise ApiError(500, "plugins_parse_failed", "clawd 返回的插件列表不是 JSON") from exc
        _PLUGIN_CACHE.update(at=time.time(), stamp=stamp, data=[
            {
                "name": str(p.get("name", "")),
                "enabled": p.get("status") == "enabled",
                "status": str(p.get("status", "")),
                "version": str(p.get("version") or ""),
                "description": redact(str(p.get("description") or ""))[:300],
                "source": str(p.get("source") or ""),
            }
            for p in items if isinstance(p, dict) and p.get("name") and not p.get("removed")
        ])
    return {"data": _PLUGIN_CACHE["data"], "cachedAt": _PLUGIN_CACHE["at"]}


def set_plugin_enabled(name: Any, enabled: Any) -> dict[str, Any]:
    if not isinstance(name, str) or not PLUGIN_NAME_RE.match(name) or not isinstance(enabled, bool):
        raise ApiError(400, "invalid_body", "需要插件名与布尔 enabled")
    with _WRITE_LOCK:
        if name not in {p["name"] for p in list_plugins(force=True)["data"]}:
            raise ApiError(404, "plugin_not_found", "插件不存在")
        proc = _hermes("plugins", "enable" if enabled else "disable", name, timeout=60)
        if proc.returncode != 0:
            raise _cli_failure(proc, "切换失败")
        after = next((p for p in list_plugins(force=True)["data"] if p["name"] == name), None)
    if not after or after["enabled"] != enabled:
        raise ApiError(500, "plugin_toggle_unverified", "已提交但回读结果不一致，请刷新确认")
    log.info("plugin %s %s via panel", name, "enabled" if enabled else "disabled")
    return {"ok": True, "name": name, "enabled": enabled, "note": "平台类插件可能需要重启网关才生效"}


# ---------------------------------------------------------------- 配置档

PROFILE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,31}$")
ACTIVE_PROFILE_FILE = HERMES_HOME / "active_profile"


_PROFILE_CACHE: dict[str, Any] = {"at": 0.0, "stamp": None, "data": None}
PROFILE_CACHE_TTL_S = 120.0  # 含网关运行状态；写操作会立即清缓存


def _profiles_stamp() -> tuple[Any, ...]:
    def mt(path: Path) -> int | None:
        try:
            return path.stat().st_mtime_ns
        except OSError:
            return None
    return _cfg_stamp(), mt(HERMES_HOME / "profiles"), mt(ACTIVE_PROFILE_FILE)


def list_profiles() -> dict[str, Any]:
    stamp = _profiles_stamp()
    if _PROFILE_CACHE["data"] is not None and _PROFILE_CACHE["stamp"] == stamp and time.time() - _PROFILE_CACHE["at"] < PROFILE_CACHE_TTL_S:
        return _PROFILE_CACHE["data"]
    result = _list_profiles_uncached()
    _PROFILE_CACHE.update(at=time.time(), stamp=stamp, data=result)
    return result


def _list_profiles_uncached() -> dict[str, Any]:
    proc = _hermes("profile", "list", timeout=30)
    if proc.returncode != 0:
        raise _cli_failure(proc, "读取配置档失败")
    text = _clean(proc.stdout)
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or set(stripped) <= set("─- ") or (stripped.startswith("Profile") and "Model" in stripped):
            continue
        cells = re.split(r"\s{2,}", stripped.replace("◆", " ").strip())
        if not cells or not PROFILE_NAME_RE.match(cells[0]):
            continue
        rows.append({
            "name": cells[0], "active": "◆" in line,
            "model": cells[1] if len(cells) > 1 and cells[1] != "—" else None,
            "gateway": cells[2] if len(cells) > 2 else None,
            "alias": cells[3] if len(cells) > 3 and cells[3] != "—" else None,
        })
    # 解析不出任何行时带上原文，让用户看到真实输出而不是空列表。
    return {"data": rows, "raw": redact(text)[:2000] if not rows else None}


def show_profile(name: Any) -> dict[str, Any]:
    if not isinstance(name, str) or not PROFILE_NAME_RE.match(name):
        raise ApiError(400, "invalid_name", "配置档名称不合法")
    proc = _hermes("profile", "show", name, timeout=30)
    if proc.returncode != 0:
        raise _cli_failure(proc, "读取配置档失败")
    return {"name": name, "text": redact(_clean(proc.stdout))[:2000]}


def create_profile(name: Any, clone_from: Any, description: Any) -> dict[str, Any]:
    if not isinstance(name, str) or not PROFILE_NAME_RE.match(name) or name == "default":
        raise ApiError(400, "invalid_name", "名称需为小写字母、数字、下划线或连字符（最长 32），且不能是 default")
    args = ["profile", "create", name, "--no-alias"]
    if clone_from is not None:
        if not isinstance(clone_from, str) or not PROFILE_NAME_RE.match(clone_from):
            raise ApiError(400, "invalid_clone", "克隆来源不合法")
        args += ["--clone", f"--clone-from={clone_from}"]  # 克隆会复制 .env，由前端醒目提示
    if description:
        if not isinstance(description, str) or len(description) > 200:
            raise ApiError(400, "invalid_description", "描述需为 200 字以内的文本")
        args.append(f"--description={description}")
    with _WRITE_LOCK:
        if name in {p["name"] for p in list_profiles()["data"]}:
            raise ApiError(409, "profile_exists", "同名配置档已存在")
        proc = _hermes(*args, timeout=120)
        if proc.returncode != 0:
            raise _cli_failure(proc, "创建失败")
        created = name in {p["name"] for p in list_profiles()["data"]}
    if not created:
        raise ApiError(500, "profile_create_unverified", "已提交但列表里没有新配置档，请刷新确认")
    log.info("profile %s created via panel (clone_from=%s)", name, clone_from)
    return {"ok": True, "name": name}


def use_profile(name: Any) -> dict[str, Any]:
    if not isinstance(name, str) or not PROFILE_NAME_RE.match(name):
        raise ApiError(400, "invalid_name", "配置档名称不合法")
    with _WRITE_LOCK:
        if name not in {p["name"] for p in list_profiles()["data"]}:
            raise ApiError(404, "profile_not_found", "配置档不存在")
        proc = _hermes("profile", "use", name, timeout=30)
        if proc.returncode != 0:
            raise _cli_failure(proc, "切换失败")
        active = next((p["name"] for p in list_profiles()["data"] if p["active"]), None)
    if active != name:
        raise ApiError(500, "profile_use_unverified", "已提交但当前档未变化，请刷新确认")
    log.info("profile -> %s via panel", name)
    return {"ok": True, "name": name, "note": "只影响之后新启动的命令行与网关，运行中的网关需重启才会切换"}


def delete_profile(name: Any) -> dict[str, Any]:
    if not isinstance(name, str) or not PROFILE_NAME_RE.match(name):
        raise ApiError(400, "invalid_name", "配置档名称不合法")
    if name == "default":
        raise ApiError(400, "protected", "不允许删除 default 配置档")
    with _WRITE_LOCK:
        if name not in {p["name"] for p in list_profiles()["data"]}:
            raise ApiError(404, "profile_not_found", "配置档不存在")
        proc = _hermes("profile", "delete", name, "-y", timeout=60)
        if proc.returncode != 0:
            raise _cli_failure(proc, "删除失败")
        still = name in {p["name"] for p in list_profiles()["data"]}
    if still:
        raise ApiError(500, "profile_delete_unverified", "已提交但配置档仍在，请刷新确认")
    log.info("profile %s deleted via panel", name)
    return {"ok": True, "name": name}


# ---------------------------------------------------------------- Webhooks

WEBHOOK_FILE = HERMES_HOME / "webhook_subscriptions.json"
WEBHOOK_LOCK_FILE = HERMES_HOME / ".webhook_subscriptions.lock"
WEBHOOK_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,63}$")
WEBHOOK_TOKEN_RE = re.compile(r"^[A-Za-z0-9_:\-]{1,40}$")
WEBHOOK_POST_RE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d).*\[webhook\] POST event=(\S+) route=(\S+) prompt_len=(\d+) delivery=(\S+)")


@contextlib.contextmanager
def _webhook_lock(exclusive: bool):
    """与 Hermes 共用 `.webhook_subscriptions.lock`（flock），读共享、写独占。"""
    fd = os.open(WEBHOOK_LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        yield
    finally:
        os.close(fd)  # 关闭即释放锁


def _read_webhooks() -> dict[str, Any]:
    try:
        raw = json.loads(WEBHOOK_FILE.read_text(encoding="utf-8")) if WEBHOOK_FILE.is_file() else {}
    except (OSError, json.JSONDecodeError) as exc:
        raise ApiError(500, "webhook_read_failed", "读取 Webhook 订阅失败") from exc
    return raw if isinstance(raw, dict) else {}


def _webhook_view(name: str, spec: Any) -> dict[str, Any]:
    spec = spec if isinstance(spec, dict) else {}
    events = spec.get("events")
    prompt = spec.get("prompt")
    return {
        "name": name,
        "description": redact(str(spec.get("description") or ""))[:300],
        "events": [str(e) for e in events] if isinstance(events, list) else [],
        "deliver": str(spec["deliver"]) if spec.get("deliver") else None,
        "deliverOnly": bool(spec.get("deliver_only")),
        "path": f"/webhooks/{name}",
        "enabled": spec.get("enabled") is not False,
        "secretSet": bool(spec.get("secret")),  # secret 本身永不外送
        "hasScript": bool(spec.get("script")),
        "createdAt": spec.get("created_at") if isinstance(spec.get("created_at"), str) else None,
        "prompt": redact(prompt)[:400] if isinstance(prompt, str) else "",
    }


def _webhook_platform_enabled() -> bool:
    cfg = config_get_json("platforms.webhook")
    return isinstance(cfg, dict) and cfg.get("enabled") is True


def list_webhooks() -> dict[str, Any]:
    with _webhook_lock(False):
        subs = _read_webhooks()
    return {"platformEnabled": _webhook_platform_enabled(), "data": [_webhook_view(n, s) for n, s in sorted(subs.items())]}


def recent_deliveries(route: str, limit: int) -> dict[str, Any]:
    path = LOG_DIR / "gateway.log"
    if not path.is_file():
        return {"data": []}
    out: list[dict[str, Any]] = []
    for line in reversed(_tail_text(path, 2 * 1024 * 1024)):
        m = WEBHOOK_POST_RE.match(line)
        if not m or (route and m.group(3) != route):
            continue
        out.append({"time": m.group(1), "event": m.group(2), "route": m.group(3), "promptLen": int(m.group(4)), "delivery": m.group(5)})
        if len(out) >= limit:
            break
    return {"data": out}


def create_webhook(body: dict[str, Any]) -> dict[str, Any]:
    name, prompt = body.get("name"), body.get("prompt")
    if not isinstance(name, str) or not WEBHOOK_NAME_RE.match(name):
        raise ApiError(400, "invalid_name", "名称需为小写字母、数字、下划线或连字符")
    if not isinstance(prompt, str) or not 0 < len(prompt.strip()) <= 4000:
        raise ApiError(400, "invalid_prompt", "需要 4000 字以内的提示词")
    args = ["webhook", "subscribe", name, f"--prompt={prompt}"]
    description, deliver, events = body.get("description"), body.get("deliver"), body.get("events")
    if description:
        if not isinstance(description, str) or len(description) > 200:
            raise ApiError(400, "invalid_description", "描述需为 200 字以内的文本")
        args.append(f"--description={description}")
    if deliver:
        if not isinstance(deliver, str) or not WEBHOOK_TOKEN_RE.match(deliver):
            raise ApiError(400, "invalid_deliver", "投递目标不合法")
        args.append(f"--deliver={deliver}")
    if events:
        if not isinstance(events, list) or len(events) > 20 or not all(isinstance(e, str) and WEBHOOK_TOKEN_RE.match(e) for e in events):
            raise ApiError(400, "invalid_events", "事件列表不合法")
        args.append(f"--events={','.join(events)}")
    secret = secrets.token_urlsafe(32)  # 由 sidecar 生成，只在本次响应里返回一次
    args.append(f"--secret={secret}")
    with _WRITE_LOCK:
        if not _webhook_platform_enabled():
            raise ApiError(409, "platform_disabled", "Webhook 平台未开启，请先在服务器上开启并重启网关")
        with _webhook_lock(False):
            if name in _read_webhooks():
                raise ApiError(409, "webhook_exists", "同名 Webhook 已存在")
        proc = _hermes(*args, timeout=60)
        if proc.returncode != 0:
            raise _cli_failure(proc, "创建失败")
        with _webhook_lock(False):
            created = name in _read_webhooks()
    if not created:
        raise ApiError(500, "webhook_create_unverified", "已提交但订阅文件里没有新路由，请刷新确认")
    log.info("webhook %s created via panel", name)
    return {"ok": True, "name": name, "path": f"/webhooks/{name}", "secret": secret}


def delete_webhook(name: Any) -> dict[str, Any]:
    if not isinstance(name, str) or not WEBHOOK_NAME_RE.match(name):
        raise ApiError(400, "invalid_name", "名称不合法")
    with _WRITE_LOCK:
        with _webhook_lock(False):
            if name not in _read_webhooks():
                raise ApiError(404, "webhook_not_found", "Webhook 不存在")
        proc = _hermes("webhook", "remove", name, timeout=60)
        if proc.returncode != 0:
            raise _cli_failure(proc, "删除失败")
        with _webhook_lock(False):
            still = name in _read_webhooks()
    if still:
        raise ApiError(500, "webhook_delete_unverified", "已提交但路由仍在，请刷新确认")
    log.info("webhook %s deleted via panel", name)
    return {"ok": True, "name": name}


def set_webhook_enabled(name: Any, enabled: Any) -> dict[str, Any]:
    """Hermes 没有启停子命令：只改该路由的 `enabled` 字段，持独占锁、同目录临时文件 + 原子替换、保持 0600。"""
    if not isinstance(name, str) or not WEBHOOK_NAME_RE.match(name) or not isinstance(enabled, bool):
        raise ApiError(400, "invalid_body", "需要名称与布尔 enabled")
    with _WRITE_LOCK, _webhook_lock(True):
        subs = _read_webhooks()
        if not isinstance(subs.get(name), dict):
            raise ApiError(404, "webhook_not_found", "Webhook 不存在")
        subs[name]["enabled"] = enabled
        tmp = WEBHOOK_FILE.with_name(f".{WEBHOOK_FILE.name}.panel-{os.getpid()}")
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(subs, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, WEBHOOK_FILE)
        finally:
            tmp.unlink(missing_ok=True)
        after = _read_webhooks().get(name)
    if not isinstance(after, dict) or (after.get("enabled") is not False) != enabled:
        raise ApiError(500, "webhook_toggle_unverified", "已提交但回读结果不一致，请刷新确认")
    log.info("webhook %s %s via panel", name, "enabled" if enabled else "disabled")
    return {"ok": True, "name": name, "enabled": enabled}


def test_webhook(name: Any) -> dict[str, Any]:
    if not isinstance(name, str) or not WEBHOOK_NAME_RE.match(name):
        raise ApiError(400, "invalid_name", "名称不合法")
    with _webhook_lock(False):
        if name not in _read_webhooks():
            raise ApiError(404, "webhook_not_found", "Webhook 不存在")
    proc = _hermes("webhook", "test", name, timeout=60)
    return {"ok": proc.returncode == 0, "output": redact(_clean(proc.stdout + "\n" + proc.stderr))[-2000:]}


# ---------------------------------------------------------------- 会话任务索引（原生 SQLite 只读）

_SESSION_RUN_ACTIVE = frozenset({"queued", "running", "waiting_for_approval", "stopping"})
_SESSION_RUN_TERMINAL = frozenset({"completed", "cancelled", "failed", "interrupted"})
_SESSION_RUN_QUERY_S = 0.2
_SESSION_RUN_MAX_STEPS = 100_000


@contextlib.contextmanager
def _session_run_db(path: Path):
    deadline = time.monotonic() + _SESSION_RUN_QUERY_S
    steps = 0

    def stop_query() -> int:
        nonlocal steps
        steps += 1000
        return int(steps >= _SESSION_RUN_MAX_STEPS or time.monotonic() >= deadline)

    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=_SESSION_RUN_QUERY_S)
    try:
        conn.set_progress_handler(stop_query, 1000)
        conn.execute("PRAGMA query_only = 1")
        yield conn
    finally:
        conn.close()


def _session_run_epoch(value: Any) -> bool:
    # Epoch seconds, not booleans, NaN/infinity, or millisecond timestamps.
    return type(value) in (int, float) and 0 <= value < 253402300800


def _session_run_schema(conn: sqlite3.Connection, table: str,
                        required: dict[str, str], primary_key: tuple[str, ...]) -> None:
    # Table/column identifiers are constants, never derived from the request.
    kind = conn.execute("SELECT type FROM sqlite_master WHERE name = ?", (table,)).fetchone()
    columns = {r[1]: r for r in conn.execute(f'PRAGMA table_info("{table}")')}
    keys = tuple(r[1] for r in sorted(columns.values(), key=lambda r: r[5]) if r[5])
    if (kind != ("table",) or keys != primary_key
            or any(name not in columns or columns[name][2].upper() != typ for name, typ in required.items())):
        raise ValueError("incompatible native schema")


def _session_run_owner_alive(pid: int, started: int) -> bool:
    if pid == 0:
        return False
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    except FileNotFoundError:
        return False
    # comm can contain spaces/parentheses; fields after its last ')' start at field 3.
    _head, separator, tail = stat.rpartition(")")
    fields = tail.split()
    if not separator or len(fields) < 20 or not fields[19].isdigit():
        raise ValueError("unreadable native owner incarnation")
    return fields[0] not in ("Z", "X") and (started == 0 or int(fields[19]) == started)


# ---------------------------------------------------------------- 会话派生的后台进程

# Hermes tools/process_registry 把仍在运行的后台进程写进 processes.json（崩溃恢复用的检查点，命令已脱敏）。
# 回合用后台模式启动脚本后就结束了，面板只显示「已完成」，用户会以为任务做完了：这里只读地列出它们。
PROCESS_REGISTRY_MAX_BYTES = 1 << 20
PROCESS_COMMAND_MAX_CHARS = 300


def get_session_background(session_id: str) -> dict[str, Any]:
    """会话派生、此刻仍活着的后台进程。登记里的条目逐个核对 /proc：进程号还在且启动时刻一致才算，
    防止网关崩溃后检查点残留、或进程号被别的进程复用。无法核实的（非本机进程号）不列出。"""
    if not isinstance(session_id, str) or SESSION_ID_RE.fullmatch(session_id) is None:
        raise ApiError(400, "invalid_session", "会话 id 不合法")
    path = HERMES_HOME / "processes.json"
    try:
        with open(path, "rb") as f:
            raw = f.read(PROCESS_REGISTRY_MAX_BYTES + 1)
    except FileNotFoundError:
        return {"processes": []}
    except OSError as exc:
        raise ApiError(503, "process_registry_unavailable", "后台进程登记暂不可读，请稍后重试") from exc
    if len(raw) > PROCESS_REGISTRY_MAX_BYTES:
        raise ApiError(503, "process_registry_unavailable", "后台进程登记过大，暂不解析")
    try:
        entries = json.loads(raw)
    except ValueError as exc:  # 写入是原子替换，读到半截说明格式变了
        raise ApiError(503, "process_registry_unavailable", "后台进程登记格式无法识别") from exc
    if not isinstance(entries, list):
        raise ApiError(503, "process_registry_unavailable", "后台进程登记格式无法识别")
    found = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        owner = entry.get("parent_session_id") or entry.get("owner_task_id")
        pid, started = entry.get("pid"), entry.get("host_start_time")
        if owner != session_id or entry.get("pid_scope") != "host" or type(pid) is not int or pid <= 0:
            continue
        try:
            alive = _session_run_owner_alive(pid, started if type(started) is int and started > 0 else 0)
        except (OSError, ValueError):
            continue  # 读不懂 /proc 就不声称它在运行
        if not alive:
            continue
        command = entry.get("command")
        started_at = entry.get("started_at")
        found.append({
            "id": entry.get("session_id") if isinstance(entry.get("session_id"), str) else "",
            "command": command[:PROCESS_COMMAND_MAX_CHARS] if isinstance(command, str) else "",
            "startedAt": started_at if _session_run_epoch(started_at) else None,
        })
    return {"processes": found}


def _session_run_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    obj = dict(pairs)
    if len(obj) != len(pairs):
        raise ValueError("ambiguous native status")
    return obj


def _session_run_record(row: tuple[Any, ...], session_id: str) -> dict[str, Any]:
    run_id, raw, created_at, updated_at, owner_pid, owner_started = row
    if not isinstance(raw, str):
        raise ValueError("invalid native status")
    status = json.loads(raw, object_pairs_hook=_session_run_json_object)
    if (not isinstance(run_id, str) or re.fullmatch(r"run_[0-9a-f]{32}", run_id) is None
            or not isinstance(status, dict) or status.get("object") != "hermes.run"
            or status.get("run_id") != run_id or status.get("session_id") != session_id
            or not isinstance(status.get("status"), str)
            or status["status"] not in _SESSION_RUN_ACTIVE | _SESSION_RUN_TERMINAL
            or type(owner_pid) is not int or not 0 <= owner_pid <= 2147483647
            or type(owner_started) is not int or owner_started < 0
            or not all(_session_run_epoch(v) for v in
                       (created_at, updated_at, status.get("created_at"), status.get("updated_at")))
            or updated_at < created_at or status["updated_at"] < status["created_at"]):
        raise ValueError("invalid native run metadata")
    if status["status"] in _SESSION_RUN_ACTIVE and not _session_run_owner_alive(owner_pid, owner_started):
        status["status"] = "interrupted"  # A discovery hint only: do not persist it or invent a timestamp.
    return {key: status[key] for key in ("run_id", "session_id", "status", "created_at", "updated_at")}


def _session_lease_identity(holder: Any) -> tuple[int, int, str] | None:
    if not isinstance(holder, str):
        return None
    match = re.fullmatch(r"pid=([1-9][0-9]*):pidns=([1-9][0-9]*):turn=.+:platform=(cli|api_server|subagent)", holder)
    if match is None:
        return None  # Unknown/legacy lease formats remain busy, never guessed idle.
    return int(match[1]), int(match[2]), match[3]


def _session_lease_process(holder: Any, acquired_at: float) -> tuple[bool | None, int]:
    identity = _session_lease_identity(holder)
    if identity is None or identity[1] != os.stat("/proc/self/ns/pid").st_ino:
        return None, 0
    pid = identity[0]
    try:
        fields = Path(f"/proc/{pid}/stat").read_text(encoding="ascii").rpartition(")")[2].split()
    except FileNotFoundError:
        return False, 0
    if len(fields) < 20 or not fields[19].isdigit():
        raise ValueError("unreadable lease process")
    started = int(fields[19])
    started_at = time.time() - time.clock_gettime(time.CLOCK_BOOTTIME) + started / os.sysconf("SC_CLK_TCK")
    # A new incarnation started after this lease was acquired cannot own it.
    alive = fields[0] not in ("Z", "X") and started_at <= acquired_at + 0.05
    return alive, started


def _session_item_from_row(r: sqlite3.Row) -> dict[str, Any]:
    col_names = set(r.keys())
    last_active = r["last_activity_at"] if "last_activity_at" in col_names else None
    message_count = int(r["message_count"]) if "message_count" in col_names and r["message_count"] is not None else 0
    pinned = bool(r["pinned"]) if "pinned" in col_names and r["pinned"] is not None else False
    archived = bool(r["archived"]) if "archived" in col_names and r["archived"] is not None else False

    return {
        "id": r["id"],
        "source": r["source"],
        "title": r["title"] if "title" in col_names else None,
        "model": r["model"] if "model" in col_names else None,
        "started_at": r["started_at"],
        "last_active": last_active,
        "message_count": message_count,
        "preview": None,
        "pinned": pinned,
        "archived": archived,
    }


def _active_delegation_parents(conn: sqlite3.Connection, now: float) -> set[str]:
    """Read pinned async_delegation dispatch ownership; never prompts or result bodies."""
    table = conn.execute("SELECT type FROM sqlite_master WHERE name = 'async_delegations'").fetchone()
    if table is None:
        return set()  # Older runtimes have no background ledger.
    columns = {row[1]: row for row in conn.execute('PRAGMA table_info(async_delegations)')}
    required = {'delegation_id', 'state', 'dispatched_at', 'completed_at', 'owner_pid', 'owner_started_at'}
    owners = [key for key in ('origin_session_id', 'parent_session_id') if key in columns]
    if table[0] != 'table' or not required <= columns.keys() or not owners:
        raise ValueError('incompatible delegation ledger')
    owner_sql = 'COALESCE(' + ', '.join(f"NULLIF({key}, '')" for key in owners) + ", '')"
    records = conn.execute(
        f'SELECT {owner_sql}, owner_pid, owner_started_at, dispatched_at, completed_at '
        "FROM async_delegations WHERE state IN ('running', 'stalling', 'finalizing') LIMIT 1001"
    ).fetchall()
    if len(records) > 1000:
        raise ValueError('delegation discovery budget exceeded')
    found: set[str] = set()
    checked: dict[tuple[int, int], bool] = {}
    for parent, pid, started, dispatched, completed in records:
        if (not isinstance(parent, str) or SESSION_ID_RE.fullmatch(parent) is None
                or type(pid) is not int or not 0 < pid <= 2147483647
                or type(started) is not int or started <= 0
                or not _session_run_epoch(dispatched) or dispatched > now or completed is not None):
            raise ValueError('unverifiable delegation ownership')
        identity = (pid, started)
        if identity not in checked:
            checked[identity] = _session_run_owner_alive(pid, started)
        if checked[identity]:
            found.add(parent)
    return found


def get_active_sessions() -> dict[str, Any]:
    """Return currently active/executing sessions excluding cron/webhook.

    Uses valid unexpired session_turn_leases joined with sessions, checking
    process liveness with _session_lease_process. Sessions whose background
    subagents are currently executing also discover and present their non-subagent
    root parent conversation. Absence or incompatible schema returns
    ApiError(503, "session_active_unavailable", ...).
    """
    try:
        with _session_run_db(HERMES_HOME / "state.db") as conn:
            _session_run_schema(conn, "sessions", {
                "id": "TEXT",
                "source": "TEXT",
                "started_at": "REAL",
            }, ("id",))
            _session_run_schema(conn, "session_turn_leases", {
                "conversation_id": "TEXT",
                "holder": "TEXT",
                "acquired_at": "REAL",
                "expires_at": "REAL",
            }, ("conversation_id",))

            conn.row_factory = sqlite3.Row

            now = time.time()
            rows = conn.execute(
                "SELECT s.*, l.holder, l.acquired_at, l.expires_at "
                "FROM session_turn_leases l "
                "JOIN sessions s ON s.id = l.conversation_id"
            ).fetchall()

            active_by_id: dict[str, dict[str, Any]] = {}
            pending_parents = _active_delegation_parents(conn, now)

            for r in rows:
                acquired_at = r["acquired_at"]
                expires_at = r["expires_at"]
                if not (_session_run_epoch(acquired_at) and _session_run_epoch(expires_at)):
                    raise ValueError("invalid lease timestamp")
                if acquired_at > expires_at:
                    raise ValueError("corrupt lease timestamp order")
                if expires_at <= now:
                    continue

                holder = r["holder"]
                alive, _started = _session_lease_process(holder, acquired_at)
                if alive is False:
                    continue

                source = r["source"]
                if source == "subagent":
                    # Trace to parent session
                    col_names = set(r.keys())
                    parent_id = r["parent_session_id"] if "parent_session_id" in col_names else None
                    if isinstance(parent_id, str) and parent_id:
                        pending_parents.add(parent_id)
                elif source not in ("cron", "webhook"):
                    active_by_id[r["id"]] = _session_item_from_row(r)

            if pending_parents:
                # Resolve subagent ancestry to root non-subagent parent
                # Bounded iteration prevents cycles
                curr_targets = set(pending_parents)
                visited_parents: set[str] = set()
                resolved_parent_ids: set[str] = set()

                for _ in range(10):
                    if not curr_targets:
                        break
                    to_query = [pid for pid in curr_targets if pid not in visited_parents]
                    if not to_query:
                        break
                    visited_parents.update(to_query)
                    placeholders = ",".join("?" for _ in to_query)
                    p_rows = conn.execute(
                        f"SELECT * FROM sessions WHERE id IN ({placeholders})",
                        to_query
                    ).fetchall()
                    next_targets: set[str] = set()
                    for pr in p_rows:
                        if "ended_at" in pr.keys() and pr["ended_at"] is not None:
                            continue
                        p_source = pr["source"]
                        if p_source == "subagent":
                            p_cols = set(pr.keys())
                            up = pr["parent_session_id"] if "parent_session_id" in p_cols else None
                            if isinstance(up, str) and up:
                                next_targets.add(up)
                        elif p_source not in ("cron", "webhook"):
                            resolved_parent_ids.add(pr["id"])
                            if pr["id"] not in active_by_id:
                                active_by_id[pr["id"]] = _session_item_from_row(pr)
                    curr_targets = next_targets

            return {"data": list(active_by_id.values())}
    except (sqlite3.Error, OSError, ValueError, TypeError, RecursionError) as exc:
        raise ApiError(503, "session_active_unavailable", "当前活动会话状态暂不可用，请稍后重试") from exc


def get_session_run(session_id: str, *, idempotency_key: str | None = None) -> dict[str, Any]:
    """Discovery only; the API server remains authoritative for run access/status."""
    if not isinstance(session_id, str) or SESSION_ID_RE.fullmatch(session_id) is None:
        raise ApiError(400, "invalid_session", "会话 id 不合法")
    if idempotency_key is not None and (
            not isinstance(idempotency_key, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}", idempotency_key) is None):
        raise ApiError(400, "invalid_idempotency_key", "请求标识不合法")
    try:
        with _session_run_db(HERMES_HOME / "state.db") as conn:
            _session_run_schema(conn, "sessions", {"id": "TEXT"}, ("id",))
            if conn.execute("SELECT 1 FROM sessions WHERE id = ? LIMIT 1", (session_id,)).fetchone() is None:
                raise ApiError(404, "session_not_found", "会话不存在")
            _session_run_schema(conn, "session_turn_leases", {
                "conversation_id": "TEXT", "holder": "TEXT", "acquired_at": "REAL", "expires_at": "REAL",
            }, ("conversation_id",))
            lease = conn.execute(
                "SELECT acquired_at, expires_at, holder FROM session_turn_leases WHERE conversation_id = ? LIMIT 1",
                (session_id,)).fetchone()
            if lease is not None and (not all(_session_run_epoch(v) for v in lease[:2]) or lease[0] > lease[1]):
                raise ApiError(503, "session_run_unavailable", "会话任务状态暂不可用，请稍后重试")
            busy = lease is not None and lease[1] > time.time()
            if busy and lease is not None:
                alive, _started = _session_lease_process(lease[2], lease[0])
                if alive is False:
                    busy = False
        result: dict[str, Any] = {"run": None, "busy": busy}
        if busy and lease is not None:
            result["acquired_at"] = lease[0]
        run_db = HERMES_HOME / "runs_idempotency.db"
        try:
            run_db.stat()
        except FileNotFoundError:
            if idempotency_key is not None:
                raise ApiError(503, "session_run_unavailable", "请求索引暂不可用，请稍后重试")
            return result  # Legacy client without a durable index; busy was still checked.
        # Only absence means legacy; permission and I/O failures stay unavailable.
        with _session_run_db(run_db) as conn:
            _session_run_schema(conn, "run_idempotency", {
                "scope": "TEXT", "idempotency_key": "TEXT", "fingerprint": "TEXT",
                "run_id": "TEXT", "status_json": "TEXT", "owner_pid": "INTEGER",
                "owner_started": "INTEGER", "retention_until": "REAL", "acknowledged_at": "REAL",
                "created_at": "REAL", "updated_at": "REAL",
            }, ("scope", "idempotency_key"))
            if idempotency_key is not None:
                # Never substitute the latest session run for the original admission key.
                rows = conn.execute(
                    "SELECT run_id, status_json, created_at, updated_at, owner_pid, owner_started, retention_until "
                    "FROM run_idempotency WHERE idempotency_key = ? LIMIT 2", (idempotency_key,)).fetchall()
                if len(rows) > 1:
                    raise ValueError("ambiguous admission key")
                result["resolution"] = {"idempotency_key": idempotency_key, "state": "not_found"}
                if rows:
                    row = rows[0]
                    retention = row[6]
                    if (type(retention) not in (int, float) or not math.isfinite(retention)
                            or retention < 0 or (retention != 0 and retention <= time.time())):
                        raise ValueError("expired or invalid admission retention")
                    public = json.loads(row[1], object_pairs_hook=_session_run_json_object)
                    if not isinstance(public, dict) or not isinstance(public.get("session_id"), str):
                        raise ValueError("invalid admission identity")
                    if public["session_id"] != session_id:
                        raise ApiError(409, "session_mismatch", "请求标识不属于当前会话")
                    result["run"] = _session_run_record(row[:6], session_id)
                    result["resolution"]["state"] = "found"
                return result
            # An unscopable record is uncertainty, never evidence that this session is idle.
            row = conn.execute(
                "SELECT run_id, status_json, created_at, updated_at, owner_pid, owner_started "
                "FROM run_idempotency "
                "WHERE json_type(status_json, '$.session_id') IS NOT 'text' "
                "OR json_extract(status_json, '$.session_id') = ? "
                "ORDER BY created_at DESC, updated_at DESC, run_id DESC LIMIT 1", (session_id,)).fetchone()
            if row is not None:
                result["run"] = _session_run_record(row, session_id)
        return result
    except (sqlite3.Error, OSError, ValueError, TypeError, RecursionError) as exc:
        raise ApiError(503, "session_run_unavailable", "会话任务状态暂不可用，请稍后重试") from exc


# CLI control is deliberately separate from native API-run cancellation. Never kill a shared gateway.
_SESSION_EXECUTION_LOCK = threading.Lock()
_SESSION_STOP_STAGES = ((signal.SIGINT, 0.4), (signal.SIGTERM, 0.6), (signal.SIGKILL, 0.6))


def _session_execution_snapshot(session_id: str) -> dict[str, Any] | None:
    if not isinstance(session_id, str) or SESSION_ID_RE.fullmatch(session_id) is None:
        raise ApiError(400, "invalid_session", "会话 id 不合法")
    with _session_run_db(HERMES_HOME / "state.db") as conn:
        row = conn.execute("SELECT source FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            raise ApiError(404, "session_not_found", "会话不存在")
        lease = conn.execute("SELECT holder, acquired_at, expires_at FROM session_turn_leases WHERE conversation_id = ?", (session_id,)).fetchone()
        if lease is None:
            return None
        holder, acquired, expires = lease
        if not all(_session_run_epoch(v) for v in (acquired, expires)) or acquired > expires:
            raise ValueError("invalid execution lease")
        if expires <= time.time():
            return None
        alive, started = _session_lease_process(holder, acquired)
        if alive is False:
            return None
        identity = _session_lease_identity(holder)
        controllable = (alive is True and row[0] == "cli" and identity is not None
                        and identity[2] == "cli" and f":turn={session_id}:" in holder)
        pid = identity[0] if identity else 0
        if controllable:
            shared = conn.execute("SELECT 1 FROM session_turn_leases WHERE conversation_id != ? AND expires_at > ? AND holder LIKE ? LIMIT 1", (session_id, time.time(), f"pid={pid}:%")).fetchone()
            controllable = shared is None and pid not in (1, os.getpid(), os.getppid())
        if controllable:
            command = Path(f"/proc/{pid}/cmdline").read_bytes()
            cgroup = Path(f"/proc/{pid}/cgroup").read_text(encoding="ascii")
            controllable = b"hermes_cli.main" in command and "hermes-gateway.service" not in cgroup
        execution_id = hashlib.sha256(json.dumps([session_id, holder, acquired, started]).encode()).hexdigest()
        return {"holder": holder, "acquired_at": acquired, "pid": pid, "started": started,
                "id": execution_id, "can_stop": controllable}


def get_session_execution(session_id: str) -> dict[str, Any]:
    try:
        execution = _session_execution_snapshot(session_id)
        return {"busy": execution is not None, "execution": {
            "id": execution["id"], "kind": "cli", "started_at": execution["acquired_at"],
        } if execution is not None and execution["can_stop"] else None}
    except (sqlite3.Error, OSError, ValueError, TypeError) as exc:
        raise ApiError(503, "session_execution_unavailable", "暂时无法核验执行器身份") from exc


def stop_session_execution(body: dict[str, Any]) -> dict[str, Any]:
    if (not isinstance(body, dict) or set(body) != {"session_id", "execution_id", "confirm"}
            or body.get("confirm") != "stop-cli" or not isinstance(body.get("execution_id"), str)
            or re.fullmatch(r"[0-9a-f]{64}", body["execution_id"]) is None):
        raise ApiError(400, "invalid_execution_stop", "停止请求不完整")
    sid, requested = body["session_id"], body["execution_id"]
    with _SESSION_EXECUTION_LOCK:
        try:
            target = _session_execution_snapshot(sid)
            if target is None or not target["can_stop"] or not hmac.compare_digest(target["id"], requested):
                raise ApiError(409, "execution_changed", "执行器已变化或不支持安全停止，请刷新任务状态")
            if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
                raise ApiError(503, "execution_stop_unsupported", "当前系统不支持安全停止独立执行器")
            # pidfd binds signals to this incarnation even if Linux reuses the numeric PID.
            fd = os.pidfd_open(target["pid"])
            try:
                for sig, grace in _SESSION_STOP_STAGES:
                    current = _session_execution_snapshot(sid)
                    if current is None:
                        break
                    if not current["can_stop"] or current["id"] != requested:
                        raise ApiError(409, "execution_changed", "会话已切换执行器，已停止后续信号")
                    try:
                        signal.pidfd_send_signal(fd, sig)
                    except ProcessLookupError:
                        break
                    if select.select([fd], [], [], grace)[0]:
                        break
                else:
                    raise ApiError(504, "execution_stop_timeout", "执行器未结束，保留会话占用，请稍后重试")
                alive = _session_run_owner_alive(target["pid"], target["started"])
                # A CLI may handle SIGINT by releasing the turn and stay open at its prompt.
                current = _session_execution_snapshot(sid)
                if current is not None and current["id"] != requested:
                    raise ApiError(409, "execution_changed", "会话已开始新的任务，未清理新任务占用")
                if alive and current is None:
                    with _session_run_db(HERMES_HOME / "state.db") as conn:
                        remaining = conn.execute("SELECT 1 FROM session_turn_leases WHERE conversation_id = ?", (sid,)).fetchone()
                    if remaining is not None:
                        raise ApiError(409, "execution_changed", "租约已过期或身份不明，不能确认任务已停止")
                if alive and current is not None:
                    raise ApiError(504, "execution_stop_timeout", "执行器仍在运行，未清理占用")
                if not alive:
                    with contextlib.closing(sqlite3.connect((HERMES_HOME / "state.db").resolve().as_uri() + "?mode=rw", uri=True, timeout=0.2)) as conn:
                        with conn:
                            conn.execute("DELETE FROM session_turn_leases WHERE conversation_id = ? AND holder = ? AND acquired_at = ?", (sid, target["holder"], target["acquired_at"]))
                            if conn.execute("SELECT 1 FROM session_turn_leases WHERE conversation_id = ?", (sid,)).fetchone() is not None:
                                raise ApiError(409, "execution_changed", "会话已有新的占用，未清理新任务")
                log.info("Stopped isolated CLI execution for session %s", sid)
                return {"status": "cancelled", "busy": False}
            finally:
                os.close(fd)
        except (sqlite3.Error, OSError, ValueError, TypeError) as exc:
            raise ApiError(503, "session_execution_unavailable", "停止未能完成，请重新核验执行器状态") from exc


# ---------------------------------------------------------------- 后台子任务完成后自动续跑（服务端）

# 顶层 delegate_task 一律后台运行，主模型派完即结束本轮；api_server 会话里子代理完成只写一行
# display_kind=async_delegation_complete 的通知、不起新回合（Hermes gateway/wake.py persist_delegation_delivery，
# 上游 #85957：客户端拥有下一轮），TUI 则会直接唤醒。这里在服务端补上这一步，所有设备都关着也能接着干。
# 规则与前端 transcript.ts continuationState 一致：完成通知晚于助手最后一条消息、其后没有人写的消息 → 续跑。
# 认不出数据（表结构/格式变了）就不动：最坏退回「停住，等用户发话」，绝不乱发请求。
CONTINUE_KEY_FILE = Path(os.environ.get("PANEL_API_KEY_FILE", "/etc/hermes-panel/api-server.key"))
CONTINUE_API = os.environ.get("PANEL_API_SERVER", "http://127.0.0.1:8642")
CONTINUE_POLL_S = 5
CONTINUE_WINDOW_S = 2 * 3600
CONTINUE_MAX_ATTEMPTS = 5
AUTO_CONTINUE_MARK = "[面板自动续跑]"
# 与 src/lib/transcript.ts AUTO_CONTINUE_TEXT 相同（面板据前缀把它显示成通知）。
AUTO_CONTINUE_TEXT = (f"{AUTO_CONTINUE_MARK} 你之前派出的后台子任务已完成，结果见上方的完成通知。"
                      "请据此继续完成之前的任务；若此前是在等用户确认或回答，就继续等待并说明。")
AUTO_CONTINUE_LIMIT = 5
# Hermes agent/context_compressor.py _SYNTHETIC_USER_ROW_PREFIXES 中不是人话的部分（插话、子任务通知另有 display_kind）。
_SCAFFOLD_PREFIXES = ("[System:", "[CONTEXT", "[PRIOR CONTEXT", "[IMPORTANT: Background", "[Your active task list",
                      "[Planning state preserved", "[Skills pruned", "[ASYNC DELEGATION", "Cronjob Response:")
_CONTENT_JSON_PREFIX = "\x00json:"  # hermes_state.py：结构化内容以此前缀存成 JSON
_continue_done: dict[str, float] = {}
_continue_failures: dict[str, int] = {}
_continue_failure_at: dict[str, float] = {}
CONTINUE_FAILURE_CACHE = 1024
# 全表找完成通知没有可用索引（线上 3 万行约 0.8 秒）：只每 5 分钟做一次兜底，平时走索引找候选会话。
CONTINUE_SWEEP_S = 300
# Hermes tools/async_delegation.py _MAX_DELIVERY_ATTEMPTS：投递重试耗尽即转 dropped。
CONTINUE_DELIVERY_ATTEMPTS = 8
# 单个会话评估时可能遇到的数据/库问题：只跳过该会话，不中止整个 tick。
_CONTINUE_SESSION_ERRORS = (sqlite3.Error, OSError, ValueError, TypeError, KeyError, RecursionError)
# 候选会话：sid → 观察到期时间（最近一条完成通知 + 窗口期）。到期前每个 tick 都重新评估，失败/租约占用后 5 秒即可重试。
_continue_watch: dict[str, float] = {}
_continue_sweep: dict[str, Any] = {"home": None, "at": 0.0}
_continue_route_cache: dict[str, Any] = {"at": 0.0, "map": {}}
_continue_warned: set[str] = set()


def _continue_text(content: Any) -> str:
    if not isinstance(content, str):
        return ""
    if content.startswith(_CONTENT_JSON_PREFIX):
        try:
            parts = json.loads(content[len(_CONTENT_JSON_PREFIX):])
        except ValueError:
            return ""
        if isinstance(parts, list):
            return "\n\n".join(p["text"] for p in parts if isinstance(p, dict) and p.get("type") == "text" and isinstance(p.get("text"), str))
        return ""
    return content


def _continue_is_human(role: str, kind: Any, text: str) -> bool:
    head = text.lstrip()
    return role == "user" and not kind and not head.startswith(AUTO_CONTINUE_MARK) and not head.startswith(_SCAFFOLD_PREFIXES)


def continuation_trigger(rows: list[tuple[Any, ...]]) -> tuple[int | None, bool]:
    """rows：(id, role, display_kind, content, display_metadata)，按展示顺序。返回 (触发它的完成通知行 id, 是否已达连续上限)。"""
    last_assistant = last_human = -1
    texts = [_continue_text(r[3]) for r in rows]
    for i, r in enumerate(rows):
        if r[1] == "assistant":
            last_assistant = i
        if _continue_is_human(r[1], r[2], texts[i]):
            last_human = i
    trigger: int | None = None
    streak = 0
    for i, r in enumerate(rows):
        if r[2] == "async_delegation_complete":
            meta: Any = r[4]
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except ValueError:
                    meta = {}
            suppressed = isinstance(meta, dict) and meta.get("presentation_suppressed") is True
            interim = (isinstance(meta, dict) and bool(meta.get("delivery_notice"))) or texts[i].startswith("[ASYNC DELEGATION TASK FAILED — ")
            if i > last_assistant and i > last_human and isinstance(r[0], int) and not suppressed and not interim:
                trigger = r[0]
        elif i > last_human and r[1] == "user" and not r[2] and texts[i].lstrip().startswith(AUTO_CONTINUE_MARK):
            streak += 1
    return trigger, trigger is not None and streak >= AUTO_CONTINUE_LIMIT


def _continue_warn(key: str, message: str, *args: Any) -> None:
    """同一类问题只记一次日志，避免每 5 秒刷屏。"""
    if key not in _continue_warned:
        _continue_warned.add(key)
        log.warning(message, *args)


def _continue_api(method: str, path: str, api_key: str, body: Any = None, headers: dict[str, str] | None = None) -> tuple[int, Any]:
    import urllib.error
    import urllib.request

    req = urllib.request.Request(CONTINUE_API + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 本机固定地址
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, None


def _continue_provider(model: str, api_key: str) -> str | None:
    """会话锁定的模型属于哪个提供方（/v1/runs 不读会话模型锁，须显式带 model+provider）。清单缓存 10 分钟。"""
    cache = _continue_route_cache
    if time.time() - cache["at"] > 600:
        status, data = _continue_api("GET", "/api/model/options", api_key)
        if status != 200 or not isinstance(data, dict):
            return None
        mapping: dict[str, str] = {}
        for p in data.get("providers") or []:
            if isinstance(p, dict) and p.get("authenticated"):
                slug = p.get("slug") or p.get("id") or p.get("name")
                for m in p.get("models") or []:
                    mid = m if isinstance(m, str) else (m.get("id") if isinstance(m, dict) else None)
                    if isinstance(mid, str) and isinstance(slug, str):
                        mapping.setdefault(mid, slug)
        cache.update(at=time.time(), map=mapping)
    return cache["map"].get(model)


def _continue_user_stopped(session_id: str) -> bool:
    """会话最近一次运行被停止（用户按「停止任务」= 放弃任务）：之后回来的子任务结果不应把任务续起来。
    依据 Hermes 原生运行索引（面板发起的运行都带 Idempotency-Key，会进这个库）；库不存在视为未停止。"""
    db = HERMES_HOME / "runs_idempotency.db"
    if not db.exists():
        return False
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=3)
    try:
        conn.execute("PRAGMA query_only = 1")
        row = conn.execute(
            "SELECT json_extract(status_json, '$.status') FROM run_idempotency "
            "WHERE json_extract(status_json, '$.session_id') = ? ORDER BY created_at DESC LIMIT 1", (session_id,)).fetchone()
    finally:
        conn.close()
    return row is not None and row[0] == "cancelled"


def _continue_notice_id(row: tuple[Any, ...]) -> str | None:
    """Stable producer unit identity; never a timestamp/name proximity match."""
    raw = row[4]
    try:
        meta = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        meta = None
    if (isinstance(meta, dict) and meta.get("delivery_notice")) or _continue_text(row[3]).startswith("[ASYNC DELEGATION TASK FAILED — "):
        return None  # An interim per-child failure is never the final unit identity.
    did = meta.get("delegation_id") if isinstance(meta, dict) else None
    if isinstance(did, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,160}", did):
        return did
    # Public process_registry_notifications.py's final completion envelopes.
    match = re.match(r"^\[ASYNC DELEGATION (?:BATCH )?COMPLETE — ([A-Za-z0-9_-]{1,160})\](?:\n|$)", _continue_text(row[3]))
    return match.group(1) if match else None


def _continue_delegations(conn: sqlite3.Connection, now: float) -> list[dict[str, Any]]:
    """Read the public producer's durable unit results, never infer them from ended children.

    origin_session is a gateway routing key, NOT the raw API session id. Prefer
    origin_session_id, falling back to parent_session_id only when absent/empty.
    Unsupported optional ledger shapes disable recovery, not normal deliveries.
    """
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(async_delegations)")}
        required = {"delegation_id", "state", "dispatched_at", "completed_at", "delivery_state", "delivery_attempts",
                    "event_json", "result_json", "task_json", "delivery_claim", "delivery_claimed_at"}
        owners = [c for c in ("origin_session_id", "parent_session_id") if c in cols]
        if not cols:
            return []  # 旧版 Hermes 没有委派账本：只靠完成通知续跑
        if not required <= cols or not owners:
            _continue_warn("delegation-columns", "auto-continue: delegation ledger lacks %s; lost-notice recovery disabled",
                           ", ".join(sorted(required - cols)) or "owner columns")
            return []
        owner_sql = "COALESCE(" + ", ".join(f"NULLIF({c}, '')" for c in owners) + ", '')"
        # 通知丢失有两种落库形态：仍是 pending（投递方挂了、认领过期），或投递重试耗尽变成 dropped——
        # api_server 会话回合持租约时写通知被拒，8 次重试约 15 秒内耗尽（线上 3 天 11 例全是这种）。
        # 用户 /new 等显式丢弃的 dropped 不满 8 次，且父会话已结束，两头都会被排除。
        records = conn.execute(
            f"SELECT delegation_id, {owner_sql}, state, dispatched_at, completed_at, event_json, result_json, task_json "
            "FROM async_delegations WHERE completed_at > ? AND state IN ('completed', 'error') AND completed_at <= ? "
            "AND ((delivery_state = 'pending' AND (delivery_claim IS NULL OR delivery_claimed_at < ?)) "
            "  OR (delivery_state = 'dropped' AND delivery_attempts >= ?)) ORDER BY completed_at, delegation_id",
            (now - CONTINUE_WINDOW_S, now - 15, now - 300, CONTINUE_DELIVERY_ATTEMPTS)).fetchall()
    except sqlite3.Error as exc:
        _continue_warn("delegation-schema", "auto-continue: optional delegation ledger unavailable: %s", exc)
        return []
    recovered = []
    for did, sid, state, dispatched, completed, event_raw, result_raw, task_raw in records:
        try:
            event, result, task = json.loads(event_raw), json.loads(result_raw), json.loads(task_raw)
        except (ValueError, TypeError):
            continue
        if not all(isinstance(v, dict) for v in (event, result, task)):
            continue
        results = event.get("results")
        if not sid or event.get("delegation_id") != did or event.get("status") != state:
            continue
        if (event.get("origin_session_id") or event.get("parent_session_id")) != sid:
            continue
        if (not all(_session_run_epoch(v) for v in (dispatched, completed)) or dispatched > completed or completed > now or
                event.get("dispatched_at") != dispatched or event.get("completed_at") != completed):
            continue
        if not isinstance(results, list) or not results or results != result.get("results"):
            continue
        goals = task.get("goals")
        if task.get("is_batch") is not True or event.get("is_batch") is not True or not isinstance(goals, list) or not goals:
            continue
        indexes = task.get("task_indexes")
        if indexes is None:
            indexes = list(range(len(goals)))
        if (not isinstance(indexes, list) or not indexes or
                any(type(i) is not int or i < 0 or i >= len(goals) for i in indexes) or len(set(indexes)) != len(indexes)):
            continue
        if (len(results) != len(indexes) or any(not isinstance(r, dict) or type(r.get("task_index")) is not int for r in results) or
                sorted(r["task_index"] for r in results) != sorted(indexes)):
            continue
        terminal = {"completed", "success", "error", "failed", "interrupted", "unknown", "stalled", "timeout"}
        if any(not isinstance(r.get("status"), str) or r["status"] not in terminal or
               not any(isinstance(r.get(k), str) and r[k].strip() for k in ("summary", "error")) for r in results):
            continue
        recovered.append({"id": did, "session_id": sid, "dispatched_at": dispatched,
                          "completed_at": completed, "event": event})
    return recovered


def _continue_prune_failure_cache(now: float) -> None:
    # The durable request holds the retry budget; eviction only drops a cache.
    for key in list(_continue_failure_at):
        if key not in _continue_failures or now - _continue_failure_at[key] > CONTINUE_WINDOW_S * 2:
            _continue_failure_at.pop(key, None)
            _continue_failures.pop(key, None)
    for key in list(_continue_failures):
        if key not in _continue_failure_at:
            _continue_failures.pop(key, None)  # Unscopable stale cache is never a receipt.
    excess = len(_continue_failures) - CONTINUE_FAILURE_CACHE
    for key in sorted(_continue_failure_at, key=_continue_failure_at.__getitem__)[:max(0, excess)]:
        _continue_failure_at.pop(key, None)
        _continue_failures.pop(key, None)


def _continue_receipts(now: float, aliases: list[str] | None = None, session_id: str | None = None) -> None:
    """Sidecar-owned journal; never writes Hermes's state/run databases."""
    path = HERMES_HOME / "panel_auto_continue.db"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        os.close(fd)
    store = sqlite3.connect(path, timeout=3)
    try:
        store.execute("CREATE TABLE IF NOT EXISTS receipts (identity TEXT PRIMARY KEY, accepted_at REAL NOT NULL)")
        store.execute("CREATE TABLE IF NOT EXISTS requests (session_id TEXT PRIMARY KEY, request_key TEXT NOT NULL, "
                      "aliases_json TEXT NOT NULL, body_json TEXT NOT NULL, attempts INTEGER NOT NULL, created_at REAL NOT NULL)")
        if aliases:
            store.executemany("INSERT OR REPLACE INTO receipts VALUES (?, ?)", ((key, now) for key in aliases))
        retired_keys = [r[0] for r in store.execute(
            "SELECT request_key FROM requests WHERE created_at < ? OR session_id = ?",
            (now - CONTINUE_WINDOW_S * 2, session_id)).fetchall()]
        if session_id:
            store.execute("DELETE FROM requests WHERE session_id = ?", (session_id,))
        store.execute("DELETE FROM receipts WHERE accepted_at < ?", (now - CONTINUE_WINDOW_S * 2,))
        store.execute("DELETE FROM requests WHERE created_at < ?", (now - CONTINUE_WINDOW_S * 2,))
        store.commit()
        for key in retired_keys:
            _continue_failures.pop(key, None)
            _continue_failure_at.pop(key, None)
        _continue_done.update(store.execute("SELECT identity, accepted_at FROM receipts").fetchall())
    finally:
        store.close()


def _continue_request_plan(sid: str, key: str, aliases: list[str], body: dict[str, Any], now: float) -> tuple[str, list[str], dict[str, Any]]:
    """Persist the exact admitted payload before POST, including across process crashes.

    New batches must wait behind a pending request, not mutate that request's
    idempotency fingerprint. Re-evaluated eligibility still owns admission.
    """
    with contextlib.closing(sqlite3.connect(HERMES_HOME / "panel_auto_continue.db", timeout=3)) as store:
        store.execute("BEGIN IMMEDIATE")
        row = store.execute("SELECT request_key, aliases_json, body_json, attempts, created_at FROM requests WHERE session_id = ?", (sid,)).fetchone()
        if row:
            planned_aliases = json.loads(row[1])
            if not set(planned_aliases) <= set(aliases):
                # The old request no longer passes the stopped/takeover/processed
                # checks; it must not be replayed just because a new batch arrived.
                store.execute("DELETE FROM requests WHERE session_id = ?", (sid,))
                _continue_failures.pop(row[0], None)
                _continue_failure_at.pop(row[0], None)
                row = None
        if row:
            _continue_failures[row[0]] = row[3]
            _continue_failure_at[row[0]] = row[4]
            store.commit()
            return row[0], json.loads(row[1]), json.loads(row[2])
        store.execute("INSERT INTO requests VALUES (?, ?, ?, ?, 0, ?)",
                      (sid, key, json.dumps(aliases), json.dumps(body, ensure_ascii=False), now))
        store.commit()
        return key, aliases, body


def _continue_native_admitted(sid: str, legacy_key: str) -> bool:
    """Pinned public durable reservations prove admission, not completion.

    We do not guess an auth/profile scope. Any matching key for this exact
    session is a conservative receipt across scopes; ambiguity or an absent
    durable store fails closed, including native in-memory fallback mode.
    """
    path = HERMES_HOME / "runs_idempotency.db"
    with _session_run_db(path) as conn:
        _session_run_schema(conn, "run_idempotency", {
            "scope": "TEXT", "idempotency_key": "TEXT", "fingerprint": "TEXT",
            "run_id": "TEXT", "status_json": "TEXT", "owner_pid": "INTEGER",
            "owner_started": "INTEGER", "retention_until": "REAL", "acknowledged_at": "REAL",
            "created_at": "REAL", "updated_at": "REAL",
        }, ("scope", "idempotency_key"))
        records = conn.execute(
            "SELECT run_id, status_json, created_at, updated_at, owner_pid, owner_started "
            "FROM run_idempotency WHERE idempotency_key = ? LIMIT 2", (legacy_key,)).fetchall()
        for record in records:
            _session_run_record(record, sid)  # Exact session/run identity and recognized public status.
        if len(records) > 1:
            raise ValueError("ambiguous legacy continuation admission")
        return bool(records)


def _continue_discover(conn: sqlite3.Connection, now: float, session_cols: set[str],
                       delegations: list[dict[str, Any]]) -> None:
    """更新候选会话集合 _continue_watch：窗口期内有完成通知的面板会话。

    平时只走索引：最近活跃的面板会话、委派账本里窗口期内完成的父会话、还有待重发请求的会话，
    逐个用 (session_id, active, timestamp) 索引查有没有完成通知。完成通知不一定刷新会话活跃时间
    （线上见过晚 20–60 秒），所以每 CONTINUE_SWEEP_S 再全表兜底一次。进了集合的会话在通知过期前
    每个 tick 都会被重新评估，续跑失败或会话正忙时下一个 tick（5 秒）就能重试。
    """
    for sid in [sid for sid, until in _continue_watch.items() if until <= now]:
        _continue_watch.pop(sid, None)
    since = now - CONTINUE_WINDOW_S

    def watch(sid: Any, ts: Any) -> None:
        if isinstance(sid, str) and _session_run_epoch(ts):
            _continue_watch[sid] = max(_continue_watch.get(sid, 0.0), ts + CONTINUE_WINDOW_S)

    sweep = _continue_sweep
    home = str(HERMES_HOME)
    if sweep["home"] != home or now - sweep["at"] >= CONTINUE_SWEEP_S:
        sweep.update(home=home, at=now)
        for sid, ts in conn.execute(
                "SELECT session_id, MAX(timestamp) FROM messages WHERE display_kind = 'async_delegation_complete' "
                "AND active = 1 AND timestamp > ? GROUP BY session_id", (since,)):
            watch(sid, ts)

    candidates: set[str] = {d["session_id"] for d in delegations}
    # 表达式与 Hermes 的 idx_sessions_effective_activity 一字不差才能走索引（只读最近活跃的几行，不随会话总数增长）。
    activity = "COALESCE(last_activity_at, started_at)" if "last_activity_at" in session_cols else "started_at"
    candidates.update(r[0] for r in conn.execute(
        f"SELECT id, source FROM sessions WHERE {activity} > ?", (now - 2 * CONTINUE_WINDOW_S,)) if r[1] == "api_server")
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(async_delegations)")}
        owners = [c for c in ("origin_session_id", "parent_session_id") if c in cols]
        if owners and "completed_at" in cols:
            owner_sql = "COALESCE(" + ", ".join(f"NULLIF({c}, '')" for c in owners) + ", '')"
            candidates.update(r[0] for r in conn.execute(
                f"SELECT DISTINCT {owner_sql} FROM async_delegations WHERE completed_at > ?", (since,)))
    except sqlite3.Error as exc:
        _continue_warn("delegation-owners", "auto-continue: delegation ledger unreadable for discovery: %s", exc)
    journal = HERMES_HOME / "panel_auto_continue.db"
    if journal.exists():
        with contextlib.closing(sqlite3.connect(journal, timeout=3)) as store:
            with contextlib.suppress(sqlite3.OperationalError):  # 表还没建：没有待重发请求
                candidates.update(r[0] for r in store.execute("SELECT session_id FROM requests"))
    for sid in sorted(c for c in candidates if c):
        row = conn.execute(
            "SELECT MAX(timestamp) FROM messages WHERE session_id = ? AND active = 1 AND timestamp > ? "
            "AND display_kind = 'async_delegation_complete'", (sid, since)).fetchone()
        if row and row[0] is not None:
            watch(sid, row[0])


def _continue_tick(api_key: str) -> None:
    now = time.time()
    _continue_prune_failure_cache(now)
    if (HERMES_HOME / "panel_auto_continue.db").exists():
        _continue_receipts(now)  # Expire persisted requests even on idle ticks.
    conn = sqlite3.connect(f"file:{HERMES_HOME / 'state.db'}?mode=ro", uri=True, timeout=3)
    try:
        conn.execute("PRAGMA query_only = 1")
        session_cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)")}
        open_parent = " AND ended_at IS NULL" if "ended_at" in session_cols else ""
        delegations = _continue_delegations(conn, now)
        _continue_discover(conn, now, session_cols, delegations)
        recent_sessions: dict[str, Any] = {}
        if _continue_watch:
            watched = sorted(_continue_watch)
            placeholders = ",".join("?" for _ in watched)
            recent_sessions = dict(conn.execute(
                f"SELECT id, model FROM sessions WHERE id IN ({placeholders}) AND source = 'api_server'" + open_parent,
                watched).fetchall())
        for delegation in delegations:
            sid = delegation["session_id"]
            parent = conn.execute("SELECT id, model FROM sessions WHERE id = ? AND source = 'api_server'" + open_parent, (sid,)).fetchone()
            if parent:
                recent_sessions[parent[0]] = parent[1]
        work: list[tuple[str, str | None, str, str]] = []
        blocked: set[str] = set()
        legacy_keys: dict[str, str] = {}
        evaluated_sessions: set[str] = set()
        for sid, model in recent_sessions.items():
            lease = conn.execute("SELECT expires_at FROM session_turn_leases WHERE conversation_id = ?", (sid,)).fetchone()
            if lease is not None and isinstance(lease[0], (int, float)) and lease[0] > now:
                continue  # 正在执行（含刚发起的续跑）
            evaluated_sessions.add(sid)

            rows = conn.execute(
                "SELECT id, role, display_kind, content, display_metadata, timestamp FROM ("
                "  SELECT id, role, display_kind, content, display_metadata, timestamp, display_order FROM messages"
                "  WHERE session_id = ? AND active = 1 ORDER BY COALESCE(display_order, id) DESC, id DESC LIMIT 300"
                ") ORDER BY COALESCE(display_order, id), id", (sid,)).fetchall()

            has_trigger = conn.execute(
                "SELECT 1 FROM messages WHERE session_id = ? AND display_kind = 'async_delegation_complete' "
                "AND timestamp > ? LIMIT 1",
                (sid, now - CONTINUE_WINDOW_S)).fetchone()

            trigger, limited = continuation_trigger(rows)
            if has_trigger and trigger is not None and not limited:
                barrier = max((i for i, row in enumerate(rows) if row[1] == "assistant" or
                               _continue_is_human(row[1], row[2], _continue_text(row[3]))), default=-1)
                for i, notice in enumerate(rows):
                    if i <= barrier or notice[5] <= now - CONTINUE_WINDOW_S:
                        continue
                    candidate, _ = continuation_trigger(rows[:i + 1])
                    if candidate != notice[0]:
                        continue
                    did = _continue_notice_id(notice)
                    # 派发之后已经答完的人话不是「人写了新消息」。是否续跑只看 continuation_trigger：
                    # 通知必须晚于最后一条助手消息、且晚于最后一条人话。用 dispatched_at 之后出现过任何人话来否决，
                    # 会把已落库的完成通知永久跳过。
                    key = f"hp-continue-batch-{sid}-{did}" if did else f"hp-continue-{sid}-{notice[0]}"
                    if did:
                        legacy_keys[key] = f"hp-continue-{sid}-{notice[0]}"
                    work.append((sid, model if isinstance(model, str) and model else None, key, AUTO_CONTINUE_TEXT))

            for delegation in delegations:
                if delegation["session_id"] != sid:
                    continue
                sub_id, sub_ended = delegation["id"], delegation["completed_at"]
                fallback_key = f"hp-continue-batch-{sid}-{sub_id}"
                if fallback_key in _continue_done:
                    continue
                notices = conn.execute(
                    "SELECT id, role, display_kind, content, display_metadata FROM messages WHERE session_id = ? AND active = 1 "
                    "AND display_kind IN ('async_delegation_complete', 'hidden')", (sid,)).fetchall()
                if any(_continue_notice_id(notice) == sub_id for notice in notices):
                    continue
                after = conn.execute(
                    "SELECT role, display_kind, content, timestamp FROM messages WHERE session_id = ? "
                    "AND active = 1 AND timestamp > ?", (sid, delegation["dispatched_at"])).fetchall()
                # Without this batch's exact delivery/receipt, an assistant may
                # only have answered A after B completed. Time alone proves nothing.
                if any(_continue_is_human(role, kind, _continue_text(content))
                       for role, kind, content, timestamp in after):
                    continue
                texts = [_continue_text(r[3]) for r in rows]
                last_human = max((idx for idx, r in enumerate(rows)
                                  if _continue_is_human(r[1], r[2], texts[idx])), default=-1)
                streak = sum(1 for idx, r in enumerate(rows)
                             if idx > last_human and r[1] == "user" and not r[2] and texts[idx].lstrip().startswith(AUTO_CONTINUE_MARK))
                if streak >= AUTO_CONTINUE_LIMIT:
                    continue
                text = (f"{AUTO_CONTINUE_MARK} 后台委派 {sub_id} 已终态，但完成通知缺失。"
                        "以下是该委派已落库的真实结果（不是新的完成通知）：\n"
                        + json.dumps(delegation["event"], ensure_ascii=False)
                        + "\n请根据这些结果继续之前的任务；若在等用户确认或回答，请继续等待。")
                work.append((sid, model if isinstance(model, str) and model else None, fallback_key, text))
        if (HERMES_HOME / "panel_auto_continue.db").exists():
            with contextlib.closing(sqlite3.connect(HERMES_HOME / "panel_auto_continue.db", timeout=3)) as store:
                pending = store.execute("SELECT session_id, request_key, aliases_json FROM requests").fetchall()
            eligible: dict[str, set[str]] = {}
            for sid, _, alias, _ in work:
                eligible.setdefault(sid, set()).add(alias)
            for sid, request_key, raw_aliases in pending:
                # A partial assistant reply changes eligibility, not admission.
                # Before retiring/splitting a frozen request, recover every alias
                # from its exact original key/session. Uncertainty skips THIS session
                # with its journal untouched (never admit a reduced key); other sessions go on.
                try:
                    if _continue_native_admitted(sid, request_key):
                        _continue_receipts(now, json.loads(raw_aliases), session_id=sid)
                        continue
                    parent = conn.execute("SELECT id FROM sessions WHERE id = ? AND source = 'api_server'" + open_parent,
                                          (sid,)).fetchone()
                    # A busy lease is NOT invalidation.
                    if (not parent or _continue_user_stopped(sid) or
                            (sid in evaluated_sessions and not set(json.loads(raw_aliases)) <= eligible.get(sid, set()))):
                        _continue_receipts(now, session_id=sid)
                except _CONTINUE_SESSION_ERRORS as exc:
                    blocked.add(sid)
                    _continue_warn(f"journal:{type(exc).__name__}", "auto-continue: session %s skipped, admission unknown: %s: %s",
                                   sid, type(exc).__name__, exc)
    finally:
        conn.close()
    if work:
        _continue_receipts(now)  # Fail closed before any API request if the identity store is unreadable.
    grouped: dict[str, list[tuple[str, str | None, str, str]]] = {}
    for item in work:
        if item[0] in blocked or item[2] in _continue_done:
            continue
        try:
            if _continue_user_stopped(item[0]):
                continue
            legacy_key = legacy_keys.get(item[2])
            if legacy_key and _continue_native_admitted(item[0], legacy_key):
                _continue_receipts(now, [item[2]])
                continue  # Receipt belongs only to A, never consume a new B.
        except _CONTINUE_SESSION_ERRORS as exc:
            # 只跳过这个会话（下个 tick 重试），不能让一处原生库异常停掉所有会话的续跑。
            blocked.add(item[0])
            _continue_warn(f"admission:{type(exc).__name__}", "auto-continue: session %s skipped, admission unknown: %s: %s",
                           item[0], type(exc).__name__, exc)
            continue
        grouped.setdefault(item[0], []).append(item)
    for sid in blocked:
        grouped.pop(sid, None)
    for sid, items in grouped.items():
        items.sort(key=lambda item: item[2])
        _, model, key, _ = items[0]
        aliases = sorted({item[2] for item in items})
        if len(aliases) > 1:
            import hashlib
            digest = hashlib.sha256(json.dumps(aliases, separators=(",", ":")).encode()).hexdigest()
            key = f"hp-continue-set-{sid}-{digest[:32]}"
        text = "\n\n".join(dict.fromkeys(item[3] for item in items))
        body: dict[str, Any] = {"input": text, "session_id": sid}
        if model:
            provider = _continue_provider(model, api_key)
            if provider is None:
                _continue_warn(f"route:{model}", "auto-continue: cannot resolve provider for model %s; skipping", model)
                continue
            body.update(model=model, provider=provider)
        key, aliases, body = _continue_request_plan(sid, key, aliases, body, now)
        attempts = _continue_failures.get(key, 0)
        _continue_prune_failure_cache(now)  # Bound memory even if the following POST raises.
        if attempts >= CONTINUE_MAX_ATTEMPTS:
            _continue_receipts(now, aliases, session_id=sid)
            continue
        status, data = _continue_api("POST", "/v1/runs", api_key, body, {"Idempotency-Key": key})
        if status in (200, 201, 202) or status == 409:
            # 409 = 同键已被（旧版面板等）以不同请求体发起过：那一轮已存在，不再重发。
            _continue_receipts(now, aliases, session_id=sid)
            _continue_done.update((alias, now) for alias in aliases)
            log.info("auto-continue session %s (%s): HTTP %s run %s", sid, key, status,
                     (data or {}).get("run_id") if isinstance(data, dict) else None)
        elif status == 401:
            _continue_warn("auth", "auto-continue: API key rejected (401); refresh %s via panel-deploy.sh", CONTINUE_KEY_FILE)
            _continue_prune_failure_cache(now)
            return
        else:
            n = attempts + 1
            _continue_failures[key] = n
            _continue_failure_at.setdefault(key, now)
            _continue_prune_failure_cache(now)
            with contextlib.closing(sqlite3.connect(HERMES_HOME / "panel_auto_continue.db", timeout=3)) as store:
                store.execute("UPDATE requests SET attempts = ? WHERE session_id = ? AND request_key = ?", (n, sid, key))
                store.commit()
            log.warning("auto-continue session %s failed (HTTP %s), attempt %d/%d", sid, status, n, CONTINUE_MAX_ATTEMPTS)
            if n >= CONTINUE_MAX_ATTEMPTS:
                _continue_receipts(now, aliases, session_id=sid)
                _continue_done.update((alias, now) for alias in aliases)
    # 记录只需覆盖窗口期，过期的清掉，常驻内存不增长。
    for k in [k for k, t in _continue_done.items() if now - t > CONTINUE_WINDOW_S * 2]:
        _continue_done.pop(k, None)
    _continue_prune_failure_cache(now)


def _continue_watcher() -> None:
    while True:
        time.sleep(CONTINUE_POLL_S)
        try:
            api_key = CONTINUE_KEY_FILE.read_text(encoding="utf-8").strip()
        except OSError:
            _continue_warn("keyfile", "auto-continue disabled: %s not readable (panel-deploy.sh writes it)", CONTINUE_KEY_FILE)
            continue
        if not api_key:
            continue
        try:
            _continue_tick(api_key)
        except (sqlite3.Error, OSError, ValueError, TypeError, KeyError) as exc:
            _continue_warn(f"tick:{type(exc).__name__}", "auto-continue skipped: %s: %s", type(exc).__name__, exc)


# ---------------------------------------------------------------- 会话上下文用量（只读解析 agent.log）

SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{5,79}$")
CTX_CALL_RE = re.compile(
    r"^(?P<ts>\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)[,.]\d+ INFO \[(?P<sid>[^\]]+)\] agent\.conversation_loop: "
    r"API call #(?P<n>\d+): model=(?P<model>\S+) provider=(?P<provider>\S+) in=(?P<inp>\d+)(?: out=(?P<out>\d+))?"
    r"(?: total=\d+)?(?: latency=\S+)?(?: cache=(?P<cr>\d+)/(?P<ct>\d+))?"
)
CTX_DEFAULT_RE = re.compile(r"agent\.model_metadata: Could not detect context length for model '(?P<model>[^']+)'.*?defaulting to (?P<n>[\d,]+) tokens")
# Hermes 决定窗口大小有两种记法：探测失败采用默认值，或匹配内置目录（"hardcoded"）。同一模型以日志里最新的一条为准——
# 只认前者会一直停在重启时那次探测失败的旧值上（网关重启后曾记 256,000，之后改走内置目录得到 1,048,576）。
CTX_HARDCODED_RE = re.compile(r"agent\.model_metadata: Using hardcoded context length (?P<n>[\d,]+) for model '(?P<model>[^']+)'")
# 只这一条模型。自定义端点会把 grok-4.7 误匹配成 grok-4 的 256000，且覆盖命中时不写新日志。
# 其他模型继续走下面的日志/全局配置，不能借这条去改它们的圆环。
GROK47_CONTEXT_MODEL = "grok-4.7"


def _grok47_override_window(provider: str | None) -> int | None:
    """grok-4.7 在该会话 provider 下的 context_window。没有、不是正整数、或 provider 对不上就返回 None。"""
    if not provider:
        return None
    overrides = config_get_json("model_overrides")
    if not isinstance(overrides, dict):
        return None
    section = overrides.get(provider)
    if not isinstance(section, dict):
        return None
    entry = section.get(GROK47_CONTEXT_MODEL)
    if not isinstance(entry, dict):
        return None
    value = entry.get("context_window")
    if type(value) is not int or value <= 0:
        return None
    return value


def get_context(session_id: str) -> dict[str, Any]:
    """会话当前上下文大小 = 该会话最近一次 API 调用的输入 token 数（agent.log 里的 `in=`）。
    窗口大小：配置 model.context_length；没配时，仅 grok-4.7 再读该会话 provider 的 model_overrides.context_window；
    其余模型仍用 Hermes 探测失败后记在 agent.log 的默认值。"""
    if not SESSION_ID_RE.match(session_id):
        raise ApiError(400, "invalid_session", "会话 id 不合法")
    tag = f"[{session_id}]"
    last: dict[str, Any] | None = None
    defaults: dict[str, int] = {}
    for name in ("agent.log", "agent.log.1"):
        path = LOG_DIR / name
        if not path.is_file():
            continue
        try:
            lines = _tail_text(path, LOG_TAIL_BYTES)
        except OSError:
            continue
        for line in reversed(lines):
            if last is None and tag in line:
                m = CTX_CALL_RE.match(line)
                if m and m.group("sid") == session_id:
                    last = {
                        "used": int(m.group("inp")), "model": m.group("model"), "provider": m.group("provider"),
                        "calls": int(m.group("n")), "at": m.group("ts"),
                        "out": int(m.group("out")) if m.group("out") else None,
                        "cached": int(m.group("cr")) if m.group("cr") else 0,  # 本次调用里命中缓存的输入 token 数
                    }
            elif "context length" in line:
                d = CTX_DEFAULT_RE.search(line) or CTX_HARDCODED_RE.search(line)
                if d and d.group("model") not in defaults:
                    defaults[d.group("model")] = int(d.group("n").replace(",", ""))
        if last is not None:
            break
    if last is None:
        return {"used": None, "window": None, "source": "no-data"}
    window: int | None = None
    source = "unknown"
    cfg = config_get_json("model")
    configured = cfg.get("context_length") if isinstance(cfg, dict) else None
    if isinstance(configured, int) and configured > 0:
        window, source = configured, "config"
    elif last["model"] == GROK47_CONTEXT_MODEL and (override := _grok47_override_window(last.get("provider"))):
        window, source = override, "override"
    elif last["model"] in defaults:
        window, source = defaults[last["model"]], "default"
    return {**last, "window": window, "source": source, "session": _session_totals(session_id)}


# ---------------------------------------------------------------- 回合等待状态（只读解析 agent.log）
# Hermes 遇到限流/超时会"等一会儿再试"（最多 3 次、每次最长 600 秒），等待期间 /v1/runs 事件流里没有任何事件，
# 面板只能显示一个静止的"思考中…"。等待信息只在 agent.log 里有：
#   WARNING [sid] agent.conversation_loop: API call failed (attempt 2/3) error_type=RateLimitError … model=X summary=HTTP 429: …
#   WARNING [sid] agent.conversation_loop: Retrying API call in 600.0s (attempt 2/3) … model=X …
#   INFO    [sid] agent.conversation_loop: API call #N: …              ← 恢复了（有新的成功调用）
#   ERROR   [sid] agent.conversation_loop: API call failed after 3 retries. …   ← 放弃
#   INFO    [sid] agent.conversation_loop: Turn ended: …               ← 这一轮结束
WAIT_LINE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)[,.]\d+ (?P<lvl>[A-Z]+) \[(?P<sid>[^\]]+)\] agent\.conversation_loop: (?P<msg>.*)$"
)
WAIT_RETRY_RE = re.compile(r"^Retrying API call in (?P<wait>[\d.]+)s \(attempt (?P<a>\d+)/(?P<b>\d+)\)")
WAIT_FAIL_RE = re.compile(r"^API call failed \(attempt (?P<a>\d+)/(?P<b>\d+)\).*?model=(?P<model>\S+)(?: summary=(?P<summary>.*))?$")
WAIT_MODEL_RE = re.compile(r"\bmodel=(?P<model>\S+)")
WAIT_GIVEUP_RE = re.compile(r"^API call failed after (?P<n>\d+) retries\.\s*(?P<detail>.*)$")
# 一次等待的上限：Hermes 的 LIVE_RETRY_WAIT_CAP_S 是 600；留 60 秒余量，超过就当作已不在等待（日志可能漏记恢复）。
WAIT_MAX_S = 660.0


_WAIT_TAIL_CACHE: dict[str, tuple[tuple[int, int, int, int], float, list[str]]] = {}
_WAIT_TAIL_LOCK = threading.Lock()


def _wait_tail(path: Path) -> list[str]:
    """Bounded read shared by foreground polls; invalidate on append or rotation."""
    info = path.stat()
    fingerprint = (info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    key = str(path)
    with _WAIT_TAIL_LOCK:
        cached = _WAIT_TAIL_CACHE.get(key)
        if cached and cached[0] == fingerprint and time.monotonic() - cached[1] < 2:
            return cached[2]
        lines = _tail_text(path, LOG_TAIL_BYTES)
        if len(_WAIT_TAIL_CACHE) >= 4:
            _WAIT_TAIL_CACHE.clear()
        _WAIT_TAIL_CACHE[key] = (fingerprint, time.monotonic(), lines)
        return lines


def get_run_wait(session_id: str, turn_started_at_ms: Any = None) -> dict[str, Any]:
    """Read retry evidence inside the requested turn window; never initiate retries."""
    if not SESSION_ID_RE.match(session_id):
        raise ApiError(400, "invalid_session", "会话 id 不合法")
    now = time.time()
    since = None
    if turn_started_at_ms is not None:
        try:
            since = float(turn_started_at_ms)
        except (TypeError, ValueError):
            raise ApiError(400, "invalid_turn_start", "回合时间无效") from None
        if not 0 < since <= (now + 60) * 1000:
            raise ApiError(400, "invalid_turn_start", "回合时间无效")
    meta: dict[str, Any] = {"source": "log", "observedAtMs": round(now * 1000), "available": False}
    if since is not None:
        meta["turnStartedAtMs"] = since
    idle = {"state": "idle", **meta}
    tag = f"[{session_id}]"
    latest: dict[str, Any] | None = None
    summary = ""
    for name in ("agent.log", "agent.log.1"):
        path = LOG_DIR / name
        try:
            lines = _wait_tail(path)
            meta["available"] = idle["available"] = True
        except FileNotFoundError:
            continue
        except OSError:
            log.warning("Retry status log unavailable: %s", name)
            continue
        for line in reversed(lines):
            if tag not in line:
                continue
            match = WAIT_LINE_RE.match(line)
            if not match or match.group("sid") != session_id:
                continue
            try:
                stamp = time.mktime(time.strptime(match.group("ts"), "%Y-%m-%d %H:%M:%S"))
                fraction = re.match(r"[,.](\d+)", line[len(match.group("ts")):])
                if fraction:
                    stamp += float("0." + fraction.group(1))
            except (ValueError, OverflowError):
                continue
            if since is not None and stamp * 1000 < since:
                break
            msg = match.group("msg")
            retry = WAIT_RETRY_RE.match(msg)
            if latest is None:
                if retry:
                    try:
                        wait = float(retry.group("wait"))
                    except ValueError:
                        continue
                    model = WAIT_MODEL_RE.search(msg)
                    latest = {"ts": stamp, "wait": wait, "attempt": int(retry.group("a")),
                              "max": int(retry.group("b")), "model": model.group("model") if model else ""}
                elif msg.startswith(("API call #", "Turn ended", "API call failed after")):
                    return idle
                continue
            fail = WAIT_FAIL_RE.match(msg)
            if fail and int(fail.group("a")) == latest["attempt"] and int(fail.group("b")) == latest["max"]:
                if latest["model"] and fail.group("model") != latest["model"]:
                    break
                summary = (fail.group("summary") or "").strip()
                break
            if msg.startswith(("API call #", "Turn ended")):
                break
        if latest is not None:
            break
    if latest is None:
        return idle
    wait = latest["wait"]
    remaining = latest["ts"] + wait - now
    if not 0 <= wait <= WAIT_MAX_S or remaining < -30:
        return idle
    result = {"state": "retrying", **meta, "attempt": latest["attempt"], "maxAttempts": latest["max"],
              "waitSeconds": round(wait, 1), "remainingSeconds": max(0, round(remaining)),
              "nextRetryAtMs": round((latest["ts"] + wait) * 1000), "model": latest["model"],
              "summary": redact(summary)[:240]}
    code = re.search(r"\b(?:HTTP(?: error)?|status(?: code)?|API error)\s*[:=]?\s*([45]\d\d)\b", summary, re.I)
    if code:
        result["httpStatus"] = int(code.group(1))
    return result


def _session_totals(session_id: str) -> dict[str, int] | None:
    """会话累计用量（sessions 表，只读）。取不到不影响圆环本身。"""
    try:
        conn = sqlite3.connect(f"file:{HERMES_HOME / 'state.db'}?mode=ro", uri=True, timeout=3)
        try:
            conn.execute("PRAGMA query_only = 1")
            row = conn.execute(
                "SELECT COALESCE(input_tokens,0), COALESCE(output_tokens,0), COALESCE(cache_read_tokens,0), "
                "COALESCE(api_call_count,0), COALESCE(message_count,0) FROM sessions WHERE id = ?", (session_id,)).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    return {"input": row[0], "output": row[1], "cacheRead": row[2], "apiCalls": row[3], "messages": row[4]}


# ---------------------------------------------------------------- 子代理活动（state.db 只读）

SUBAGENT_STALL_S = 600  # 未结束但超过这么久没有任何消息：按"疑似卡住"显示


def _brief_args(raw: Any) -> dict[str, Any]:
    try:
        args = json.loads(raw) if isinstance(raw, str) else {}
    except json.JSONDecodeError:
        return {}
    return _safe_args(args)


def get_subagents(parent_id: str, limit: int) -> dict[str, Any]:
    """子代理 = source 为 subagent 的会话，parent_session_id 指向发起它的会话。
    运行状态：ended_at 为空即未结束；再看最近一条消息的时间区分"进行中"与"疑似卡住"。只用 SQL 读，不写库。"""
    if parent_id and not SESSION_ID_RE.match(parent_id):
        raise ApiError(400, "invalid_session", "会话 id 不合法")
    conn = sqlite3.connect(f"file:{HERMES_HOME / 'state.db'}?mode=ro", uri=True, timeout=5)
    try:
        conn.execute("PRAGMA query_only = 1")
        conn.row_factory = sqlite3.Row
        where, params = ("s.source = 'subagent'", [])
        if parent_id:
            where += " AND s.parent_session_id = ?"
            params.append(parent_id)
        rows = conn.execute(
            "SELECT s.id, s.parent_session_id AS parentId, s.title, s.model, s.started_at AS startedAt, s.ended_at AS endedAt, "
            "s.end_reason AS endReason, COALESCE(s.message_count,0) AS messages, COALESCE(s.tool_call_count,0) AS toolCalls, "
            "COALESCE(s.api_call_count,0) AS apiCalls, COALESCE(s.input_tokens,0)+COALESCE(s.output_tokens,0) AS tokens, "
            "p.title AS parentTitle "
            f"FROM sessions s LEFT JOIN sessions p ON p.id = s.parent_session_id WHERE {where} "
            "ORDER BY (s.ended_at IS NULL) DESC, s.started_at DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
        now = time.time()
        out: list[dict[str, Any]] = []
        for r in rows:
            item = dict(r)
            recent = conn.execute(
                "SELECT role, tool_name, tool_calls, substr(content, 1, 240) AS text, timestamp "
                "FROM messages WHERE session_id = ? ORDER BY id DESC LIMIT 8", (r["id"],),
            ).fetchall()
            last_at = recent[0]["timestamp"] if recent else r["startedAt"]
            tool, snippet = None, ""
            for m in recent:
                if tool is None and m["role"] == "assistant" and m["tool_calls"] and m["tool_calls"] not in ("[]", ""):
                    try:
                        calls = json.loads(m["tool_calls"])
                    except json.JSONDecodeError:
                        calls = []
                    if isinstance(calls, list) and calls:
                        fn = (calls[-1] or {}).get("function") or {}
                        tool = {"name": fn.get("name") or "tool", "args": _brief_args(fn.get("arguments"))}
                if not snippet and m["role"] == "assistant" and m["text"] and m["text"].strip():
                    snippet = m["text"].strip()
                if tool is not None and snippet:
                    break
            if r["endedAt"] is not None:
                status = "done" if (r["endReason"] or "") in ("agent_close", "") else "ended"
            else:
                status = "running" if now - (last_at or 0) < SUBAGENT_STALL_S else "stalled"
            item.update({"status": status, "lastActivityAt": last_at, "lastTool": tool, "snippet": redact(snippet)})
            out.append(item)
    except sqlite3.Error as exc:
        raise ApiError(500, "subagents_query_failed", f"读取子代理失败：{exc}") from exc
    finally:
        conn.close()
    return {"data": out, "running": sum(1 for i in out if i["status"] == "running"), "now": time.time()}


# ---------------------------------------------------------------- 设置表单（schema 来自 Hermes 自带的配置结构）

HERMES_PY = os.environ.get("HERMES_PY", "/usr/local/lib/hermes-agent/venv/bin/python3")
HERMES_SRC = Path(os.environ.get("HERMES_SRC", "/usr/local/lib/hermes-agent"))
# 在 Hermes 自己的 Python 环境里一次性导出：配置项结构（类型/分组/说明/下拉选项）+ 当前生效值 + 用户在 config.yaml 里设置过的键。
# 这是本服务里唯一一处调用 Hermes 内部模块的地方，失败时设置页退回"YAML 原文 + 按键修改"；写入仍一律走 `hermes config set`。
SETTINGS_HELPER = r'''
import json, sys
sys.path.insert(0, %r)
from hermes_cli.config import load_config, read_raw_config
from hermes_cli.web_server_config import _normalize_config_for_web, _schema_with_dynamic_provider_options

def dig(d, path):
    cur = d
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None, False
        cur = cur[part]
    return cur, True

fields = _schema_with_dynamic_provider_options()
effective = _normalize_config_for_web(load_config())
raw = read_raw_config()
out = {"fields": fields, "values": {k: dig(effective, k)[0] for k in fields}, "userSet": [k for k in fields if dig(raw, k)[1]]}
sys.stdout.write("\n@@PANEL-JSON@@" + json.dumps(out, default=str, ensure_ascii=False))
''' % str(HERMES_SRC)

# 表单里不出现的项：默认模型在「管理 → 模型」里改；model_context_length 是 Hermes 虚拟字段，实际写 model.context_length。
SETTINGS_HIDDEN = {"model"}
SETTINGS_KEY_MAP = {"model_context_length": "model.context_length"}
SETTINGS_CATEGORY_ORDER = ["general", "agent", "terminal", "display", "delegation", "memory", "compression", "security", "browser", "voice", "tts", "stt", "logging", "discord", "auxiliary"]
SETTINGS_CATEGORY_LABELS = {
    "general": "通用", "agent": "智能体", "terminal": "终端", "display": "显示", "delegation": "子代理委派", "memory": "记忆",
    "compression": "上下文压缩", "security": "安全", "browser": "浏览器", "voice": "语音", "tts": "语音合成", "stt": "语音识别",
    "logging": "日志", "discord": "聊天平台", "auxiliary": "辅助模型", "database": "数据库", "attachments": "附件", "web": "网页",
    "tool_output": "工具输出", "tool_loop_guardrails": "工具循环防护", "vision": "视觉", "wake_word": "唤醒词", "loops": "循环",
    "moa": "多模型协作", "curator": "整理器", "slack": "Slack", "openrouter": "OpenRouter", "bedrock": "Bedrock",
}
_SETTINGS_CACHE: dict[str, Any] = {"stamp": None, "at": 0.0, "data": None}
_SETTINGS_LOCK = threading.Lock()


def _settings_dump() -> dict[str, Any]:
    """带缓存：config.yaml 没变就复用（导出一次约 1 秒）。"""
    with _SETTINGS_LOCK:
        stamp = _cfg_stamp()
        if (_SETTINGS_CACHE["data"] is not None and stamp is not None and _SETTINGS_CACHE["stamp"] == stamp
                and time.monotonic() - _SETTINGS_CACHE["at"] < CFG_CACHE_MAX_AGE_S):
            return _SETTINGS_CACHE["data"]
        try:
            with _CLI_SLOTS:
                proc = subprocess.run(
                    [HERMES_PY, "-I", "-c", SETTINGS_HELPER], capture_output=True, text=True, timeout=60, check=False,
                    cwd=str(HERMES_SRC), stdin=subprocess.DEVNULL, env={**os.environ, "HERMES_HOME": str(HERMES_HOME), "NO_COLOR": "1"},
                )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ApiError(503, "settings_unavailable", "读取配置结构失败（clawd 环境不可用或超时）") from exc
        marker = "@@PANEL-JSON@@"
        if proc.returncode != 0 or marker not in proc.stdout:
            log.error("settings dump failed rc=%s: %s", proc.returncode, redact(proc.stderr)[-400:])
            raise ApiError(503, "settings_unavailable", "读取配置结构失败（clawd 版本可能变了），请先用「YAML 原文」修改")
        try:
            data = json.loads(proc.stdout.split(marker, 1)[1])
        except json.JSONDecodeError as exc:
            raise ApiError(503, "settings_unavailable", "配置结构解析失败") from exc
        _SETTINGS_CACHE.update(stamp=stamp, at=time.monotonic(), data=data)
        return data


def _contains_secret(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_is_secret_name(str(k)) or _contains_secret(v) for k, v in value.items())
    if isinstance(value, list):
        return any(_contains_secret(v) for v in value)
    return False


def _mask_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [_mask_value(v) for v in value]
    if isinstance(value, dict):
        return {k: ("***" if _is_secret_name(str(k)) else _mask_value(v)) for k, v in value.items()}
    return value


def get_settings() -> dict[str, Any]:
    dump = _settings_dump()
    user_set = set(dump.get("userSet", []))
    fields: list[dict[str, Any]] = []
    for key, f in dump["fields"].items():
        if key in SETTINGS_HIDDEN or not isinstance(f, dict):
            continue
        typ = f.get("type") or "string"
        value = dump["values"].get(key)
        secret = _is_secret_key(key)
        locked = ""
        if secret:
            locked = "密钥类配置不能在面板里查看或修改，请在服务器上改"
        elif typ in ("list", "object") and _contains_secret(value):
            locked = "内容里含密钥，请在服务器上改"
        elif typ == "object":
            locked = "复杂结构，请用「YAML 原文」修改"
        fields.append({
            "key": key, "type": typ, "category": f.get("category") or "general",
            "description": str(f.get("description") or "")[:300],
            "options": [str(o) for o in f["options"]] if isinstance(f.get("options"), list) else None,
            "clearable": bool(f.get("clearable")), "searchable": bool(f.get("searchable")),
            "value": None if secret else _mask_value(value), "valueSet": bool(value) if secret else None,
            "userSet": key in user_set, "locked": locked,
        })
    cats: dict[str, int] = {}
    for f in fields:
        cats[f["category"]] = cats.get(f["category"], 0) + 1
    ordered = [c for c in SETTINGS_CATEGORY_ORDER if c in cats] + sorted(c for c in cats if c not in SETTINGS_CATEGORY_ORDER)
    return {
        "fields": fields,
        "categories": [{"id": c, "label": SETTINGS_CATEGORY_LABELS.get(c, c), "count": cats[c]} for c in ordered],
    }


def _settings_field(key: Any) -> tuple[str, dict[str, Any]]:
    if not isinstance(key, str) or key in SETTINGS_HIDDEN:
        raise ApiError(400, "invalid_key", "这个配置项不能在表单里修改")
    dump = _settings_dump()
    f = dump["fields"].get(key)
    if not isinstance(f, dict):
        raise ApiError(404, "unknown_key", "没有这个配置项")
    if _is_secret_key(key):
        raise ApiError(403, "secret_key", "密钥类配置不允许在面板里修改")
    if f.get("type") == "object" or (f.get("type") == "list" and _contains_secret(dump["values"].get(key))):
        raise ApiError(403, "locked", "这个配置项请在服务器上或用「YAML 原文」修改")
    return SETTINGS_KEY_MAP.get(key, key), f


def _coerce_setting(f: dict[str, Any], value: Any) -> str:
    typ = f.get("type")
    if typ == "boolean":
        if not isinstance(value, bool):
            raise ApiError(400, "invalid_value", "需要 true / false")
        return "true" if value else "false"
    if typ == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or value in (float("inf"), float("-inf")):
            raise ApiError(400, "invalid_value", "需要一个数字")
        return str(int(value)) if float(value).is_integer() else repr(float(value))
    if typ == "select":
        options = [str(o) for o in f["options"]] if isinstance(f.get("options"), list) else None
        if not isinstance(value, str) or (options is not None and value not in options and not (value == "" and f.get("clearable"))):
            raise ApiError(400, "invalid_value", "不在可选范围内")
        return value
    if typ == "list":
        if not isinstance(value, list) or len(value) > 500:
            raise ApiError(400, "invalid_value", "需要一个列表（最多 500 项）")
        return json.dumps(value, ensure_ascii=False)
    if not isinstance(value, str) or len(value) > 4000 or "\x00" in value:
        raise ApiError(400, "invalid_value", "需要 4000 字以内的文本")
    return value


def set_setting(key: Any, value: Any) -> dict[str, Any]:
    real_key, f = _settings_field(key)
    text = _coerce_setting(f, value)
    result = config_set(real_key, text)  # 内部再校验键名、拒绝密钥、遇到 Hermes 不认识的键会撤回、写后回读
    return {"ok": True, "key": key, "output": result.get("output", "")}


def reset_setting(key: Any) -> dict[str, Any]:
    real_key, _f = _settings_field(key)
    with _WRITE_LOCK:
        proc = _hermes("config", "unset", real_key)
    if proc.returncode != 0:
        raise _cli_failure(proc, "恢复默认失败")
    log.info("config unset %s via panel", real_key)
    return {"ok": True, "key": key}


# ---------------------------------------------------------------- HTTP


# ---------------------------------------------------------------- Hermes 更新检查（只读）

HERMES_INSTALL = Path(os.environ.get("HERMES_INSTALL", "/usr/local/lib/hermes-agent"))
UPDATE_REPO = "NousResearch/hermes-agent"
UPDATE_BRANCH = "main"
UPDATE_TTL_S = 1800.0
UPDATE_COMMIT_LIMIT = 15
SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
_UPDATE_CACHE: dict[str, Any] = {"at": 0.0, "data": None}
_UPDATE_LOCK = threading.Lock()


def _git_local(*args: str) -> str:
    """只读 git 命令（本地对象库，不联网）。失败返回空串。"""
    try:
        proc = subprocess.run(
            ["git", "-c", "safe.directory=*", "-C", str(HERMES_INSTALL), *args],
            capture_output=True, text=True, timeout=15, check=False, stdin=subprocess.DEVNULL,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"},
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout.rstrip("\n") if proc.returncode == 0 else ""


def _local_hermes_version() -> dict[str, Any]:
    try:
        proc = subprocess.run([HERMES_BIN, "--version"], capture_output=True, text=True, timeout=20, check=False,
                              stdin=subprocess.DEVNULL, env={**os.environ, "NO_COLOR": "1", "TERM": "dumb"})
        first = _clean(proc.stdout).splitlines()[0] if proc.stdout.strip() else ""
    except (OSError, subprocess.TimeoutExpired):
        first = ""
    m = re.match(r"Hermes Agent (v\S+)(?:\s+\(([\d.]+)\))?", first)
    return {"version": m.group(1) if m else first[:80], "releaseDate": (m.group(2) or "") if m else ""}


def _github_json(path: str) -> Any:
    import urllib.error
    import urllib.request
    req = urllib.request.Request(
        f"https://api.github.com{path}",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "hermes-panel-sidecar", "X-GitHub-Api-Version": "2022-11-28"},
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            return json.loads(resp.read(6 * 1024 * 1024))
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 429):
            raise ApiError(502, "github_rate_limited", "GitHub 请求过于频繁，稍后再试") from exc
        if exc.code == 404:
            raise ApiError(502, "github_not_found", "GitHub 上找不到当前版本的提交（可能是自行编译的版本）") from exc
        raise ApiError(502, "github_error", f"GitHub 返回 {exc.code}") from exc
    except ValueError as exc:
        raise ApiError(502, "github_bad_response", "GitHub 返回了无法解析的数据") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ApiError(502, "github_unreachable", "无法连接 GitHub") from exc


def _update_compute() -> dict[str, Any]:
    head = _git_local("rev-parse", "HEAD").strip()
    if not SHA_RE.match(head):
        raise ApiError(500, "no_install", "读不到 clawd 安装目录的版本信息")
    dirty = [ln[3:] for ln in _git_local("status", "--porcelain").splitlines() if len(ln) > 3]
    info: dict[str, Any] = {
        **_local_hermes_version(),
        "installDir": str(HERMES_INSTALL),
        "commit": head[:10],
        "localChanges": [redact(x)[:200] for x in dirty[:30]],
        "branch": _git_local("rev-parse", "--abbrev-ref", "HEAD").strip(),
        "repo": UPDATE_REPO,
        "checkedAt": time.time(),
    }
    cmp = _github_json(f"/repos/{UPDATE_REPO}/compare/{head}...{UPDATE_BRANCH}")
    behind = cmp.get("ahead_by")
    if not isinstance(behind, int):
        raise ApiError(502, "github_bad_response", "GitHub 返回了意料之外的数据")
    commits = cmp.get("commits") if isinstance(cmp.get("commits"), list) else []
    recent = []
    for c in commits[-UPDATE_COMMIT_LIMIT:][::-1]:
        meta = c.get("commit") or {}
        recent.append({
            "sha": str(c.get("sha", ""))[:10],
            "title": redact(str(meta.get("message", "")).split("\n", 1)[0])[:120],
            "date": str((meta.get("committer") or {}).get("date", "")),
        })
    info.update(
        behindBy=behind,
        status=str(cmp.get("status", "")),
        latestCommit=str((commits[-1].get("sha", "") if commits else head))[:10],
        recent=recent,
        compareUrl=f"https://github.com/{UPDATE_REPO}/compare/{head[:12]}...{UPDATE_BRANCH}",
    )
    return info


def get_update_info(force: bool = False) -> dict[str, Any]:
    """本地版本 + 与上游 main 的差距。GitHub 比较接口一次请求，结果缓存 30 分钟；失败时返回旧缓存并带 error。"""
    with _UPDATE_LOCK:
        data = _UPDATE_CACHE["data"]
        age = time.time() - _UPDATE_CACHE["at"]
        # 强制刷新也限频（每分钟最多一次），GitHub 匿名接口每小时只有 60 次额度
        if data is not None and (age < 60 or (age < UPDATE_TTL_S and not force)):
            return data
        try:
            data = _update_compute()
        except ApiError as exc:
            if _UPDATE_CACHE["data"] is not None:
                return {**_UPDATE_CACHE["data"], "error": exc.message, "stale": True}
            # 没有旧缓存：至少给出本地版本，让前端能显示"当前版本"
            return {**_local_hermes_version(), "commit": _git_local("rev-parse", "--short=10", "HEAD").strip(),
                    "localChanges": [], "error": exc.message, "behindBy": None}
        _UPDATE_CACHE.update(at=time.time(), data=data)
        return data


# ---------------------------------------------------------------- Hermes 更新执行
# 与官方面板同一做法：脱离面板进程，后台跑 `hermes update`，日志落盘，前端轮询日志与结果。
# 区别只有两点：(1) 更新前把安装目录里的本地改动导出成补丁存档；(2) 放进独立 systemd 单元里跑，
# 这样不占本服务 768M 的内存上限（装依赖时被 OOM 杀掉会留下半成品安装），本服务重启也不会中断更新。

UPDATE_UNIT = "hermes-panel-update"
UPDATE_RUN_ROOT = HERMES_HOME / "panel-updates"
UPDATE_MIN_FREE = 2 * 1024 ** 3
UPDATE_KEEP_RUNS = 10
UPDATE_LOG_TAIL_LINES = 120
RUN_ID_RE = re.compile(r"^\d{8}-\d{6}$")
# sh 包一层只为把退出码落盘：临时单元正常退出后会被 systemd 回收，退出码随之丢失。
UPDATE_SH = '"$1" update --yes </dev/null; echo $? > "$2/exit-code"'


def _unit_active() -> bool:
    try:
        proc = subprocess.run(["systemctl", "is-active", f"{UPDATE_UNIT}.service"], capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.stdout.strip() in ("active", "activating", "deactivating")


def _run_dirs() -> list[Path]:
    try:
        return sorted((d for d in UPDATE_RUN_ROOT.iterdir() if d.is_dir() and RUN_ID_RE.match(d.name)), key=lambda d: d.name)
    except OSError:
        return []


def _read_small(path: Path, limit: int = 4096) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read(limit).strip()
    except OSError:
        return ""


def _log_tail(path: Path) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 64 * 1024))
            raw = f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""
    lines = [re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", ln).rstrip() for ln in raw.splitlines()]
    return redact("\n".join(lines[-UPDATE_LOG_TAIL_LINES:]))[-20000:]


def get_update_plan() -> dict[str, Any]:
    """hermes update --plan：只读预览（会重启哪些服务）。"""
    with _CLI_SLOTS:
        try:
            proc = subprocess.run([HERMES_BIN, "update", "--plan"], capture_output=True, text=True, timeout=90, check=False,
                                  stdin=subprocess.DEVNULL, env={**os.environ, "NO_COLOR": "1", "TERM": "dumb"})
        except subprocess.TimeoutExpired as exc:
            raise ApiError(504, "cli_timeout", "hermes update --plan 超时") from exc
    text = redact(_clean(proc.stdout or proc.stderr))[:4000]
    if proc.returncode != 0 and not text:
        raise ApiError(500, "plan_failed", "读取更新计划失败")
    return {"plan": text, "exitCode": proc.returncode}


def get_update_status() -> dict[str, Any]:
    running = _unit_active()
    out: dict[str, Any] = {"running": running, "state": "idle", "runId": None, "logTail": ""}
    dirs = _run_dirs()
    if not dirs:
        return out
    d = dirs[-1]
    code_s = _read_small(d / "exit-code")
    if running:
        state = "running"
    elif code_s == "0":
        state = "done"
    elif code_s.isdigit():
        state = "failed"
    else:
        state = "interrupted"  # 单元已退出但没留下退出码
    old = _read_small(d / "old-head")
    new = _git_local("rev-parse", "HEAD").strip()
    out.update(
        state=state, runId=d.name, exitCode=int(code_s) if code_s.isdigit() else None, oldHead=old[:10], newHead=new[:10],
        changed=bool(old) and old != new, backupDir=str(d), patchBytes=(d / "local-changes.patch").stat().st_size if (d / "local-changes.patch").exists() else 0,
        hasUntracked=(d / "untracked.tar.gz").exists(), logTail=_log_tail(d / "update.log"),
    )
    if not running and not (d / "caches-cleared").exists():
        # 新版本可能改了配置结构/插件：让读缓存失效，并重新比对版本
        _clear_caches()
        _SETTINGS_CACHE.update(stamp=None, at=0.0, data=None)
        _UPDATE_CACHE.update(at=0.0, data=None)
        with contextlib.suppress(OSError):
            (d / "caches-cleared").write_text("1", encoding="ascii")
    return out


def _backup_local_changes(run: Path) -> None:
    """更新前存档：当前提交号、未提交改动的补丁（暂存区+工作区）、全部未跟踪文件。失败直接抛出——备份不成就不更新。"""
    head = _git_local("rev-parse", "HEAD").strip()
    if not SHA_RE.match(head):
        raise ApiError(500, "backup_failed", "读不到当前提交，已取消更新")
    (run / "old-head").write_text(head + "\n", encoding="ascii")

    def backup_git(*args: str) -> bytes:
        proc = subprocess.run(
            ["git", "-c", "safe.directory=*", "-C", str(HERMES_INSTALL), *args],
            capture_output=True, timeout=30, check=False, stdin=subprocess.DEVNULL,
        )
        if proc.returncode != 0:
            raise ApiError(500, "backup_failed", "导出本地改动失败，已取消更新")
        return proc.stdout

    index_patch = backup_git("diff", "--cached", "--binary")
    worktree_patch = backup_git("diff", "--binary")
    (run / "index.patch").write_bytes(index_patch)
    (run / "worktree.patch").write_bytes(worktree_patch)
    # 保持对旧前端/脚本的向后兼容：合成全量补丁（git diff HEAD --binary）写入 local-changes.patch
    full_patch = backup_git("diff", "HEAD", "--binary")
    (run / "local-changes.patch").write_bytes(full_patch)

    paths_raw = backup_git("ls-files", "--others", "--exclude-standard", "-z")
    if paths_raw:
        with tarfile.open(run / "untracked.tar.gz", "w:gz", dereference=False) as tar:
            for raw in paths_raw.split(b"\0"):
                if not raw:
                    continue
                rel = os.fsdecode(raw)
                src = HERMES_INSTALL / rel
                # 保留普通文件以及符号链接（包括指向普通文件的symlink和dangling symlink）
                if src.is_symlink() or src.is_file():
                    tar.add(src, arcname=rel, recursive=False)


def start_update(confirm: Any) -> dict[str, Any]:
    if confirm != "update":
        raise ApiError(400, "not_confirmed", "需要确认更新")
    with _WRITE_LOCK:
        if _unit_active():
            raise ApiError(409, "update_running", "已有更新正在进行")
        free = shutil.disk_usage("/").free
        if free < UPDATE_MIN_FREE:
            raise ApiError(400, "low_disk", f"磁盘剩余 {free // 2 ** 20} MB，不足 {UPDATE_MIN_FREE // 2 ** 20} MB，先清理再更新")
        if not (HERMES_INSTALL / ".git").exists():
            raise ApiError(400, "not_git_install", "安装目录不是 git 安装，不支持面板内更新")
        UPDATE_RUN_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
        run = UPDATE_RUN_ROOT / time.strftime("%Y%m%d-%H%M%S")
        run.mkdir(mode=0o700)
        try:
            _backup_local_changes(run)
            (run / "update.log").write_text(f"[panel] 更新开始 {time.strftime('%F %T')}\n", encoding="utf-8")
            subprocess.run(["systemctl", "reset-failed", f"{UPDATE_UNIT}.service"], capture_output=True, timeout=10, check=False)
            log_path = run / "update.log"
            cmd = [
                "systemd-run", f"--unit={UPDATE_UNIT}", "--quiet", "--property=MemoryMax=2G", f"--working-directory={HERMES_INSTALL}",
                f"--property=StandardOutput=append:{log_path}", f"--property=StandardError=append:{log_path}",
                f"--setenv=HOME={os.environ.get('HOME', '/root')}", f"--setenv=HERMES_HOME={HERMES_HOME}", "--setenv=NO_COLOR=1", "--setenv=TERM=dumb",
                "--", "/bin/sh", "-c", UPDATE_SH, "hermes-update", HERMES_BIN, str(run),
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False, stdin=subprocess.DEVNULL)
            if proc.returncode != 0:
                raise ApiError(500, "start_failed", redact((proc.stderr or proc.stdout).strip())[-300:] or "启动更新失败")
        except BaseException:
            shutil.rmtree(run, ignore_errors=True)
            raise
        for old in _run_dirs()[:-UPDATE_KEEP_RUNS]:
            shutil.rmtree(old, ignore_errors=True)
    log.info("hermes update started via panel (run %s)", run.name)
    return {"ok": True, "runId": run.name}


def _image_studio():
    # systemd 用 python3 -I 启动。隔离模式不把脚本目录放进 sys.path，同目录 import 会失败。
    cached = getattr(_image_studio, "mod", None)
    if cached is not None:
        return cached
    try:
        if __package__:
            from . import image_studio as mod
        else:
            import importlib.util
            import sys
            from pathlib import Path
            path = Path(__file__).resolve().parent / "image_studio.py"
            spec = importlib.util.spec_from_file_location("hermes_panel_image_studio", path)
            if spec is None or spec.loader is None or not path.is_file():
                raise ImportError(path.name)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = mod
            try:
                spec.loader.exec_module(mod)
            except Exception:
                sys.modules.pop(spec.name, None)
                raise
    except ImportError as exc:
        raise ApiError(503, "image_studio_unavailable", "生图模块未安装") from exc
    _image_studio.mod = mod
    return mod


def _read_regular_at(dir_fd: int, name: str, *, missing_ok: bool = False) -> str | None:
    """只读 profile 自己的 config.yaml / .env。符号链接失败关闭，不跟随。"""
    if name not in {"config.yaml", ".env"}:
        raise ApiError(403, "denied", "该文件不允许通过面板访问")
    try:
        fd = os.open(name, os.O_RDONLY | _require_nofollow() | os.O_CLOEXEC, dir_fd=dir_fd)
    except OSError as exc:
        if missing_ok and exc.errno == errno.ENOENT:
            return None
        code = "env_unavailable" if name == ".env" else "config_unavailable"
        message = "生图密钥暂不可读" if name == ".env" else "生图配置暂不可读"
        raise ApiError(503, code, message) from exc
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ApiError(403, "unsafe_file", "无法安全打开该文件")
        with os.fdopen(os.dup(fd), "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()
    finally:
        _close_fd(fd)


def _open_relative_file(root_fd: int, relative: str) -> int:
    parts = [part for part in relative.split("/") if part]
    if len(parts) < 2 or any(part in {".", ".."} or part.startswith(".") or _segment_denied(part) for part in parts):
        raise ApiError(403, "denied", "该文件不允许通过面板访问")
    dir_fd = file_fd = None
    try:
        dir_fd = _walk_from(root_fd, parts[:-1], create=False, missing="not_found")
        file_fd = os.open(parts[-1], os.O_RDONLY | _require_nofollow() | os.O_CLOEXEC, dir_fd=dir_fd)
        if not stat.S_ISREG(os.fstat(file_fd).st_mode):
            raise ApiError(403, "unsafe_file", "无法安全打开该文件")
        opened = file_fd
        file_fd = None
        return opened
    except OSError as exc:
        raise ApiError(403, "unsafe_file", "无法安全打开该文件") from exc
    finally:
        _close_fd(file_fd)
        _close_fd(dir_fd)


def _studio_prefixes(home: Path, uploads_fd: int) -> list[str]:
    prefix = _injected_prefix(home, "uploads")
    identity = _proc_fd_path(uploads_fd)
    if _production_text(str(identity)) or _legacy_file_root(identity):
        raise ApiError(403, "denied", "该文件不允许通过面板访问")
    ident = str(identity)
    if not ident.endswith("/"):
        ident += "/"
    prefixes = [prefix]
    if ident != prefix and not _production_text(ident):
        prefixes.append(ident)
    return prefixes


def _image_runtime():
    mod = _image_studio()
    if not _clawd_home_raw():
        try:
            return mod, mod.load_runtime(HERMES_HOME, FILE_ROOTS["uploads"]["path"])
        except mod.ImageStudioError as exc:
            raise ApiError(exc.status, exc.code, exc.message) from exc
    home = _home_path()
    home_fd = uploads_fd = None
    try:
        home_fd = _open_home_fd(home)
        identity = _proc_fd_path(home_fd)
        if _production_text(str(identity)) or _legacy_file_root(identity):
            raise ApiError(403, "denied", "该文件不允许通过面板访问")
        uploads_fd, _configured, _spec = _open_root_fd("uploads")
        uploads_identity = _proc_fd_path(uploads_fd)
        if _production_text(str(uploads_identity)) or _legacy_file_root(uploads_identity):
            raise ApiError(403, "denied", "该文件不允许通过面板访问")
        if uploads_identity != identity and identity not in uploads_identity.parents:
            raise ApiError(403, "outside_root", "文件根不在当前 CLAWD_HOME 内")
        config_text = _read_regular_at(home_fd, "config.yaml")
        if config_text is None:
            raise ApiError(503, "config_unavailable", "生图配置暂不可读")
        env_text = _read_regular_at(home_fd, ".env", missing_ok=True) or ""
        db_path = identity / "panel_image_studio.db"
        if _production_text(str(db_path)) or _legacy_file_root(db_path):
            raise ApiError(403, "denied", "该文件不允许通过面板访问")
        try:
            runtime = mod.load_runtime_texts(config_text, env_text, uploads_identity, db_path)
        except mod.ImageStudioError as exc:
            raise ApiError(exc.status, exc.code, exc.message) from exc
        return mod, runtime
    finally:
        _close_fd(uploads_fd)
        _close_fd(home_fd)


def image_models_view() -> dict[str, Any]:
    mod, runtime = _image_runtime()
    return {"data": mod.available_models(runtime.config, runtime.secrets)}


def image_history_view() -> dict[str, Any]:
    mod, runtime = _image_runtime()
    home_fd = None
    try:
        if _clawd_home_raw():
            home_fd = _open_home_fd(_home_path())
        try:
            return {"data": mod.list_history(runtime.db_path, parent_fd=home_fd)}
        except mod.ImageStudioError as exc:
            raise ApiError(exc.status, exc.code, exc.message) from exc
    finally:
        _close_fd(home_fd)


def image_generate_view(body: dict[str, Any]) -> dict[str, Any]:
    mod, runtime = _image_runtime()
    if not _clawd_home_raw():
        try:
            return mod.generate(body, runtime)
        except mod.ImageStudioError as exc:
            raise ApiError(exc.status, exc.code, exc.message) from exc
    home = _home_path()
    home_fd = root_fd = None
    try:
        home_fd = _open_home_fd(home)
        root_fd, _configured, _spec = _open_root_fd("uploads")

        def publish(relative: str) -> str:
            file_fd = _open_relative_file(root_fd, relative)
            try:
                return _abs_for_injection(home, "uploads", relative, file_fd)
            finally:
                _close_fd(file_fd)

        try:
            return mod.generate(body, runtime, root_fd=root_fd, publish=publish, parent_fd=home_fd)
        except mod.ImageStudioError as exc:
            raise ApiError(exc.status, exc.code, exc.message) from exc
    finally:
        _close_fd(root_fd)
        _close_fd(home_fd)


def image_delete_view(body: dict[str, Any]) -> dict[str, Any]:
    mod, runtime = _image_runtime()
    item_id = body.get("id")
    if not isinstance(item_id, str):
        raise ApiError(400, "invalid_id", "没有这条生图记录")
    if not _clawd_home_raw():
        try:
            return mod.delete_history(runtime.db_path, runtime.uploads, item_id)
        except mod.ImageStudioError as exc:
            raise ApiError(exc.status, exc.code, exc.message) from exc
    home = _home_path()
    home_fd = root_fd = None
    try:
        home_fd = _open_home_fd(home)
        root_fd, _configured, _spec = _open_root_fd("uploads")
        prefixes = _studio_prefixes(home, root_fd)
        try:
            return mod.delete_history(
                runtime.db_path,
                runtime.uploads,
                item_id,
                root_fd=root_fd,
                prefixes=prefixes,
                parent_fd=home_fd,
            )
        except mod.ImageStudioError as exc:
            raise ApiError(exc.status, exc.code, exc.message) from exc
    finally:
        _close_fd(root_fd)
        _close_fd(home_fd)


def _upload_home(handler: Any, query: dict[str, str]) -> dict[str, Any]:
    """CLAWD_HOME 下的上传：目录 fd + O_NOFOLLOW，absPath 对齐活的注入前缀。"""
    root_id = query.get("root", "uploads")
    spec = FILE_ROOTS.get(root_id)
    if spec is None:
        raise ApiError(404, "unknown_root", "未知的目录")
    if not spec["writable"]:
        raise ApiError(403, "read_only", f"{spec['label']} 是只读目录")
    try:
        length = int(handler.headers.get("Content-Length") or -1)
    except (TypeError, ValueError):
        length = -1
    if length < 0:
        raise ApiError(411, "length_required", "缺少 Content-Length")
    if length > MAX_UPLOAD_BYTES:
        raise ApiError(413, "too_large", f"单个文件不能超过 {MAX_UPLOAD_BYTES // 1024 // 1024}MB")
    name = _reject_raw_upload_name(query.get("name", ""))
    sub = query.get("dir") or (time.strftime("%Y-%m-%d") if root_id == "uploads" else "")
    if "\\" in sub or "\x00" in sub or sub.startswith(("/", "\\")) or any(ord(c) < 32 or ord(c) == 127 for c in sub):
        raise ApiError(400, "invalid_path", "路径不合法")
    sub_parts = [part for part in sub.split("/") if part]
    if any(part in {".", ".."} or _segment_denied(part) for part in sub_parts):
        raise ApiError(400, "invalid_path", "路径不合法")
    try:
        _reject_workspace_browse(root_id, sub)
        _safe_path(root_id, sub)
    except RuntimeError as exc:
        raise ApiError(403, "unsafe_path", "不允许访问该路径") from exc
    home = _home_path()
    root_fd, _configured, checked = _open_root_fd(root_id)
    dir_fd = file_fd = None
    temp_name = None
    final_name = None
    committed = False
    try:
        if not checked["writable"]:
            raise ApiError(403, "read_only", f"{checked['label']} 是只读目录")
        dir_fd = _walk_from(root_fd, sub_parts, create=True, missing="not_found") if sub_parts else os.dup(root_fd)
        try:
            name_max = os.fpathconf(dir_fd, "PC_NAME_MAX")
        except (ValueError, OSError):
            name_max = 255
        if len(os.fsencode(name)) > name_max:
            raise ApiError(400, "invalid_name", "文件名超过文件系统字节限制")
        temp_name = ".upload-" + secrets.token_hex(8)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _require_nofollow() | os.O_CLOEXEC
        file_fd = os.open(temp_name, flags, 0o600, dir_fd=dir_fd)
        remaining = length
        while remaining > 0:
            chunk = handler.rfile.read(min(65536, remaining))
            if not chunk:
                raise ApiError(400, "incomplete", "上传中断")
            view = memoryview(chunk)
            while view:
                written = os.write(file_fd, view)
                if written <= 0:
                    raise ApiError(400, "incomplete", "上传中断")
                view = view[written:]
            remaining -= len(chunk)
        stem, suffix = Path(name).stem, Path(name).suffix
        candidates = [name, *[f"{stem} ({index}){suffix}" for index in range(1, 1000)]]
        for cand in candidates:
            if len(os.fsencode(cand)) > name_max:
                continue
            try:
                os.link(temp_name, cand, src_dir_fd=dir_fd, dst_dir_fd=dir_fd, follow_symlinks=False)
            except (FileExistsError, IsADirectoryError):
                continue
            final_name = cand
            break
        if final_name is None:
            raise ApiError(409, "name_exhausted", "同名文件过多")
        info = os.lstat(final_name, dir_fd=dir_fd)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise ApiError(403, "unsafe_file", "无法安全打开该文件")
        relative = "/".join([*sub_parts, final_name])
        try:
            abs_path = _abs_for_injection(home, root_id, relative, file_fd)
        except ApiError:
            try:
                os.unlink(final_name, dir_fd=dir_fd)
            except OSError:
                pass
            raise
        committed = True
        log.info("uploaded %s (%d bytes) via panel", abs_path, length)
        return {"ok": True, "root": root_id, "path": relative, "absPath": abs_path, "size": length}
    except ApiError:
        raise
    except OSError as exc:
        raise ApiError(403, "unsafe_file", "无法安全打开该文件") from exc
    finally:
        _close_fd(file_fd)
        if dir_fd is not None and dir_fd >= 0:
            if temp_name:
                try:
                    os.unlink(temp_name, dir_fd=dir_fd)
                except OSError:
                    pass
            if final_name and not committed:
                try:
                    os.unlink(final_name, dir_fd=dir_fd)
                except OSError:
                    pass
        _close_fd(dir_fd)
        _close_fd(root_fd)


def _resolve_artifact(absolute: str) -> tuple[str, str]:
    """Validate an absolute artifact path and resolve it to (root_id, rel).

    Same checks as the old Handler._artifact; no I/O beyond path validation.
    Raises ApiError exactly like the old code.
    """
    # Validate the undecorated lexical path before Path/resolve can erase segments.
    if (not absolute.startswith("/") or absolute.startswith("//") or "\\" in absolute
            or any(ord(c) < 32 or ord(c) == 127 for c in absolute)
            or any(part in (".", "..") for part in absolute.split("/"))):
        raise ApiError(400, "invalid_path", "需要合法的绝对文件路径")
    target = Path(absolute)
    if (any(target == p or p in target.parents for p in DENY_PREFIXES)
            or any(DENY_NAME_RE.search(part) for part in target.parts)):
        raise ApiError(403, "denied", "该文件不允许通过面板访问")
    if _clawd_home_raw():
        root_id, rel = _match_home_artifact(absolute)
        _reject_workspace_browse(root_id, rel)
        return root_id, rel
    for root_id, spec in FILE_ROOTS.items():
        try:
            rel = target.relative_to(spec["path"])
        except ValueError:
            continue
        # 根目录之下的点文件/点目录（.git、.aws、.npmrc…）与凭据容器不经助手引用对外提供。
        if any(part.startswith(".") or ARTIFACT_SECRET_RE.search(part) for part in rel.parts):
            raise ApiError(403, "denied", "该文件不允许通过面板访问")
        _reject_workspace_browse(root_id, str(rel))
        return root_id, str(rel)
    raise ApiError(403, "outside_root", "不允许访问该路径")


def _download_response_headers(f: Any, target: Path, size: int, preview: bool) -> dict[str, str]:
    """Response headers for a file download/artifact. Sniffs raster bytes for preview."""
    ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    inline_type = None
    if preview:
        inline_type = _raster_mime(target.name, f.read(32))
        f.seek(0)
        if not inline_type and target.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"):
            ctype = "application/octet-stream"
    disposition = "inline" if inline_type else "attachment"
    return {
        "Content-Type": inline_type or ctype,
        "Content-Length": str(size),
        "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(target.name)}",
        "Content-Security-Policy": "sandbox; default-src 'none'; base-uri 'none'",
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "no-store",
    }


@contextlib.contextmanager
def open_download(root_id: str, rel: str, *, preview: bool = False):
    """Open a file for download: yields (headers, fileobj, size). Caller closes."""
    with _open_download(root_id, rel) as (f, info, target):
        yield _download_response_headers(f, target, info.st_size, preview), f, info.st_size


class Handler:
    """请求分发器：不是 HTTP 服务器，不监听、不读 socket。

    调用方填好 `key` / `path`（含查询串）/ `headers`（`.get` 即可）/
    `rfile`（读 body 的二进制流）/ `wfile`（写响应的二进制流），再调
    `_dispatch(method)`。`_send` 会把状态行、头、体写进 `wfile`。
    """

    key = ""
    close_connection = False

    def __init__(self) -> None:
        self.path = "/"
        self.headers: Any = {}
        self.rfile: Any = None
        self.wfile: Any = None

    def send_response(self, status: int) -> None:
        self._resp_status = status
        self._resp_headers: list[tuple[str, str]] = []
        self._resp_head = f"HTTP/1.1 {status} \r\n".encode("ascii")

    def send_header(self, key: str, value: str) -> None:
        self._resp_headers.append((key, value))

    def end_headers(self) -> None:
        head = bytearray(self._resp_head)
        for key, value in self._resp_headers:
            head += f"{key}: {value}\r\n".encode("utf-8", "replace")
        head += b"\r\n"
        self.wfile.write(bytes(head))

    def _send(self, status: int, payload: Any) -> None:
        try:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    def _json_body(self, limit: int = MAX_BODY_BYTES) -> dict[str, Any]:
        cl_raw = self.headers.get("Content-Length")
        try:
            length = int(cl_raw or 0)
        except (ValueError, TypeError) as exc:
            raise ApiError(400, "invalid_content_length", "Content-Length 不合法") from exc
        if length < 0:
            raise ApiError(400, "invalid_content_length", "Content-Length 不能为负数")
        if length > limit:
            raise ApiError(413, "too_large", "请求体过大")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw or b"{}")
        except json.JSONDecodeError as exc:
            raise ApiError(400, "invalid_json", "请求体不是合法 JSON") from exc
        if not isinstance(body, dict):
            raise ApiError(400, "invalid_json", "请求体必须是对象")
        return body

    def _dispatch(self, method: str, *, key_required: bool = True) -> None:
        try:
            if key_required:
                provided_key = self.headers.get("X-Panel-Key", "")
                if not provided_key.isascii() or not hmac.compare_digest(provided_key, self.key):
                    raise ApiError(401, "unauthorized", "unauthorized")
            url = urlsplit(self.path)
            path = unquote(url.path).rstrip("/") or "/"
            query = {k: v[0] for k, v in parse_qs(url.query, keep_blank_values=True).items()}
            try:
                if method == "GET" and path == "/files/artifact":
                    self._artifact(query.get("path", ""), query.get("preview") == "1")
                    return
                if method == "GET" and path == "/files/download":
                    root_id, rel = query.get("root", ""), query.get("path", "")
                    _reject_workspace_browse(root_id, rel)
                    self._download(root_id, rel)
                    return
                if method == "POST" and path == "/files/upload":
                    self._send(200, self._upload(query))
                    return
            except (BrokenPipeError, ConnectionResetError):
                # 直接读写客户端 socket 的路径：断开时静默终止，不追加响应。
                self.close_connection = True
                return
            self._send(200, self._route(method, path, query))
        except (BrokenPipeError, ConnectionResetError):
            # Request URLs can contain credentials; do not put query strings in logs.
            log.exception("upstream connection reset on %s request", method)
            self._send(502, {"error": {"code": "upstream_reset", "message": "上游连接被重置，请稍后重试"}})
        except ApiError as exc:
            self._send(exc.status, {"error": {"code": exc.code, "message": exc.message}})
        except Exception:
            # Request URLs can contain credentials; do not put query strings in logs.
            log.exception("unhandled error on %s request", method)
            self._send(500, {"error": {"code": "internal", "message": "内部错误"}})

    def _artifact(self, absolute: str, preview: bool) -> None:
        root_id, rel = _resolve_artifact(absolute)
        self._download(root_id, rel, preview=preview)

    def _download(self, root_id: str, rel: str, *, preview: bool = False) -> None:
        with open_download(root_id, rel, preview=preview) as (headers, f, size):
            self.send_response(200)
            for key, value in headers.items():
                self.send_header(key, value)
            try:
                self.end_headers()
                remaining = size
                while remaining:
                    chunk = f.read(min(64 * 1024, remaining))
                    if not chunk:
                        self.close_connection = True  # Concurrent truncate: no JSON can follow file headers.
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
            except OSError as exc:
                # Client disconnect / disk error after headers: close, never append a second response.
                self.close_connection = True
                if not isinstance(exc, (BrokenPipeError, ConnectionResetError)):
                    log.exception("file response stream failed")

    def _upload(self, query: dict[str, str]) -> dict[str, Any]:
        """原始请求体即文件内容（不用 multipart）。写同目录临时文件，完整收齐才 rename，失败不留半截文件。"""
        if _clawd_home_raw():
            return _upload_home(self, query)
        root_id = query.get("root", "uploads")
        base, spec = _root(root_id)
        if not spec["writable"]:
            raise ApiError(403, "read_only", f"{spec['label']} 是只读目录")
        try:
            length = int(self.headers.get("Content-Length") or -1)
        except ValueError:
            length = -1
        if length < 0:
            raise ApiError(411, "length_required", "缺少 Content-Length")
        if length > MAX_UPLOAD_BYTES:
            raise ApiError(413, "too_large", f"单个文件不能超过 {MAX_UPLOAD_BYTES // 1024 // 1024}MB")
        name = UPLOAD_NAME_RE.sub("_", query.get("name", "")).strip(" .")[:180]
        if not name or DENY_NAME_RE.search(name):
            raise ApiError(400, "invalid_name", "文件名不合法")
        sub = query.get("dir") or (time.strftime("%Y-%m-%d") if root_id == "uploads" else "")
        _reject_workspace_browse(root_id, sub)
        _b, folder, _s = _safe_path(root_id, sub)
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            name_max = os.pathconf(folder, "PC_NAME_MAX")
        except (ValueError, OSError):
            name_max = 255
        if len(os.fsencode(name)) > name_max:
            raise ApiError(400, "invalid_name", "文件名超过文件系统字节限制")

        fd, temporary_name = tempfile.mkstemp(prefix=".upload-", dir=folder)
        tmp = Path(temporary_name)
        try:
            with os.fdopen(fd, "wb") as f:
                remaining = length
                while remaining > 0:
                    chunk = self.rfile.read(min(65536, remaining))
                    if not chunk:
                        raise ApiError(400, "incomplete", "上传中断")
                    f.write(chunk)
                    remaining -= len(chunk)

            # F02 atomic commit without overwriting existing files
            dest: Path | None = None
            for attempt in range(100):
                cand = _unique(folder / name)
                try:
                    os.link(tmp, cand)
                    dest = cand
                    break
                except FileExistsError:
                    continue
            if dest is None:
                raise ApiError(409, "upload_name_busy", "同名上传过多，请稍后重试")
        finally:
            tmp.unlink(missing_ok=True)
        log.info("uploaded %s (%d bytes) via panel", dest, length)
        return {"ok": True, "root": root_id, "path": str(dest.relative_to(base)), "absPath": str(dest), "size": length}

    def _route(self, method: str, path: str, query: dict[str, str]) -> Any:
        if method == "POST" and path == "/sessions/folders":
            return update_session_folders(self._json_body())
        if method == "GET" and path == "/sessions/folders":
            return get_session_folders(query.get("sessions") == "1")
        if method == "GET" and path == "/sessions/active":
            return get_active_sessions()
        if method == "GET" and path == "/sessions/message-origins":
            return get_message_origins(query.get("session_id", ""), query.get("ids", ""))
        if method == "GET" and path == "/sessions/run":
            return get_session_run(query.get("session_id", ""))
        if method == "GET" and path == "/sessions/run/resolve":
            return get_session_run(query.get("session_id", ""), idempotency_key=query.get("idempotency_key", ""))
        if method == "GET" and path == "/sessions/execution":
            return get_session_execution(query.get("session_id", ""))
        if method == "POST" and path == "/sessions/execution/stop":
            return stop_session_execution(self._json_body())
        if method == "GET" and path == "/sessions/background":
            return get_session_background(query.get("session_id", ""))
        if method == "POST" and path == "/sessions/edit-branch":
            return create_edit_branch(HERMES_HOME / "state.db", self._json_body(MAX_BODY_BYTES))
        if method == "GET" and path == "/live/task":
            cid = query.get("client_id")
            if cid is not None:
                return live_task(query.get("session_id", ""), query.get("task_id", ""), cid)
            return live_task(query.get("session_id", ""), query.get("task_id", ""))
        if method == "POST" and path == "/live/task/control":
            return live_task_control(self._json_body())
        if method == "POST" and path == "/live/task/input":
            return live_task_input(self._json_body())
        if method == "GET" and path == "/live/state":
            return live_state(query.get("client_id"))
        if method == "POST" and path == "/live/takeover":
            return live_takeover(self._json_body())
        if method == "POST" and path == "/live/release":
            return live_release(self._json_body())
        if method == "POST" and path == "/live/type":
            return live_type(self._json_body())
        if method == "POST" and path == "/sessions/commentary":
            return get_commentary(self._json_body())
        if method == "GET" and path == "/health":
            return {"ok": True}
        if method == "GET" and path == "/files/roots":
            return list_roots()
        if method == "GET" and path == "/files":
            return list_dir(query.get("root", ""), query.get("path", ""))
        if method == "POST" and path == "/files/delete":
            body = self._json_body()
            root_id, rel = body.get("root"), body.get("path")
            if not isinstance(root_id, str) or not isinstance(rel, str):
                raise ApiError(400, "invalid_body", "需要 root 与 path")
            return delete_file(root_id, rel)
        if method == "GET" and path == "/activity":
            try:
                limit = max(1, min(200, int(query.get("limit", "60"))))
            except ValueError:
                limit = 60
            return get_activity(limit)
        if method == "GET" and path == "/status":
            return get_status()
        if method == "POST" and path.startswith("/services/") and path.endswith("/restart"):
            return restart_service(path[len("/services/"):-len("/restart")], force=query.get("force") in ("1", "true"))
        if method == "GET" and path == "/config":
            return get_config()
        if method == "POST" and path == "/config/set":
            body = self._json_body()
            key, value = body.get("key"), body.get("value")
            if not isinstance(key, str) or not isinstance(value, str):
                raise ApiError(400, "invalid_body", "需要字符串 key 与 value")
            return config_set(key, value)
        if method == "GET" and path == "/skills":
            return list_skills()
        if method == "GET" and path == "/skill-backups":
            return list_skill_backups(query.get("path", ""))
        if method == "GET" and path == "/skill-backup":
            return get_skill_backup(query.get("path", ""), query.get("id", ""))
        if method == "POST" and path == "/skills/save":
            body = self._json_body(MAX_SKILL_BODY_BYTES)
            return save_skill(body.get("path"), body.get("content"), body.get("baseModifiedAt"))
        if method == "GET" and path.startswith("/skills/"):
            return get_skill(path[len("/skills/"):])
        if method == "POST" and path == "/skills/enabled":
            body = self._json_body()
            name, enabled = body.get("name"), body.get("enabled")
            if not isinstance(name, str) or not isinstance(enabled, bool):
                raise ApiError(400, "invalid_body", "需要 name 与布尔 enabled")
            return set_skill_enabled(name, enabled)
        if method == "GET" and path == "/image/models":
            return image_models_view()
        if method == "GET" and path == "/image/history":
            return image_history_view()
        if method == "POST" and path == "/image/generate":
            return image_generate_view(self._json_body())
        if method == "POST" and path == "/image/delete":
            return image_delete_view(self._json_body())
        return self._route_admin(method, path, query)

    def _route_admin(self, method: str, path: str, query: dict[str, str]) -> Any:
        """第 8 段：用量、日志、模型、MCP、插件、配置档、Webhooks。"""
        if method == "GET":
            if path == "/settings":
                return get_settings()
            if path == "/update/status":
                return get_update_status()
            if path == "/update/plan":
                return get_update_plan()
            if path == "/update/check":
                return get_update_info(query.get("force") == "1")
            if path == "/usage":
                try:
                    days = int(query.get("days", "30"))
                except ValueError:
                    days = 0
                return get_usage(days)
            if path == "/subagents":
                try:
                    n = max(1, min(50, int(query.get("limit", "20"))))
                except ValueError:
                    n = 20
                return get_subagents(query.get("session", ""), n)
            if path == "/context":
                return get_context(query.get("session", ""))
            if path == "/run-wait":
                return get_run_wait(query.get("session", ""), query.get("started_at_ms"))
            if path == "/logs/sources":
                return list_log_sources()
            if path == "/logs":
                try:
                    n = int(query.get("lines", "200"))
                except ValueError:
                    n = 200
                return get_logs(query.get("source", ""), n, query.get("level", ""), query.get("component", ""), query.get("q", ""))
            if path == "/models":
                return get_models()
            if path == "/cron-model":
                return get_cron_model()
            if path == "/mcp":
                return list_mcp()
            if path == "/plugins":
                return list_plugins()
            if path == "/profiles":
                return list_profiles()
            if path.startswith("/profiles/"):
                return show_profile(path[len("/profiles/"):])
            if path == "/webhooks":
                return list_webhooks()
            if path == "/webhooks/deliveries":
                try:
                    n = max(1, min(100, int(query.get("limit", "30"))))
                except ValueError:
                    n = 30
                return recent_deliveries(query.get("route", ""), n)
        elif method == "POST":
            body = self._json_body()
            routes: dict[str, Any] = {
                "/update/start": lambda: start_update(body.get("confirm")),
                "/settings/set": lambda: set_setting(body.get("key"), body.get("value")),
                "/settings/reset": lambda: reset_setting(body.get("key")),
                "/models/default": lambda: set_default_model(body.get("model"), body.get("provider")),
                "/models/provider-models": lambda: set_provider_models(body.get("provider"), body.get("models")),
                "/models/provider-discover": lambda: set_provider_discover(body.get("provider"), body.get("discover")),
                "/models/excluded": lambda: set_excluded_providers(body.get("providers")),
                "/cron-model": lambda: set_cron_model(body.get("model"), body.get("provider")),
                "/mcp/enabled": lambda: set_mcp_enabled(body.get("name"), body.get("enabled")),
                "/mcp/remove": lambda: remove_mcp(body.get("name")),
                "/mcp/test": lambda: test_mcp(body.get("name")),
                "/plugins/enabled": lambda: set_plugin_enabled(body.get("name"), body.get("enabled")),
                "/profiles/create": lambda: create_profile(body.get("name"), body.get("cloneFrom"), body.get("description")),
                "/profiles/use": lambda: use_profile(body.get("name")),
                "/profiles/delete": lambda: delete_profile(body.get("name")),
                "/webhooks/create": lambda: create_webhook(body),
                "/webhooks/delete": lambda: delete_webhook(body.get("name")),
                "/webhooks/enabled": lambda: set_webhook_enabled(body.get("name"), body.get("enabled")),
                "/webhooks/test": lambda: test_webhook(body.get("name")),
            }
            if path in routes:
                return routes[path]()
        raise ApiError(404, "not_found", "not found")

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")


# ---------------------------------------------------------------------------
# aiohttp 接入层：把同一批路由挂到面板统一进程上（前缀 /panel-core/）。
# 鉴权由 PanelServer._authed（cookie + Origin）负责，本层不再校验内网密钥。
# ---------------------------------------------------------------------------


def new_handler(method: str, path: str, headers: dict[str, str], body: bytes) -> Handler:
    """Build a dispatch Handler for one request (same shape tests use)."""
    handler = object.__new__(Handler)
    handler.key = ""
    handler.path = path
    handler.headers = headers
    handler.rfile = io.BytesIO(body)
    handler.wfile = io.BytesIO()
    handler.close_connection = False
    handler.request_version = "HTTP/1.1"
    handler.protocol_version = "HTTP/1.1"
    handler.requestline = f"{method} {path} HTTP/1.1"
    return handler


def parse_raw_response(raw: bytes) -> tuple[int, dict[str, str], bytes]:
    """Split a Handler-written response (status line + headers + body)."""
    head, _, body = raw.partition(b"\r\n\r\n")
    if not head:
        raise ValueError("handler wrote no HTTP response")
    lines = head.split(b"\r\n")
    status = int(lines[0].split()[1])
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if b":" in line:
            name, value = line.split(b":", 1)
            headers[name.decode().strip()] = value.strip().decode()
    return status, headers, body


def _resolve_file_response(tail: str, query: dict[str, str]) -> tuple[str, str, bool]:
    """Resolve a download/artifact request to (root_id, rel, preview)."""
    if tail == "/files/artifact":
        root_id, rel = _resolve_artifact(query.get("path", ""))
        return root_id, rel, query.get("preview") == "1"
    root_id, rel = query.get("root", ""), query.get("path", "")
    _reject_workspace_browse(root_id, rel)
    return root_id, rel, False


def _json_error_response(status: int, code: str, message: str) -> web.Response:
    return web.json_response(
        {"error": {"code": code, "message": message}},
        status=status,
        headers={"Cache-Control": "no-store"},
    )


async def _handle_file_stream(request: web.Request, tail: str, dispatch_path: str) -> web.StreamResponse:
    """Stream /files/download and /files/artifact without buffering the file."""
    query = {k: v[0] for k, v in parse_qs(urlsplit(dispatch_path).query, keep_blank_values=True).items()}
    try:
        root_id, rel, preview = await asyncio.to_thread(_resolve_file_response, tail, query)
    except ApiError as exc:
        return _json_error_response(exc.status, exc.code, exc.message)
    except Exception:
        log.exception("file response failed")
        return _json_error_response(500, "internal", "内部错误")
    stack = contextlib.ExitStack()
    prepared = False
    try:
        f, info, target = stack.enter_context(_open_download(root_id, rel))
        headers = _download_response_headers(f, target, info.st_size, preview)
        response = web.StreamResponse(status=200, headers=headers)
        await response.prepare(request)
        prepared = True
        remaining = info.st_size
        try:
            while remaining:
                chunk = await asyncio.to_thread(f.read, min(64 * 1024, remaining))
                if not chunk:
                    break
                await response.write(chunk)
                remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError, asyncio.CancelledError):
            pass
        try:
            await response.write_eof()
        except (BrokenPipeError, ConnectionResetError, asyncio.CancelledError):
            pass
        return response
    except ApiError as exc:
        if prepared:
            raise
        return _json_error_response(exc.status, exc.code, exc.message)
    except OSError:
        log.exception("file response stream failed")
        if prepared:
            raise
        return _json_error_response(500, "internal", "内部错误")
    finally:
        stack.close()


async def _handle_panel_core(server: Any, request: web.Request, *, prefix: str = "/panel-core") -> web.StreamResponse:
    denied = server._authed(request)
    if denied is not None:
        return denied
    raw_path = request.rel_url.raw_path
    tail = raw_path[len(prefix):] if prefix else raw_path
    raw_qs = request.rel_url.raw_query_string
    dispatch_path = tail + ("?" + raw_qs if raw_qs else "")
    if tail in ("/files/download", "/files/artifact"):
        return await _handle_file_stream(request, tail, dispatch_path)
    length_raw = request.headers.get("Content-Length")
    cap = MAX_UPLOAD_BYTES if tail == "/files/upload" else MAX_SKILL_BODY_BYTES
    if length_raw is not None:
        try:
            length = int(length_raw)
        except (ValueError, TypeError):
            length = -1
        if length > cap:
            return _json_error_response(413, "too_large", "请求体过大")
    body = await request.read()
    if len(body) > cap:
        return _json_error_response(413, "too_large", "请求体过大")
    headers = {"Content-Length": length_raw} if length_raw is not None else {}
    handler = new_handler(request.method, dispatch_path, headers, body)
    await asyncio.to_thread(handler._dispatch, request.method, key_required=False)
    status, resp_headers, resp_body = parse_raw_response(handler.wfile.getvalue())
    out_headers = {k: v for k, v in resp_headers.items() if k.lower() != "content-length"}
    return web.Response(status=status, body=resp_body, headers=out_headers)


def register_panel_core_routes(app: web.Application, server: Any, *, prefix: str = "/panel-core") -> None:
    """Mount the panel-core routes (``prefix/*``) on the panel app.

    Must be registered before the static catch-all. Auth is PanelServer._authed
    (panel cookie + Origin), the same check the old /panel-sidecar/ proxy used.
    ``prefix=""`` mounts the same dispatcher at the root; test fixtures use it
    to serve panel-core routes without the panel prefix.
    """
    async def panel_core(request: web.Request) -> web.StreamResponse:
        return await _handle_panel_core(server, request, prefix=prefix)

    if prefix:
        app.router.add_route("*", prefix + "/{tail:.*}", panel_core)
        app.router.add_route("*", prefix, panel_core)
    else:
        app.router.add_route("*", "/{tail:.*}", panel_core)
