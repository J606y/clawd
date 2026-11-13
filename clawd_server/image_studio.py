"""面板生图：只调用已核对过的模型，密钥不出响应。

对话模型选择不走这里。这里也不写 image_gen，避免改掉对话里的生图工具默认模型。
"""

from __future__ import annotations

import base64
import errno
import json
import os
import re
import sqlite3
import stat
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

PROMPT_MAX = 4000
HISTORY_LIMIT = 40
RESPONSE_LIMIT = 30 * 1024 * 1024
IMAGE_LIMIT = 20 * 1024 * 1024
GENERATE_TIMEOUT_S = 100
DOWNLOAD_TIMEOUT_S = 30
ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
ITEM_ID = re.compile(r"^img_[0-9a-f]{16}$")
DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DATA_URI = re.compile(r"data:image/(png|jpeg|jpg|webp|gif);base64,([A-Za-z0-9+/=]+)")

ASPECTS = {
    "square": {"openai_size": "1024x1024", "xai_ratio": "1:1"},
    "landscape": {"openai_size": "1536x1024", "xai_ratio": "16:9", "hint": "画面比例：16:9。"},
    "portrait": {"openai_size": "1024x1536", "xai_ratio": "9:16", "hint": "画面比例：9:16。"},
}

# 每个后端只留当前最强、且这次在 /v1/models 里实际出现的图像模型。
# Grok 的 quality / 初代已被 Imagine 2.0 接替，不进列表。
MODELS: tuple[dict[str, str], ...] = (
    {"id": "gpt-image-2.5-sunburst", "label": "GPT Image 2.5 Sunburst", "provider": "sub2api-codex", "providerName": "Codex", "protocol": "openai-images", "quality": "auto", "key_env": "SUB2API_CODEX_API_KEY"},
    {"id": "gemini-3.1-flash-image", "label": "Gemini 3.1 Flash Image", "provider": "sub2api-gemini", "providerName": "Gemini", "protocol": "gemini-chat", "quality": "", "key_env": "SUB2API_GEMINI_API_KEY"},
    {"id": "grok-imagine-image-2.0", "label": "Grok Imagine 2.0", "provider": "sub2api-grok", "providerName": "Grok", "protocol": "xai-images", "quality": "", "key_env": "SUB2API_GROK_API_KEY"},
)

_GEN_LOCK = threading.Lock()


class ImageStudioError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Runtime:
    config: dict[str, Any]
    secrets: dict[str, str]
    uploads: Path
    db_path: Path


def sanitize(text: str) -> str:
    cleaned = re.sub(r"Bearer\s+\S+", "Bearer [redacted]", text, flags=re.I)
    cleaned = re.sub(r"sk-[A-Za-z0-9_\-]{8,}", "[redacted]", cleaned)
    cleaned = re.sub(r"(?i)(api[_-]?key|token|secret)(['\"]?\s*[:=]\s*['\"]?)[^\s'\",}]+", r"\1\2[redacted]", cleaned)
    return " ".join(cleaned.split())[:300]


def _provider(config: Mapping[str, Any], provider_id: str) -> dict[str, Any]:
    providers = config.get("providers")
    if not isinstance(providers, dict):
        return {}
    block = providers.get(provider_id)
    return block if isinstance(block, dict) else {}


def _key_name(config: Mapping[str, Any], spec: Mapping[str, str]) -> str:
    name = _provider(config, spec["provider"]).get("key_env") or spec["key_env"]
    if not isinstance(name, str) or not ENV_NAME.fullmatch(name):
        raise ImageStudioError(503, "config_invalid", f"{spec['providerName']} 的密钥变量名不合法")
    return name


def _base_url(config: Mapping[str, Any], spec: Mapping[str, str]) -> str:
    raw = _provider(config, spec["provider"]).get("base_url")
    if not isinstance(raw, str) or not raw.strip():
        raise ImageStudioError(503, "config_invalid", f"{spec['providerName']} 没有配置接口地址")
    parts = urlsplit(raw.strip())
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise ImageStudioError(503, "config_invalid", f"{spec['providerName']} 的接口地址不可用")
    host = parts.hostname
    if ":" in host:
        host = f"[{host}]"
    port = f":{parts.port}" if parts.port else ""
    return f"https://{host}{port}{parts.path.rstrip('/')}"


def _secrets_from_lines(lines: list[str], wanted: set[str], environ: Mapping[str, str]) -> dict[str, str]:
    found: dict[str, str] = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key in wanted and key not in found:
            found[key] = value.strip().strip('"').strip("'")
    out: dict[str, str] = {}
    for name in wanted:
        live = environ.get(name, "").strip()
        if live:
            out[name] = live
        elif found.get(name, "").strip():
            out[name] = found[name].strip()
    return out


def read_named_secrets(path: Path, names: set[str], environ: Mapping[str, str]) -> dict[str, str]:
    wanted = {name for name in names if ENV_NAME.fullmatch(name)}
    lines: list[str] = []
    if path.is_file():
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            raise ImageStudioError(503, "env_unavailable", "生图密钥暂不可读") from exc
    return _secrets_from_lines(lines, wanted, environ)


def load_runtime(home: Path, uploads: Path, environ: Mapping[str, str] | None = None) -> Runtime:
    if yaml is None:
        raise ImageStudioError(503, "yaml_unavailable", "生图配置读取不可用")
    try:
        loaded = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8")) or {}
    except OSError as exc:
        raise ImageStudioError(503, "config_unavailable", "生图配置暂不可读") from exc
    except yaml.YAMLError as exc:
        raise ImageStudioError(503, "config_invalid", "生图配置无法解析") from exc
    if not isinstance(loaded, dict):
        raise ImageStudioError(503, "config_invalid", "生图配置格式异常")
    names = {_key_name(loaded, spec) for spec in MODELS}
    secrets = read_named_secrets(home / ".env", names, environ if environ is not None else os.environ)
    return Runtime(loaded, secrets, uploads, home / "panel_image_studio.db")


def load_runtime_texts(
    config_text: str,
    env_text: str,
    uploads: Path,
    db_path: Path,
    environ: Mapping[str, str] | None = None,
) -> Runtime:
    """配置和密钥文本已由调用方从可信 home fd 读出。这里不再按路径打开。"""
    if yaml is None:
        raise ImageStudioError(503, "yaml_unavailable", "生图配置读取不可用")
    try:
        loaded = yaml.safe_load(config_text) or {}
    except yaml.YAMLError as exc:
        raise ImageStudioError(503, "config_invalid", "生图配置无法解析") from exc
    if not isinstance(loaded, dict):
        raise ImageStudioError(503, "config_invalid", "生图配置格式异常")
    names = {_key_name(loaded, spec) for spec in MODELS}
    secrets = _secrets_from_lines(env_text.splitlines(), names, environ if environ is not None else os.environ)
    return Runtime(loaded, secrets, uploads, db_path)


def _spec(model_id: str) -> dict[str, str]:
    for spec in MODELS:
        if spec["id"] == model_id:
            return spec
    raise ImageStudioError(400, "unknown_model", "这个模型不在生图列表里")


def available_models(config: Mapping[str, Any], secrets: Mapping[str, str]) -> list[dict[str, str]]:
    out = []
    for spec in MODELS:
        try:
            name = _key_name(config, spec)
            _base_url(config, spec)
        except ImageStudioError:
            continue
        if not secrets.get(name, "").strip():
            continue
        out.append({
            "id": spec["id"],
            "label": spec["label"],
            "provider": spec["provider"],
            "providerName": spec["providerName"],
            "protocol": spec["protocol"],
        })
    return out


def build_request(spec: Mapping[str, str], prompt: str, aspect: str) -> tuple[str, dict[str, Any]]:
    if aspect not in ASPECTS:
        raise ImageStudioError(400, "invalid_aspect", "比例只能是方形、横向或纵向")
    chosen = ASPECTS[aspect]
    if spec["protocol"] == "openai-images":
        body: dict[str, Any] = {"model": spec["id"], "prompt": prompt, "n": 1, "size": chosen["openai_size"]}
        if spec.get("quality"):
            body["quality"] = spec["quality"]
        return "/images/generations", body
    if spec["protocol"] == "xai-images":
        return "/images/generations", {
            "model": spec["id"],
            "prompt": prompt,
            "n": 1,
            "aspect_ratio": chosen["xai_ratio"],
            "response_format": "b64_json",
        }
    if spec["protocol"] == "gemini-chat":
        text = prompt if "hint" not in chosen else f"{prompt}\n{chosen['hint']}"
        return "/chat/completions", {"model": spec["id"], "messages": [{"role": "user", "content": text}]}
    raise ImageStudioError(500, "unknown_protocol", "这个模型没有可用的调用方式")


def _b64_image(raw: str) -> tuple[bytes, str] | None:
    try:
        data = base64.b64decode(raw, validate=True)
    except (ValueError, TypeError):
        return None
    ext = sniff(data)
    if not ext or len(data) > IMAGE_LIMIT:
        return None
    return data, ext


def extract_image(protocol: str, payload: Any) -> tuple[bytes, str] | None:
    if protocol == "gemini-chat":
        content = ""
        if isinstance(payload, dict):
            choices = payload.get("choices")
            if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                message = choices[0].get("message")
                if isinstance(message, dict):
                    content = message.get("content")
        return _from_content(content)
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        return None
    encoded = data[0].get("b64_json")
    if isinstance(encoded, str):
        return _b64_image(encoded)
    return None


def _from_content(content: Any) -> tuple[bytes, str] | None:
    if isinstance(content, str):
        match = DATA_URI.search(content)
        return _b64_image(match.group(2)) if match else None
    if isinstance(content, list):
        for part in content:
            if not isinstance(part, dict):
                continue
            if isinstance(part.get("text"), str):
                found = _from_content(part["text"])
                if found:
                    return found
            url = part.get("image_url")
            if isinstance(url, dict) and isinstance(url.get("url"), str):
                found = _from_content(url["url"])
                if found:
                    return found
            if isinstance(part.get("url"), str):
                found = _from_content(part["url"])
                if found:
                    return found
    return None


def sniff(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "webp"
    return None


def image_url(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    data = payload.get("data")
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        return ""
    url = data[0].get("url")
    if not isinstance(url, str):
        return ""
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        return ""
    return url


def _studio(uploads: Path) -> Path:
    return uploads / "image-studio"


_STUDIO_FILE = re.compile(r"^image-studio/\d{4}-\d{2}-\d{2}/img_[0-9a-f]{16}\.(?:png|jpg|gif|webp)$")


def _nofollow() -> int:
    flag = getattr(os, "O_NOFOLLOW", 0)
    if not flag:
        raise ImageStudioError(500, "nofollow_unavailable", "当前系统没有 O_NOFOLLOW，拒绝写入生成结果")
    return flag


def _close(fd: int | None) -> None:
    if fd is None or fd < 0:
        return
    try:
        os.close(fd)
    except OSError:
        pass


def _open_dir_at(parent: int, name: str, *, create: bool) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | _nofollow() | os.O_CLOEXEC
    try:
        return os.open(name, flags, dir_fd=parent)
    except OSError as exc:
        if not create or exc.errno != errno.ENOENT:
            raise ImageStudioError(500, "invalid_output", "生成结果无法保存") from exc
    try:
        os.mkdir(name, 0o700, dir_fd=parent)
    except FileExistsError:
        pass
    except OSError as exc:
        raise ImageStudioError(500, "invalid_output", "生成结果无法保存") from exc
    try:
        return os.open(name, flags, dir_fd=parent)
    except OSError as exc:
        raise ImageStudioError(500, "invalid_output", "生成结果无法保存") from exc


def _unlink_relative(root_fd: int, relative: str) -> None:
    parts = relative.split("/")
    if not _STUDIO_FILE.fullmatch(relative) or any(part in {"", ".", ".."} for part in parts):
        raise ImageStudioError(403, "outside_studio", "不能删除这个文件")
    owned = [os.dup(root_fd)]
    try:
        current = owned[-1]
        for part in parts[:-1]:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | _nofollow() | os.O_CLOEXEC, dir_fd=current)
            owned.append(nxt)
            current = nxt
        try:
            os.unlink(parts[-1], dir_fd=current)
        except FileNotFoundError:
            return
    except OSError as exc:
        if exc.errno == errno.ENOENT:
            return
        raise ImageStudioError(403, "outside_studio", "不能删除这个文件") from exc
    finally:
        for fd in reversed(owned):
            _close(fd)


def _save_image_fd(root_fd: int, image_id: str, data: bytes, ext: str, day: str) -> str:
    if not ITEM_ID.fullmatch(image_id) or ext not in {"png", "jpg", "gif", "webp"} or not DAY.fullmatch(day):
        raise ImageStudioError(500, "invalid_output", "生成结果无法保存")
    if sniff(data) != ext or len(data) > IMAGE_LIMIT:
        raise ImageStudioError(502, "invalid_image", "上游返回的不是可用图片")
    relative = f"image-studio/{day}/{image_id}.{ext}"
    temp = f".{image_id}.{ext}.tmp"
    final = f"{image_id}.{ext}"
    studio_fd = day_fd = file_fd = None
    committed = False
    try:
        studio_fd = _open_dir_at(root_fd, "image-studio", create=True)
        day_fd = _open_dir_at(studio_fd, day, create=True)
        file_fd = os.open(
            temp,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | _nofollow() | os.O_CLOEXEC,
            0o600,
            dir_fd=day_fd,
        )
        view = memoryview(data)
        while view:
            written = os.write(file_fd, view)
            if written <= 0:
                raise OSError("short write")
            view = view[written:]
        os.fsync(file_fd)
        os.close(file_fd)
        file_fd = None
        try:
            os.link(temp, final, src_dir_fd=day_fd, dst_dir_fd=day_fd, follow_symlinks=False)
        except FileExistsError as exc:
            raise ImageStudioError(500, "invalid_output", "生成结果无法保存") from exc
        committed = True
        info = os.lstat(final, dir_fd=day_fd)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise ImageStudioError(500, "invalid_output", "生成结果无法保存")
        check = os.open(final, os.O_RDONLY | _nofollow() | os.O_CLOEXEC, dir_fd=day_fd)
        try:
            header = os.read(check, 16)
        finally:
            os.close(check)
        if sniff(header) != ext:
            raise ImageStudioError(500, "invalid_output", "生成结果无法保存")
        return relative
    except ImageStudioError:
        if committed and day_fd is not None:
            try:
                os.unlink(final, dir_fd=day_fd)
            except OSError:
                pass
        raise
    except OSError as exc:
        if committed and day_fd is not None:
            try:
                os.unlink(final, dir_fd=day_fd)
            except OSError:
                pass
        raise ImageStudioError(500, "invalid_output", "生成结果无法保存") from exc
    finally:
        if day_fd is not None:
            try:
                os.unlink(temp, dir_fd=day_fd)
            except OSError:
                pass
        _close(file_fd)
        _close(day_fd)
        _close(studio_fd)


def save_image(
    uploads: Path,
    image_id: str,
    data: bytes,
    ext: str,
    day: str,
    *,
    root_fd: int | None = None,
    publish: Callable[[str], str] | None = None,
) -> Path:
    if root_fd is not None:
        if publish is None:
            raise ImageStudioError(500, "invalid_output", "生成结果无法保存")
        relative = _save_image_fd(root_fd, image_id, data, ext, day)
        try:
            text = publish(relative)
        except Exception:
            try:
                _unlink_relative(root_fd, relative)
            except Exception:
                pass
            raise
        if not isinstance(text, str) or not text.startswith("/") or text.startswith("//") or ".." in Path(text).parts:
            try:
                _unlink_relative(root_fd, relative)
            except Exception:
                pass
            raise ImageStudioError(500, "invalid_output", "生成结果无法保存")
        return Path(text)
    if not ITEM_ID.fullmatch(image_id) or ext not in {"png", "jpg", "gif", "webp"} or not DAY.fullmatch(day):
        raise ImageStudioError(500, "invalid_output", "生成结果无法保存")
    if sniff(data) != ext or len(data) > IMAGE_LIMIT:
        raise ImageStudioError(502, "invalid_image", "上游返回的不是可用图片")
    folder = _studio(uploads) / day
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    dest = folder / f"{image_id}.{ext}"
    tmp = folder / f".{image_id}.{ext}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("short write")
            view = view[written:]
        os.fsync(fd)
    except OSError:
        os.close(fd)
        tmp.unlink(missing_ok=True)
        raise
    else:
        os.close(fd)
    os.replace(tmp, dest)
    with dest.open("rb") as handle:
        header = handle.read(16)
    if dest.is_symlink() or sniff(header) != ext:
        dest.unlink(missing_ok=True)
        raise ImageStudioError(500, "invalid_output", "生成结果无法保存")
    return dest


def _rel_under_studio(uploads: Path, abs_path: str) -> Path:
    if (not abs_path.startswith("/") or abs_path.startswith("//") or "\\" in abs_path
            or any(ord(c) < 32 or ord(c) == 127 for c in abs_path)):
        raise ImageStudioError(400, "invalid_path", "不能删除这个文件")
    path = Path(abs_path)
    try:
        rel = path.relative_to(_studio(uploads))
    except ValueError as exc:
        raise ImageStudioError(403, "outside_studio", "不能删除这个文件") from exc
    if not rel.parts or any(part in ("", ".", "..") or part.startswith(".") for part in rel.parts):
        raise ImageStudioError(403, "outside_studio", "不能删除这个文件")
    return rel


def _studio_rel(abs_path: str, prefixes: list[str]) -> str:
    if (not abs_path.startswith("/") or abs_path.startswith("//") or "\\" in abs_path
            or any(ord(c) < 32 or ord(c) == 127 for c in abs_path)):
        raise ImageStudioError(400, "invalid_path", "不能删除这个文件")
    for prefix in prefixes:
        base = prefix if prefix.endswith("/") else prefix + "/"
        if not base.startswith("/") or ".." in Path(base).parts:
            continue
        if abs_path.startswith(base) and _STUDIO_FILE.fullmatch(abs_path[len(base):]):
            return abs_path[len(base):]
    raise ImageStudioError(403, "outside_studio", "不能删除这个文件")


def unlink_studio_file(
    uploads: Path,
    abs_path: str,
    *,
    root_fd: int | None = None,
    prefixes: list[str] | None = None,
) -> None:
    if root_fd is not None:
        if not prefixes:
            raise ImageStudioError(403, "outside_studio", "不能删除这个文件")
        _unlink_relative(root_fd, _studio_rel(abs_path, prefixes))
        return
    rel = _rel_under_studio(uploads, abs_path)
    studio = _studio(uploads)
    if not studio.is_dir():
        return
    dir_fd = os.open(studio, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in rel.parts[:-1]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dir_fd)
            os.close(dir_fd)
            dir_fd = next_fd
        try:
            os.unlink(rel.parts[-1], dir_fd=dir_fd)
        except FileNotFoundError:
            return
    finally:
        os.close(dir_fd)


def _store(db_path: Path, *, parent_fd: int | None = None):
    if parent_fd is None:
        db_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            os.close(os.open(db_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
        except FileExistsError:
            pass
    else:
        flag = _nofollow()
        try:
            fd = os.open(db_path.name, os.O_RDWR | os.O_CREAT | flag | os.O_CLOEXEC, 0o600, dir_fd=parent_fd)
        except OSError as exc:
            raise ImageStudioError(503, "history_unavailable", "生图记录暂不可用") from exc
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise ImageStudioError(403, "unsafe_file", "生图记录路径不可用")
            try:
                st = os.stat(db_path, follow_symlinks=False)
            except OSError as exc:
                raise ImageStudioError(503, "history_unavailable", "生图记录暂不可用") from exc
            if stat.S_ISLNK(st.st_mode) or (st.st_dev, st.st_ino) != (info.st_dev, info.st_ino):
                raise ImageStudioError(403, "unsafe_file", "生图记录路径不可用")
        finally:
            os.close(fd)
    conn = sqlite3.connect(db_path, timeout=3)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("BEGIN IMMEDIATE")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS generations (
            id TEXT PRIMARY KEY,
            created_at INTEGER NOT NULL,
            model TEXT NOT NULL,
            provider TEXT NOT NULL,
            prompt TEXT NOT NULL,
            aspect TEXT NOT NULL,
            abs_path TEXT NOT NULL DEFAULT '',
            bytes INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL,
            error TEXT NOT NULL DEFAULT ''
        )"""
    )
    return conn


def _row(row: sqlite3.Row) -> dict[str, Any]:
    spec = next((item for item in MODELS if item["id"] == row["model"]), None)
    return {
        "id": row["id"],
        "createdAt": row["created_at"],
        "model": row["model"],
        "provider": row["provider"],
        "providerName": spec["providerName"] if spec else "",
        "label": spec["label"] if spec else row["model"],
        "prompt": row["prompt"],
        "aspect": row["aspect"],
        "absPath": row["abs_path"],
        "bytes": row["bytes"],
        "status": row["status"],
        "error": row["error"],
    }


def list_history(db_path: Path, limit: int = HISTORY_LIMIT, *, parent_fd: int | None = None) -> list[dict[str, Any]]:
    if parent_fd is None and not db_path.exists():
        return []
    try:
        conn = _store(db_path, parent_fd=parent_fd)
    except (sqlite3.Error, OSError) as exc:
        raise ImageStudioError(503, "history_unavailable", "生图记录暂不可读") from exc
    try:
        rows = conn.execute(
            "SELECT * FROM generations ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (max(1, min(limit, HISTORY_LIMIT)),),
        ).fetchall()
        conn.commit()
        return [_row(row) for row in rows]
    finally:
        conn.close()


def _insert(db_path: Path, item: dict[str, Any], *, parent_fd: int | None = None) -> None:
    conn = _store(db_path, parent_fd=parent_fd)
    try:
        conn.execute(
            """INSERT INTO generations (id, created_at, model, provider, prompt, aspect, abs_path, bytes, status, error)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (item["id"], item["createdAt"], item["model"], item["provider"], item["prompt"], item["aspect"],
             item["absPath"], item["bytes"], item["status"], item["error"]),
        )
        conn.commit()
    finally:
        conn.close()


def delete_history(
    db_path: Path,
    uploads: Path,
    item_id: str,
    *,
    root_fd: int | None = None,
    prefixes: list[str] | None = None,
    parent_fd: int | None = None,
) -> dict[str, bool]:
    if not ITEM_ID.fullmatch(item_id):
        raise ImageStudioError(400, "invalid_id", "没有这条生图记录")
    try:
        conn = _store(db_path, parent_fd=parent_fd)
    except (sqlite3.Error, OSError) as exc:
        raise ImageStudioError(503, "history_unavailable", "生图记录暂不可用") from exc
    try:
        row = conn.execute("SELECT abs_path FROM generations WHERE id = ?", (item_id,)).fetchone()
        if row is None:
            raise ImageStudioError(404, "not_found", "没有这条生图记录")
        path = row["abs_path"]
        if path:
            unlink_studio_file(uploads, path, root_fd=root_fd, prefixes=prefixes)
        conn.execute("DELETE FROM generations WHERE id = ?", (item_id,))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


def _prompt(value: Any) -> str:
    if not isinstance(value, str):
        raise ImageStudioError(400, "invalid_prompt", "请写下要生成的画面")
    text = value.strip()
    if not text or any(ord(c) < 32 and c not in "\n\t" for c in text):
        raise ImageStudioError(400, "invalid_prompt", "请写下要生成的画面")
    if len(text) > PROMPT_MAX:
        raise ImageStudioError(400, "prompt_too_long", f"提示词不能超过 {PROMPT_MAX} 字")
    return text


def _aspect(value: Any) -> str:
    if value not in ASPECTS:
        raise ImageStudioError(400, "invalid_aspect", "比例只能是方形、横向或纵向")
    return str(value)


class UrllibTransport:
    def post_json(self, url: str, headers: Mapping[str, str], payload: Mapping[str, Any], timeout: float) -> Any:
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=dict(headers), method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(_read_capped(resp, RESPONSE_LIMIT).decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = sanitize(exc.read(800).decode("utf-8", "replace"))
            raise _http_error(exc.code, detail) from exc
        except TimeoutError as exc:
            raise ImageStudioError(504, "generation_timeout", "生成超时，请稍后重试") from exc
        except OSError as exc:
            raise ImageStudioError(502, "upstream_failed", "生图上游不可达") from exc
        except json.JSONDecodeError as exc:
            raise ImageStudioError(502, "upstream_invalid", "上游返回了无法解析的结果") from exc

    def get_bytes(self, url: str, timeout: float, limit: int) -> bytes:
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.hostname:
            raise ImageStudioError(502, "invalid_image", "上游没有返回可用图片")
        req = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return _read_capped(resp, limit)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ImageStudioError(502, "invalid_image", "上游图片下载失败") from exc


def _read_capped(resp: Any, limit: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = resp.read(65536)
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > limit:
            raise ImageStudioError(502, "upstream_too_large", "上游返回过大，已中止")
        chunks.append(chunk)


def _http_error(status: int, detail: str) -> ImageStudioError:
    detail = sanitize(detail)
    if status in (401, 403):
        return ImageStudioError(502, "credential_rejected", "这个模型的密钥被上游拒绝")
    if status == 404:
        return ImageStudioError(404, "model_unavailable", "当前分组不支持这个模型")
    if status in (429, 503):
        return ImageStudioError(503, "upstream_unavailable", "生图上游暂时不可用，请稍后重试")
    if status == 400:
        return ImageStudioError(400, "upstream_rejected", detail or "上游拒绝了这次请求")
    return ImageStudioError(502, "upstream_failed", detail or "生图失败")


def _public_item(spec: Mapping[str, str], item_id: str, created: int, prompt: str, aspect: str, path: str, size: int, status: str, error: str) -> dict[str, Any]:
    return {
        "id": item_id,
        "createdAt": created,
        "model": spec["id"],
        "provider": spec["provider"],
        "providerName": spec["providerName"],
        "label": spec["label"],
        "prompt": prompt,
        "aspect": aspect,
        "absPath": path,
        "bytes": size,
        "status": status,
        "error": error,
    }


def generate(
    body: Mapping[str, Any],
    runtime: Runtime,
    *,
    transport: Any | None = None,
    now: Callable[[], int] | None = None,
    new_id: Callable[[], str] | None = None,
    day: str | None = None,
    root_fd: int | None = None,
    publish: Callable[[str], str] | None = None,
    parent_fd: int | None = None,
) -> dict[str, Any]:
    prompt = _prompt(body.get("prompt"))
    aspect = _aspect(body.get("aspect") or "square")
    model = body.get("model")
    if not isinstance(model, str):
        raise ImageStudioError(400, "unknown_model", "请选择生图模型")
    spec = _spec(model)
    if not any(item["id"] == spec["id"] for item in available_models(runtime.config, runtime.secrets)):
        raise ImageStudioError(503, "model_unconfigured", "这个模型当前没有可用密钥")
    if not _GEN_LOCK.acquire(blocking=False):
        raise ImageStudioError(429, "busy", "已有一张图在生成，请等它完成")
    created = int(now() if now else time.time())
    item_id = new_id() if new_id else f"img_{os.urandom(8).hex()}"
    client = transport or UrllibTransport()
    try:
        path, payload = build_request(spec, prompt, aspect)
        key_name = _key_name(runtime.config, spec)
        url = _base_url(runtime.config, spec) + path
        headers = {"Authorization": f"Bearer {runtime.secrets[key_name]}", "Content-Type": "application/json"}
        try:
            upstream = client.post_json(url, headers, payload, GENERATE_TIMEOUT_S)
            found = extract_image(spec["protocol"], upstream)
            if found is None:
                remote = image_url(upstream)
                if not remote:
                    raise ImageStudioError(502, "invalid_image", "上游没有返回图片")
                raw = client.get_bytes(remote, DOWNLOAD_TIMEOUT_S, IMAGE_LIMIT)
                ext = sniff(raw)
                if not ext:
                    raise ImageStudioError(502, "invalid_image", "上游没有返回可用图片")
                found = (raw, ext)
            data, ext = found
            saved = save_image(
                runtime.uploads,
                item_id,
                data,
                ext,
                day or time.strftime("%Y-%m-%d", time.localtime(created)),
                root_fd=root_fd,
                publish=publish,
            )
        except ImageStudioError as exc:
            failed = _public_item(spec, item_id, created, prompt, aspect, "", 0, "error", exc.message)
            try:
                _insert(runtime.db_path, failed, parent_fd=parent_fd)
            except (sqlite3.Error, OSError):
                pass
            raise
        item = _public_item(spec, item_id, created, prompt, aspect, str(saved), len(data), "success", "")
        try:
            _insert(runtime.db_path, item, parent_fd=parent_fd)
            history_saved = True
        except (sqlite3.Error, OSError):
            history_saved = False
        return {"item": item, "historySaved": history_saved}
    finally:
        _GEN_LOCK.release()
