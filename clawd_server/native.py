"""In-process adapters for panel config, skills, and message origins.

These call core Python services. They do not shell out to hermes and do not
open state.db with a private query. File routes stay on the panel path policy
because the core has no file-browser API; folder routes are frozen.
"""

from __future__ import annotations

import io
import json
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any


def install(mod) -> None:
    """Replace compensation entry points on an already-bound sidecar module.

    The stdout config_set wrapper is assigned first. Browser and runtime install
    at most once. install_admin always runs after that wrapper so its config_set
    is the one left in place.
    """
    from clawd_server import yaml_adapter

    mod.yaml = yaml_adapter
    mod.config_set = lambda key, value: config_set(mod, key, value)
    mod._clawd_config_set_owner = "wrapper"
    mod._disabled_skills = lambda: disabled_skills()
    mod.set_skill_enabled = lambda name, enabled: set_skill_enabled(mod, name, enabled)
    mod.get_message_origins = lambda session_id, raw_ids: message_origins(mod, session_id, raw_ids)
    mod._clawd_native = ("config_set", "skills.disabled", "message_origins")
    if not getattr(mod, "_clawd_browser_installed", False):
        from clawd_server.browser_native import install as install_browser

        install_browser(mod)
        mod._clawd_browser_installed = True
    if not getattr(mod, "_clawd_runtime_installed", False):
        from clawd_server.runtime_native import install as install_runtime

        install_runtime(mod)
        mod._clawd_runtime_installed = True
    from clawd_server.admin_native import install as install_admin

    install_admin(mod)
    if getattr(mod, "_clawd_config_set_owner", None) != "admin":
        raise RuntimeError("install_admin did not replace the stdout config_set wrapper")
    if not getattr(mod, "_clawd_admin_routes_attached", False):
        raise RuntimeError("admin delete routes were not attached to Handler._route")


def disabled_skills() -> set[str]:
    from hermes_cli.config import load_config
    from hermes_cli.skills_config import get_disabled_skills

    return set(get_disabled_skills(load_config()))


def config_set(mod, key: str, value: str) -> dict[str, Any]:
    if not mod.CONFIG_KEY_RE.match(key):
        raise mod.ApiError(400, "invalid_key", "配置项名称不合法")
    if mod._is_secret_key(key):
        raise mod.ApiError(403, "secret_key", "密钥类配置不允许在面板里修改")
    before = _lookup(key)
    out = _set(key, value)
    if "⚠" in out:
        rollback = _unset(key) if before is None else _set(key, before)
        if rollback.startswith("ERR:"):
            raise mod.ApiError(500, "config_rollback_failed", f"核心不认识 {key}，且自动撤回失败")
        raise mod.ApiError(400, "unknown_key", f"核心不认识配置项 {key}，未写入")
    if out.startswith("ERR:"):
        raise mod.ApiError(400, "config_set_failed", out[4:] or "写入配置失败")
    return {"ok": True, "key": key, "value": _lookup(key), "output": mod.redact(out)}


def set_skill_enabled(mod, name: str, enabled: bool) -> dict[str, Any]:
    if not mod.SKILL_NAME_RE.match(name):
        raise mod.ApiError(400, "invalid_skill", "技能名不合法")
    known = {item["name"] for item in mod.list_skills()["data"]}
    if name not in known:
        raise mod.ApiError(404, "skill_not_found", "技能不存在")
    from hermes_cli.config import load_config
    from hermes_cli.skills_config import save_disabled_skills

    disabled = disabled_skills()
    disabled = disabled - {name} if enabled else disabled | {name}
    save_disabled_skills(load_config(), disabled)
    actual = disabled_skills()
    if (name in actual) == enabled:
        raise mod.ApiError(500, "skill_toggle_unverified", "已提交但回读结果不一致，请刷新确认")
    return {"ok": True, "name": name, "enabled": enabled}


def message_origins(mod, session_id: str, raw_ids: str) -> dict[str, Any]:
    import re

    if not isinstance(session_id, str) or not session_id or len(session_id) > 512:
        raise mod.ApiError(400, "invalid_session", "需要有效会话 ID")
    if not isinstance(raw_ids, str) or len(raw_ids) > 4096:
        raise mod.ApiError(400, "invalid_ids", "消息 ID 列表过长")
    parts = raw_ids.split(",") if raw_ids else []
    if len(parts) > 200 or any(re.fullmatch(r"[1-9][0-9]{0,18}", value) is None or int(value) > 9223372036854775807 for value in parts):
        raise mod.ApiError(400, "invalid_ids", "需要不超过 200 个正整数消息 ID")
    ids = list(dict.fromkeys(int(value) for value in parts))
    if not ids:
        return {"items": {}}
    try:
        from hermes_state import SessionDB
        from hermes_state_runtime import get_session_admission

        db = SessionDB()
        try:
            rows = db.get_messages(session_id, include_inactive=True)
            wanted = set(ids)
            items: dict[str, str] = {}
            for row in rows:
                mid = row.get("id")
                if mid not in wanted or row.get("role") != "user":
                    continue
                admission_id = row.get("platform_message_id")
                if not isinstance(admission_id, str) or not admission_id:
                    continue
                admission = get_session_admission(db, admission_id=admission_id)
                if not admission or admission.get("target_session_id") != session_id:
                    continue
                if admission.get("principal_id") != "api" or admission.get("intent") not in {"queue", "steer"}:
                    continue
                payload = admission.get("payload_json")
                if isinstance(payload, str):
                    try:
                        payload = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                if isinstance(payload, dict) and isinstance(payload.get("api_turn_v1"), dict):
                    items[str(mid)] = "api_" + str(admission["intent"])
            return {"items": items}
        finally:
            db.close()
    except mod.ApiError:
        raise
    except Exception as exc:
        raise mod.ApiError(503, "message_origin_unavailable", "消息来源暂不可用") from exc


def _lookup(key: str) -> str | None:
    from hermes_cli.config import load_config

    cur: Any = load_config()
    for part in key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    if cur is None:
        return None
    if isinstance(cur, str):
        return cur
    return json.dumps(cur, ensure_ascii=False)


def _set(key: str, value: str) -> str:
    from hermes_cli.config import set_config_value

    out, err = io.StringIO(), io.StringIO()
    try:
        with redirect_stdout(out), redirect_stderr(err):
            set_config_value(key, value)
    except SystemExit as exc:
        text = (out.getvalue() + err.getvalue()).strip()
        return "ERR:" + (text or f"exit {exc.code}")
    return (out.getvalue() + err.getvalue()).strip()


def _unset(key: str) -> str:
    from hermes_cli.config import unset_config_value

    out, err = io.StringIO(), io.StringIO()
    try:
        with redirect_stdout(out), redirect_stderr(err):
            unset_config_value(key)
    except SystemExit:
        return "ERR:" + (out.getvalue() + err.getvalue()).strip()
    return (out.getvalue() + err.getvalue()).strip()


def file_roots_under_home(mod, home: Path) -> None:
    """Panel file policy, rooted only in the clawd home. No core browser API exists."""
    root = home.resolve()
    for spec in mod.FILE_ROOTS.values():
        path = Path(spec["path"]).resolve()
        if root != path and root not in path.parents:
            raise RuntimeError(f"file root {path} is outside CLAWD_HOME")
        if "/usr/local/lib/hermes-agent" in str(path) or str(path).startswith("/root/.hermes"):
            raise RuntimeError(f"file root still points at a production path: {path}")
