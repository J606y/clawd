"""Profile list, show, create, and sticky select.

use_profile writes the sticky active_profile file only. ``active`` in the list
is the running process profile, not that sticky file. delete_profile(name)
does not delete: the existing route cannot carry a path preview plus an
explicit confirm. preview_delete_profile and confirm_delete_profile are the
new interface; confirm never calls the core delete that rewrites os.environ.
"""

from __future__ import annotations

import errno
import hashlib
import hmac
import json
import logging
import os
import secrets
import stat
import threading
import time
from pathlib import Path
from typing import Any

from clawd_server.admin_common import PROFILE_NAME_RE, fail, redact_text, run, write_lock

log = logging.getLogger("clawd.admin")


def _running_name() -> str:
    from hermes_cli.profiles import get_active_profile_name

    return get_active_profile_name()


def _sticky_name() -> str:
    from hermes_cli.profiles import get_active_profile

    return get_active_profile()


def _row(profile: Any) -> dict[str, Any]:
    sticky = _sticky_name()
    running = _running_name()
    alias = None
    if profile.alias_path and not profile.is_default:
        alias = profile.alias_name or profile.name
    gateway = "running" if profile.gateway_running else "stopped"
    return {
        "name": profile.name,
        "active": profile.name == running,
        "sticky": profile.name == sticky,
        "model": profile.model,
        "gateway": gateway,
        "alias": alias if alias and alias != "—" else None,
    }


def list_profiles(mod: Any) -> dict[str, Any]:
    def _read() -> dict[str, Any]:
        from hermes_cli.profiles import list_profiles as core_list

        from clawd_server.admin_common import panel_home

        panel_home(mod)
        try:
            profiles = core_list(lazy_skill_count=True)
        except Exception as exc:
            fail(503, "profiles_unavailable", "读取配置档失败，不以空列表冒充没有配置档")
            raise AssertionError(exc)
        return {
            "data": [_row(profile) for profile in profiles],
            "raw": None,
            "runningProfile": _running_name(),
            "stickyProfile": _sticky_name(),
            "activeMeans": "running-process",
        }

    return run(mod, _read)


def show_profile(mod: Any, name: Any) -> dict[str, Any]:
    def _read() -> dict[str, Any]:
        if not isinstance(name, str) or not PROFILE_NAME_RE.match(name):
            fail(400, "invalid_name", "配置档名称不合法")
        from hermes_cli.profiles import (
            _check_gateway_running,
            _count_skills,
            _read_config_model,
            _read_distribution_meta,
            _served_by_running_multiplexer,
            find_alias_for_profile,
            get_profile_dir,
            profile_exists,
            read_profile_meta,
        )

        from clawd_server.admin_common import panel_home

        panel_home(mod)
        if not profile_exists(name):
            fail(404, "profile_not_found", "配置档不存在")
        directory = get_profile_dir(name)
        model, provider = _read_config_model(directory)
        running = _check_gateway_running(directory) or _served_by_running_multiplexer(name)
        skills = _count_skills(directory)
        dist_name, dist_version, dist_source = _read_distribution_meta(directory)
        alias = find_alias_for_profile(name)
        meta = read_profile_meta(directory)
        lines = [
            f"Profile: {name}",
            f"Path: {directory}",
            f"Model: {model or '—'}" + (f" ({provider})" if provider else ""),
            f"Gateway: {'running' if running else 'stopped'}",
            f"Skills: {skills}",
            f".env: {'exists' if (directory / '.env').exists() else 'not configured'}",
            f"SOUL.md: {'exists' if (directory / 'SOUL.md').exists() else 'not configured'}",
            f"Sticky: {_sticky_name()}",
            f"Process: {_running_name()}",
        ]
        if dist_name:
            lines.append(f"Distribution: {dist_name}@{dist_version or '?'}")
        if dist_source:
            lines.append(f"Installed from: {dist_source}")
        description = meta.get("description") if isinstance(meta, dict) else ""
        if description:
            lines.append(f"Description: {description}")
        if alias:
            lines.append(f"Alias: {alias}")
        text = redact_text(mod, "\n".join(lines), 2000) or ""
        return {"name": name, "text": text}

    return run(mod, _read)


def create_profile(mod: Any, name: Any, clone_from: Any, description: Any) -> dict[str, Any]:
    def _write() -> dict[str, Any]:
        if not isinstance(name, str) or not PROFILE_NAME_RE.match(name) or name == "default":
            fail(400, "invalid_name", "名称需为小写字母、数字、下划线或连字符（最长 32），且不能是 default")
        if clone_from is not None and (not isinstance(clone_from, str) or not PROFILE_NAME_RE.match(clone_from)):
            fail(400, "invalid_clone", "克隆来源不合法")
        if description:
            if not isinstance(description, str) or len(description) > 200:
                fail(400, "invalid_description", "描述需为 200 字以内的文本")
        from hermes_cli.profiles import create_profile as core_create
        from hermes_cli.profiles import profile_exists

        from clawd_server.admin_common import panel_home

        panel_home(mod)
        with write_lock(mod):
            if profile_exists(name):
                fail(409, "profile_exists", "同名配置档已存在")
            try:
                core_create(
                    name=name,
                    clone_from=clone_from,
                    no_alias=True,
                    description=description or None,
                    clone_channels=False,
                )
            except FileExistsError as exc:
                fail(409, "profile_exists", "同名配置档已存在")
                raise AssertionError(exc)
            except FileNotFoundError as exc:
                fail(404, "profile_not_found", "克隆来源不存在")
                raise AssertionError(exc)
            except ValueError as exc:
                fail(400, "profile_create_rejected", "核心拒绝了这个配置档名称或克隆方式")
                raise AssertionError(exc)
            except Exception as exc:
                if profile_exists(name):
                    fail(500, "profile_create_unverified", "目录已出现但创建收尾失败，请刷新确认")
                fail(500, "profile_create_failed", "创建配置档失败")
                raise AssertionError(exc)
            if not profile_exists(name):
                fail(500, "profile_create_unverified", "已提交但列表里没有新配置档，请刷新确认")
        return {"ok": True, "name": name, "note": "未创建 shell 别名，未复制消息频道凭证"}

    return run(mod, _write)


def use_profile(mod: Any, name: Any) -> dict[str, Any]:
    def _write() -> dict[str, Any]:
        if not isinstance(name, str) or not PROFILE_NAME_RE.match(name):
            fail(400, "invalid_name", "配置档名称不合法")
        from hermes_cli.profiles import get_active_profile, profile_exists, set_active_profile

        from clawd_server.admin_common import panel_home

        panel_home(mod)
        with write_lock(mod):
            if not profile_exists(name):
                fail(404, "profile_not_found", "配置档不存在")
            before = _running_name()
            set_active_profile(name)
            sticky = get_active_profile()
            after = _running_name()
        if sticky != name:
            fail(500, "profile_use_unverified", "已提交但 sticky 配置档未变化，请刷新确认")
        if after != before:
            fail(500, "profile_process_switched", "进程 profile 身份发生变化，这不是面板允许的切换方式")
        return {
            "ok": True,
            "applied": False,
            "processSwitched": False,
            "stickyOnly": True,
            "name": name,
            "stickyProfile": sticky,
            "runningProfile": after,
            "note": f"只写入了 sticky 文件，本进程仍是「{after}」。请求的配置档切换尚未在本进程或运行中的网关生效。",
        }

    return run(mod, _write)


DELETE_PREVIEW_ROUTE = "POST /profiles/delete-preview {name}"
DELETE_CONFIRM_ROUTE = "POST /profiles/delete-confirm {name, confirm, paths, token, fingerprint}"
_PREVIEW_TTL_S = 120.0
_TICKETS: dict[str, dict[str, Any]] = {}
_TICKET_LOCK = threading.Lock()
_PRODUCTION = ("/root/.hermes", "/usr/local/lib/hermes-agent")
_TREE_MAX = 100_000
_TREE_DEPTH = 40


def _production_text(text: str) -> bool:
    normalized = text.rstrip("/") or "/"
    return any(normalized == marker or normalized.startswith(marker + "/") for marker in _PRODUCTION)


def _kind(path: Path) -> str:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "unreadable"
    if stat.S_ISLNK(mode):
        return "symlink"
    if stat.S_ISDIR(mode):
        return "dir"
    if stat.S_ISREG(mode):
        return "file"
    return "other"


def _reject_symlink_parents(path: Path) -> None:
    current = path
    while True:
        kind = _kind(current)
        if kind == "symlink":
            fail(409, "profile_delete_symlink", "路径经过符号链接，未删除")
        parent = current.parent
        if parent == current:
            break
        current = parent


def _within(path: Path, root: Path) -> bool:
    if _kind(path) == "symlink" or _kind(root) == "symlink":
        return False
    try:
        resolved = path.resolve()
        base = root.resolve()
    except OSError:
        return False
    return resolved == base or base in resolved.parents


def _plan(name: str) -> dict[str, Any]:
    from hermes_cli.profiles import get_profile_dir, profile_removal_paths

    directory = get_profile_dir(name)
    if _production_text(str(directory)):
        fail(403, "profile_delete_production_refused", "不会读取或删除生产 Hermes 目录")
    kind = _kind(directory)
    if kind == "symlink":
        fail(409, "profile_delete_symlink", "配置档目录是符号链接，未删除")
    if kind == "missing":
        fail(404, "profile_not_found", "配置档目录不存在，未删除")
    if kind != "dir":
        fail(409, "profile_delete_unknown_directory", "配置档路径不是目录，未删除")
    _reject_symlink_parents(directory)
    if _production_text(str(directory.resolve())):
        fail(403, "profile_delete_production_refused", "不会读取或删除生产 Hermes 目录")
    try:
        plan = profile_removal_paths(name)
    except FileNotFoundError as exc:
        fail(404, "profile_not_found", "配置档不存在")
        raise AssertionError(exc)
    except ValueError as exc:
        fail(400, "profile_delete_rejected", "核心拒绝了这个配置档路径，未删除")
        raise AssertionError(exc)
    _assert_plan_safe(plan)
    return plan


def _assert_plan_safe(plan: dict[str, Any]) -> None:
    from hermes_cli.profiles import _get_active_profile_path, _get_profiles_root, _get_wrapper_dir
    from hermes_constants import profile_tombstone_path

    directory = Path(plan["directory"])
    if directory.name != plan["name"] or _kind(directory) != "dir":
        fail(409, "profile_delete_unknown_directory", "配置档目录与名称不一致，未删除")
    root = _get_profiles_root()
    if not _within(directory, root) or directory.resolve() == root.resolve():
        fail(409, "profile_delete_unknown_directory", "配置档目录不在已知配置档根下，未删除")
    tombstone = profile_tombstone_path(directory.resolve())
    if plan.get("create") != [str(tombstone)]:
        fail(409, "profile_delete_unknown_directory", "将创建的路径不是这个配置档的墓碑，未删除")
    if _kind(tombstone) == "symlink" or _kind(tombstone.parent) == "symlink":
        fail(409, "profile_delete_symlink", "墓碑路径是符号链接，未删除")
    if _production_text(str(tombstone)):
        fail(403, "profile_delete_production_refused", "不会读取或删除生产 Hermes 目录")
    sticky = _get_active_profile_path()
    rewrite = list(plan.get("rewrite") or [])
    if rewrite not in ([], [str(sticky)]):
        fail(409, "profile_delete_unknown_directory", "将改写的路径不是 sticky 文件，未删除")
    wrapper_root = _get_wrapper_dir()
    for text in plan["remove"]:
        path = Path(text)
        if _production_text(text) or _production_text(str(path)):
            fail(403, "profile_delete_production_refused", "不会读取或删除生产 Hermes 目录")
        kind = _kind(path)
        if kind == "symlink":
            fail(409, "profile_delete_symlink", "将删除的路径是符号链接，未删除")
        if kind not in {"dir", "file"}:
            fail(409, "profile_delete_unknown_directory", "将删除的路径不存在或不是已知文件，未删除")
        if not (_within(path, root) or _within(path, wrapper_root)):
            fail(409, "profile_delete_unknown_directory", "将删除的路径不在已知目录内，未删除")
        _reject_symlink_parents(path)
    for text in rewrite:
        path = Path(text)
        if _kind(path) != "file":
            fail(409, "profile_delete_unknown_directory", "sticky 文件不存在或不是普通文件，未删除")
        _reject_symlink_parents(path)


def _touched(plan: dict[str, Any]) -> list[str]:
    return [*plan["remove"], *plan["rewrite"], *plan["create"]]


def _file_digest(path: Path, size: int) -> str:
    del size
    flag = getattr(os, "O_NOFOLLOW", 0)
    if not flag:
        fail(503, "profile_delete_fingerprint_unavailable", "不能在不跟随符号链接时计算指纹，未删除")
    try:
        fd = os.open(path, os.O_RDONLY | flag)
    except OSError as exc:
        if exc.errno in {getattr(errno, "ELOOP", 40), errno.EPERM}:
            fail(409, "profile_delete_symlink", "路径在计算指纹时是符号链接，未删除")
        fail(409, "profile_delete_fingerprint_unavailable", "无法读取路径指纹，未删除")
    try:
        digest = hashlib.sha256()
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        return digest.hexdigest()
    finally:
        os.close(fd)


def _path_identity(path: Path) -> dict[str, Any]:
    kind = _kind(path)
    if kind == "symlink":
        fail(409, "profile_delete_symlink", "路径是符号链接，未删除")
    if kind == "missing":
        return {"path": str(path), "kind": "missing"}
    if kind not in {"dir", "file"}:
        fail(409, "profile_delete_unknown_directory", "路径不是可识别的文件或目录，未删除")
    st = path.lstat()
    record: dict[str, Any] = {
        "path": str(path),
        "kind": kind,
        "inode": st.st_ino,
        "size": st.st_size,
        "mtime_ns": st.st_mtime_ns,
    }
    if kind == "file":
        record["content"] = _file_digest(path, st.st_size)
    return record


def _reject_tree_links(directory: Path) -> None:
    if _kind(directory) != "dir":
        fail(409, "profile_delete_unknown_directory", "配置档目录不可用，未删除")
    stack = [directory]
    seen = 0
    while stack:
        current = stack.pop()
        seen += 1
        if seen > _TREE_MAX:
            fail(409, "profile_delete_fingerprint_unavailable", "配置档太大，无法确认没有符号链接，未删除")
        try:
            children = list(os.scandir(current))
        except OSError:
            fail(409, "profile_delete_fingerprint_unavailable", "无法读取配置档目录，未删除")
        for child in children:
            try:
                is_link = child.is_symlink()
            except OSError:
                fail(409, "profile_delete_fingerprint_unavailable", "无法识别目录项，未删除")
            if is_link:
                fail(409, "profile_delete_symlink", "配置档内含符号链接，未删除")
            kind = _kind(Path(child.path))
            if kind == "dir":
                stack.append(Path(child.path))
            elif kind != "file":
                fail(409, "profile_delete_unknown_directory", "配置档内含未知目录项，未删除")


def _tree_identity(directory: Path) -> list[dict[str, Any]]:
    if _kind(directory) != "dir":
        fail(409, "profile_delete_unknown_directory", "配置档目录不可用，未删除")
    records: list[dict[str, Any]] = []
    stack = [(directory, 0)]
    while stack:
        current, depth = stack.pop()
        if depth > _TREE_DEPTH or len(records) > _TREE_MAX:
            fail(409, "profile_delete_fingerprint_unavailable", "配置档太大或太深，无法绑定指纹，未删除")
        try:
            children = list(os.scandir(current))
        except OSError:
            fail(409, "profile_delete_fingerprint_unavailable", "无法读取配置档目录，未删除")
        children.sort(key=lambda item: item.name)
        for child in children:
            child_path = Path(child.path)
            try:
                is_link = child.is_symlink()
            except OSError:
                fail(409, "profile_delete_fingerprint_unavailable", "无法识别目录项，未删除")
            if is_link:
                fail(409, "profile_delete_symlink", "配置档内含符号链接，未删除")
            kind = _kind(child_path)
            if kind == "dir":
                stack.append((child_path, depth + 1))
            elif kind != "file":
                fail(409, "profile_delete_unknown_directory", "配置档内含未知目录项，未删除")
            rel = child_path.relative_to(directory).as_posix()
            if rel.startswith("../") or "/../" in f"/{rel}/":
                fail(409, "profile_delete_unknown_directory", "配置档路径越出目录，未删除")
            records.append(_path_identity(child_path) | {"rel": rel})
    records.sort(key=lambda item: str(item.get("rel")))
    return records


def _fingerprint(plan: dict[str, Any]) -> str:
    payload = {
        "name": plan["name"],
        "directory": plan["directory"],
        "remove": list(plan["remove"]),
        "rewrite": list(plan["rewrite"]),
        "create": list(plan["create"]),
        "gateway": bool(plan["gateway_running"]),
        "paths": [_path_identity(Path(item)) for item in _touched(plan)],
        "tree": _tree_identity(Path(plan["directory"])),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _drop_expired(now: float) -> None:
    stale = [key for key, item in _TICKETS.items() if item["expires"] <= now]
    for key in stale:
        del _TICKETS[key]


def _issue_ticket(home: str, name: str, paths: list[str], fingerprint: str) -> dict[str, Any]:
    token = secrets.token_urlsafe(32)
    now = time.monotonic()
    with _TICKET_LOCK:
        _drop_expired(now)
        stale = [key for key, item in _TICKETS.items() if item["name"] == name and item["home"] == home]
        for key in stale:
            del _TICKETS[key]
        while len(_TICKETS) >= 32:
            oldest = min(_TICKETS, key=lambda key: _TICKETS[key]["expires"])
            del _TICKETS[oldest]
        _TICKETS[token] = {
            "name": name,
            "home": home,
            "paths": list(paths),
            "fingerprint": fingerprint,
            "expires": now + _PREVIEW_TTL_S,
        }
    return {"token": token, "expiresInS": int(_PREVIEW_TTL_S)}


def _consume_ticket(token: str, home: str) -> tuple[dict[str, Any] | None, str]:
    now = time.monotonic()
    with _TICKET_LOCK:
        item = _TICKETS.pop(token, None)
        _drop_expired(now)
    if item is None:
        return None, "missing"
    if item["expires"] <= now:
        return None, "expired"
    if item["home"] != home:
        return None, "home"
    return item, "ok"


def _preview_body(plan: dict[str, Any], *, ticket: dict[str, Any] | None, blocked: str | None) -> dict[str, Any]:
    paths = _touched(plan)
    confirmable = ticket is not None and blocked is None
    note = blocked or (
        "这是路径预览，没有删除。systemd/launchd 单元未列出，也未停止。"
        "确认必须原样带回 paths、token 与 fingerprint，且 confirm 等于名称。"
        f"令牌 {int(_PREVIEW_TTL_S)} 秒内有效，使用一次即失效。"
    )
    return {
        "ok": False,
        "deleted": False,
        "confirmable": confirmable,
        "name": plan["name"],
        "remove": list(plan["remove"]),
        "rewrite": list(plan["rewrite"]),
        "create": list(plan["create"]),
        "paths": paths,
        "token": ticket["token"] if ticket else None,
        "fingerprint": ticket["fingerprint"] if ticket else None,
        "expiresInS": ticket["expiresInS"] if ticket else 0,
        "gatewayRunning": bool(plan["gateway_running"]),
        "serviceUnitsRemoved": False,
        "processEnvChanged": False,
        "applied": False,
        "note": note,
    }


def preview_delete_profile(mod: Any, name: Any) -> dict[str, Any]:
    def _read() -> dict[str, Any]:
        if not isinstance(name, str) or not PROFILE_NAME_RE.match(name):
            fail(400, "invalid_name", "配置档名称不合法")
        if name == "default":
            fail(400, "protected", "不允许删除 default 配置档")
        from clawd_server.admin_common import panel_home

        home = str(panel_home(mod))
        plan = _plan(name)
        blocked = _block_reason(plan)
        if blocked:
            return _preview_body(plan, ticket=None, blocked=blocked)
        fingerprint = _fingerprint(plan)
        issued = _issue_ticket(home, plan["name"], _touched(plan), fingerprint)
        issued["fingerprint"] = fingerprint
        return _preview_body(plan, ticket=issued, blocked=None)

    return run(mod, _read)


def _block_reason(plan: dict[str, Any]) -> str | None:
    from clawd_server.admin_common import ConfigError

    try:
        _guard_delete(plan)
    except ConfigError as exc:
        return exc.message
    return None


def _guard_delete(plan: dict[str, Any]) -> None:
    from hermes_constants import get_process_hermes_home

    directory = Path(plan["directory"])
    if _kind(directory) == "symlink":
        fail(409, "profile_delete_symlink", "配置档目录是符号链接，未删除")
    if _kind(directory) != "dir":
        fail(409, "profile_delete_unknown_directory", "配置档目录不存在或不是目录，未删除")
    process = get_process_hermes_home()
    if _kind(process) == "symlink":
        fail(409, "profile_delete_process_home", "本进程数据目录身份不明，未删除")
    resolved = directory.resolve()
    process_resolved = process.resolve()
    try:
        owns_process = process_resolved.is_relative_to(resolved)
    except ValueError:
        owns_process = False
    if resolved == process_resolved or owns_process:
        fail(409, "profile_delete_process_home", "这是本进程的数据目录，未删除")
    if plan["gateway_running"]:
        fail(
            409,
            "profile_delete_gateway_running",
            "该配置档的网关仍在运行。停止网关的核心路径会改写进程级环境，面板不调用。请先在面板外停止该网关，再重新预览。未删除。",
        )


def _apply_confirmed_delete(plan: dict[str, Any]) -> dict[str, Any]:
    from hermes_cli.profiles import (
        _notify_multiplexer,
        _purge_identity,
        remove_wrapper_script,
        set_active_profile,
    )
    from hermes_cli.profiles_remove import _rmtree_make_writable, _rmtree_with_retry, wait_profile_unowned
    from hermes_constants import mark_named_profile_deleted

    directory = Path(plan["directory"])
    if _kind(directory) != "dir":
        fail(409, "profile_delete_unknown_directory", "配置档目录在删除前已变化，未删除")
    _reject_tree_links(directory)
    if not wait_profile_unowned(directory, timeout=5):
        fail(409, "profile_delete_owned", "仍有网关占用该目录的锁，未删除")
    mark_named_profile_deleted(directory)
    try:
        _notify_multiplexer(plan["name"])
    except Exception:
        log.warning("profile delete notify failed", exc_info=True)
    identity_settled = False
    try:
        identity_settled = bool(_purge_identity(plan["name"]))
    except Exception:
        log.warning("profile identity purge failed", exc_info=True)
    if len(plan["remove"]) > 1:
        remove_wrapper_script(plan["name"])
    try:
        _rmtree_with_retry(directory, _rmtree_make_writable)
    except Exception as exc:
        fail(500, "profile_delete_failed", "墓碑已写，但目录没有删掉，请刷新确认")
        raise AssertionError(exc)
    if plan["rewrite"]:
        set_active_profile("default")
    if _kind(directory) != "missing":
        fail(500, "profile_delete_unverified", "已提交但配置档目录仍在，请刷新确认")
    note = "已删除预览列出的路径。未改本进程环境，未停止网关，未删除 systemd/launchd 单元。"
    if not identity_settled:
        note += " 身份结算未完成，不要把目录删除当成路由身份已清除。"
    return {
        "ok": True,
        "deleted": True,
        "name": plan["name"],
        "removed": list(plan["remove"]),
        "processSwitched": False,
        "applied": False,
        "processEnvChanged": False,
        "gatewayStopped": False,
        "serviceUnitsRemoved": False,
        "identitySettled": identity_settled,
        "note": note,
    }


def _token_ok(value: Any) -> bool:
    return isinstance(value, str) and value.isascii() and 20 <= len(value) <= 128 and value == value.strip()


def _fingerprint_ok(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _paths_exact(paths: Any, expected: list[str]) -> bool:
    return (
        isinstance(paths, list)
        and len(paths) == len(expected)
        and all(isinstance(item, str) and item == expected[index] for index, item in enumerate(paths))
    )


def confirm_delete_profile(mod: Any, name: Any, confirm: Any, paths: Any, token: Any = None, fingerprint: Any = None) -> dict[str, Any]:
    def _write() -> dict[str, Any]:
        if not isinstance(name, str) or not PROFILE_NAME_RE.match(name):
            fail(400, "invalid_name", "配置档名称不合法")
        if name == "default":
            fail(400, "protected", "不允许删除 default 配置档")
        if confirm != name or not _token_ok(token) or not _fingerprint_ok(fingerprint) or not isinstance(paths, list):
            fail(409, "profile_delete_confirmation_required", "确认缺少名称、一次性令牌或内容指纹，未删除。请先预览。")
        from clawd_server.admin_common import panel_home

        home = str(panel_home(mod))
        with write_lock(mod):
            ticket, reason = _consume_ticket(token, home)
            if ticket is None:
                why = {"missing": "不存在或已使用", "expired": "已过期", "home": "不属于本进程数据目录"}.get(reason, "无效")
                fail(409, "profile_delete_confirmation_mismatch", f"预览令牌不可用（{why}），未删除。请重新预览。")
            if ticket["name"] != name or not _paths_exact(paths, ticket["paths"]) or not hmac.compare_digest(fingerprint, ticket["fingerprint"]):
                fail(409, "profile_delete_confirmation_mismatch", "确认与当次预览不一致，令牌已作废，未删除。")
            plan = _plan(name)
            fresh = _fingerprint(plan)
            if _touched(plan) != ticket["paths"] or not hmac.compare_digest(fresh, ticket["fingerprint"]):
                fail(409, "profile_delete_confirmation_mismatch", "路径或内容在预览后已变化，未删除。请重新预览。")
            _guard_delete(plan)
            return _apply_confirmed_delete(plan)

    return run(mod, _write)


def delete_profile(mod: Any, name: Any, confirm: Any = None, paths: Any = None, token: Any = None, fingerprint: Any = None) -> dict[str, Any]:
    """Name-only route stays blocked. Extra confirm fields do not authorize deletion."""

    def _route() -> dict[str, Any]:
        del confirm, paths, token, fingerprint
        if not isinstance(name, str) or not PROFILE_NAME_RE.match(name):
            fail(400, "invalid_name", "配置档名称不合法")
        if name == "default":
            fail(400, "protected", "不允许删除 default 配置档")
        from clawd_server.admin_common import panel_home

        panel_home(mod)
        plan = _plan(name)
        shown = "、".join(_touched(plan))
        fail(
            409,
            "profile_delete_confirmation_required",
            "现有删除请求只带名称，不能当作已确认。这份路径列表不是确认令牌。"
            f"将触及的路径：{shown}。"
            f"需要新接口 {DELETE_PREVIEW_ROUTE} 与 {DELETE_CONFIRM_ROUTE}，"
            "confirm 必须等于名称，paths、token、fingerprint 必须与当次预览一致。未删除。",
        )

    return run(mod, _route)
