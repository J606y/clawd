"""Replace the sidecar's task-live functions with the browser tool registry.

``install(mod)`` is the only cutover. It does not open ``PANEL_LIVE_TASK_DB``
and it does not import ``live_task_store``. The parent binds this onto the
sidecar module; this file does not edit the sidecar source.

Frames and input use the endpoint and target the tool registered. A missing
identity, endpoint, or target is an error. There is no visible-page or desktop
VNC fallback. The 45-second lease stays on the sidecar's lease objects so the
existing global-live checks still see it; the lock file is the profile gate.
"""

from __future__ import annotations

import re
import threading
import time
from typing import Any

_ID_RE = re.compile(r"[A-Za-z0-9_.:@-]{1,200}")
_HARD_LEASE_S = 300.0
_MAX_TEXT = 2000
_KEYS = {
    "Enter": ("Enter", 13),
    "Backspace": ("Backspace", 8),
    "Tab": ("Tab", 9),
    "Escape": ("Escape", 27),
}
_REQUIRED = (
    "ApiError", "_LIVE_LOCK", "_live_task_lease", "_live", "LIVE_LEASE_S", "_live_client_id",
    "_live_task_drop", "_live_task_expire", "_live_expire", "_live_task_check_renewal",
    "_live_task_require_owner", "_live_task_timeout", "_LIVE_CAPPED_CLIENTS", "redact",
    "_LIVE_FRAME_SLOTS",
)


def install(mod) -> None:
    """Replace task-live entry points. Raises before replacing any if the sidecar lacks one."""
    missing = [name for name in _REQUIRED if not hasattr(mod, name)]
    if missing:
        raise RuntimeError("browser_native.install missing sidecar symbols: " + ", ".join(missing))
    mod.live_task = lambda session_id, task_id="", client_id=None: live_task(mod, session_id, task_id, client_id)
    mod.live_task_control = lambda body: live_task_control(mod, body)
    mod.live_task_input = lambda body: live_task_input(mod, body)
    mod._live_task_store = lambda: _retired(mod)
    mod._live_task_open_gate = lambda: _retired(mod)
    mod._clawd_browser_native = ("live_task", "live_task_control", "live_task_input", "_live_task_store")


def _retired(mod):
    raise mod.ApiError(503, "live_registry_retired", "任务窗口不再读取影子登记库")


def _ids(mod, *values: object) -> None:
    for value in values:
        if not isinstance(value, str) or _ID_RE.fullmatch(value) is None:
            raise mod.ApiError(400, "invalid_live_task", "任务、会话或目标页标识不合法")


def _registry_error(mod, exc: Exception) -> None:
    raise mod.ApiError(503, "live_registry_unavailable", "任务窗口登记服务不可用") from exc


def _view(mod, task: dict[str, Any] | None, session_id: str, client_id: str | None) -> dict[str, Any]:
    task_id = str((task or {}).get("taskId") or "")
    status = "ended" if task and task.get("status") in ("ended", "closing") else "waiting"
    with mod._LIVE_LOCK:
        remaining = mod._live_task_expire()
        global_remaining = mod._live_expire()
        lease = mod._live_task_lease
        if (status == "ended" and remaining > 0 and lease.get("sessionId") == session_id
                and (not task_id or lease.get("taskId") == task_id)):
            mod._live_task_drop()
            lease["until"] = 0.0
            remaining = 0.0
        same = bool(remaining > 0 and lease.get("sessionId") == session_id and task_id and lease.get("taskId") == task_id)
        yours = bool(same and client_id and lease.get("clientId") == client_id)
        taken = bool(same or (global_remaining > 0 and (not client_id or mod._live.get("clientId") != client_id)))
    return {
        "taskId": task_id, "sessionId": session_id, "status": status, "targetId": None, "title": "",
        "image": None, "cleanupError": str((task or {}).get("cleanupError") or "")[:300],
        "takenOver": taken, "controlledByYou": yours, "leaseRemainingSeconds": remaining if same else 0.0,
    }


def _load(mod, session_id: str, task_id: str):
    from tools.browser_task_registry import RegistryError, session_view

    try:
        task = session_view(session_id, task_id)
    except RegistryError as exc:
        _registry_error(mod, exc)
    if task and (task.get("sessionId") != session_id or (task_id and task.get("taskId") != task_id)):
        raise mod.ApiError(409, "live_task_mismatch", "任务窗口归属不匹配")
    return task


def live_task(mod, session_id: str, task_id: str = "", client_id: str | None = None) -> dict[str, Any]:
    if (not isinstance(session_id, str) or _ID_RE.fullmatch(session_id) is None
            or not isinstance(task_id, str) or (task_id and _ID_RE.fullmatch(task_id) is None)):
        raise mod.ApiError(400, "invalid_live_task", "任务标识不合法")
    if client_id is not None:
        client_id = mod._live_client_id(client_id)
    task = _load(mod, session_id, task_id)
    if not task_id and task and task.get("status") in ("ended", "closing"):
        task = None
    result = _view(mod, task, session_id, client_id)
    if not task or task.get("status") != "active" or not task.get("targetId") or not task.get("endpoint"):
        return result
    if not mod._LIVE_FRAME_SLOTS.acquire(blocking=False):
        raise mod.ApiError(503, "live_frame_busy", "画面服务繁忙，请稍后重试")
    try:
        return _frame(mod, task, session_id, result, client_id)
    finally:
        mod._LIVE_FRAME_SLOTS.release()


def _supervisor_for(task_id: str, endpoint: str):
    from tools.browser_supervisor import SUPERVISOR_REGISTRY
    supervisor = SUPERVISOR_REGISTRY.get(task_id)
    if supervisor is None or supervisor.cdp_url != endpoint:
        return None
    return supervisor


def _harness_of(task: dict[str, Any]) -> tuple[str, str, int] | None:
    directory, stem, pid = str(task.get("daemonDir") or ""), str(task.get("daemonStem") or ""), int(task.get("daemonPid") or 0)
    endpoint = str(task.get("endpoint") or "")
    if directory and stem and pid > 0 and (task.get("transport") == "harness" or endpoint == "harness" or endpoint.startswith("wss://")):
        return directory, stem, pid
    return None


def _frame(mod, task: dict[str, Any], session_id: str, result: dict[str, Any], client_id: str | None) -> dict[str, Any]:
    from tools.browser_task_cdp import CdpError, capture_jpeg, page_ws
    from tools.browser_task_registry import owns

    endpoint, target_id, browser_id = task.get("endpoint"), task.get("targetId"), task.get("browserId")
    if not endpoint or not target_id or not browser_id:
        raise mod.ApiError(409, "live_target_unowned", "目标页面不属于本任务")
    if not owns(session_id, task["taskId"], target_id, browser_id):
        raise mod.ApiError(409, "live_target_unowned", "目标页面不属于本任务")
    try:
        locator = _harness_of(task)
        if str(endpoint).startswith("wss://") or endpoint == "harness" or task.get("transport") == "harness":
            if locator is not None:
                from tools.browser_task_cdp import capture_harness_jpeg
                image, title = capture_harness_jpeg(*locator, target_id)
            else:
                supervisor = _supervisor_for(task["taskId"], endpoint) if str(endpoint).startswith("wss://") else None
                if supervisor is None:
                    raise mod.ApiError(502, "live_supervisor_required", "云端画面只能走持有签名连接的 daemon，不能新建静态连接")
                image, title = supervisor.capture_owned(target_id)
        else:
            ws_url, title = page_ws(endpoint, target_id, browser_id)
            image = capture_jpeg(ws_url)
    except mod.ApiError:
        raise
    except (CdpError, RuntimeError) as exc:
        if isinstance(exc, CdpError) and exc.code == "target_closed":
            return result
        if isinstance(exc, CdpError) and exc.code == "browser_replaced":
            raise mod.ApiError(409, "live_browser_replaced", "任务浏览器已变化，不显示其他窗口") from exc
        raise mod.ApiError(502, "live_frame_unavailable", "任务画面暂不可用") from exc
    current = _load(mod, session_id, task["taskId"])
    if (not current or current.get("status") != "active" or current.get("targetId") != target_id
            or current.get("browserId") != browser_id or current.get("endpoint") != endpoint):
        return _view(mod, current, session_id, client_id)
    if not owns(session_id, task["taskId"], target_id, browser_id):
        raise mod.ApiError(409, "live_target_unowned", "任务页面归属已变化")
    refreshed = _view(mod, current, session_id, client_id)
    result.update(refreshed, status="active", targetId=target_id, title=mod.redact(title),
                  image="data:image/jpeg;base64," + image)
    return result


def _verify(mod, session_id: str, task_id: str, target_id: str) -> dict[str, Any]:
    from tools.browser_task_cdp import CdpError, page_ws
    from tools.browser_task_registry import owns

    task = _load(mod, session_id, task_id)
    if not task or task.get("sessionId") != session_id or task.get("taskId") != task_id:
        raise mod.ApiError(409, "live_task_mismatch", "任务窗口归属不匹配")
    if task.get("status") != "active" or not task.get("targetId") or not task.get("endpoint"):
        raise mod.ApiError(409, "live_task_ended", "任务已结束或页面已关闭，不能接管或输入")
    if task.get("targetId") != target_id:
        raise mod.ApiError(409, "live_target_changed", "任务当前目标页面已变化，请等待画面刷新后重试")
    browser_id = str(task.get("browserId") or "")
    if not owns(session_id, task_id, target_id, browser_id):
        raise mod.ApiError(409, "live_target_unowned", "目标页面不属于本任务")
    if str(task.get("endpoint") or "").startswith("wss://") or task.get("endpoint") == "harness" or task.get("transport") == "harness":
        if _harness_of(task) is not None:
            task["transport"] = "harness"
            return task
        if str(task.get("endpoint") or "").startswith("wss://") and _supervisor_for(task_id, task["endpoint"]) is not None:
            task["transport"] = "supervisor"
            return task
        raise mod.ApiError(502, "live_supervisor_required", "云端控制只能走持有签名连接的 daemon，不能新建静态连接")
    try:
        ws_url, _title = page_ws(task["endpoint"], target_id, browser_id)
    except CdpError as exc:
        if exc.code == "browser_replaced":
            raise mod.ApiError(409, "live_browser_replaced", "任务浏览器已变化，不显示或控制其他窗口") from exc
        if exc.code == "target_closed":
            raise mod.ApiError(409, "live_target_closed", "目标页面已关闭") from exc
        raise mod.ApiError(502, "live_browser_unavailable", "任务浏览器暂不可用") from exc
    task["wsUrl"] = ws_url
    return task


def live_task_control(mod, body: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise mod.ApiError(400, "invalid_body", "请求体需为 JSON 对象")
    action = body.get("action")
    client_id = mod._live_client_id(body.get("clientId"))
    if action == "release":
        with mod._LIVE_LOCK:
            if mod._live_task_expire() and mod._live_task_lease.get("clientId") != client_id:
                raise mod.ApiError(409, "live_owned_elsewhere", "控制权已由另一个窗口持有")
            if mod._live_task_expire():
                mod._live_task_drop()
                mod._live_task_lease["until"] = 0.0
        return {"ok": True, "controlledByYou": False}
    if action != "takeover":
        raise mod.ApiError(400, "invalid_action", "action 必须为 takeover 或 release")
    session_id, task_id, target_id = body.get("sessionId", ""), body.get("taskId", ""), body.get("targetId", "")
    _ids(mod, session_id, task_id, target_id)
    with mod._LIVE_LOCK:
        mod._live_task_check_renewal(client_id, session_id, task_id, target_id)
    task = _verify(mod, session_id, task_id, target_id)
    browser_id = task["browserId"]
    from tools.browser_task_registry import RegistryError, acquire_lease_gate

    with mod._LIVE_LOCK:
        mod._live_task_check_renewal(client_id, session_id, task_id, target_id, browser_id)
        gate = mod._live_task_lease.get("gate")
        if gate is None:
            try:
                gate = acquire_lease_gate()
            except (RegistryError, OSError) as exc:
                raise mod.ApiError(503, "live_gate_unavailable", "浏览器执行锁不可用，未授予控制权") from exc
            if gate is None:
                return {"ok": True, "pending": True, "controlledByYou": False, "taskId": task_id, "targetId": target_id}
            mod._live_task_lease["gate"] = gate
            mod._live_task_lease["hardUntil"] = time.monotonic() + _HARD_LEASE_S
        from tools.browser_task_registry import RegistryError, session_view
        try:
            current = session_view(session_id, task_id)
            if (not current or current.get("status") != "active" or current.get("targetId") != target_id
                    or current.get("browserId") != browser_id):
                raise mod.ApiError(409, "live_task_ended", "任务已结束，未授予控制权")
        except mod.ApiError:
            mod._live_task_drop()
            raise
        except Exception as exc:
            mod._live_task_drop()
            _registry_error(mod, exc)
        previous = mod._live_task_lease.get("timer")
        if previous is not None:
            previous.cancel()
        until = min(time.monotonic() + float(mod.LIVE_LEASE_S), mod._live_task_lease["hardUntil"])
        if until <= time.monotonic():
            mod._live_task_drop()
            raise mod.ApiError(409, "live_control_limit", "本次接管已满五分钟，已交还浏览器")
        if mod._live_expire() and mod._live.get("clientId") == client_id:
            mod._live.clear()
            mod._live["until"] = 0.0
        mod._live_task_lease.update(until=until, clientId=client_id, sessionId=session_id, taskId=task_id,
                                    targetId=target_id, browserId=browser_id)
        timer = threading.Timer(max(0.001, until - time.monotonic()), mod._live_task_timeout, (gate, until))
        timer.daemon = True
        mod._live_task_lease["timer"] = timer
        timer.start()
    return {
        "ok": True, "takenOver": True, "controlledByYou": True, "leaseSeconds": mod.LIVE_LEASE_S,
        "leaseRemainingSeconds": float(mod.LIVE_LEASE_S), "taskId": task_id, "targetId": target_id,
    }


def _reverify(mod, session_id: str, task_id: str, target_id: str, browser_id: str) -> None:
    current = _verify(mod, session_id, task_id, target_id)
    if current.get("browserId") != browser_id:
        with mod._LIVE_LOCK:
            if (mod._live_task_expire() and mod._live_task_lease.get("sessionId") == session_id
                    and mod._live_task_lease.get("taskId") == task_id):
                mod._live_task_drop()
                mod._live_task_lease["until"] = 0.0
        raise mod.ApiError(409, "live_task_ended", "任务已结束或页面归属已变化，已拒绝输入")


def _cloud_input(mod, task, session_id, task_id, target_id, browser_id, client_id, kind,
                 x_ratio, y_ratio, delta_x, delta_y, text, key_name):
    locator = _harness_of(task)
    supervisor = None if locator else _supervisor_for(task_id, task["endpoint"])
    if locator is None and supervisor is None:
        raise mod.ApiError(502, "live_supervisor_required", "云端输入只能走持有签名连接的 daemon，不能新建静态连接")

    def call(method, params, bound=locator):
        if bound is not None:
            from tools.browser_task_cdp import CdpError, harness_call, harness_ping
            try:
                report = harness_ping(bound[0], bound[1])
            except CdpError as exc:
                raise mod.ApiError(502, "live_browser_unavailable", "任务浏览器暂不可用") from exc
            if report["pid"] != bound[2]:
                raise mod.ApiError(409, "live_browser_replaced", "任务浏览器已变化，不显示或控制其他窗口")
            return harness_call(*bound, target_id, method, params)
        return supervisor.call_owned(target_id, method, params)

    try:
        if kind in ("click", "scroll"):
            metrics = call("Page.getLayoutMetrics", {})
            viewport = metrics.get("cssVisualViewport") or metrics.get("visualViewport") or metrics.get("layoutViewport") or {}
            width = float(viewport.get("clientWidth") or 0) or 1280.0
            height = float(viewport.get("clientHeight") or 0) or 720.0
            x = round(max(0.0, min(width - 1.0, x_ratio * width)), 2)
            y = round(max(0.0, min(height - 1.0, y_ratio * height)), 2)
            _reverify(mod, session_id, task_id, target_id, browser_id)
            fresh = _load(mod, session_id, task_id) or {}
            bound = _harness_of(fresh)
            if bound is None and _supervisor_for(task_id, task["endpoint"]) is None:
                raise mod.ApiError(502, "live_supervisor_required", "云端输入只能走持有签名连接的 daemon，不能新建静态连接")
            with mod._LIVE_LOCK:
                mod._live_task_require_owner(client_id, session_id, task_id, target_id, browser_id)
                if kind == "click":
                    call("Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1}, bound)
                    call("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1}, bound)
                else:
                    call("Input.dispatchMouseEvent", {"type": "mouseWheel", "x": x, "y": y, "deltaX": delta_x, "deltaY": delta_y}, bound)
            return {"ok": True, "x": x, "y": y}
        _reverify(mod, session_id, task_id, target_id, browser_id)
        bound = _harness_of(_load(mod, session_id, task_id) or {})
        with mod._LIVE_LOCK:
            mod._live_task_require_owner(client_id, session_id, task_id, target_id, browser_id)
            if kind == "text":
                call("Input.insertText", {"text": text}, bound)
                return {"ok": True, "inserted": True}
            code, vk = _KEYS[key_name]
            down = {"type": "keyDown", "key": key_name, "code": code, "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk}
            if key_name == "Enter":
                down["text"] = "\r"
                down["unmodifiedText"] = "\r"
            call("Input.dispatchKeyEvent", down, bound)
            call("Input.dispatchKeyEvent", {"type": "keyUp", "key": key_name, "code": code, "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk}, bound)
        return {"ok": True, "key": key_name}
    except mod.ApiError:
        raise
    except Exception as exc:
        raise mod.ApiError(502, "live_input_failed", "任务页面拒绝了输入") from exc


def live_task_input(mod, body: dict[str, Any]) -> dict[str, Any]:
    from tools.browser_task_cdp import CdpError, PageSocket

    if not isinstance(body, dict):
        raise mod.ApiError(400, "invalid_body", "请求体需为 JSON 对象")
    client_id = mod._live_client_id(body.get("clientId"))
    session_id, task_id, target_id = body.get("sessionId", ""), body.get("taskId", ""), body.get("targetId", "")
    kind = body.get("kind", "")
    _ids(mod, session_id, task_id, target_id)
    if kind not in ("click", "scroll", "text", "key"):
        raise mod.ApiError(400, "invalid_input_kind", "输入类型不合法")
    x_ratio = y_ratio = delta_x = delta_y = 0.0
    text = key_name = ""
    if kind in ("click", "scroll"):
        xr, yr = body.get("xRatio"), body.get("yRatio")
        if type(xr) not in (int, float) or type(yr) not in (int, float) or not (0.0 <= float(xr) <= 1.0 and 0.0 <= float(yr) <= 1.0):
            raise mod.ApiError(400, "invalid_coordinates", "坐标比例需在 0 到 1 之间")
        x_ratio, y_ratio = float(xr), float(yr)
        if kind == "scroll":
            dx, dy = body.get("deltaX", 0), body.get("deltaY", 0)
            if (type(dx) not in (int, float) or type(dy) not in (int, float)
                    or not (-4000.0 <= float(dx) <= 4000.0 and -4000.0 <= float(dy) <= 4000.0)):
                raise mod.ApiError(400, "invalid_scroll", "滚动增量超出范围")
            delta_x, delta_y = float(dx), float(dy)
    elif kind == "text":
        raw = body.get("text")
        if not isinstance(raw, str) or not raw or len(raw) > _MAX_TEXT:
            raise mod.ApiError(400, "invalid_text", f"文字需为 1–{_MAX_TEXT} 个字符")
        text = raw
    else:
        raw_key = body.get("key")
        if not isinstance(raw_key, str) or raw_key not in _KEYS:
            raise mod.ApiError(400, "invalid_key", "仅支持 Enter、Backspace、Tab、Escape")
        key_name = raw_key
    task = _verify(mod, session_id, task_id, target_id)
    browser_id = task["browserId"]
    with mod._LIVE_LOCK:
        mod._live_task_require_owner(client_id, session_id, task_id, target_id, browser_id)
    if task.get("transport") in {"supervisor", "harness"}:
        return _cloud_input(mod, task, session_id, task_id, target_id, browser_id, client_id, kind,
                            x_ratio, y_ratio, delta_x, delta_y, text, key_name)
    socket = PageSocket(task["wsUrl"], timeout=3.0)
    try:
        if kind in ("click", "scroll"):
            metrics = socket.call("Page.getLayoutMetrics")
            viewport = metrics.get("cssVisualViewport") or metrics.get("visualViewport") or metrics.get("layoutViewport") or {}
            width = float(viewport.get("clientWidth") or 0) or 1280.0
            height = float(viewport.get("clientHeight") or 0) or 720.0
            if width <= 0 or height <= 0:
                width, height = 1280.0, 720.0
            x = round(max(0.0, min(width - 1.0, x_ratio * width)), 2)
            y = round(max(0.0, min(height - 1.0, y_ratio * height)), 2)
            _reverify(mod, session_id, task_id, target_id, browser_id)
            with mod._LIVE_LOCK:
                mod._live_task_require_owner(client_id, session_id, task_id, target_id, browser_id)
                if kind == "click":
                    socket.call("Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1})
                    socket.call("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1})
                else:
                    socket.call("Input.dispatchMouseEvent", {"type": "mouseWheel", "x": x, "y": y, "deltaX": delta_x, "deltaY": delta_y})
            return {"ok": True, "x": x, "y": y}
        _reverify(mod, session_id, task_id, target_id, browser_id)
        with mod._LIVE_LOCK:
            mod._live_task_require_owner(client_id, session_id, task_id, target_id, browser_id)
            if kind == "text":
                socket.call("Input.insertText", {"text": text})
                return {"ok": True, "inserted": True}
            code, vk = _KEYS[key_name]
            down = {"type": "keyDown", "key": key_name, "code": code, "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk}
            if key_name == "Enter":
                down["text"] = "\r"
                down["unmodifiedText"] = "\r"
            socket.call("Input.dispatchKeyEvent", down)
            socket.call("Input.dispatchKeyEvent", {"type": "keyUp", "key": key_name, "code": code,
                                                   "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk})
        return {"ok": True, "key": key_name}
    except CdpError as exc:
        raise mod.ApiError(502, "live_input_failed", "任务页面拒绝了输入") from exc
    finally:
        socket.close()
