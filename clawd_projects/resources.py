"""Profile-scoped project resources for the panel.

This is the human project-management contract: instructions, workspace files,
and a read of this project's real memory entries. It does not create a
Principal, does not require a session, and does not call the session-bound
memory authorization gate. Agent tool writes stay on that gate.

Filesystem identity is the project row in the caller's profile home
(``get_hermes_home()`` at the HTTP boundary). A URL id is only a lookup key.
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
import stat
import threading
from pathlib import Path
from typing import BinaryIO, Iterator, Optional

from hermes_cli import projects_db as pdb

from clawd_projects.service import (
    _ARCHIVE_LIST_LIMIT,
    _INSTRUCTION_LIMIT,
    _archive_name,
    _project_workspace,
    get_project_by_id,
    get_project_memory_dir,
    write_project_instructions,
    read_project_instructions,
)

logger = logging.getLogger("clawd_projects.resources")

# Panel-sidecar product cap. Text archives keep service._ARCHIVE_LIMIT.
FILE_BYTE_LIMIT = 50 * 1024 * 1024
INSTRUCTION_CHAR_LIMIT = _INSTRUCTION_LIMIT
FILE_LIST_LIMIT = _ARCHIVE_LIST_LIMIT
# Read safety only. Not a per-entry timestamp and not a new memory budget.
MEMORY_READ_BYTE_CAP = 1_000_000
_TITLE_CHARS = 80
_UPLOAD_PREFIX = ".upload-"
_KNOWN_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".json": "application/json",
    ".csv": "text/csv",
    ".html": "text/html",
    ".htm": "text/html",
    ".xhtml": "application/xhtml+xml",
    ".svg": "image/svg+xml",
    ".svgz": "image/svg+xml",
}
_INLINE_ACTIVE = {
    ".html",
    ".htm",
    ".xhtml",
    ".svg",
    ".svgz",
}
_INLINE_ACTIVE_TYPES = {
    "text/html",
    "application/xhtml+xml",
    "image/svg+xml",
}


class ResourceError(Exception):
    """A project-resource failure with an explicit HTTP status. Never a fake empty success."""

    def __init__(self, code: str, message: str, status: int = 400) -> None:
        self.code = code
        self.message = message
        self.status = status
        super().__init__(message)


def memory_entry_id(content: str) -> str:
    """Stable id for one real entry. Identical text hashes the same; nothing else is mixed in."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def memory_title(content: str) -> str:
    """First meaningful short line. Not a category and not a fabricated label."""
    for raw in content.splitlines():
        text = raw.strip()
        if text.startswith("#"):
            text = text.lstrip("#").strip()
        if text:
            return text[:_TITLE_CHARS]
    compact = " ".join(content.split())
    return compact[:_TITLE_CHARS]


def guessed_content_type(name: str) -> str:
    return _KNOWN_TYPES.get(Path(name).suffix.lower(), "application/octet-stream")


def download_content_type(name: str) -> str:
    """Types a browser would render are forced to a download, never an inline document."""
    suffix = Path(name).suffix.lower()
    guessed = guessed_content_type(name)
    if suffix in _INLINE_ACTIVE or guessed in _INLINE_ACTIVE_TYPES:
        return "application/octet-stream"
    return guessed


def download_headers(name: str, size: int) -> dict[str, str]:
    return {
        "Content-Type": download_content_type(name),
        "Content-Length": str(int(size)),
        "Content-Disposition": f'attachment; filename="{name}"',
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "sandbox; default-src 'none'",
        "Cache-Control": "no-store",
        "X-Download-Options": "noopen",
    }


def _require_project(project_id: str, home: Path) -> pdb.Project:
    pid = str(project_id or "").strip()
    if not pid or "\x00" in pid:
        raise ResourceError("invalid_project", "project id is required", 400)
    try:
        project = get_project_by_id(pid, hermes_home=home)
    except ValueError as exc:
        raise ResourceError("invalid_project", str(exc), 400) from exc
    if project is None:
        raise ResourceError("project_not_found", f"Project not found: {pid}", 404)
    return project


def _require_writable(project: pdb.Project) -> None:
    if project.archived:
        raise ResourceError("project_archived", "项目已归档，只读", 409)


def _file_name(name: str) -> str:
    if not isinstance(name, str):
        raise ResourceError("invalid_name", "档案名必须是单个文件名", 400)
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in name):
        raise ResourceError("invalid_name", "档案名含有控制字符，未写入", 400)
    if '"' in name or "\\" in name:
        raise ResourceError("invalid_name", "档案名不能包含引号或反斜杠", 400)
    try:
        cleaned = _archive_name(name)
    except ValueError as exc:
        raise ResourceError("invalid_name", str(exc), 400) from exc
    return cleaned


def _workspace(project: pdb.Project, home: Path) -> Path:
    try:
        root = _project_workspace(project, home)
    except ValueError as exc:
        raise ResourceError("workspace_unavailable", str(exc), 409) from exc
    try:
        st = root.lstat()
    except OSError as exc:
        raise ResourceError("workspace_unavailable", "项目工作目录不可用，未写入", 409) from exc
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise ResourceError("workspace_unavailable", "项目工作目录不可用，未写入", 409)
    return root


def _nofollow_flag() -> int:
    flag = getattr(os, "O_NOFOLLOW", 0)
    if not flag:
        raise ResourceError("file_unreadable", "无法在不跟随符号链接的情况下打开档案", 500)
    return flag


def _file_record(name: str, st: os.stat_result) -> dict[str, object]:
    return {
        "name": name,
        "size": int(st.st_size),
        "updated_at": int(st.st_mtime),
        "content_type": guessed_content_type(name),
    }


def _list_files(project: pdb.Project, home: Path) -> list[dict[str, object]]:
    root = _workspace(project, home)
    try:
        children = list(root.iterdir())
    except OSError as exc:
        raise ResourceError("files_unreadable", "项目档案无法读取，未当作空列表", 409) from exc
    rows: list[dict[str, object]] = []
    for child in sorted(children, key=lambda item: item.name):
        if child.name.startswith(".") or child.name.endswith(".tmp"):
            continue
        if child.parent != root:
            continue
        try:
            st = child.lstat()
        except OSError as exc:
            raise ResourceError("files_unreadable", "项目档案无法读取，未当作空列表", 409) from exc
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
            continue
        rows.append(_file_record(child.name, st))
        if len(rows) > FILE_LIST_LIMIT:
            raise ResourceError(
                "archive_list_exceeded",
                "项目档案超过列出上限，未返回不完整列表",
                409,
            )
    return rows


def _read_memory_entries(project: pdb.Project, home: Path) -> list[str]:
    """Real MEMORY.md entries only. Missing file is empty. Unreadable is an error, not []."""
    try:
        mem_dir = get_project_memory_dir(project.id, hermes_home=home)
    except ValueError as exc:
        raise ResourceError("memory_unreadable", str(exc), 409) from exc
    path = mem_dir / "MEMORY.md"
    try:
        if path.is_symlink():
            raise ResourceError("memory_unreadable", "项目记忆不是普通文件，未当作空列表", 409)
        if not path.exists():
            return []
        if not path.is_file():
            raise ResourceError("memory_unreadable", "项目记忆不是普通文件，未当作空列表", 409)
    except ResourceError:
        raise
    except OSError as exc:
        raise ResourceError("memory_unreadable", "项目记忆无法读取，未当作空列表", 409) from exc
    fd: Optional[int] = None
    try:
        fd = os.open(path, os.O_RDONLY | _nofollow_flag())
    except FileNotFoundError as exc:
        raise ResourceError("memory_unreadable", "项目记忆无法读取，未当作空列表", 409) from exc
    except OSError as exc:
        raise ResourceError("memory_unreadable", "项目记忆无法读取，未当作空列表", 409) from exc
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise ResourceError("memory_unreadable", "项目记忆不是普通文件，未当作空列表", 409)
        if st.st_size > MEMORY_READ_BYTE_CAP:
            raise ResourceError("memory_unreadable", "项目记忆文件过大，未当作空列表", 409)
        raw = bytearray()
        remaining = int(st.st_size)
        while remaining:
            chunk = os.read(fd, remaining)
            if not chunk:
                break
            raw.extend(chunk)
            remaining -= len(chunk)
        if remaining:
            raise ResourceError("memory_unreadable", "项目记忆无法读取，未当作空列表", 409)
        raw_bytes = bytes(raw)
    except ResourceError:
        raise
    except OSError as exc:
        raise ResourceError("memory_unreadable", "项目记忆无法读取，未当作空列表", 409) from exc
    finally:
        os.close(fd)
    try:
        text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ResourceError("memory_unreadable", "项目记忆无法读取，未当作空列表", 409) from exc
    from tools.memory_tool_store import MemoryStore

    return MemoryStore._parse_entries(text)


def _memory_items(entries: list[str]) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for entry in entries:
        content = str(entry)
        if not content.strip():
            continue
        items.append(
            {
                "id": memory_entry_id(content),
                "title": memory_title(content),
                "content": content,
                # MemoryStore entries have no per-entry clock. Do not borrow the file mtime.
                "updated_at": None,
            }
        )
    return items


def build_project_resources(project_id: str, home: Path) -> dict[str, object]:
    """GET payload. Any read failure raises; it does not become an empty 200."""
    project = _require_project(project_id, home)
    try:
        instructions = read_project_instructions(project.id, home)
    except ValueError as exc:
        raise ResourceError("instructions_unreadable", str(exc), 409) from exc
    except OSError as exc:
        raise ResourceError("instructions_unreadable", "项目指示无法读取，未当作空内容", 409) from exc
    return {
        "project_id": project.id,
        "archived": bool(project.archived),
        "instructions": instructions,
        "files": _list_files(project, home),
        "memories": _memory_items(_read_memory_entries(project, home)),
        "limits": {
            "file_bytes": FILE_BYTE_LIMIT,
            "instructions_chars": INSTRUCTION_CHAR_LIMIT,
        },
    }


def save_project_instructions(project_id: str, home: Path, content: str) -> dict[str, object]:
    project = _require_project(project_id, home)
    _require_writable(project)
    if not isinstance(content, str):
        raise ResourceError("invalid_instructions", "指示必须是文本，未写入", 400)
    try:
        saved = write_project_instructions(project.id, home, content)
    except ValueError as exc:
        message = str(exc)
        code = "invalid_instructions"
        if "长度上限" in message:
            code = "instructions_too_long"
        elif "安全检查" in message:
            code = "instructions_rejected"
        raise ResourceError(code, message, 400) from exc
    except OSError as exc:
        raise ResourceError("instructions_write_failed", "指示写入失败，未改全局记忆", 500) from exc
    return {"success": True, "project_id": project.id, "instructions": saved}


class ProjectFileWriter:
    """Stream one new workspace file. Same-name and symlink destinations are refused, not replaced."""

    def __init__(self, project_id: str, home: Path, name: str) -> None:
        self.project = _require_project(project_id, home)
        _require_writable(self.project)
        self.home = home
        self.name = _file_name(name)
        self.root = _workspace(self.project, home)
        self.dest = self.root / self.name
        if self.dest.parent != self.root or self.dest.name != self.name:
            raise ResourceError("invalid_name", "档案名不能是路径", 400)
        self._reject_destination()
        self._tmp = self.root / f"{_UPLOAD_PREFIX}{secrets.token_hex(8)}.tmp"
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | _nofollow_flag()
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        self._lock = threading.RLock()
        self._fd: Optional[int] = None
        self.size = 0
        self._finished = False
        self._aborted = False
        self._fd = os.open(self._tmp, flags, 0o600)

    def __enter__(self) -> "ProjectFileWriter":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        # Normal exit without finish still owns an fd and a temp name.
        self.abort()
        return False

    def _close_owned_fd(self) -> None:
        fd = self._fd
        if fd is None:
            return
        try:
            os.close(fd)
        except OSError:
            logger.exception("project file temporary descriptor close failed")
        finally:
            # Clear only after the close attempt. A failed fsync must not drop
            # the fd and then skip close.
            self._fd = None

    def _discard_tmp(self) -> None:
        tmp = getattr(self, "_tmp", None)
        dest = getattr(self, "dest", None)
        if tmp is None:
            return
        if dest is not None and (tmp == dest or tmp.name == dest.name):
            logger.error("refusing to unlink project destination during cleanup")
            return
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            return
        except OSError:
            logger.exception("project file temporary unlink failed")

    def _reject_destination(self) -> None:
        try:
            st = self.dest.lstat()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise ResourceError("file_unreadable", "无法确认同名档案，未写入", 409) from exc
        if stat.S_ISLNK(st.st_mode):
            raise ResourceError("symlink_rejected", "拒绝覆盖符号链接", 409)
        raise ResourceError("file_exists", "同名档案已存在，未覆盖", 409)

    def write(self, chunk: bytes) -> None:
        with self._lock:
            if self._finished or self._aborted or self._fd is None:
                raise ResourceError("invalid_file", "档案写入已经结束", 409)
            if not isinstance(chunk, (bytes, bytearray)):
                self.abort()
                raise ResourceError("invalid_file", "档案必须是原始字节，未写入", 400)
            if not chunk:
                return
            self.size += len(chunk)
            if self.size > FILE_BYTE_LIMIT:
                self.abort()
                raise ResourceError("file_too_large", "档案超过大小上限，未写入", 413)
            view = memoryview(bytes(chunk))
            while view:
                try:
                    wrote = os.write(self._fd, view)
                except OSError as exc:
                    self.abort()
                    raise ResourceError("file_write_failed", "档案写入失败", 500) from exc
                if wrote <= 0:
                    self.abort()
                    raise ResourceError("file_write_failed", "档案写入失败", 500)
                view = view[wrote:]

    def finish(self) -> dict[str, object]:
        with self._lock:
            if self._finished:
                raise ResourceError("invalid_file", "档案写入已经结束", 409)
            if self._aborted or self._fd is None:
                raise ResourceError("file_write_failed", "档案写入已中止", 500)
            fsync_error: Optional[BaseException] = None
            try:
                os.fsync(self._fd)
            except OSError as exc:
                fsync_error = exc
            self._close_owned_fd()
            if fsync_error is not None:
                self.abort()
                raise ResourceError("file_write_failed", "档案写入失败", 500) from fsync_error
            self._reject_destination()
            try:
                os.link(self._tmp, self.dest)
            except FileExistsError as exc:
                self.abort()
                raise ResourceError("file_exists", "同名档案已存在，未覆盖", 409) from exc
            except OSError as exc:
                self.abort()
                raise ResourceError("file_write_failed", "档案写入失败", 500) from exc
            self._finished = True
            self._discard_tmp()
            try:
                os.chmod(self.dest, 0o600)
                st = self.dest.lstat()
            except OSError as exc:
                raise ResourceError("file_write_failed", "档案写入后无法确认", 500) from exc
            if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
                raise ResourceError("symlink_rejected", "拒绝覆盖符号链接", 409)
            return _file_record(self.name, st)

    def abort(self) -> None:
        with self._lock:
            if self._finished:
                return
            if self._aborted:
                return
            self._aborted = True
            self._close_owned_fd()
            self._discard_tmp()


def open_project_file(project_id: str, home: Path, name: str) -> tuple[int, dict[str, str]]:
    """Return an unlinked-from-path fd and download headers. Caller closes the fd.

    The fd is opened with ``O_NOFOLLOW`` and then streamed by the caller. This
    does not read the file into one bytes object.
    """
    project = _require_project(project_id, home)
    filename = _file_name(name)
    root = _workspace(project, home)
    path = root / filename
    if path.parent != root or path.name != filename:
        raise ResourceError("invalid_name", "档案名不能是路径", 400)
    fd: Optional[int] = None
    try:
        try:
            fd = os.open(path, os.O_RDONLY | _nofollow_flag())
        except FileNotFoundError as exc:
            raise ResourceError("file_not_found", "档案不存在", 404) from exc
        except OSError as exc:
            if path.is_symlink():
                raise ResourceError("symlink_rejected", "拒绝读取符号链接", 400) from exc
            raise ResourceError("file_unreadable", "档案无法读取", 409) from exc
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise ResourceError("invalid_file", "档案不是普通文件", 400)
        try:
            current = path.lstat()
        except OSError as exc:
            raise ResourceError("file_unreadable", "档案无法读取", 409) from exc
        if stat.S_ISLNK(current.st_mode) or current.st_ino != st.st_ino or current.st_dev != st.st_dev:
            raise ResourceError("symlink_rejected", "拒绝读取符号链接", 400)
        headers = download_headers(filename, st.st_size)
        owned = fd
        fd = None
        return owned, headers
    finally:
        if fd is not None:
            os.close(fd)


def write_project_file_from_stream(
    project_id: str,
    home: Path,
    name: str,
    chunks: Iterator[bytes],
) -> dict[str, object]:
    """Sync helper for tests and non-HTTP callers. Stops at the byte cap without keeping the body."""
    writer = ProjectFileWriter(project_id, home, name)
    try:
        for chunk in chunks:
            writer.write(chunk)
        record = writer.finish()
    finally:
        writer.abort()
    return {"success": True, "project_id": writer.project.id, "file": record}


def copy_project_file_from_reader(
    project_id: str,
    home: Path,
    name: str,
    source: BinaryIO,
    *,
    chunk_size: int = 65536,
) -> dict[str, object]:
    def _chunks() -> Iterator[bytes]:
        while True:
            chunk = source.read(chunk_size)
            if not chunk:
                break
            if isinstance(chunk, str):
                raise ResourceError("invalid_file", "档案必须是原始字节，未写入", 400)
            yield chunk

    return write_project_file_from_stream(project_id, home, name, _chunks())
