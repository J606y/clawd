"""Native (no harness daemon) execution of ``browser_exec`` code.

Runs model code in-process against the core's own CDP supervisor
(``tools.browser_supervisor.CDPSupervisor``) instead of the vendored
``browser_harness`` daemon subprocess. Applies only to the local live
browser (loopback ``127.0.0.1:39234``); every other backend (cloud
``wss://``, autospawn, Lightpanda, real profile, hybrid sidecar) keeps
using the harness path in ``tools/browser_use_cli.py``.

Why this exists: the harness executes helpers as synchronous IPC requests
to the daemon with a 5 s read timeout, so a slow ``Target.attachToTarget``
(cold Chromium after the 10 s idle auto-close on a 2-core box) fails even
though the browser is alive. The supervisor's CDP calls use 8-15 s
timeouts, and the worker is woken *before* the code runs
(:func:`ensure_live_browser`), so the attach budget is not eaten by a
cold start.

Guard semantics preserved (same as the harness path):
- one blank page is created per call via ``tools.browser_task_registry``
  (``open_and_register``) and pinned to this task/session/turn;
- the supervisor is started with ``owner`` + ``attach_target`` + ``browser_id``,
  so ``call_owned``/``capture_owned`` only ever touch this task's own tab;
- the exec gate (``acquire_exec_gate``) is held for the whole call and the
  created target is adopted into the registry spool on completion
  (``adopt_spool``), exactly like ``_finish_owned``;
- a supervisor started by this call is stopped when it ends, so its
  websocket does not count as a viewer on the 39234 proxy and the
  worker can still idle-close after 10 s (a pre-existing supervisor is
  left alone; vault fill re-attaches on demand);
- no second websocket is ever opened to a signed cloud endpoint (native
  is loopback-only by construction).

Limitations (honest, not silent):
- ``iframe_target`` is not supported on the native path and raises a clear
  error; use the harness path for iframe sessions.
- ``wait_for_network_idle`` is approximated from ``document.readyState`` +
  stable resource count (the supervisor has no harness event buffer);
  ``drain_events`` returns ``[]``.
- Code runs in a worker thread; on timeout the call returns an error but
  the thread keeps running to completion in the background (same caveat
  the harness documents for its daemon).
"""

from __future__ import annotations

import base64
import contextlib
import io
import json
import logging
import os
import threading
import time
import traceback
import urllib.request
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

LIVE_PROXY_PORT = 39234
LIVE_PROXY_HTTP = f"http://127.0.0.1:{LIVE_PROXY_PORT}/json/version"
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


class NativeExecError(RuntimeError):
    """Native execution failed (or a helper is unsupported on this path)."""


# --------------------------------------------------------------------------
# Endpoint eligibility + wake
# --------------------------------------------------------------------------

def is_native_live_endpoint(cdp_url: str) -> bool:
    """True only for the local live-browser CDP proxy (loopback :39234)."""
    raw = (cdp_url or "").strip()
    if not raw:
        return False
    lowered = raw.lower()
    if lowered.startswith("wss://"):
        return False
    try:
        parts = urlsplit(raw if "://" in raw else "http://" + raw)
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    port = parts.port
    if host not in _LOOPBACK_HOSTS:
        return False
    if port is None:
        return False
    return port == LIVE_PROXY_PORT


def ensure_live_browser(deadline_s: float = 20.0) -> bool:
    """Wake the live worker (if idle-stopped) and wait until CDP answers.

    A request through the 39234 proxy is the runtime's own wake path
    (``/health`` and direct 39233 do not wake). Returns True once
    ``/json/version`` answers, False on timeout.
    """
    deadline = time.monotonic() + max(1.0, float(deadline_s))
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(LIVE_PROXY_HTTP, timeout=5) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


# --------------------------------------------------------------------------
# Helpers: same names the model code uses against the harness
# --------------------------------------------------------------------------

_KEY_CODES = {
    "Enter": 13, "Backspace": 8, "Tab": 9, "Escape": 27, "Delete": 46,
    "ArrowUp": 38, "ArrowDown": 40, "ArrowLeft": 37, "ArrowRight": 39,
    "Home": 36, "End": 35, "PageUp": 33, "PageDown": 34,
}
for _i in range(1, 13):
    _KEY_CODES[f"F{_i}"] = 111 + _i


class _NativeHelpers:
    """Harness-helper-compatible operations backed by CDPSupervisor."""

    def __init__(self, supervisor, target_id: str, browser_id: str,
                 owner: tuple[str, str, str], endpoint: str,
                 workspace: Optional[str]) -> None:
        self._sup = supervisor
        self._target = target_id
        self._initial_target = target_id
        self._browser_id = browser_id
        self._owner = owner
        self._endpoint = endpoint
        self.workspace = workspace
        self._stdout: Optional[io.StringIO] = None

    # -- low level ------------------------------------------------------
    def _call(self, method: str, params: Optional[dict] = None,
              target_id: Optional[str] = None) -> dict:
        return self._sup.call_owned(target_id or self._target, method, params or {})

    def _eval(self, expression: str, target_id: Optional[str] = None):
        result = self._call("Runtime.evaluate", {
            "expression": expression, "returnByValue": True,
            "awaitPromise": True, "userGesture": True,
        }, target_id=target_id)
        details = result.get("exceptionDetails")
        if details:
            desc = (details.get("exception") or {}).get("description") or details.get("text") or "JS exception"
            raise NativeExecError(f"js failed: {desc}")
        inner = result.get("result") or {}
        if "value" in inner:
            return inner["value"]
        return inner.get("description")

    # -- navigation / info ----------------------------------------------
    def goto_url(self, url: str):
        self._call("Page.navigate", {"url": url})
        self.wait_for_load()
        return True

    def new_tab(self, url: str = "about:blank"):
        from tools.browser_task_registry import register_created
        target_id = self._sup.create_blank_target()
        self._sup.remember_created(target_id)
        task, session, turn = self._owner
        register_created(task_id=task, session_id=session, turn_id=turn,
                         target_id=target_id, browser_id=self._browser_id,
                         endpoint=self._endpoint)
        self._target = target_id
        if url and url != "about:blank":
            self.goto_url(url)
        return {"targetId": target_id, "url": url}

    def page_info(self):
        return self._eval(
            "(() => ({title: document.title, url: location.href, "
            "readyState: document.readyState}))()")

    def js(self, expression, target_id=None):
        return self._eval(expression, target_id=target_id)

    def cdp(self, method, session_id=None, **params):
        target = None
        if session_id:
            # Harness passes a CDP session id; on the native path the only
            # meaningful override is another owned target id.
            target = str(session_id)
        return self._call(method, params, target_id=target)

    # -- input ----------------------------------------------------------
    def fill_input(self, selector, text, clear_first=True, timeout=0.0):
        if timeout:
            self.wait_for_element(selector, timeout=timeout)
        js_selector = json.dumps(str(selector))
        found = self._eval(
            f"(() => {{ const el = document.querySelector({js_selector}); "
            f"if (!el) return 'missing'; el.scrollIntoView({{block: 'center'}}); "
            f"el.focus(); return 'ok'; }})()")
        if found != "ok":
            raise NativeExecError(f"fill_input: no element matches {selector!r}")
        if clear_first:
            self._eval(
                f"(() => {{ const el = document.querySelector({js_selector}); "
                f"if ('value' in el) {{ el.value = ''; "
                f"el.dispatchEvent(new Event('input', {{bubbles: true}})); }} "
                f"try {{ el.select(); }} catch (e) {{}} return true; }})()")
        self._call("Input.insertText", {"text": str(text)})
        return True

    def type_text(self, text):
        self._call("Input.insertText", {"text": str(text)})
        return True

    def click_at_xy(self, x, y, button="left", clicks=1):
        for _ in range(max(1, int(clicks))):
            self._call("Input.dispatchMouseEvent", {
                "type": "mousePressed", "x": float(x), "y": float(y),
                "button": str(button), "clickCount": 1})
            self._call("Input.dispatchMouseEvent", {
                "type": "mouseReleased", "x": float(x), "y": float(y),
                "button": str(button), "clickCount": 1})
        return True

    def press_key(self, key, modifiers=0):
        code = _KEY_CODES.get(str(key), 0)
        text = ""
        if key == "Enter":
            text = "\r"
        elif isinstance(key, str) and len(key) == 1:
            text = key
        down = {"type": "keyDown", "key": str(key), "code": str(key),
                "windowsVirtualKeyCode": code, "nativeVirtualKeyCode": code,
                "modifiers": int(modifiers or 0)}
        if text:
            down["text"] = text
            down["unmodifiedText"] = text
        self._call("Input.dispatchKeyEvent", down)
        self._call("Input.dispatchKeyEvent", {
            "type": "keyUp", "key": str(key), "code": str(key),
            "windowsVirtualKeyCode": code, "nativeVirtualKeyCode": code,
            "modifiers": int(modifiers or 0)})
        return True

    def dispatch_key(self, selector, key="Enter", event="keypress"):
        js_selector = json.dumps(str(selector))
        found = self._eval(
            f"(() => {{ const el = document.querySelector({js_selector}); "
            f"if (!el) return 'missing'; el.focus(); return 'ok'; }})()")
        if found != "ok":
            raise NativeExecError(f"dispatch_key: no element matches {selector!r}")
        return self.press_key(key)

    def scroll(self, x, y, dy=-300, dx=0):
        self._call("Input.dispatchMouseEvent", {
            "type": "mouseWheel", "x": float(x), "y": float(y),
            "deltaX": float(dx), "deltaY": float(dy)})
        return True

    def upload_file(self, selector, path):
        doc = self._call("DOM.getDocument", {"depth": -1, "pierce": True})
        root = (doc.get("root") or {}).get("nodeId")
        if not root:
            raise NativeExecError("upload_file: no document root")
        node = self._call("DOM.querySelector", {"nodeId": root, "selector": str(selector)})
        node_id = node.get("nodeId")
        if not node_id:
            raise NativeExecError(f"upload_file: no element matches {selector!r}")
        self._call("DOM.setFileInputFiles", {"files": [str(path)], "nodeId": node_id})
        return True

    # -- waiting --------------------------------------------------------
    def wait(self, seconds=1.0):
        time.sleep(float(seconds))
        return True

    def wait_for_load(self, timeout=15.0):
        deadline = time.monotonic() + float(timeout)
        while time.monotonic() < deadline:
            try:
                if self._eval("document.readyState") == "complete":
                    return True
            except Exception:
                pass
            time.sleep(0.25)
        raise NativeExecError(f"wait_for_load timed out after {timeout}s")

    def wait_for_element(self, selector, timeout=10.0, visible=False):
        js_selector = json.dumps(str(selector))
        visible_js = "true" if visible else "false"
        check = (
            "(() => { const el = document.querySelector(" + js_selector
            + "); if (!el) return false; "
            + "if (!" + visible_js + ") return true; const r = el.getBoundingClientRect(); "
            + "return r.width > 0 && r.height > 0; })()"
        )
        deadline = time.monotonic() + float(timeout)
        while time.monotonic() < deadline:
            try:
                if self._eval(check):
                    return True
            except Exception:
                pass
            time.sleep(0.25)
        raise NativeExecError(f"wait_for_element timed out: {selector!r}")

    def wait_for_network_idle(self, timeout=10.0, idle_ms=500):
        # Approximation (no harness event buffer on this path): page complete
        # and the resource-entry count stable for idle_ms.
        deadline = time.monotonic() + float(timeout)
        last_count: Optional[int] = None
        stable_since: Optional[float] = None
        while time.monotonic() < deadline:
            try:
                state = self._eval(
                    "(() => ({ready: document.readyState, "
                    "n: performance.getEntriesByType('resource').length}))()")
            except Exception:
                state = {}
            count = state.get("n") if isinstance(state, dict) else None
            ready = isinstance(state, dict) and state.get("ready") == "complete"
            now = time.monotonic()
            if ready and count is not None and count == last_count:
                if stable_since is not None and (now - stable_since) * 1000 >= float(idle_ms):
                    return True
            else:
                last_count = count
                stable_since = now if ready else None
            time.sleep(0.2)
        return False

    # -- tabs -----------------------------------------------------------
    def list_tabs(self, include_chrome=True):
        result = self._sup.call_browser("Target.getTargets")
        infos = result.get("targetInfos") or []
        if not include_chrome:
            infos = [t for t in infos if t.get("type") == "page"]
        return infos

    def current_tab(self):
        for tab in self.list_tabs():
            if tab.get("targetId") == self._target:
                return tab
        return {"targetId": self._target}

    def _resolve_tab(self, target):
        if isinstance(target, dict):
            return str(target.get("targetId") or "")
        if isinstance(target, int):
            tabs = [t for t in self.list_tabs() if t.get("type") == "page"]
            if 0 <= target < len(tabs):
                return str(tabs[target]["targetId"])
            raise NativeExecError(f"switch_tab: no tab at index {target}")
        return str(target)

    def switch_tab(self, target, activate=False):
        target_id = self._resolve_tab(target)
        try:
            self._sup.remember_created(target_id)
        except Exception:
            pass  # already attached (initial owned target)
        self._target = target_id
        if activate:
            self._call("Page.bringToFront")
        return True

    def activate_tab(self, target):
        return self.switch_tab(target, activate=True)

    def close_tab(self, target=None):
        target_id = self._resolve_tab(target) if target is not None else self._target
        self._sup.close_exact(target_id, self._browser_id)
        if target_id == self._target:
            self._target = self._initial_target
        return True

    def ensure_real_tab(self):
        try:
            info = self.page_info()
            url = str((info or {}).get("url") or "")
        except Exception:
            url = ""
        if url.startswith("chrome://") or not url:
            self.new_tab("about:blank")
        return True

    # -- screenshots / misc ---------------------------------------------
    def capture_screenshot(self, path=None, full=False, max_dim=None):
        # capture_owned returns JPEG base64 + title; full/max_dim are
        # accepted for API parity (the supervisor always captures the
        # current viewport at quality 65).
        data_b64, _title = self._sup.capture_owned(self._target)
        raw = base64.b64decode(data_b64)
        if path:
            out = Path(str(path))
        else:
            base = Path(self.workspace) if self.workspace else Path("/tmp")
            base.mkdir(parents=True, exist_ok=True)
            out = base / f"screenshot-{int(time.time() * 1000)}.jpg"
        out.write_bytes(raw)
        print(str(out))
        return str(out)

    def http_get(self, url, headers=None, timeout=20.0):
        req = urllib.request.Request(url, headers=dict(headers or {}))
        with urllib.request.urlopen(req, timeout=float(timeout)) as resp:
            return resp.read().decode("utf-8", errors="replace")

    def drain_events(self):
        return []

    def iframe_target(self, url_substr):
        raise NativeExecError(
            "iframe_target is not supported on the native path; "
            "use the harness browser_exec path for iframe sessions")


# --------------------------------------------------------------------------
# Entry point (called from tools/browser_use_cli.py, loopback live only)
# --------------------------------------------------------------------------

def execute_native(code: str, *, task_id: str, session_id: str, turn_id: str,
                   cdp_url: str, timeout_s: int = 300, session_name: str = "",
                   route_key: str = "") -> str:
    """Run ``browser_exec`` code natively. Returns a ``tool_result`` string
    shaped like the harness dispatch (``success`` / ``output`` /
    ``screenshot_path``), so callers see no difference."""
    from tools.registry import tool_error, tool_result
    from tools.browser_task_registry import (
        RegistryError, acquire_exec_gate, adopt_spool, open_and_register,
        resolve_call_identity, spool_path)

    started = time.time()
    try:
        timeout = _clamp(timeout_s)
    except Exception:
        timeout = 300
    try:
        from tools.browser_use_cli import _workspace_dir
        workspace = _workspace_dir(task_id)
    except Exception:
        workspace = None

    # Wake the worker first: a cold start must not eat the attach budget.
    if not ensure_live_browser(deadline_s=20.0):
        return tool_error(
            "native browser_exec refused: live browser did not wake within 20s "
            "(CDP proxy 127.0.0.1:39234 did not answer)")

    try:
        task, session, turn = resolve_call_identity(task_id, session_id)
    except Exception as exc:
        return tool_error(f"native browser_exec refused: {exc}")

    gate = acquire_exec_gate()
    if gate is None:
        return tool_error(
            "native browser_exec refused: lease_held (manual control did not release)")
    owned_state: Optional[dict] = None
    # Release the per-task supervisor when this call ends (only if this call
    # started it): its websocket counts as a viewer on the 39234 proxy, and
    # leaving it open would keep the worker awake past the 10 s idle close.
    stop_supervisor = False
    try:
        from tools.browser_session_cdp import get_dialog_policy_config, resolve_cdp_override
        from tools.browser_supervisor import SUPERVISOR_REGISTRY
        ws_url = resolve_cdp_override(cdp_url)
        if not ws_url or str(ws_url).startswith("wss://"):
            return tool_error("native browser_exec refused: endpoint is not a local CDP URL")
        try:
            owned = open_and_register(cdp_url, task_id=task, session_id=session, turn_id=turn)
        except RegistryError as exc:
            return tool_error(f"native browser_exec refused: {exc}")
        except Exception as exc:
            return tool_error(f"native browser_exec refused: target_create_failed {type(exc).__name__}")
        owned_state = {
            "task_id": task, "session_id": session, "turn_id": turn,
            "browser_id": owned["browserId"], "endpoint": owned["endpoint"],
            "spool": spool_path(task),
        }
        policy, dialog_timeout = get_dialog_policy_config()
        try:
            stop_supervisor = SUPERVISOR_REGISTRY.get(task) is None
            supervisor = SUPERVISOR_REGISTRY.get_or_start(
                task_id=task, cdp_url=ws_url, dialog_policy=policy,
                dialog_timeout_s=dialog_timeout, owner=(task, session, turn),
                browser_id=owned["browserId"], attach_target=owned["targetId"])
        except Exception as exc:
            return tool_error(f"native browser_exec refused: supervisor attach failed: {exc}")

        helpers = _NativeHelpers(
            supervisor, owned["targetId"], owned["browserId"],
            (task, session, turn), owned["endpoint"], workspace)
        namespace: dict[str, Any] = {
            "__name__": "__main__",
            "workspace": workspace,
            "goto_url": helpers.goto_url,
            "new_tab": helpers.new_tab,
            "page_info": helpers.page_info,
            "js": helpers.js,
            "fill_input": helpers.fill_input,
            "click_at_xy": helpers.click_at_xy,
            "capture_screenshot": helpers.capture_screenshot,
            "cdp": helpers.cdp,
            "type_text": helpers.type_text,
            "press_key": helpers.press_key,
            "dispatch_key": helpers.dispatch_key,
            "scroll": helpers.scroll,
            "upload_file": helpers.upload_file,
            "wait": helpers.wait,
            "wait_for_load": helpers.wait_for_load,
            "wait_for_element": helpers.wait_for_element,
            "wait_for_network_idle": helpers.wait_for_network_idle,
            "list_tabs": helpers.list_tabs,
            "current_tab": helpers.current_tab,
            "switch_tab": helpers.switch_tab,
            "activate_tab": helpers.activate_tab,
            "close_tab": helpers.close_tab,
            "ensure_real_tab": helpers.ensure_real_tab,
            "http_get": helpers.http_get,
            "drain_events": helpers.drain_events,
            "iframe_target": helpers.iframe_target,
        }
        # agent_helpers.py in the workspace is auto-imported by the harness
        # path too; run it first so model code can use its functions.
        if workspace:
            agent_helpers = Path(workspace) / "agent_helpers.py"
            if agent_helpers.is_file():
                try:
                    exec(compile(agent_helpers.read_text(encoding="utf-8"),
                                 str(agent_helpers), "exec"), namespace)
                except Exception as exc:
                    return tool_error(
                        f"native browser_exec refused: agent_helpers.py failed: {exc}")

        stdout_buf = io.StringIO()
        outcome: dict[str, Any] = {}

        def _run() -> None:
            old_workspace = os.environ.get("BH_AGENT_WORKSPACE")
            try:
                if workspace:
                    os.environ["BH_AGENT_WORKSPACE"] = workspace
                with contextlib.redirect_stdout(stdout_buf):
                    exec(compile(code, "<browser_exec>", "exec"), namespace)
                outcome["ok"] = True
            except Exception:
                outcome["ok"] = False
                outcome["traceback"] = traceback.format_exc()
            finally:
                if old_workspace is None:
                    os.environ.pop("BH_AGENT_WORKSPACE", None)
                else:
                    os.environ["BH_AGENT_WORKSPACE"] = old_workspace

        worker = threading.Thread(target=_run, name="native-browser-exec", daemon=True)
        worker.start()
        worker.join(timeout)
        stdout_text = stdout_buf.getvalue()
        if worker.is_alive():
            return _finish_native(
                owned_state, gate, tool_error(
                    f"native browser exec timed out after {timeout}s. "
                    "The code may still be running in the background; retry "
                    "with a larger timeout_s or split the work across calls."),
                started)
        if not outcome.get("ok", False):
            tb = str(outcome.get("traceback") or "unknown error")
            result = {
                "success": False, "exit_code": 1,
                "output": stdout_text, "error": tb[-4000:],
            }
            if workspace:
                result["workspace"] = workspace
            if session_name:
                result["session"] = session_name
            return _finish_native(owned_state, gate, tool_result(result), started)

        result = {"success": True, "exit_code": 0, "output": stdout_text}
        if workspace:
            result["workspace"] = workspace
        if session_name:
            result["session"] = session_name
        try:
            from tools.browser_use_cli import _find_screenshot, _native_screenshot_result
            screenshot = _find_screenshot(stdout_text, started)
            if screenshot:
                result["screenshot_path"] = screenshot
                native = _native_screenshot_result(result, screenshot)
                if native is not None:
                    return _finish_native(owned_state, gate, native, started)
        except Exception:
            logger.debug("native screenshot attach skipped", exc_info=True)
        return _finish_native(owned_state, gate, tool_result(result), started)
    finally:
        if stop_supervisor:
            try:
                from tools.browser_supervisor import SUPERVISOR_REGISTRY as _REG
                _REG.stop(task)
            except Exception:
                pass
        # _finish_native already adopted the spool + closed the gate when it
        # ran (it marks owned_state["finished"]); this is only a safety net
        # for early returns above it.
        if owned_state is not None and not owned_state.get("finished") and gate is not None:
            try:
                adopt_spool(owned_state["spool"], task_id=owned_state["task_id"],
                            session_id=owned_state["session_id"],
                            turn_id=owned_state["turn_id"],
                            browser_id=owned_state["browser_id"],
                            endpoint=owned_state["endpoint"])
            except Exception:
                pass
            try:
                gate.close()
            except Exception:
                pass


def _finish_native(owned_state, gate, result, started) -> str:
    """Adopt the created target into the registry spool, close the gate."""
    if owned_state is not None:
        owned_state["finished"] = True
        try:
            from tools.browser_task_registry import adopt_spool
            adopt_spool(owned_state["spool"], task_id=owned_state["task_id"],
                        session_id=owned_state["session_id"],
                        turn_id=owned_state["turn_id"],
                        browser_id=owned_state["browser_id"],
                        endpoint=owned_state["endpoint"])
        except Exception as exc:
            logger.warning("native browser target spool was not registered: %s",
                           type(exc).__name__)
    if gate is not None:
        try:
            gate.close()
        except Exception:
            pass
    return result


def _clamp(timeout_s) -> int:
    try:
        from tools.browser_use_cli import _clamp_timeout
        return _clamp_timeout(timeout_s)
    except Exception:
        try:
            value = int(timeout_s)
        except (TypeError, ValueError):
            return 300
        return max(5, min(1800, value))
