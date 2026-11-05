"""Path and report gates for offline cutover preparation.

No production home is a default. Banned paths are rejected on the lexical
form before any directory listing. This module does not remove directories.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path


class GuardError(Exception):
    def __init__(self, message: str, *, exit_code: int = 2) -> None:
        super().__init__(message)
        self.exit_code = exit_code


_TOO_BROAD = frozenset({
    "/",
    "/root",
    "/usr",
    "/usr/local",
    "/var",
    "/etc",
    "/home",
    "/opt",
    "/tmp",
    "/run",
    "/lib",
    "/bin",
    "/sbin",
})

_SECRET_VALUE = re.compile(
    r"(?i)(api[_-]?key|secret|token|password|bearer)\s*[:=]\s*\S+"
)
_SECRET_TOKEN = re.compile(r"sk-[A-Za-z0-9]{8,}")
_PEM = "-----BEGIN "
_VERSION_TEXT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")
_PROFILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")

CONFIRM = {
    "backup": "BACKUP",
    "restore": "RESTORE",
    "apply": "APPLY",
    "rollback": "ROLLBACK",
    "render": "RENDER_TEMPLATES",
    "copy_credentials": "COPY_CREDENTIALS",
}


def production_home_texts() -> tuple[str, ...]:
    """Lexical production homes. Does not list either directory."""
    return ("/root/.hermes", str(Path.home() / ".hermes"))


def banned_roots() -> tuple[str, ...]:
    homes = production_home_texts()
    return homes + (
        "/usr/local/lib/hermes-agent",
        "/usr/local/bin/hermes",
        "/etc",
        "/lib/systemd",
        "/run/systemd",
        "/usr/lib/systemd",
        "/var/www/hermes-panel",
        "/root/.ssh",
        "/root/hermes-workspace/hermes-panel",
    )


def normalize_lexical(raw: str) -> str:
    if raw is None or not str(raw).strip():
        raise GuardError("路径必须显式给出，不能为空")
    text = str(raw).strip()
    if text.startswith("~"):
        raise GuardError("拒绝波浪号展开；请传绝对路径")
    if not text.startswith("/"):
        raise GuardError(f"路径必须是绝对路径: {text}")
    parts: list[str] = []
    for part in text.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise GuardError(f"路径不得包含 '..': {text}")
        parts.append(part)
    return "/" + "/".join(parts)


def ban_reason(text: str) -> str | None:
    norm = normalize_lexical(text)
    for root in banned_roots():
        root_norm = normalize_lexical(root)
        if norm == root_norm or norm.startswith(root_norm + "/"):
            return root_norm
    return None


def assert_not_banned(text: str, *, kind: str) -> str:
    norm = normalize_lexical(text)
    reason = ban_reason(norm)
    if reason:
        raise GuardError(f"拒绝受保护路径 ({kind}): {norm}")
    return norm


def assert_allow_root(text: str) -> str:
    norm = assert_not_banned(text, kind="allow-root")
    if norm in _TOO_BROAD:
        raise GuardError(f"allow-root 过宽: {norm}")
    return norm


def under_any(path: str, roots: list[str]) -> bool:
    norm = normalize_lexical(path)
    for root in roots:
        base = normalize_lexical(root)
        if norm == base or norm.startswith(base + "/"):
            return True
    return False


def assert_under(path: str, roots: list[str], *, kind: str) -> str:
    norm = assert_not_banned(path, kind=kind)
    if not roots:
        raise GuardError("必须显式传入至少一个 --allow-root")
    if not under_any(norm, roots):
        raise GuardError(f"路径超出 allow-root ({kind}): {norm}")
    return norm


def resolve_without_listing_banned(text: str, *, hops: int = 0) -> str:
    """Resolve symlinks without listing a banned directory.

    A banned lexical path is rejected before stat. Symlink targets are
    readlink'd and re-checked; '..' in a link target is rejected.
    """
    if hops > 8:
        raise GuardError("符号链接层数过多")
    norm = assert_not_banned(text, kind="resolve")
    current = "/"
    parts = [part for part in norm.split("/") if part]
    index = 0
    while index < len(parts):
        part = parts[index]
        nxt = normalize_lexical(current + "/" + part)
        if ban_reason(nxt):
            raise GuardError(f"拒绝受保护路径: {nxt}")
        path = Path(nxt)
        try:
            is_link = path.is_symlink()
        except OSError as exc:
            raise GuardError(f"无法检查路径 {nxt}: {exc.strerror}") from exc
        if is_link:
            raw_target = os.readlink(path)
            target = Path(raw_target)
            if not target.is_absolute():
                target = Path(current) / target
            rest = parts[index + 1 :]
            combined = str(target)
            if rest:
                combined = str(target.joinpath(*rest))
            return resolve_without_listing_banned(combined, hops=hops + 1)
        if path.exists():
            current = nxt
            index += 1
            continue
        missing = normalize_lexical("/" + "/".join(parts[index:]))
        if current != "/":
            missing = normalize_lexical(current + "/" + "/".join(parts[index:]))
        if ban_reason(missing):
            raise GuardError(f"拒绝受保护路径: {missing}")
        return missing
    return current if current != "" else "/"


def checked_path(raw: str, roots: list[str], *, kind: str) -> Path:
    lexical = assert_under(raw, roots, kind=kind)
    resolved = resolve_without_listing_banned(lexical)
    if not under_any(resolved, [resolve_without_listing_banned(root) for root in roots]):
        raise GuardError(f"解析后的路径超出 allow-root ({kind}): {resolved}")
    if ban_reason(resolved):
        raise GuardError(f"解析后落入受保护路径 ({kind}): {resolved}")
    return Path(resolved)


def checked_allow_roots(raw_roots: list[str]) -> list[str]:
    if not raw_roots:
        raise GuardError("必须显式传入至少一个 --allow-root")
    checked: list[str] = []
    for raw in raw_roots:
        lexical = assert_allow_root(raw)
        resolved = resolve_without_listing_banned(lexical)
        if ban_reason(resolved) or normalize_lexical(resolved) in _TOO_BROAD:
            raise GuardError(f"allow-root 解析后不可用: {resolved}")
        path = Path(resolved)
        if path.is_symlink():
            raise GuardError("allow-root 不能是符号链接")
        if not path.is_dir():
            raise GuardError("allow-root 必须是已存在的目录，本工具不创建它")
        checked.append(resolved)
    return checked


def distinct_trees(*labeled: tuple[str, str]) -> None:
    seen: dict[str, str] = {}
    for kind, text in labeled:
        if not text:
            continue
        norm = normalize_lexical(text)
        other = seen.get(norm)
        if other:
            raise GuardError(f"{kind} 与 {other} 相同，拒绝")
        for previous, previous_kind in seen.items():
            if norm.startswith(previous + "/") or previous.startswith(norm + "/"):
                raise GuardError(f"{kind} 与 {previous_kind} 嵌套，拒绝")
        seen[norm] = kind


def assert_version_text(value: str | None, *, kind: str) -> str | None:
    if value is None or value == "":
        return None
    if not _VERSION_TEXT.fullmatch(value):
        raise GuardError(f"{kind} 含非法字符或过长")
    return value


def assert_profile(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    if not _PROFILE_NAME.fullmatch(value):
        raise GuardError("profile 必须显式且只含字母数字及 ._- ，不接受通配")
    return value


def assert_report_clean(payload: object) -> None:
    try:
        blob = json.dumps(payload, ensure_ascii=False)
    except TypeError as exc:
        raise GuardError("报告无法序列化") from exc
    if _SECRET_VALUE.search(blob) or _SECRET_TOKEN.search(blob) or _PEM in blob:
        raise GuardError("拒绝写出含秘密值的报告")
    for text in _strings(payload):
        if len(text) > 4096:
            raise GuardError("拒绝写出过长字符串，疑似文件内容")


def _strings(value: object) -> list[str]:
    found: list[str] = []
    if isinstance(value, str):
        found.append(value)
    elif isinstance(value, dict):
        for key, item in value.items():
            found.extend(_strings(key))
            found.extend(_strings(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_strings(item))
    return found


def confirm_has(given: list[str], name: str) -> bool:
    expected = CONFIRM[name]
    return any(item == expected for item in given)


def parent_exists(path: Path) -> None:
    parent = path.parent
    if parent.is_symlink() or not parent.is_dir():
        raise GuardError(f"父目录必须已存在且不是符号链接: {parent}")
