"""Settings form backed by the web config schema, in-process.

Replaces the sidecar's HERMES_PY -c dump. Writes go through commit_set,
not the CLI and not native.py's stdout wrapper.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from clawd_server.admin_common import ConfigError, fail, redact_text, run, secret_key, secret_name, write_lock
from clawd_server.admin_config import commit_set, commit_unset

log = logging.getLogger("clawd.admin")

_HIDDEN = {"model"}
_KEY_MAP = {"model_context_length": "model.context_length"}
_ORDER = [
    "general", "agent", "terminal", "display", "delegation", "memory", "compression", "security",
    "browser", "voice", "tts", "stt", "logging", "discord", "auxiliary",
]
_LABELS = {
    "general": "通用", "agent": "智能体", "terminal": "终端", "display": "显示", "delegation": "子代理委派",
    "memory": "记忆", "compression": "上下文压缩", "security": "安全", "browser": "浏览器", "voice": "语音",
    "tts": "语音合成", "stt": "语音识别", "logging": "日志", "discord": "聊天平台", "auxiliary": "辅助模型",
    "database": "数据库", "attachments": "附件", "web": "网页", "tool_output": "工具输出",
    "tool_loop_guardrails": "工具循环防护", "vision": "视觉", "wake_word": "唤醒词", "loops": "循环",
    "moa": "多模型协作", "curator": "整理器", "slack": "Slack", "openrouter": "OpenRouter", "bedrock": "Bedrock",
}
_CACHE: dict[str, Any] = {"stamp": None, "at": 0.0, "data": None}
_LOCK = threading.Lock()


def _dig(data: Any, path: str) -> tuple[Any, bool]:
    cur = data
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None, False
        cur = cur[part]
    return cur, True


def _stamp() -> tuple[Any, ...] | None:
    from hermes_cli.config import get_config_path

    path = get_config_path()
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size, stat.st_ino)


def _dump(mod: Any) -> dict[str, Any]:
    from clawd_server.admin_common import panel_home

    home = str(panel_home(mod))
    with _LOCK:
        stamp = _stamp()
        if (
            _CACHE["data"] is not None
            and _CACHE.get("home") == home
            and stamp is not None
            and _CACHE["stamp"] == stamp
            and time.monotonic() - _CACHE["at"] < 30
        ):
            return _CACHE["data"]
        try:
            from hermes_cli.config import load_config, read_raw_config
            from hermes_cli.web_server_config import _normalize_config_for_web, _schema_with_dynamic_provider_options

            fields = _schema_with_dynamic_provider_options()
            effective = _normalize_config_for_web(load_config())
            raw = read_raw_config()
        except Exception as exc:
            log.warning("settings schema failed: %s", type(exc).__name__)
            fail(503, "settings_unavailable", "读取配置结构失败，请先用「YAML 原文」修改")
        if not isinstance(fields, dict) or not isinstance(effective, dict) or not isinstance(raw, dict):
            fail(503, "settings_unavailable", "配置结构解析失败")
        data = {
            "fields": fields,
            "values": {key: _dig(effective, key)[0] for key in fields},
            "userSet": [key for key in fields if _dig(raw, key)[1]],
        }
        _CACHE.update(stamp=stamp, at=time.monotonic(), data=data, home=home)
        return data


def _contains_secret(mod: Any, value: Any) -> bool:
    if isinstance(value, dict):
        return any(secret_name(mod, str(key)) or _contains_secret(mod, item) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_secret(mod, item) for item in value)
    return False


def _mask(mod: Any, value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(mod, value)
    if isinstance(value, list):
        return [_mask(mod, item) for item in value]
    if isinstance(value, dict):
        return {key: ("***" if secret_name(mod, str(key)) else _mask(mod, item)) for key, item in value.items()}
    return value


def get_settings(mod: Any) -> dict[str, Any]:
    def _read() -> dict[str, Any]:
        hidden = getattr(mod, "SETTINGS_HIDDEN", _HIDDEN)
        labels = getattr(mod, "SETTINGS_CATEGORY_LABELS", _LABELS)
        order = getattr(mod, "SETTINGS_CATEGORY_ORDER", _ORDER)
        dump = _dump(mod)
        user_set = set(dump.get("userSet", []))
        fields = []
        for key, spec in dump["fields"].items():
            if key in hidden or not isinstance(spec, dict):
                continue
            kind = spec.get("type") or "string"
            value = dump["values"].get(key)
            secret = secret_key(mod, key)
            locked = ""
            if secret:
                locked = "密钥类配置不能在面板里查看或修改，请在服务器上改"
            elif kind in ("list", "object") and _contains_secret(mod, value):
                locked = "内容里含密钥，请在服务器上改"
            elif kind == "object":
                locked = "复杂结构，请用「YAML 原文」修改"
            fields.append({
                "key": key,
                "type": kind,
                "category": spec.get("category") or "general",
                "description": str(spec.get("description") or "")[:300],
                "options": [str(item) for item in spec["options"]] if isinstance(spec.get("options"), list) else None,
                "clearable": bool(spec.get("clearable")),
                "searchable": bool(spec.get("searchable")),
                "value": None if secret else _mask(mod, value),
                "valueSet": bool(value) if secret else None,
                "userSet": key in user_set,
                "locked": locked,
            })
        counts: dict[str, int] = {}
        for field in fields:
            counts[field["category"]] = counts.get(field["category"], 0) + 1
        ordered = [item for item in order if item in counts] + sorted(item for item in counts if item not in order)
        return {
            "fields": fields,
            "categories": [{"id": item, "label": labels.get(item, item), "count": counts[item]} for item in ordered],
        }

    return run(mod, _read)


def _field(mod: Any, key: Any) -> tuple[str, dict[str, Any]]:
    hidden = getattr(mod, "SETTINGS_HIDDEN", _HIDDEN)
    mapping = getattr(mod, "SETTINGS_KEY_MAP", _KEY_MAP)
    if not isinstance(key, str) or key in hidden:
        fail(400, "invalid_key", "这个配置项不能在表单里修改")
    dump = _dump(mod)
    spec = dump["fields"].get(key)
    if not isinstance(spec, dict):
        fail(404, "unknown_key", "没有这个配置项")
    if secret_key(mod, key):
        fail(403, "secret_key", "密钥类配置不允许在面板里修改")
    if spec.get("type") == "object" or (spec.get("type") == "list" and _contains_secret(mod, dump["values"].get(key))):
        fail(403, "locked", "这个配置项请在服务器上或用「YAML 原文」修改")
    return mapping.get(key, key), spec


def _coerce(spec: dict[str, Any], value: Any) -> str:
    kind = spec.get("type")
    if kind == "boolean":
        if not isinstance(value, bool):
            fail(400, "invalid_value", "需要 true / false")
        return "true" if value else "false"
    if kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or value in (float("inf"), float("-inf")):
            fail(400, "invalid_value", "需要一个数字")
        return str(int(value)) if float(value).is_integer() else repr(float(value))
    if kind == "select":
        options = [str(item) for item in spec["options"]] if isinstance(spec.get("options"), list) else None
        if not isinstance(value, str) or (options is not None and value not in options and not (value == "" and spec.get("clearable"))):
            fail(400, "invalid_value", "不在可选范围内")
        return value
    if kind == "list":
        if not isinstance(value, list) or len(value) > 500:
            fail(400, "invalid_value", "需要一个列表（最多 500 项）")
        import json

        return json.dumps(value, ensure_ascii=False)
    if not isinstance(value, str) or len(value) > 4000 or "\x00" in value:
        fail(400, "invalid_value", "需要 4000 字以内的文本")
    return value


def set_setting(mod: Any, key: Any, value: Any) -> dict[str, Any]:
    def _write() -> dict[str, Any]:
        real_key, spec = _field(mod, key)
        text = _coerce(spec, value)
        with write_lock(mod):
            commit_set(mod, real_key, text)
        with _LOCK:
            _CACHE.update(stamp=None, data=None, home=None)
        from clawd_server.admin_config import effect_fields, effect_text

        fields = effect_fields(real_key)
        return {"ok": True, "key": key, "output": "已写入本进程 profile 的配置文件。" + effect_text(fields), **fields}

    return run(mod, _write)


def reset_setting(mod: Any, key: Any) -> dict[str, Any]:
    def _write() -> dict[str, Any]:
        real_key, _spec = _field(mod, key)
        with write_lock(mod):
            commit_unset(mod, real_key, missing_ok=False)
        with _LOCK:
            _CACHE.update(stamp=None, data=None, home=None)
        from clawd_server.admin_config import effect_fields, effect_text

        fields = effect_fields(real_key)
        return {"ok": True, "key": key, "output": "已从本进程 profile 的配置文件清除。" + effect_text(fields), **fields}

    return run(mod, _write)


# Matrix openContainers whose plan is advanced-object or masked-advanced.
# models-tab, exclude and exclude-secret-map stay out. Order matches the matrix.
ADVANCED_ALLOWLIST = (
    "credential_pool_strategies",
    "agent.reasoning_overrides",
    "terminal.docker_env",
    "web.provider_tier",
    "browser.camofox",
    "compression.model_thresholds",
    "auxiliary.vision.extra_body",
    "auxiliary.compression.extra_body",
    "auxiliary.skills_hub.extra_body",
    "auxiliary.approval.extra_body",
    "auxiliary.mcp.extra_body",
    "auxiliary.title_generation.extra_body",
    "auxiliary.memory_query_rewrite.extra_body",
    "auxiliary.tts_audio_tags.extra_body",
    "auxiliary.voice_chat.extra_body",
    "auxiliary.triage_specifier.extra_body",
    "auxiliary.kanban_decomposer.extra_body",
    "auxiliary.profile_describer.extra_body",
    "auxiliary.goal_judge.extra_body",
    "auxiliary.curator.extra_body",
    "auxiliary.monitor.extra_body",
    "auxiliary.background_review.extra_body",
    "auxiliary.moa_reference.extra_body",
    "auxiliary.moa_aggregator.extra_body",
    "display.status_phrases",
    "delegation.backends",
    "delegation.request_overrides",
    "honcho",
    "slack.channel_prompts",
    "discord.channel_prompts",
    "whatsapp",
    "telegram.channel_prompts",
    "mattermost.channel_prompts",
    "quick_commands",
    "platform_hints",
    "hooks",
    "personalities",
    "lsp.servers",
)
MASKED_ADVANCED_KEYS = frozenset({"credential_pool_strategies", "terminal.docker_env"})
_ADVANCED_INDEX = {key: index for index, key in enumerate(ADVANCED_ALLOWLIST)}
_MASK = "***"
_MASK_WORDS = frozenset({_MASK, "********", "已设置", "未设置"})
_ABSENT = object()
_MAX_DEPTH = 8
_MAX_NODES = 800
_MAX_STRING = 8000
_MAX_BYTES = 262144
_MAX_KEYS = 200
_MAX_LIST = 200


def advanced_plan(key: str) -> str | None:
    if key not in _ADVANCED_INDEX:
        return None
    if key in MASKED_ADVANCED_KEYS:
        return "masked-advanced"
    return "advanced-object"


def _require_advanced_key(key: Any) -> str:
    if not isinstance(key, str) or not key:
        fail(400, "invalid_body", "需要开放对象的键")
    if advanced_plan(key) is None:
        fail(403, "advanced_key_denied", "这个键不在允许的开放对象名单里")
    return key


def _require_secret_policy(mod: Any) -> None:
    """Fail closed if the integrator's policy is missing. Do not ship a second list."""
    secret_name(mod, "enabled")
    secret_key(mod, "enabled")


def _named_secret(mod: Any, path: str, leaf: str) -> bool:
    if secret_name(mod, leaf):
        return True
    return secret_key(mod, path)


def _is_preserve_mask(value: Any) -> bool:
    if isinstance(value, str):
        return value in _MASK_WORDS
    if isinstance(value, dict) and value.get("masked") is True and set(value) <= {"masked", "valueSet", "kind"}:
        return True
    return False


def _has_secret(mod: Any, value: Any, path: str, force_mask: bool) -> bool:
    if force_mask and _credential_present(value) and not isinstance(value, (dict, list)):
        return True
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                return True
            child = f"{path}.{key}" if path else key
            if (force_mask or _named_secret(mod, child, key)) and _credential_present(item):
                return True
            if isinstance(item, (dict, list)) and _has_secret(mod, item, child, force_mask):
                return True
        return False
    if isinstance(value, list):
        return any(_has_secret(mod, item, f"{path}.{index}", force_mask) for index, item in enumerate(value))
    return False


def _contains_preserve_mask(value: Any) -> bool:
    if _is_preserve_mask(value):
        return True
    if isinstance(value, dict):
        return any(_contains_preserve_mask(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_preserve_mask(item) for item in value)
    return False


def _merge_list(mod: Any, existing: Any, incoming: list[Any], *, force_mask: bool, path: str) -> list[Any]:
    """Align list items so a mask inside a dict cannot replace a stored secret."""
    if not isinstance(existing, list):
        if _contains_preserve_mask(incoming):
            fail(400, "advanced_invalid", "列表里的掩码不能覆盖秘密，未写入")
        return incoming
    if len(incoming) != len(existing):
        dropped_secret = any(_has_secret(mod, item, path, force_mask) for item in existing)
        if dropped_secret or _contains_preserve_mask(incoming):
            fail(400, "advanced_invalid", "列表长度变化会丢掉或覆盖秘密，未写入")
        return incoming
    merged: list[Any] = []
    for index, item in enumerate(incoming):
        old = existing[index]
        child = f"{path}.{index}"
        if isinstance(item, dict):
            if not isinstance(old, dict):
                if _contains_preserve_mask(item):
                    fail(400, "advanced_invalid", "列表里的掩码不能覆盖秘密，未写入")
                merged.append(item)
                continue
            merged.append(merge_advanced(mod, old, item, force_mask=force_mask, path=child))
            continue
        if isinstance(item, list):
            merged.append(_merge_list(mod, old if isinstance(old, list) else [], item, force_mask=force_mask, path=child))
            continue
        if (_is_preserve_mask(item) or item == "") and (force_mask or _has_secret(mod, old, child, False)):
            merged.append(old)
            continue
        if item is None:
            merged.append(None)
            continue
        merged.append(item)
    return merged


def _credential_present(value: Any) -> bool:
    if value is None or value == "":
        return False
    if isinstance(value, (dict, list)) and len(value) == 0:
        return False
    return True


def _validate_advanced(value: Any) -> None:
    import json

    nodes = 0

    def walk(node: Any, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > _MAX_NODES or depth > _MAX_DEPTH:
            fail(400, "advanced_invalid", "对象超出面板深度或节点上限，未写入")
        if isinstance(node, dict):
            if len(node) > _MAX_KEYS:
                fail(400, "advanced_invalid", "对象子键过多，未写入")
            for key, item in node.items():
                if not isinstance(key, str) or not key or len(key) > 128 or any(ch in key for ch in "\x00\n\r"):
                    fail(400, "advanced_invalid", "对象子键不合法，未写入")
                walk(item, depth + 1)
            return
        if isinstance(node, list):
            if len(node) > _MAX_LIST:
                fail(400, "advanced_invalid", "列表过长，未写入")
            for item in node:
                walk(item, depth + 1)
            return
        if isinstance(node, bool) or node is None:
            return
        if isinstance(node, int):
            return
        if isinstance(node, float):
            if node != node or node in (float("inf"), float("-inf")):
                fail(400, "advanced_invalid", "数字不合法，未写入")
            return
        if isinstance(node, str):
            if len(node) > _MAX_STRING or "\x00" in node:
                fail(400, "advanced_invalid", "文本超出面板上限或含空字符，未写入")
            return
        fail(400, "advanced_invalid", "对象里有不允许的类型，未写入")

    walk(value, 0)
    try:
        encoded = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        fail(400, "advanced_invalid", "对象不能序列化，未写入")
    if len(encoded.encode("utf-8")) > _MAX_BYTES:
        fail(400, "advanced_invalid", "对象超出面板大小上限，未写入")


def mask_advanced(mod: Any, value: Any, *, force_mask: bool) -> tuple[Any, list[str]]:
    """Copy with secret leaves replaced by a fixed mask. Never returns the stored secret."""
    leaves: list[str] = []

    def hide(path: str) -> str:
        if path:
            leaves.append(path)
        return _MASK

    def walk(node: Any, path: str) -> Any:
        if isinstance(node, list):
            return [walk(entry, path) if isinstance(entry, (dict, list)) else entry for entry in node]
        if isinstance(node, dict):
            shown: dict[str, Any] = {}
            for key, item in node.items():
                if not isinstance(key, str):
                    fail(400, "advanced_invalid", "已存储的对象有非文本键，面板不展示")
                child = f"{path}.{key}" if path else key
                leaf_secret = force_mask or _named_secret(mod, child, key)
                if leaf_secret and _credential_present(item) and not isinstance(item, dict):
                    shown[key] = hide(child)
                elif isinstance(item, dict):
                    if leaf_secret and not force_mask and _credential_present(item):
                        shown[key] = hide(child)
                    else:
                        shown[key] = walk(item, child)
                elif isinstance(item, list):
                    if (force_mask or leaf_secret) and _credential_present(item):
                        shown[key] = hide(child)
                    else:
                        shown[key] = [walk(entry, child) if isinstance(entry, (dict, list)) else entry for entry in item]
                else:
                    shown[key] = item
            return shown
        return node

    if value is None:
        return None, leaves
    if not isinstance(value, dict):
        fail(400, "advanced_invalid", "已存储的值不是对象，面板不展示也不覆盖")
    return walk(value, ""), leaves


def merge_advanced(mod: Any, existing: Any, incoming: Any, *, force_mask: bool, path: str = "") -> dict[str, Any]:
    """Replace non-secret keys. A mask, blank, or omitted secret never overwrites the stored secret."""
    if not isinstance(incoming, dict):
        fail(400, "advanced_invalid", "需要一个对象")
    if existing is None:
        existing = {}
    if not isinstance(existing, dict):
        fail(400, "advanced_invalid", "已存储的值不是对象，未覆盖")
    for key in existing:
        if not isinstance(key, str):
            fail(400, "advanced_invalid", "已存储的对象有非文本键，面板不覆盖")
    if path == "":
        _validate_advanced(incoming)
    out: dict[str, Any] = {}
    for key, item in incoming.items():
        child = f"{path}.{key}" if path else key
        old = existing[key] if key in existing else _ABSENT
        secret = force_mask or _named_secret(mod, child, key)
        if isinstance(item, dict):
            if secret and not force_mask:
                if _is_preserve_mask(item) and old is not _ABSENT:
                    out[key] = old
                elif not _is_preserve_mask(item):
                    fail(400, "advanced_invalid", "秘密子键不能改成对象，未写入")
                continue
            if old is not _ABSENT and not isinstance(old, dict):
                fail(400, "advanced_invalid", "不能把已有标量子键改成对象，未写入")
            out[key] = merge_advanced(
                mod,
                {} if old is _ABSENT else old,
                item,
                force_mask=force_mask,
                path=child,
            )
            continue
        if secret and (_is_preserve_mask(item) or item == ""):
            if old is not _ABSENT:
                out[key] = old
            continue
        if item is None:
            continue
        if isinstance(item, list):
            if secret and not force_mask:
                fail(400, "advanced_invalid", "秘密子键不能改成列表，未写入")
            if old is not _ABSENT and isinstance(old, dict):
                fail(400, "advanced_invalid", "不能把对象子键改成标量，未写入")
            out[key] = _merge_list(
                mod,
                [] if old is _ABSENT else old,
                item,
                force_mask=force_mask or secret,
                path=child,
            )
            continue
        if old is not _ABSENT and isinstance(old, dict):
            fail(400, "advanced_invalid", "不能把对象子键改成标量，未写入")
        if not isinstance(item, (str, int, float, bool)):
            fail(400, "advanced_invalid", "对象里有不允许的类型，未写入")
        out[key] = item
    for key, old in existing.items():
        if key in incoming:
            continue
        child = f"{path}.{key}" if path else key
        if force_mask or _named_secret(mod, child, key):
            out[key] = old
    return out


def _same_stored(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left is right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    if isinstance(left, dict) and isinstance(right, dict):
        if set(left) != set(right):
            return False
        return all(_same_stored(left[key], right[key]) for key in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(_same_stored(a, b) for a, b in zip(left, right))
    return left == right


def _profile_facts(mod: Any) -> dict[str, Any]:
    from hermes_cli.config import get_config_path
    from clawd_server.admin_common import assert_process_scope

    home = assert_process_scope(mod)
    try:
        from hermes_cli.config import _worker_config_snapshot

        if _worker_config_snapshot() is not None:
            fail(409, "profile_scope_unsafe", "当前线程有冻结的配置快照，已拒绝")
    except ConfigError:
        raise
    except Exception as exc:
        log.warning("advanced snapshot check failed: %s", type(exc).__name__)
        fail(503, "settings_unavailable", "无法确认配置读取范围，已拒绝")
    config_path = get_config_path()
    if config_path.resolve().parent != home.resolve():
        fail(409, "profile_scope_unsafe", "配置文件不在本进程 profile 目录，已拒绝")
    try:
        from hermes_cli.profiles import get_active_profile_name

        running = get_active_profile_name()
    except Exception as exc:
        log.warning("advanced profile name failed: %s", type(exc).__name__)
        fail(409, "profile_scope_unsafe", "无法从本进程目录确定配置档名称，已拒绝，不猜测 default")
    if not isinstance(running, str) or not running:
        fail(409, "profile_scope_unsafe", "无法从本进程目录确定配置档名称，已拒绝，不猜测 default")
    sticky_known: bool | None = None
    sticky_name: str | None = None
    sticky_note = "未能定位 sticky 文件，没有改用 default，也没有写入 sticky。"
    try:
        from hermes_cli.profiles import _get_active_profile_path

        sticky_path = _get_active_profile_path()
        sticky_known = sticky_path.is_file()
        if not sticky_known:
            sticky_note = "sticky 文件不存在。未把它当成 default，也未写入 sticky。"
        else:
            text = sticky_path.read_text(encoding="utf-8-sig").strip()
            sticky_name = text or None
            sticky_note = "sticky 只报告，本次没有修改它。" if text else "sticky 文件为空，没有改用 default。"
    except Exception as exc:
        log.warning("advanced sticky read failed: %s", type(exc).__name__)
        sticky_known = None
        sticky_name = None
    return {
        "profileHome": str(home),
        "configPath": str(config_path),
        "runningProfile": running,
        "profileSource": "process-home",
        "stickyProfile": sticky_name,
        "stickyKnown": sticky_known,
        "processSwitched": False,
        "stickyNote": sticky_note,
    }


def _effect_body(key: str, *, saved: bool) -> dict[str, Any]:
    if saved:
        from clawd_server.admin_config import effect_fields

        fields = dict(effect_fields(key))
    else:
        fields = {
            "saved": False,
            "applied": False,
            "processEnvChanged": False,
            "restartRequired": False,
            "hotLoaded": False,
        }
    fields["saved"] = saved
    fields["applied"] = False
    fields["processEnvChanged"] = False
    fields["hotLoaded"] = False
    fields["effectiveNow"] = False
    fields["loadedNow"] = False
    fields["processSwitched"] = False
    return fields


def _raw_tree() -> dict[str, Any]:
    from hermes_cli.config import read_raw_config
    from hermes_cli.config_read_errors import FailedConfigRead

    try:
        raw = read_raw_config()
    except Exception as exc:
        log.warning("advanced read failed: %s", type(exc).__name__)
        fail(503, "settings_unavailable", "读取配置失败，不以空对象冒充")
    if isinstance(raw, FailedConfigRead) or not isinstance(raw, dict):
        fail(503, "settings_unavailable", "配置文件读失败，不以空对象冒充")
    return raw


def _stored_object(key: str) -> tuple[Any, bool]:
    from hermes_cli.config import _MISSING, _get_nested

    value = _get_nested(_raw_tree(), key)
    if value is _MISSING:
        return None, False
    return value, True


def _refuse_env_target(key: str) -> None:
    from hermes_cli.config import _is_env_config_key
    from hermes_cli.config_env_routing import is_env_setting_key

    if _is_env_config_key(key) or is_env_setting_key(key):
        fail(403, "config_locked", "开放对象不写环境变量，未改进程环境")


def _write_object(key: str, value: dict[str, Any]) -> None:
    from hermes_cli.config import (
        _redirect_platform_display_key,
        _set_nested,
        _validate_config_key,
        _write_user_config,
        get_config_path,
        require_readable_config_before_write,
    )
    from hermes_cli.config_backend import pinning_keys

    canonical, _note = _redirect_platform_display_key(key)
    if canonical != key:
        fail(400, "advanced_invalid", "这个开放对象不能改写到别的路径，未写入")
    known, _suggestion = _validate_config_key(canonical)
    if not known:
        fail(400, "unknown_key", "核心不认识这个开放对象，未写入")
    _refuse_env_target(canonical)
    from clawd_server.admin_config import _reject_managed

    _reject_managed(canonical, "修改")
    config_path = get_config_path()
    user_config = require_readable_config_before_write(config_path)
    if not isinstance(user_config, dict):
        fail(503, "settings_unavailable", "配置文件不是对象，未写入")
    try:
        _set_nested(user_config, canonical, value)
    except (TypeError, ValueError) as exc:
        log.warning("advanced nested write refused: %s", type(exc).__name__)
        fail(400, "advanced_invalid", "不能写入这个对象，未改配置")
    with pinning_keys(canonical):
        _write_user_config(config_path, user_config)


def _advanced_view(mod: Any, key: str, stored: Any, present: bool, *, saved: bool) -> dict[str, Any]:
    plan = advanced_plan(key)
    shown, leaves = (None, []) if not present else mask_advanced(mod, stored, force_mask=plan == "masked-advanced")
    fields = _effect_body(key, saved=saved)
    profile = _profile_facts(mod)
    note = (
        f"读写的是本进程配置档目录 {profile['profileHome']}，文件 {profile['configPath']}。"
        f"runningProfile={profile['runningProfile']} 由该目录派生，不是请求里的 default。"
        f"{profile['stickyNote']}"
    )
    if saved:
        from clawd_server.admin_config import effect_text

        note += effect_text(fields)
        note += "开放对象没有热加载。"
    return {
        "ok": True,
        "key": key,
        "plan": plan,
        "value": shown,
        "userSet": present,
        "secretLeaves": leaves,
        "note": note,
        **fields,
        **profile,
    }


def get_advanced(mod: Any, key: Any) -> dict[str, Any]:
    """GET /settings/advanced?key= — masked subtree of an allowlisted object. No plaintext secrets."""

    def _read() -> dict[str, Any]:
        checked = _require_advanced_key(key)
        _require_secret_policy(mod)
        _profile_facts(mod)
        stored, present = _stored_object(checked)
        if present and not isinstance(stored, dict):
            fail(400, "advanced_invalid", "已存储的值不是对象，面板不展示也不覆盖")
        return _advanced_view(mod, checked, stored, present, saved=False)

    return run(mod, _read)


def set_advanced(mod: Any, key: Any, value: Any) -> dict[str, Any]:
    """POST /settings/advanced {key,value}. Saves the allowlisted object only. applied stays false."""

    def _write() -> dict[str, Any]:
        import copy

        checked = _require_advanced_key(key)
        if not isinstance(value, dict):
            fail(400, "advanced_invalid", "需要一个对象")
        _require_secret_policy(mod)
        _profile_facts(mod)
        plan = advanced_plan(checked)
        try:
            with write_lock(mod):
                stored, present = _stored_object(checked)
                if present and not isinstance(stored, dict):
                    fail(400, "advanced_invalid", "已存储的值不是对象，未覆盖")
                merged = merge_advanced(
                    mod,
                    {} if not present else stored,
                    value,
                    force_mask=plan == "masked-advanced",
                )
                try:
                    material = copy.deepcopy(merged)
                except Exception as exc:
                    log.warning("advanced copy failed: %s", type(exc).__name__)
                    fail(500, "advanced_unverified", "写入没有确认成功，不能当成已保存或已生效")
                _write_object(checked, material)
                with _LOCK:
                    _CACHE.update(stamp=None, data=None, home=None)
                seen, seen_present = _stored_object(checked)
                if not seen_present or not _same_stored(seen, merged):
                    fail(500, "advanced_unverified", "已提交但回读与写入不一致，不能当成已生效")
        except ConfigError:
            raise
        except SystemExit as exc:
            log.warning("advanced write blocked SystemExit: %s", type(exc).__name__)
            fail(500, "core_exit_blocked", "核心试图退出进程，已拦截，未当成成功")
        except Exception as exc:
            log.warning("advanced write failed: %s", type(exc).__name__)
            fail(500, "advanced_unverified", "写入没有确认成功，不能当成已保存或已生效")
        viewed = _advanced_view(mod, checked, merged, True, saved=True)
        from clawd_server.admin_config import effect_text

        viewed["output"] = "已写入本进程 profile 的配置文件。" + effect_text(_effect_body(checked, saved=True))
        return viewed

    return run(mod, _write)
