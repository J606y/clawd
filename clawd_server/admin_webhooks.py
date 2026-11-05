"""Webhook subscriptions via hermes_cli.webhook store helpers.

Delivery-log scraping is not a core read. recent_deliveries fails closed.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
import urllib.request
from typing import Any

from clawd_server.admin_common import WEBHOOK_NAME_RE, WEBHOOK_TOKEN_RE, fail, redact_text, run, write_lock

log = logging.getLogger("clawd.admin")


def _platform_enabled() -> bool:
    from clawd_server.admin_config import optional

    cfg = optional("platforms.webhook")
    return isinstance(cfg, dict) and cfg.get("enabled") is True


def _view(mod: Any, name: str, spec: Any) -> dict[str, Any]:
    spec = spec if isinstance(spec, dict) else {}
    events = spec.get("events")
    prompt = spec.get("prompt")
    description = redact_text(mod, str(spec.get("description") or ""), 300) or ""
    return {
        "name": name,
        "description": description,
        "events": [str(item) for item in events] if isinstance(events, list) else [],
        "deliver": str(spec["deliver"]) if spec.get("deliver") else None,
        "deliverOnly": bool(spec.get("deliver_only")),
        "path": f"/webhooks/{name}",
        "enabled": spec.get("enabled") is not False,
        "secretSet": bool(spec.get("secret")),
        "hasScript": bool(spec.get("script")),
        "createdAt": spec.get("created_at") if isinstance(spec.get("created_at"), str) else None,
        "prompt": redact_text(mod, prompt, 400) if isinstance(prompt, str) else "",
    }


def list_webhooks(mod: Any) -> dict[str, Any]:
    def _read() -> dict[str, Any]:
        from hermes_cli.webhook import _load_subscriptions

        from clawd_server.admin_common import panel_home

        panel_home(mod)
        subs = _load_subscriptions()
        return {"platformEnabled": _platform_enabled(), "data": [_view(mod, name, spec) for name, spec in sorted(subs.items())]}

    return run(mod, _read)


def recent_deliveries(mod: Any, route: str, limit: int) -> dict[str, Any]:
    del route, limit

    def _refuse() -> dict[str, Any]:
        fail(501, "webhook_deliveries_unavailable", "投递记录属于宿主网关日志，没有进程内读接口。未支持，不返回空列表。")

    return run(mod, _refuse)


def create_webhook(mod: Any, body: dict[str, Any]) -> dict[str, Any]:
    def _write() -> dict[str, Any]:
        import secrets

        from hermes_cli.webhook import _MISSING_SUBSCRIPTION, _load_subscriptions, _replace_subscription

        if not isinstance(body, dict):
            fail(400, "invalid_body", "需要对象")
        name, prompt = body.get("name"), body.get("prompt")
        if not isinstance(name, str) or not WEBHOOK_NAME_RE.match(name):
            fail(400, "invalid_name", "名称需为小写字母、数字、下划线或连字符")
        route_name = name
        if not isinstance(prompt, str) or not 0 < len(prompt.strip()) <= 4000:
            fail(400, "invalid_prompt", "需要 4000 字以内的提示词")
        prompt_text = prompt.strip()
        description, deliver, events = body.get("description"), body.get("deliver"), body.get("events")
        if description and (not isinstance(description, str) or len(description) > 200):
            fail(400, "invalid_description", "描述需为 200 字以内的文本")
        if deliver and (not isinstance(deliver, str) or not WEBHOOK_TOKEN_RE.match(deliver)):
            fail(400, "invalid_deliver", "投递目标不合法")
        if events and (not isinstance(events, list) or len(events) > 20 or not all(isinstance(item, str) and WEBHOOK_TOKEN_RE.match(item) for item in events)):
            fail(400, "invalid_events", "事件列表不合法")
        from clawd_server.admin_common import panel_home

        panel_home(mod)
        secret = secrets.token_urlsafe(32)
        route = {
            "description": description if isinstance(description, str) and description else f"Agent-created subscription: {route_name}",
            "events": list(events) if isinstance(events, list) else [],
            "secret": secret,
            "prompt": prompt_text,
            "skills": [],
            "deliver": deliver if isinstance(deliver, str) and deliver else "log",
            "profile": "default",
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        with write_lock(mod):
            if not _platform_enabled():
                fail(409, "platform_disabled", "Webhook 平台未开启，请先在服务器上开启并重启网关")
            if route_name in _load_subscriptions():
                fail(409, "webhook_exists", "同名 Webhook 已存在")
            try:
                _replace_subscription(route_name, route, _MISSING_SUBSCRIPTION)
            except Exception as exc:
                log.warning("webhook create failed: %s", type(exc).__name__)
                fail(500, "webhook_create_failed", "创建失败")
            created = route_name in _load_subscriptions()
        if not created:
            fail(500, "webhook_create_unverified", "已提交但订阅文件里没有新路由，请刷新确认")
        return {"ok": True, "name": route_name, "path": f"/webhooks/{route_name}", "secret": secret}

    return run(mod, _write)


def delete_webhook(mod: Any, name: Any) -> dict[str, Any]:
    def _write() -> dict[str, Any]:
        from hermes_cli.webhook import _load_subscriptions, _mutate_subscriptions

        if not isinstance(name, str) or not WEBHOOK_NAME_RE.match(name):
            fail(400, "invalid_name", "名称不合法")
        from clawd_server.admin_common import panel_home

        panel_home(mod)

        def remove(subscriptions: dict[str, dict]) -> None:
            if name not in subscriptions:
                raise KeyError(name)
            del subscriptions[name]

        with write_lock(mod):
            if name not in _load_subscriptions():
                fail(404, "webhook_not_found", "Webhook 不存在")
            try:
                _mutate_subscriptions(remove)
            except KeyError as exc:
                fail(404, "webhook_not_found", "Webhook 不存在")
                raise AssertionError(exc)
            except Exception as exc:
                log.warning("webhook delete failed: %s", type(exc).__name__)
                fail(500, "webhook_delete_failed", "删除失败")
            still = name in _load_subscriptions()
        if still:
            fail(500, "webhook_delete_unverified", "已提交但路由仍在，请刷新确认")
        return {"ok": True, "name": name}

    return run(mod, _write)


def set_webhook_enabled(mod: Any, name: Any, enabled: Any) -> dict[str, Any]:
    def _write() -> dict[str, Any]:
        from hermes_cli.webhook import _load_subscriptions, _mutate_subscriptions

        if not isinstance(name, str) or not WEBHOOK_NAME_RE.match(name) or not isinstance(enabled, bool):
            fail(400, "invalid_body", "需要名称与布尔 enabled")
        from clawd_server.admin_common import panel_home

        panel_home(mod)

        def mutate(subscriptions: dict[str, dict]) -> None:
            current = subscriptions.get(name)
            if not isinstance(current, dict):
                raise KeyError(name)
            current["enabled"] = enabled

        with write_lock(mod):
            try:
                _mutate_subscriptions(mutate)
            except KeyError as exc:
                fail(404, "webhook_not_found", "Webhook 不存在")
                raise AssertionError(exc)
            after = _load_subscriptions().get(name)
        if not isinstance(after, dict) or (after.get("enabled") is not False) != enabled:
            fail(500, "webhook_toggle_unverified", "已提交但回读结果不一致，请刷新确认")
        return {"ok": True, "name": name, "enabled": enabled}

    return run(mod, _write)


def test_webhook(mod: Any, name: Any) -> dict[str, Any]:
    def _post() -> dict[str, Any]:
        from hermes_cli.webhook import _load_subscriptions, _route_url

        if not isinstance(name, str) or not WEBHOOK_NAME_RE.match(name):
            fail(400, "invalid_name", "名称不合法")
        from clawd_server.admin_common import panel_home

        panel_home(mod)
        subs = _load_subscriptions()
        if name not in subs:
            fail(404, "webhook_not_found", "Webhook 不存在")
        route = subs[name] if isinstance(subs[name], dict) else {}
        raw_secret = route.get("secret")
        secret_value = raw_secret if isinstance(raw_secret, str) else ""
        url = _route_url(name, route)
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            return {"ok": False, "output": "Webhook 地址不是 http(s)，未发送"}
        payload = '{"test": true, "event_type": "test", "message": "Hello from clawd webhook test"}'
        signature = "sha256=" + hmac.new(secret_value.encode(), payload.encode(), hashlib.sha256).hexdigest()
        request = urllib.request.Request(
            url,
            data=payload.encode(),
            headers={"Content-Type": "application/json", "X-Hub-Signature-256": signature, "X-GitHub-Event": "test"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                body = response.read(4096).decode("utf-8", errors="replace")
                text = f"Response ({response.status}): {body}"
                return {"ok": 200 <= response.status < 300, "output": redact_text(mod, text, 2000) or ""}
        except Exception as exc:
            text = redact_text(mod, f"{type(exc).__name__}: {exc}", 2000) or "请求失败"
            return {"ok": False, "output": text}

    return run(mod, _post)
