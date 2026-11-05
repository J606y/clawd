"""Skill enable switch. Writes ``skills.disabled`` and reads it back.

The integrator owns route registration. This module exports
``set_skill_enabled`` and does not install routes, call the retired CLI, or
add a skill installer. A successful save is not a hot load of this process.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from clawd_server.admin_common import fail, run, write_lock
from clawd_server.admin_config import commit_set, commit_unset

__all__ = ["set_skill_enabled"]

_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-]{0,127}$")
_KEY = "skills.disabled"
_NOTE = (
    "已写入本进程配置文件的 skills.disabled 并回读一致。"
    "applied、effectiveNow、hotLoaded、loadedNow 均为 false，本进程不会热加载，"
    "当前对话不会改用新名单。新对话读取该文件后才生效。"
    "未修改进程环境，也没有把未标明的 profile 当成 default。"
)


def set_skill_enabled(mod: Any, name: Any, enabled: Any) -> dict[str, Any]:
    """Enable or disable one catalog skill by rewriting ``skills.disabled``."""

    def _write() -> dict[str, Any]:
        checked = _name(mod, name)
        if not isinstance(enabled, bool):
            fail(400, "invalid_body", "需要布尔 enabled")
        _require_identity(mod)
        if not enabled and checked in _essential():
            fail(400, "skill_required", "该技能不能停用，未写入")
        with write_lock(mod):
            if checked not in _known_names(mod):
                fail(404, "skill_not_found", "技能不存在")
            current = _disabled_names(_raw_disabled())
            updated = (current - {checked}) if enabled else (current | {checked})
            if updated:
                written = commit_set(mod, _KEY, json.dumps(sorted(updated), ensure_ascii=False))
            else:
                commit_unset(mod, _KEY, missing_ok=True)
                written = _raw_disabled()
            actual = _disabled_names(written)
            again = _disabled_names(_raw_disabled())
        if actual != again or (checked in actual) == enabled:
            fail(500, "skill_toggle_unverified", "已提交但回读结果不一致，请刷新确认")
        return _saved(checked, enabled)

    return run(mod, _write)


def _saved(name: str, enabled: bool) -> dict[str, Any]:
    return {
        "ok": True,
        "name": name,
        "enabled": enabled,
        "saved": True,
        "applied": False,
        "processEnvChanged": False,
        "hotLoaded": False,
        "effectiveNow": False,
        "loadedNow": False,
        "restartRequired": False,
        "note": _NOTE,
    }


def _name(mod: Any, name: Any) -> str:
    pattern = getattr(mod, "SKILL_NAME_RE", None) or _NAME_RE
    if not isinstance(name, str) or pattern.match(name) is None:
        fail(400, "invalid_skill", "技能名不合法")
    return name


def _require_identity(mod: Any) -> Path:
    """Refuse a guessed home. Do not assign ``os.environ`` or invent ``default``."""
    clawd = os.environ.get("CLAWD_HOME", "").strip()
    hermes = os.environ.get("HERMES_HOME", "").strip()
    if not clawd and not hermes:
        fail(409, "profile_scope_unsafe", "进程没有明确的 CLAWD_HOME 或 HERMES_HOME，拒绝写到默认目录")
    if clawd and hermes and _resolved(clawd) != _resolved(hermes):
        fail(409, "profile_scope_unsafe", "CLAWD_HOME 与 HERMES_HOME 不一致，拒绝写入")
    if getattr(mod, "HERMES_HOME", None) is None:
        fail(409, "profile_scope_unsafe", "面板没有绑定数据目录，不能猜测 default profile")
    from clawd_server.admin_common import panel_home

    home = _resolved(panel_home(mod))
    if _production(home):
        fail(409, "profile_scope_unsafe", "拒绝把技能开关写到生产 Hermes 目录")
    return home


def _resolved(value: Path | str) -> Path:
    try:
        return Path(value).expanduser().resolve()
    except OSError as exc:
        fail(409, "profile_scope_unsafe", "数据目录无法解析，拒绝写入")
        raise AssertionError(exc)


def _production(home: Path) -> bool:
    from clawd_server.home import PRODUCTION_HOMES, is_production_home

    if is_production_home(home):
        return True
    text = str(home)
    if "/usr/local/lib/hermes-agent" in text or text.startswith("/root/.hermes"):
        return True
    for banned in PRODUCTION_HOMES:
        try:
            home.relative_to(Path(banned).expanduser().resolve())
        except (OSError, ValueError):
            continue
        return True
    return False


def _essential() -> frozenset[str]:
    try:
        from agent.skill_utils import ESSENTIAL_SKILLS
    except Exception as exc:
        fail(503, "skills_unavailable", "无法核对不可停用技能，未写入")
        raise AssertionError(exc)
    if not isinstance(ESSENTIAL_SKILLS, frozenset) or not ESSENTIAL_SKILLS:
        fail(503, "skills_unavailable", "不可停用技能名单不可用，未写入")
    return ESSENTIAL_SKILLS


def _known_names(mod: Any) -> set[str]:
    list_skills = getattr(mod, "list_skills", None)
    if not callable(list_skills):
        fail(503, "skills_unavailable", "没有技能目录读取函数，拒绝按空名单切换")
    try:
        payload = list_skills()
    except Exception as exc:
        api_error = getattr(mod, "ApiError", None)
        if api_error is not None and isinstance(exc, api_error):
            raise
        fail(503, "skills_unavailable", "技能目录读取失败，未切换")
        raise AssertionError(exc)
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        fail(503, "skills_unavailable", "技能目录没有返回名单，拒绝切换")
    names: set[str] = set()
    for item in data:
        if isinstance(item, dict) and isinstance(item.get("name"), str) and item["name"]:
            names.add(item["name"])
    return names


def _raw_disabled() -> Any:
    from hermes_cli.config import _MISSING, _get_nested, read_raw_config_readonly

    raw = read_raw_config_readonly()
    if not isinstance(raw, dict):
        fail(503, "skills_unavailable", "读不到配置文件，未改写技能开关")
    value = _get_nested(raw, _KEY)
    return None if value is _MISSING else value


def _disabled_names(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        return _names_from_text(value)
    if isinstance(value, (list, tuple, set, frozenset)):
        names: set[str] = set()
        for item in value:
            if item is None or item == "":
                continue
            if not isinstance(item, str) or not item.strip():
                fail(400, "skill_disabled_unreadable", "skills.disabled 含非文本项，未改写以免覆盖原值")
            names.add(item.strip())
        return names
    fail(400, "skill_disabled_unreadable", "skills.disabled 不是名单，未改写以免覆盖原值")
    raise AssertionError(value)


def _names_from_text(value: str) -> set[str]:
    text = value.strip()
    if not text:
        return set()
    if text.startswith("["):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            fail(400, "skill_disabled_unreadable", "skills.disabled 无法安全解析，未改写")
            raise AssertionError(exc)
        return _disabled_names(parsed)
    return {text}
