"""``browser_exec``: drive a real browser through the Browser Use CLI engine (browser-harness,
https://browser-use.com), Hermes' only core browser tool.

The harness speaks CDP to whatever ``tools.browser_session`` resolved for the call: an operator
``BU_CDP_*`` endpoint, a ``/browser connect`` override, a cloud provider's session, or a local browser
(real profile, Lightpanda, or the packaged Chromium started by ``tools.browser_launcher``).
"""

import contextlib
import contextvars
import functools
import importlib
import importlib.util
import json
import hashlib
import logging
import os
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, NamedTuple, Optional

from hermes_cli.process_identity import (
    own_process_groups, register_child, release_process_group_once_empty, spawn_env, spawned_by_this_process)
from hermes_constants import get_hermes_home, hermes_home_key
from tools.browser_harness_dirs import (daemon_env, daemon_identity, fit_default_socket_dir, harness_pid_file,
                                        namespace_harness_dirs)
from utils import is_truthy_value

logger = logging.getLogger(__name__)

_BROWSER_USE_PROVIDER = "browser-use"
# The one credential the harness itself reads (BU_AUTOSPAWN of a Browser Use cloud browser).
_HARNESS_PASSTHROUGH_KEYS: tuple[str, ...] = ("BROWSER_USE_API_KEY",)

# Cloud daemon names become the BU_NAME env var
_SESSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

# Set on the env dict by the CDP resolvers when the resolved browser is EXCLUSIVE to this named session
# (per-name provider / named BU cloud / Lightpanda). Popped before the subprocess launches — never exported.
_PRIVATE_BROWSER_SENTINEL = "_HERMES_BU_PRIVATE_BROWSER"
# Internal route provenance: this exec resolved to a browser on the Bot Desktop display and must use
# the same human-control lease fence as the built-in browser tools. Popped before launching the CLI.
_BOT_DESKTOP_BROWSER_SENTINEL = "_HERMES_BU_BOT_DESKTOP_BROWSER"
# The note when the consented real profile could not load (the route fell back). Popped before launch.
_REAL_PROFILE_FALLBACK_SENTINEL = "_HERMES_BU_REAL_PROFILE_FALLBACK"
# local=True whose real profile could not load: run on the local sidecar browser. Popped before launch.
_LOCAL_FALLBACK_SENTINEL = "_HERMES_BU_LOCAL_FALLBACK"
# Where new/hidden tabs may be brought to the front: a browser_use_shot.ACTIVATE_* mode. Popped before launch.
_CLOUD_BROWSER_SENTINEL = "_HERMES_BU_CLOUD_BROWSER"
# Popped before launch. Marks the legacy Browser Use autospawn route so ownership
# can wait for the harness's own backend return instead of refusing the call.
_AUTOSPAWN_SENTINEL = "_HERMES_BU_AUTOSPAWN_ROUTE"
# ``cloud:<provider session id>`` from get_session_info. Popped before launch.
_CLOUD_ID_SENTINEL = "_HERMES_BU_CLOUD_BROWSER_ID"
# argv must stay ``-m browser_harness.daemon``. ``_own_harness_daemon`` matches this string.
_DAEMON_MODULE = "browser_harness.daemon"
_VENDOR_DIRNAME = "browser_harness_0_1_13"
_IPC_SHA256 = "b46ec6e55ddae9919ada069053dfa12a2555d33a832de57ef5eb3a457cec80b3"
_UNPATCHED_DAEMON_SHA256 = "7f05f904e62af8c07153c34a1fd3d334acf5c5aded8d0ddab974596bb50a8c97"
_LICENSE_COPYRIGHT = "Copyright (c) 2026 Browser Use"
_VENDOR_PACKAGE_FILES = (
    "__init__.py", "_ipc.py", "admin.py", "auth.py", "macos.py", "mcp_cli.py",
    "paths.py", "recorder.py", "run.py", "telemetry.py", "video.py", "video_render.py",
    "SKILL.md", "video-template.html", "daemon.py", "helpers.py",
)


class VendorPathError(RuntimeError):
    """Vendor tree failed checks. Do not fall back to the unpatched package."""


def _vendor_root() -> Path:
    return Path(__file__).resolve().parent / "vendor" / _VENDOR_DIRNAME


def _refuse_vendor(reason: str) -> VendorPathError:
    return VendorPathError(f"{reason}; refusing the unpatched package")


def _vendor_file(root: Path, path: Path) -> Path:
    if path.is_symlink():
        raise _refuse_vendor("vendor path is a symlink")
    resolved = path.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise _refuse_vendor("vendor path escapes the vendor tree") from exc
    if not resolved.is_file():
        raise _refuse_vendor("vendor tree is incomplete")
    return resolved


def verify_vendor_tree(root: Optional[Path] = None) -> Path:
    """Return vendor ``src``, or raise. Never a site-packages path."""
    root = Path(root) if root is not None else _vendor_root()
    if root.is_symlink():
        raise _refuse_vendor("vendor root is a symlink")
    src = root / "src"
    if src.is_symlink():
        raise _refuse_vendor("vendor src is a symlink")
    package = src / "browser_harness"
    license_path = _vendor_file(root, root / "LICENSE")
    if _LICENSE_COPYRIGHT not in license_path.read_text(encoding="utf-8", errors="replace"):
        raise _refuse_vendor("vendor LICENSE copyright line is missing")
    for name in _VENDOR_PACKAGE_FILES:
        _vendor_file(root, package / name)
    ipc_hash = hashlib.sha256((package / "_ipc.py").read_bytes()).hexdigest()
    if ipc_hash != _IPC_SHA256:
        raise _refuse_vendor("vendor _ipc.py hash does not match the locked copy")
    daemon_bytes = (package / "daemon.py").read_bytes()
    if hashlib.sha256(daemon_bytes).hexdigest() == _UNPATCHED_DAEMON_SHA256:
        raise _refuse_vendor("vendor daemon.py is the unpatched package")
    daemon_text = daemon_bytes.decode("utf-8", errors="replace")
    if ("task_grants" not in daemon_text or "dialog_identity" not in daemon_text
            or "task_grant_version" not in daemon_text):
        raise _refuse_vendor("vendor daemon.py has no task grant capabilities")
    if b"def task_grant_for" not in (package / "helpers.py").read_bytes():
        raise _refuse_vendor("vendor helpers.py has no task_grant_for")
    return src


def _subprocess_pythonpath(vendor_src: str, site_dir: Optional[str]) -> str:
    """Vendor src first, site dir second. Never site dir alone."""
    if not vendor_src:
        raise _refuse_vendor("vendor src is missing")
    if site_dir and os.path.abspath(site_dir) == os.path.abspath(vendor_src):
        raise _refuse_vendor("vendor src resolved to site-packages")
    if site_dir:
        return vendor_src + os.pathsep + site_dir
    return vendor_src

# Prepended to the model's code for named sessions on SHARED browsers (a /browser connect CDP override): the
# harness daemon attaches to the first existing page at startup, so two fresh named daemons can land on the
# SAME tab. Steering each onto a tab it created prevents clobbering. Runs once per daemon (marker keyed by
# BU_NAME + daemon pid).
_OWN_TAB_PREAMBLE = """\
# hermes: pin this named session to its own tab (once per daemon process)
def _hermes_ensure_own_tab():
    import os as _os, tempfile as _tf
    _name = _os.environ.get("BU_NAME", "default")
    try:
        # Key the marker by the daemon's pid so a daemon restart (which
        # re-attaches to the first shared page) re-pins automatically,
        # while agent-driven tab switches mid-session are left alone.
        from browser_harness import _ipc as _bipc
        _dpid = _bipc.pid_path(_name).read_text().strip() or "0"
    except Exception:
        _dpid = "0"
    _uid = _os.getuid() if hasattr(_os, "getuid") else 0
    _marker = _os.path.join(
        _tf.gettempdir(), "hermes-bu-owntab-%s-%s-%s" % (_uid, _name, _dpid)
    )
    if _os.path.exists(_marker):
        return
    try:
        # Force a fresh target: new_tab() would REUSE a blank current tab,
        # which is exactly the tab a sibling daemon may also hold.
        _tid = cdp("Target.createTarget", url="about:blank").get("targetId")
        if _tid:
            switch_tab(_tid)
    except Exception:
        pass  # best-effort: worst case is pre-fix behavior
    try:
        # Shared temp root + predictable name: never truncate through a planted symlink.
        _os.close(_os.open(_marker, _os.O_WRONLY | _os.O_CREAT | getattr(_os, "O_NOFOLLOW", 0), 0o600))
    except OSError:
        pass
_hermes_ensure_own_tab()
del _hermes_ensure_own_tab
"""


def ownership_preamble(target_id: str, spool_path: str) -> str:
    """Pin the harness to a target this process already created.

    Records only ids returned by createTarget/new_tab. Does not list pages,
    open the shadow store, or adopt the visible tab.
    """
    from tools.browser_task_registry import valid_id

    valid_id(target_id)
    lines = [
        "# hermes: pin this call to the target this tool created",
        "def _hermes_pin_owned():",
        "    import os as _os",
        f"    _owned = {{{target_id!r}}}",
        f"    _spool = {spool_path!r}",
        f"    _current = [{target_id!r}]",
        "    def _record(tid):",
        "        if not isinstance(tid, str) or not tid:",
        "            raise RuntimeError('created target has no id')",
        "        _owned.add(tid)",
        "        _os.makedirs(_os.path.dirname(_spool), exist_ok=True)",
        "        with open(_spool, 'a', encoding='utf-8') as _fh:",
        "            _fh.write(tid + chr(10))",
        "    def _ensure(tid):",
        "        if tid not in _owned:",
        "            raise PermissionError('target is not owned by this task')",
        "    _orig_cdp, _orig_switch, _orig_goto = cdp, switch_tab, goto_url",
        "    def _guarded_cdp(method, **kwargs):",
        "        if method in ('Target.attachToTarget', 'Target.activateTarget', 'Target.closeTarget'):",
        "            tid = kwargs.get('targetId')",
        "            if tid:",
        "                _ensure(tid)",
        "        res = _orig_cdp(method, **kwargs)",
        "        if method == 'Target.createTarget':",
        "            tid = res.get('targetId') if isinstance(res, dict) else res",
        "            if not tid:",
        "                raise RuntimeError('Target.createTarget returned no target')",
        "            _record(tid)",
        "            _current[0] = tid",
        "        elif method == 'Target.activateTarget' and kwargs.get('targetId'):",
        "            _current[0] = kwargs['targetId']",
        "        return res",
        "    def _guarded_switch(target):",
        "        tid = str(target)",
        "        _ensure(tid)",
        "        res = _orig_switch(tid)",
        "        _current[0] = tid",
        "        return res",
        "    def _guarded_new(url='about:blank'):",
        "        res = _orig_cdp('Target.createTarget', url='about:blank')",
        "        tid = res.get('targetId') if isinstance(res, dict) else res",
        "        if not tid:",
        "            raise RuntimeError('new tab was not created')",
        "        _record(tid)",
        "        _current[0] = tid",
        "        _orig_cdp('Target.activateTarget', targetId=tid)",
        "        _orig_switch(tid)",
        "        if url and url != 'about:blank':",
        "            _orig_goto(url)",
        "        return tid",
        "    def _guarded_goto(url):",
        "        if not _current[0] or _current[0] not in _owned:",
        "            raise PermissionError('no owned target is active')",
        "        return _orig_goto(url)",
        "    globals()['cdp'] = _guarded_cdp",
        "    globals()['switch_tab'] = _guarded_switch",
        "    globals()['new_tab'] = _guarded_new",
        "    globals()['goto_url'] = _guarded_goto",
        "    _ensure(_current[0])",
        "    _orig_cdp('Target.activateTarget', targetId=_current[0])",
        "    _orig_switch(_current[0])",
        "_hermes_pin_owned()",
        "del _hermes_pin_owned",
        "",
    ]
    return "\n".join(lines)


def ownership_route(cdp: str, autospawn: Optional[str]) -> str:
    """``loopback``, ``cloud``, ``autospawn``, ``blocked``, or ``missing``. Does not connect."""
    if not cdp and autospawn:
        return "autospawn"
    if not cdp:
        return "missing"
    if cdp.startswith("wss://"):
        return "cloud"
    if cdp.startswith(("http://127.0.0.1:", "http://localhost:", "https://127.0.0.1:", "https://localhost:",
                       "ws://127.0.0.1:", "ws://localhost:")):
        return "loopback"
    return "blocked"


def cloud_autospawn_preamble(receipt_path: str, spool_path: str, expected_kind: str = "cloud") -> str:
    """Create one page on the daemon that already holds the socket.

    The connection receipt is the daemon's own ping (kind and pid), not another
    process's environment and not a page scan. The signed URL, when one exists,
    was returned by ``start_remote_daemon`` or the provider before this runs.
    """
    if expected_kind not in {"cloud", "cdp", "local"}:
        raise ValueError("unexpected daemon kind")
    lines = [
        "# hermes: pin this call to the page it creates on the live daemon socket",
        "def _hermes_cloud_pin():",
        "    import os as _os",
        "    from browser_harness.admin import daemon_browser_kind as _kind",
        "    from browser_harness import _ipc as _bipc",
        "    _name = _os.environ.get('BU_NAME', 'default')",
        "    _reported = _kind(_name)",
        f"    if _reported != {expected_kind!r}:",
        "        raise RuntimeError('daemon connection receipt does not match this route')",
        "    _pid = _bipc.identify(_name)",
        "    if type(_pid) is not int or _pid <= 0:",
        "        raise RuntimeError('daemon did not report its process id')",
        "    try:",
        "        _sock, _token = _bipc.connect(_name, timeout=2.0)",
        "        try:",
        "            _caps = _bipc.request(_sock, _token, {'meta': 'capabilities'})",
        "        finally:",
        "            _sock.close()",
        "    except Exception:",
        "        raise RuntimeError('daemon_grant_unsupported')",
        "    if (not isinstance(_caps, dict) or _caps.get('error')",
        "            or _caps.get('task_grants') is not True",
        "            or _caps.get('dialog_identity') is not True",
        "            or type(_caps.get('task_grant_version')) is not int",
        "            or _caps.get('task_grant_version') != 1):",
        "        raise RuntimeError('daemon_grant_unsupported')",
        "    from browser_harness.helpers import task_grant_for as _grant_for",
        "    _tid = cdp('Target.createTarget', url='about:blank').get('targetId')",
        "    if not isinstance(_tid, str) or not _tid:",
        "        raise RuntimeError('cloud createTarget returned no target')",
        "    _grant = _grant_for(_tid)",
        f"    _receipt, _spool = {receipt_path!r}, {spool_path!r}",
        "    _os.makedirs(_os.path.dirname(_receipt), exist_ok=True)",
        "    _os.makedirs(_os.path.dirname(_spool), exist_ok=True)",
        "    if not isinstance(_grant, str) or not _grant or chr(9) in _grant or chr(10) in _grant or chr(13) in _grant:",
        "        with open(_receipt, 'w', encoding='utf-8') as _fh:",
        "            _fh.write(_reported + chr(10) + str(_pid) + chr(10) + _tid + chr(10))",
        "        try:",
        "            cdp('Target.closeTarget', targetId=_tid)",
        "        except Exception:",
        "            pass",
        "        raise RuntimeError('task grant missing; closed only the new page')",
        "    with open(_receipt, 'w', encoding='utf-8') as _fh:",
        "        _fh.write(_reported + chr(10) + str(_pid) + chr(10) + _tid + chr(10) + _grant + chr(10))",
        "    _owned = {_tid}",
        "    _current = [_tid]",
        "    def _record(tid):",
        "        if not isinstance(tid, str) or not tid:",
        "            raise RuntimeError('created target has no id')",
        "        _g = _grant_for(tid)",
        "        if not isinstance(_g, str) or not _g or chr(9) in _g or chr(10) in _g or chr(13) in _g:",
        "            with open(_receipt, 'a', encoding='utf-8') as _fh:",
        "                _fh.write(tid + chr(10))",
        "            try:",
        "                _orig_cdp('Target.closeTarget', targetId=tid)",
        "            except Exception:",
        "                pass",
        "            raise RuntimeError('task grant missing; closed only the new page')",
        "        _owned.add(tid)",
        "        with open(_receipt, 'a', encoding='utf-8') as _fh:",
        "            _fh.write(tid + chr(10) + _g + chr(10))",
        "        with open(_spool, 'a', encoding='utf-8') as _fh:",
        "            _fh.write(tid + chr(9) + _g + chr(10))",
        "    def _ensure(tid):",
        "        if tid not in _owned:",
        "            raise PermissionError('target is not owned by this task')",
        "    _orig_cdp, _orig_switch, _orig_goto = cdp, switch_tab, goto_url",
        "    def _guarded_cdp(method, **kwargs):",
        "        if method in ('Target.attachToTarget', 'Target.activateTarget', 'Target.closeTarget', 'Target.getTargets'):",
        "            tid = kwargs.get('targetId')",
        "            if method == 'Target.getTargets' or (tid and tid not in _owned):",
        "                raise PermissionError('target is not owned by this task')",
        "        res = _orig_cdp(method, **kwargs)",
        "        if method == 'Target.createTarget':",
        "            tid = res.get('targetId') if isinstance(res, dict) else res",
        "            if not tid:",
        "                raise RuntimeError('Target.createTarget returned no target')",
        "            _record(tid)",
        "            _current[0] = tid",
        "        elif method == 'Target.activateTarget' and kwargs.get('targetId'):",
        "            _current[0] = kwargs['targetId']",
        "        return res",
        "    def _guarded_switch(target):",
        "        tid = str(target)",
        "        _ensure(tid)",
        "        res = _orig_switch(tid)",
        "        _current[0] = tid",
        "        return res",
        "    def _guarded_new(url='about:blank'):",
        "        res = _orig_cdp('Target.createTarget', url='about:blank')",
        "        tid = res.get('targetId') if isinstance(res, dict) else res",
        "        if not tid:",
        "            raise RuntimeError('new tab was not created')",
        "        _record(tid)",
        "        _current[0] = tid",
        "        _orig_cdp('Target.activateTarget', targetId=tid)",
        "        _orig_switch(tid)",
        "        if url and url != 'about:blank':",
        "            _orig_goto(url)",
        "        return tid",
        "    def _guarded_goto(url):",
        "        if not _current[0] or _current[0] not in _owned:",
        "            raise PermissionError('no owned target is active')",
        "        return _orig_goto(url)",
        "    globals()['cdp'] = _guarded_cdp",
        "    globals()['switch_tab'] = _guarded_switch",
        "    globals()['new_tab'] = _guarded_new",
        "    globals()['goto_url'] = _guarded_goto",
        "    _orig_cdp('Target.activateTarget', targetId=_tid)",
        "    _orig_switch(_tid)",
        "_hermes_cloud_pin()",
        "del _hermes_cloud_pin",
        "",
    ]
    return "\n".join(lines)


def _daemon_locator(env: dict) -> tuple[str, str]:
    """Absolute daemon directory and socket stem for ``env``. Does not connect."""
    from tools.browser_harness_dirs import harness_pid_file
    pid_file = harness_pid_file(env)
    stem = pid_file.name[:-4] if pid_file.name.endswith(".pid") else pid_file.name
    return str(pid_file.parent), stem


def _installed_chrome_listening() -> bool:
    """The harness's own loopback Chrome check. Failure means not listening, matching the harness."""
    try:
        from browser_harness.run import _local_chrome_listening
        return bool(_local_chrome_listening())
    except Exception:
        return False


_REMOTE_CAPTURE = (
    "import json, os\n"
    "from browser_harness.admin import start_remote_daemon\n"
    "browser = start_remote_daemon(os.environ.get('BU_NAME', 'default'))\n"
    "if not isinstance(browser, dict):\n"
    "    raise SystemExit(2)\n"
    "print(json.dumps({'cdpUrl': browser.get('cdpUrl') or '', 'id': browser.get('id') or ''}))\n"
)
_REMOTE_STOP = (
    "import os\n"
    "from browser_harness.admin import stop_remote_daemon\n"
    "stop_remote_daemon(os.environ.get('BU_NAME', 'default'))\n"
)


def _stop_captured_remote(env: dict) -> None:
    try:
        subprocess.run([sys.executable, "-c", _REMOTE_STOP], env=env, capture_output=True, text=True, timeout=30)
    except Exception:
        logger.warning("cloud autospawn cleanup did not confirm stop")


def _capture_remote_browser(env: dict) -> dict[str, str]:
    """Run the harness's own ``start_remote_daemon`` and keep only its return. No environ read."""
    from tools.browser_task_cdp import CdpError, validate_cloud_browser_id, validate_cloud_ws
    from tools.browser_task_registry import RegistryError

    child = dict(env)
    child["BH_OPEN_LIVE_URL"] = "0"
    try:
        proc = subprocess.run([sys.executable, "-c", _REMOTE_CAPTURE], env=child,
                              capture_output=True, text=True, timeout=90)
    except subprocess.TimeoutExpired as exc:
        _stop_captured_remote(child)
        raise RegistryError("target_create_failed", "cloud autospawn did not return a connection receipt") from exc
    payload = None
    for line in reversed((proc.stdout or "").splitlines()):
        if line.strip().startswith("{"):
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                payload = None
            break
    if proc.returncode != 0 or not isinstance(payload, dict):
        _stop_captured_remote(child)
        raise RegistryError("target_create_failed", "cloud autospawn did not return a connection receipt")
    try:
        endpoint = validate_cloud_ws(str(payload.get("cdpUrl") or ""))
        raw_id = str(payload.get("id") or "")
        browser_id = validate_cloud_browser_id("cloud:" + raw_id)
    except (CdpError, TypeError) as exc:
        _stop_captured_remote(child)
        raise RegistryError("cdp_endpoint_invalid", "cloud autospawn receipt failed validation") from exc
    return {"endpoint": endpoint, "browser_id": browser_id, "raw_id": raw_id}


def _prepare_autospawn(env: dict, task: str, cloud_id: Optional[str]) -> tuple[str, str, str]:
    """Choose the daemon receipt for a route that has no CDP URL yet."""
    from tools.browser_task_cdp import CdpError, harness_ping, validate_cloud_browser_id, validate_cloud_ws
    from tools.browser_task_registry import RegistryError, active_cloud_endpoint, task_connection

    directory, stem = _daemon_locator(env)
    report = None
    try:
        report = harness_ping(directory, stem)
    except CdpError:
        report = None
    if report and report["browser_kind"] == "cloud":
        stored = active_cloud_endpoint(task)
        saved = task_connection(task) or {}
        browser_id = cloud_id or str(saved.get("browserId") or "")
        if not stored or not browser_id:
            raise RegistryError("receipt_missing", "live cloud daemon has no stored connection receipt; refusing to read its environment")
        env["BU_CDP_WS"] = stored
        env.pop("BU_AUTOSPAWN", None)
        return "cloud", validate_cloud_ws(stored), validate_cloud_browser_id(browser_id)
    if report and report["browser_kind"] in {"local", "cdp"}:
        env["BU_AUTOSPAWN"] = ""
        return report["browser_kind"], "harness", ""
    if _installed_chrome_listening():
        env["BU_AUTOSPAWN"] = ""
        return "local", "harness", ""
    captured = _capture_remote_browser(env)
    env["BU_CDP_WS"] = captured["endpoint"]
    env["BU_BROWSER_ID"] = captured["raw_id"]
    env.pop("BU_AUTOSPAWN", None)
    return "cloud", captured["endpoint"], captured["browser_id"]


def _capability_supported(response: object) -> bool:
    if not isinstance(response, dict) or response.get("error"):
        return False
    version = response.get("task_grant_version")
    return (
        response.get("task_grants") is True
        and response.get("dialog_identity") is True
        and type(version) is int
        and version == 1
    )


def _probe_grant_capabilities(directory: str, stem: str) -> str:
    """``ok``, ``down``, or ``unsupported``. Existing daemon socket only."""
    from tools.browser_task_cdp import CdpError, harness_exchange
    try:
        response = harness_exchange(directory, stem, {"meta": "capabilities"}, timeout=2.0)
    except CdpError:
        return "down"
    return "ok" if _capability_supported(response) else "unsupported"


def _reload_owned_daemon(env: dict) -> bool:
    """Existing ``--reload`` only when this process owns the daemon."""
    if _own_harness_daemon(env) is not True:
        return False
    _reload_daemons([daemon_identity(env)])
    return True


def _require_task_grant_daemon(env: dict, directory: str, stem: str) -> None:
    """Refuse a task page when a live daemon cannot issue grants.

    A daemon this process does not own is left running. An owned daemon uses
    the existing ``--reload``; this call still does not create a page.
    """
    from tools import browser_task_cdp
    from tools.browser_task_cdp import CdpError, harness_ping
    from tools.browser_task_registry import RegistryError

    state = _probe_grant_capabilities(directory, stem)
    if state == "down":
        return
    if state != "ok":
        _reload_owned_daemon(env)
        raise RegistryError(
            "daemon_grant_unsupported",
            "daemon does not support task grants; task page was not created",
        )
    try:
        pid = harness_ping(directory, stem)["pid"]
        ensure = getattr(browser_task_cdp, "ensure_harness_task_grants", None)
        if ensure is None:
            raise RegistryError(
                "daemon_grant_unsupported",
                "task grant client is not linked; refusing to create a task page",
            )
        ensure(directory, stem, pid)
    except RegistryError:
        raise
    except (CdpError, OSError, TypeError, AttributeError, ValueError) as exc:
        raise RegistryError("daemon_grant_unsupported", "daemon task grants are not usable") from exc


def _pin_owned_target(env: dict, code: str, task_id, session_id, turn_id,
                      autospawn: Optional[str] = None, cloud_id: Optional[str] = None):
    """Create and register one blank page on the resolved endpoint, then pin to it."""
    from tools.browser_task_cdp import CdpError, promote_provider_discovery
    from tools.browser_task_registry import (
        RegistryError, acquire_exec_gate, open_and_register, receipt_path, resolve_call_identity, spool_path)

    task, session, turn = resolve_call_identity(task_id, session_id)
    cdp = env.get("BU_CDP_WS") or env.get("BU_CDP_URL") or ""
    try:
        promoted = promote_provider_discovery(cdp)
    except CdpError as exc:
        raise RegistryError(exc.code, str(exc)) from exc
    if promoted and promoted != cdp:
        _set_cdp_env(env, promoted)
        cdp = promoted
    route = ownership_route(cdp, autospawn)
    if route == "missing":
        raise RegistryError("endpoint_missing", "browser endpoint is missing; refusing an untracked browser")
    if route == "blocked":
        raise RegistryError("discovery_unresolved", "provider HTTP discovery did not yield a signed websocket; refusing a new protocol")
    gate = acquire_exec_gate()
    if gate is None:
        raise RegistryError("lease_held", "manual control did not release; refusing to touch the browser")
    try:
        if route in {"autospawn", "cloud"}:
            if route == "cloud":
                from tools.browser_task_cdp import validate_cloud_browser_id, validate_cloud_ws
                if not cloud_id:
                    raise RegistryError("browser_id_missing", "cloud browser identity was not in the backend return")
                kind, endpoint, browser_id = "cdp", validate_cloud_ws(cdp), validate_cloud_browser_id(cloud_id)
            else:
                kind, endpoint, browser_id = _prepare_autospawn(env, task, cloud_id)
            receipt = receipt_path(task)
            spool = spool_path(task)
            directory, stem = _daemon_locator(env)
            _require_task_grant_daemon(env, directory, stem)
            state = {"task_id": task, "session_id": session, "turn_id": turn, "browser_id": browser_id,
                     "endpoint": endpoint, "spool": spool, "receipt": receipt, "autospawn": True,
                     "expected_kind": kind, "daemon_dir": directory, "daemon_stem": stem}
            return cloud_autospawn_preamble(str(receipt), str(spool), kind) + code, gate, state
        owned = open_and_register(cdp, task_id=task, session_id=session, turn_id=turn)
    except RegistryError:
        gate.close()
        raise
    except Exception as exc:
        gate.close()
        raise RegistryError("target_create_failed", type(exc).__name__) from exc
    spool = spool_path(task)
    state = {"task_id": task, "session_id": session, "turn_id": turn, "browser_id": owned["browserId"],
             "endpoint": owned["endpoint"], "spool": spool}
    return ownership_preamble(owned["targetId"], str(spool)) + code, gate, state


def _finish_owned(gate, state) -> Optional[str]:
    error = None
    if state is not None and state.get("autospawn"):
        error = _register_autospawn_receipt(state)
    elif state is not None:
        try:
            from tools.browser_task_registry import adopt_spool
            adopt_spool(state["spool"], task_id=state["task_id"], session_id=state["session_id"],
                        turn_id=state["turn_id"], browser_id=state["browser_id"], endpoint=state["endpoint"])
        except Exception as exc:
            logger.warning("browser target spool was not registered: %s", type(exc).__name__)
            error = f"created targets were not registered: {type(exc).__name__}"
    if gate is not None:
        gate.close()
    return error


def _register_autospawn_receipt(state) -> Optional[str]:
    """Register the daemon ping, created targets, and their grants.

    Does not open a second websocket. A missing fourth line is not registered.
    Only the target named in this receipt is closed. Never Browser.close.
    """
    from tools.browser_task_cdp import CdpError, harness_exchange
    from tools.browser_task_registry import (
        IdentityError, RegistryError, register_created, register_spooled_text, valid_id)

    path = state["receipt"]
    spool = state.get("spool")
    text = ""
    spool_text = ""
    try:
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
    except OSError as exc:
        return f"daemon receipt could not be read: {type(exc).__name__}"
    finally:
        try:
            if path.is_file():
                path.write_text("", encoding="utf-8")
        except OSError:
            pass
    try:
        if spool is not None and spool.is_file():
            spool_text = spool.read_text(encoding="utf-8")
    except OSError:
        spool_text = ""
    finally:
        try:
            if spool is not None and spool.is_file():
                spool.write_text("", encoding="utf-8")
        except OSError:
            pass
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 3:
        return "daemon did not return a connection receipt; refusing to claim a page"
    kind, pid_text, target_id = lines[0], lines[1], lines[2]
    try:
        pid = int(pid_text)
        if type(pid) is not int or not 0 < pid < (1 << 31):
            raise ValueError
    except (TypeError, ValueError):
        return "daemon connection receipt was not a process id and target id"
    endpoint = state.get("endpoint") or ""
    browser_id = state.get("browser_id") or ""
    if endpoint == "harness" or not endpoint.startswith("wss://"):
        endpoint = "harness"
        browser_id = "daemon:" + str(pid)
    locator = {"transport": "harness", "daemon_dir": state.get("daemon_dir") or "",
               "daemon_stem": state.get("daemon_stem") or "", "daemon_pid": pid}

    def close_one(raw_id: str, grant: str = "") -> None:
        """Close only this new target. Unregistered pages are not in the grant table."""
        if not locator["daemon_dir"] or not locator["daemon_stem"] or not raw_id:
            return
        try:
            close_id = valid_id(raw_id)
        except (IdentityError, TypeError, ValueError):
            return
        payload = {"method": "Target.closeTarget", "params": {"targetId": close_id}}
        if isinstance(grant, str) and grant and "\n" not in grant and "\r" not in grant:
            payload["task_grant"] = grant
        try:
            harness_exchange(locator["daemon_dir"], locator["daemon_stem"], payload)
        except CdpError:
            return

    if kind != state.get("expected_kind"):
        close_one(target_id)
        return "daemon connection receipt does not match this route"
    if len(lines) < 4 or not lines[3]:
        close_one(target_id)
        return "daemon did not return a task grant; refusing to register"
    pairs = [(target_id, lines[3])]
    rest = lines[4:]
    incomplete = False
    if len(rest) % 2:
        close_one(rest[-1])
        rest = rest[:-1]
        incomplete = True
    pairs.extend((rest[index], rest[index + 1]) for index in range(0, len(rest), 2))
    for raw_id, grant in pairs:
        if not isinstance(grant, str) or not grant or "\n" in grant or "\r" in grant:
            close_one(raw_id)
            incomplete = True
            continue
        try:
            register_created(task_id=state["task_id"], session_id=state["session_id"],
                             turn_id=state["turn_id"], target_id=valid_id(raw_id),
                             browser_id=browser_id, endpoint=endpoint, task_grant=grant, **locator)
        except TypeError:
            close_one(raw_id, grant)
            return "created target was not registered"
        except (CdpError, RegistryError, OSError, RuntimeError, IdentityError, ValueError):
            close_one(raw_id, grant)
            return "created target was not registered"
    if incomplete:
        return "created target was not registered"
    try:
        register_spooled_text(spool_text, task_id=state["task_id"], session_id=state["session_id"],
                              turn_id=state["turn_id"], browser_id=browser_id, endpoint=endpoint, **locator)
    except (CdpError, RegistryError, OSError, RuntimeError, TypeError):
        return "additional spooled targets were not registered"
    return None


_DEFAULT_TIMEOUT_S = 300
_MIN_TIMEOUT_S = 5
_MAX_TIMEOUT_S = 1800
_STDERR_CAP_CHARS = 4000

_TASK_ID_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")  # filesystem-safe task ids
# Screenshot paths printed by capture_screenshot(): POSIX or Windows drive-letter absolute.
_IMAGE_PATH_RE = re.compile(r"((?:[A-Za-z]:[\\/]|/)[^\s\"']+?\.(?:png|jpe?g|webp))", re.IGNORECASE)
# http(s) URL literals in exec code, checked against the browser URL policy
_URL_RE = re.compile(r"https?://[^\s'\"\\)]+", re.IGNORECASE)
_FHS_BIN_DIRS = ("/opt/homebrew/bin", "/opt/homebrew/sbin", "/usr/local/sbin", "/usr/local/bin", "/usr/sbin",
                 "/usr/bin", "/sbin", "/bin")


def _quiet(fn: Callable[[], Any], default: Any, log_prefix: str = "") -> Any:
    """``fn()``, or ``default`` on any exception (debug-logged when ``log_prefix`` is set)."""
    try:
        return fn()
    except Exception as e:
        if log_prefix:
            logger.debug("%s: %s", log_prefix, e)
    return default


def _lazy_call(module: str, name: str, default: Any, log_prefix: str) -> Any:
    """Call ``module.name()`` resolved at call time (tests stub the module / patch the
    attribute); on any failure log ``log_prefix`` and return ``default``."""
    return _quiet(lambda: getattr(importlib.import_module(module), name)(), default, log_prefix)


def _real_profile_consented() -> bool:
    return _lazy_call("tools.browser_session_cloud", "use_real_profile", False, "real-profile consent lookup failed")


def _set_cdp_env(env: dict, cdp: str) -> None:
    """Export a CDP endpoint under the BU_CDP_* contract (http(s) → URL, else WS)."""
    env["BU_CDP_URL" if cdp.startswith(("http://", "https://")) else "BU_CDP_WS"] = cdp


def _has_cdp_env(env: dict) -> bool:
    return bool(env.get("BU_CDP_WS") or env.get("BU_CDP_URL"))


def _export_session_cdp(env: dict, get_session_info: Callable[[str], Any], cache_key: str,
                        fail_msg: Callable[[Exception], str], no_cdp_msg: str) -> Optional[str]:
    """Export the CDP endpoint from ``get_session_info(cache_key)``; error string on failure / no CDP."""
    try:
        cdp = str((get_session_info(cache_key) or {}).get("cdp_url") or "")
    except Exception as e:
        return fail_msg(e)
    if not cdp:
        return no_cdp_msg
    _set_cdp_env(env, cdp)
    return None


class _CodeUrlVerdict(NamedTuple):
    """What one pass over a code blob's URL literals decided (see ``_code_url_verdict``)."""

    blocked: Optional[str]
    local_sidecar: bool


def _code_url_verdict(code: str) -> _CodeUrlVerdict:
    """One pass over the code's URL literals: the blocking error, if any, and whether the call must run on
    the hybrid LOCAL sidecar. Each URL's routing predicate is evaluated ONCE and feeds both answers: it
    resolves DNS unmemoised, so a rebinding host evaluated twice could relax the private-address floor and
    still route to the cloud provider."""
    from tools.browser_url_policy import evaluate_url_safety, hybrid_routes_locally
    local_sidecar = False
    for url in _URL_RE.findall(code or ""):
        routes_local = hybrid_routes_locally(url)
        local_sidecar = local_sidecar or routes_local
        err = evaluate_url_safety(url, auto_local=routes_local)
        if err:
            return _CodeUrlVerdict(err.get("error", "Blocked: unsafe URL"), local_sidecar)
    return _CodeUrlVerdict(None, local_sidecar)


def _harness_credentials() -> dict:
    """The harness's own credential from the active profile's secret scope, never ``os.environ``: under
    multiplex that holds the LAUNCH profile's key, and a served profile's browser runs on its own (or none)."""
    from agent.secret_scope import current_secret_scope, get_secret, serves_routed_profile
    routed = serves_routed_profile()
    scope = (current_secret_scope() or {}) if routed else None
    values = {key: (scope.get(key) if routed else get_secret(key)) for key in _HARNESS_PASSTHROUGH_KEYS}
    return {key: value for key, value in values.items() if value is not None}


def _base_subprocess_env() -> dict:
    from tools.browser_session import build_browser_env
    env = build_browser_env()
    env.update(_harness_credentials())
    # Vendor src must precede site-packages so ``-m browser_harness.daemon`` loads the patched
    # tree. Site dir stays so ``cdp_use`` still imports. A failed check raises; do not fall back.
    env.pop("PYTHONHOME", None)
    env["PYTHONPATH"] = _subprocess_pythonpath(str(verify_vendor_tree()), _harness_site_dir())
    env["PATH"] = _floor_subprocess_path(env.get("PATH", ""))
    env.setdefault("ANONYMIZED_TELEMETRY", "false")
    namespace_harness_dirs(env, _served_profile_tag())  # a served profile's own harness runtime/tmp dirs
    # A daemon the CLI starts inherits this tag; _own_harness_daemon owns only one that names us.
    env.update(spawn_env("browser-harness-daemon"))
    return env


def _floor_subprocess_path(path: str) -> str:
    """Keep system commands reachable from profile workers with a minimal PATH.

    Browser subprocesses may launch shell helpers; version-manager-only paths
    omit /usr/bin. Reuse the shared browser PATH floor on POSIX.
    """
    if os.name == "nt":
        return path
    parts = [p for p in (path or "").split(os.pathsep) if p]
    return os.pathsep.join(parts + [d for d in _FHS_BIN_DIRS if d not in set(parts) and os.path.isdir(d)])


def _read_browser_cfg() -> dict:
    """Return the ``browser:`` config section, or {} on any failure."""
    try:
        from tools.browser_session_cloud import browser_section
        return browser_section()
    except Exception as e:
        logger.debug("Could not read browser config section: %s", e)
        return {}


def _use_gateway(browser_cfg: dict) -> bool:
    """True when the browser section selects the Nous Tool Gateway — by the current ``hermes tools``
    picker row (``cloud_provider: nous``) or the pre-picker ``use_gateway: true`` flag. Reading only
    the legacy flag missed every picker-configured gateway, and the direct-API branch it fell into
    holds no credentials in managed mode (#108310)."""
    if is_truthy_value(browser_cfg.get("use_gateway"), default=False):
        return True
    try:
        from tools.tool_backend_helpers import NOUS_MANAGED_PROVIDER
    except Exception:  # pragma: no cover — helper ships with the package
        return False
    return str(browser_cfg.get("cloud_provider") or "").strip().lower() == NOUS_MANAGED_PROVIDER


def is_legacy_browser_use_cloud_config(browser_cfg: dict) -> bool:
    """True for pre-CLI direct-API Browser Use cloud configs: no cloud_provider (or ``browser-use``), not the
    Nous gateway, and BROWSER_USE_API_KEY set; the CLI then reaches Browser Use cloud natively."""
    if not isinstance(browser_cfg, dict):
        return False
    provider = str(browser_cfg.get("cloud_provider") or "").strip().lower()
    if provider not in {"browser-use", ""} or _use_gateway(browser_cfg):
        return False
    return _browser_use_api_key_set()


def _browser_use_api_key_set() -> bool:
    """BROWSER_USE_API_KEY for the served profile: the only credential Hermes autospawns BU cloud with.
    Profile-scoped so a multiplexed secondary never inherits the default's cloud mode."""
    from agent.secret_scope import get_secret
    return bool(get_secret("BROWSER_USE_API_KEY", ""))


def _harness_site_dir() -> Optional[str]:
    """The site dir Hermes's interpreter imports ``browser_harness`` from, or None."""
    spec = importlib.util.find_spec("browser_harness")
    if spec is None or not spec.origin:
        return None
    return str(Path(spec.origin).resolve().parent.parent)


def _find_cli() -> Optional[list[str]]:
    """The Browser Use CLI's engine (browser-harness) is a core dependency of Hermes's own venv,
    so every install, the Desktop bundle included, runs it on the current interpreter."""
    if _harness_site_dir() is None:
        return None
    return [sys.executable, "-m", "browser_harness.run"]


def browser_exec_available() -> bool:
    """check_fn: browser-harness is importable (a core dependency). Never launches or opens sockets."""
    return _find_cli() is not None


def _workspace_dir(task_id: Optional[str]) -> Optional[str]:
    """Stable per-task scratch dir that persists across browser_exec calls"""
    if os.environ.get("BH_AGENT_WORKSPACE"):
        return os.environ["BH_AGENT_WORKSPACE"]
    try:
        safe = _TASK_ID_SAFE_RE.sub("_", str(task_id or "default"))[:80] or "default"
        path = Path(get_hermes_home()) / "cache" / "browser-use" / "workspace" / safe
        path.mkdir(parents=True, exist_ok=True)
        return str(path)
    except Exception as e:
        logger.debug("browser_exec workspace unavailable: %s", e)
        return None


def _find_screenshot(stdout: str, since: float) -> Optional[str]:
    """Last screenshot path printed during this exec that exists and was written after
    the exec started, or None."""
    for path in reversed(_IMAGE_PATH_RE.findall(stdout or "")):
        try:
            if os.path.isfile(path) and os.path.getmtime(path) >= since - 1:
                return path
        except OSError:
            continue
    return None


def _native_screenshot_result(result: dict[str, Any], path: str) -> Optional[dict[str, Any]]:
    """Build a multimodal tool result attaching path for vision models"""
    try:
        from tools.vision_tools import (_EMBED_MAX_DIMENSION,
                                        _resize_image_for_vision, _should_use_native_vision_fast_path)
        from tools.vision_tools_history_budget import resolve_embed_target_bytes
        if not _should_use_native_vision_fast_path():
            return None
        # History-reuse cap: this data URL bakes into the tool result and is re-sent every later turn —
        # same policy as the vision_analyze native embed.
        data_url = _resize_image_for_vision(Path(path), mime_type="image/png",
                                            max_base64_bytes=resolve_embed_target_bytes(),
                                            max_dimension=_EMBED_MAX_DIMENSION, force_jpeg=True)
        text = json.dumps(result, ensure_ascii=False)
        attached = text + "\n\nThe screenshot from this call is attached — inspect it with your native vision."
        return {"_multimodal": True, "text_summary": text, "meta": {"screenshot_path": path, "native_vision": True},
                "content": [{"type": "text", "text": attached}, {"type": "image_url", "image_url": {"url": data_url}}]}
    except Exception as e:
        logger.debug("Native screenshot attach failed (falling back to text): %s", e)
        return None


def _served_profile_tag() -> str:
    """``""`` outside a served-profile scope (every legacy key stays byte-identical); under a
    multiplexed turn, the routed profile's home key — one profile's browser must never be handed
    to another that happens to use the same session name or task id (#110032)."""
    from hermes_constants import get_hermes_home_override, hermes_home_key
    return "" if get_hermes_home_override() is None else hermes_home_key()


def _backend_cache_key(task_id: Optional[str], session_name: str = "", sidecar: bool = False) -> str:
    """Session-cache key for a backend browser: named sessions get their own; served profiles get their own.
    ``sidecar`` appends the hybrid ``::local`` suffix, which makes ``_get_session_info`` build local Chromium
    even when a cloud provider is configured."""
    key = f"bu-named-{session_name}" if session_name else (task_id or "browser-exec-default")
    tag = _served_profile_tag()
    key = f"{key}@{tag}" if tag else key
    return f"{key}::local" if sidecar else key


def _sidecar_daemon_name(name: str) -> str:
    """BU_NAME of the harness daemon serving the hybrid local sidecar. The daemon reads BU_CDP_* once at
    spawn and outlives the call, so sharing the cloud route's daemon would relay a private URL to the
    cloud browser it is already attached to. User session names cannot start with ``_``."""
    return f"_local_{name or 'default'}"[:64]


def _export_real_profile_note(env: dict, key: str) -> None:
    """The session layer decides real profile vs fresh browser once per key; carry its note (if it fell
    back) so the result reports the browser this call actually drives."""
    from tools.browser_session import real_profile_note
    note = real_profile_note(key)
    if note:
        env.setdefault(_REAL_PROFILE_FALLBACK_SENTINEL, note)


def _mark_local_browser(env: dict, session: dict, key: str) -> None:
    """Flags for a LOCAL session browser: it runs on the Bot Desktop display (lease fence), and unless a
    persistent profile / the real-profile browser is shared by every key, it is exclusive to this key (no
    own-tab preamble: nothing to share a tab with)."""
    features = session.get("features") or {}
    env[_BOT_DESKTOP_BROWSER_SENTINEL] = "lightpanda" if features.get("lightpanda") else "local"
    if not session.get("shared_profile") and not features.get("real_profile"):
        env[_PRIVATE_BROWSER_SENTINEL] = "1"
    _export_real_profile_note(env, key)


def _resolve_local_cdp(env: dict, task_id: Optional[str], session_name: str = "",
                       sidecar: bool = False) -> Optional[str]:
    """Local browser for this cache key through the session layer: the consented real profile, Lightpanda
    (``browser.engine: lightpanda``), or the packaged Chromium. Never the harness's own discovery of the
    user's installed Chrome (chrome://inspect toggle + Allow popup per run, blocked on Chrome >=136)."""
    from tools.browser_lightpanda import using_lightpanda_engine
    from tools.browser_session import get_session_info
    key = _backend_cache_key(task_id, session_name, sidecar)
    session: dict = {}

    def _session_info(cache_key: str) -> dict:
        session.update(get_session_info(cache_key) or {})
        return session

    if _quiet(using_lightpanda_engine, False):
        fail = lambda e: (f"Lightpanda could not be started: {e} Set browser.engine to auto "
                          "to use local Chrome, or switch backends via `hermes tools` → Browser Automation.")
    else:
        fail = lambda e: (f"The local browser could not be started: {e} "
                          "Run `hermes tools` → Browser Automation to (re)install Chromium, or switch backends.")
    err = _export_session_cdp(env, _session_info, key, fail, "The local browser session returned no CDP endpoint.")
    if err is None:
        _mark_local_browser(env, session, key)
    return err


def _resolve_backend_cdp(env: dict, task_id: Optional[str], session_name: str = "",
                         force_local_sidecar: bool = False) -> Optional[str]:
    """Point the harness at the configured backend's CDP endpoint; error string on failure.

    Precedence: (1) ``BU_CDP_WS``/``BU_CDP_URL`` already in env (operator override); (2) ``BROWSER_CDP_URL``
    env / ``browser.cdp_url`` (``/browser connect``); (3) a cloud provider via ``browser_session.get_session_info``
    (per-key cache, expiry, reaper, atexit; a provider failure falls back to a local browser);
    (4) the local engine — ``browser.engine: lightpanda`` or Hermes' packaged Chromium via the launcher
    (never the harness's own discovery of the user's installed Chrome); (5) BU direct-API configs WITH
    ``BROWSER_USE_API_KEY`` → None (checked before (3)): the CLI reaches BU cloud natively (BU_AUTOSPAWN); a
    keyless ``browser-use`` provider (managed-gateway autodetect, or a selection missing its key) takes (3).
    ``session_name`` (BU_NAME) keys the session cache so each name gets its OWN browser — what makes named
    sessions concurrent-safe.

    ``force_local_sidecar`` skips step (3): the call runs on the local engine under the ``::local`` cache
    key (hybrid routing; see ``browser_url_policy.hybrid_routes_locally``). It sits BELOW the two operator overrides, and the
    sidecar verdict never fires while a CDP override is set."""
    if _has_cdp_env(env):
        return None
    from tools.browser_session import get_session_info
    from tools.browser_session_cdp import get_cdp_override
    from tools.browser_session_cloud import get_cloud_provider
    override = _quiet(get_cdp_override, "")
    if override:
        _set_cdp_env(env, override)
        return None
    if force_local_sidecar:
        return _resolve_local_cdp(env, task_id, session_name, sidecar=True)
    provider = _quiet(get_cloud_provider, None, "Cloud provider lookup failed")
    if provider is None:
        return _resolve_local_cdp(env, task_id, session_name)

    # Browser Use direct-API configs: the CLI talks to BU cloud natively (BU_AUTOSPAWN) — the
    # legacy provider would create a second, redundant session. Nous-gateway configs (cloud_provider: nous
    # from the picker, or the pre-picker use_gateway: true) DO resolve through the provider: the gateway
    # provisions the browser server-side and returns its CDP URL. Without BROWSER_USE_API_KEY the CLI cannot
    # autospawn, and the provider is either the managed gateway (autodetect on a never-configured install) or
    # a selection missing its key; both go through the session path (gateway CDP, or the session layer's
    # local-Chromium fallback) instead of leaving the harness to hunt for an installed Chrome.
    provider_key = str(getattr(provider, "name", "") or "").strip().lower()
    if provider_key == _BROWSER_USE_PROVIDER and not _use_gateway(_read_browser_cfg()) and _browser_use_api_key_set():
        env[_PRIVATE_BROWSER_SENTINEL] = "1"  # named BU cloud browsers are exclusive to their daemon
        env[_AUTOSPAWN_SENTINEL] = "1"
        # Only intent: the harness attaches to a local Chrome already listening instead of autospawning, so
        # the live daemon's own report decides.
        from tools.browser_use_shot import ACTIVATE_IF_CLOUD
        env[_CLOUD_BROWSER_SENTINEL] = ACTIVATE_IF_CLOUD
        return None

    provider_name = type(provider).__name__
    session: dict = {}

    def _session_info(key: str) -> dict:
        session.update(get_session_info(key) or {})
        return session

    key = _backend_cache_key(task_id, session_name)
    err = _export_session_cdp(
        env, _session_info, key,
        lambda e: (f"Cloud browser provider {provider_name} failed to provide a session: {e}. "
                   "Fix the provider configuration or switch backends via `hermes tools` → Browser Automation."),
        f"Cloud browser provider {provider_name} returned no CDP endpoint, so browser_exec "
        "cannot drive it. Pick a CDP-capable provider via `hermes tools` → Browser Automation.",
    )
    if session.get("fallback_from_cloud"):
        # The provider failed (rate limit, quota, outage) and the session layer fell back to a LOCAL
        # browser for this key; it already carries its CDP URL.
        logger.warning("browser_exec: %s failed (%s); using local Chromium for this session",
                       provider_name, session.get("fallback_reason"))
        if err is None:
            _mark_local_browser(env, session, key)
        return err
    if err is None:
        from tools.browser_use_shot import ACTIVATE_ALWAYS
        env[_CLOUD_BROWSER_SENTINEL] = ACTIVATE_ALWAYS  # the provider's own session: the harness connects only there
        cloud_id = session.get("bb_session_id")
        if isinstance(cloud_id, str) and cloud_id:
            env[_CLOUD_ID_SENTINEL] = "cloud:" + cloud_id
    # A provider browser keyed bu-named-<name> is exclusive to this session — the
    # own-tab preamble would just leak a blank tab into it.
    if err is None and session_name:
        env[_PRIVATE_BROWSER_SENTINEL] = "1"
    return err


def _resolve_real_profile_cdp(env: dict, force_local: bool) -> Optional[str]:
    """Point the harness at the user's real-profile copy-browser (a SNAPSHOT of their default Chromium
    profile, hermes_cli.browser_connect) when consented. Two ways in: the effective backend is already local
    (no provider, CDP override, or legacy BU cloud config) → silent upgrade; or ``force_local`` (consent-gated
    ``local`` arg) → the user's browser even under a cloud backend. Operator overrides (BU_CDP_* env,
    /browser connect, ``browser.cdp_url``) own the session either way. A launch error is returned;
    ``_route_backend`` then falls back to the normal backend and reports it, never silently."""
    if not _real_profile_consented() or _has_cdp_env(env):
        return None
    from tools.browser_session_cdp import get_cdp_override_raw
    from tools.browser_session_cloud import get_cloud_provider
    from tools.browser_session_real_profile import real_profile_cdp
    if _quiet(get_cdp_override_raw, ""):
        return None
    # Only auto-upgrade genuinely-local attaches; any cloud path (provider, provider lookup failure, or
    # legacy BU cloud config) stays on its backend unless the model passes local=true.
    if not force_local and (_quiet(get_cloud_provider, object()) is not None
                            or is_legacy_browser_use_cloud_config(_read_browser_cfg())):
        return None
    cdp, err = real_profile_cdp()
    if cdp and not err:
        _set_cdp_env(env, cdp)
        env[_BOT_DESKTOP_BROWSER_SENTINEL] = "local"  # the user's real Chromium, whatever browser.engine says
    return err or None


def _attach_vault_supervisor(env: dict, task_id: Optional[str]) -> None:
    """Attach the per-task CDP supervisor to a local browser so ``browser_vault`` fill has a
    secret-capable socket into that same browser. A signed cloud URL is skipped: the daemon
    already holds it, and vault fill uses that registered socket instead of a second one."""
    cdp = env.get("BU_CDP_WS") or env.get("BU_CDP_URL")
    if not cdp or str(cdp).startswith("wss://"):
        return
    try:
        from tools.browser_session_cdp import get_dialog_policy_config, resolve_cdp_override
        from tools.browser_supervisor import SUPERVISOR_REGISTRY
        resolved = resolve_cdp_override(str(cdp))
        if not resolved or str(resolved).startswith("wss://"):
            return
        policy, timeout_s = get_dialog_policy_config()
        SUPERVISOR_REGISTRY.get_or_start(task_id=task_id or "default", cdp_url=resolved,
                                         dialog_policy=policy, dialog_timeout_s=timeout_s)
    except Exception as exc:
        logger.debug("browser_exec: CDP supervisor attach failed (non-fatal): %s", type(exc).__name__)


def _route_backend(env: dict, session: str, task_id: Optional[str], local: bool,
                   force_local_sidecar: bool = False, real_profile: bool = True) -> Optional[str]:
    """Resolve where the harness connects; returns an error string or None. Real-profile consent runs
    BEFORE provider resolution so a hit short-circuits the cloud path via the BU_CDP_* env contract. Named
    sessions compose with the backend: BU_NAME namespaces the harness daemon (IPC socket, log, pid) and on
    provider backends additionally keys its own cloud browser.

    ``force_local_sidecar`` (a call carrying a private URL) declines the cloud provider for the hybrid local
    sidecar; unlike ``local`` (the consent-gated real-profile browser) it never touches the user's profile."""
    # ``real_profile=False``: this route already fell back, so it stays on its fresh browser instead
    # of swapping browsers mid-task once the profile frees up.
    rp_err = _resolve_real_profile_cdp(env, force_local=local) if real_profile else None
    # local=True is only served by the real-profile route; consent off must not pretend.
    if local and not _has_cdp_env(env) and not _real_profile_consented():
        return ("local=true was requested but browser.use_real_profile is off. Enable it in config.yaml "
                "(browser.use_real_profile: true) or the desktop Settings → Browser section, then retry.")
    # local=True promised the user's own machine: a profile that can't load runs on a fresh LOCAL browser
    # (the sidecar, which never loads the profile), never the cloud one. Without ``local`` the session
    # layer makes the one real-profile-or-fresh decision and reports it (_resolve_local_cdp).
    if local and not _has_cdp_env(env) and (rp_err or not real_profile):
        if rp_err:
            from tools.browser_session_real_profile import real_profile_fallback_note
            env[_REAL_PROFILE_FALLBACK_SENTINEL] = real_profile_fallback_note(rp_err)
        env[_LOCAL_FALLBACK_SENTINEL] = "1"
        force_local_sidecar = True
    return _resolve_backend_cdp(env, task_id, session_name=session,
                                force_local_sidecar=force_local_sidecar)


def _group_popen_kwargs() -> dict:
    """Popen kwargs starting the CLI in its own process group (a new session on POSIX) so a
    timeout can take down every process that inherited the capture pipes, not just the CLI
    child. Windows also hides the console window."""
    def _flags() -> dict:
        from hermes_cli._subprocess_compat import windows_hide_flags
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        return {"creationflags": windows_hide_flags() | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
                "startupinfo": si}
    # detached-spawn: ok — awaited CLI; a timeout kills its whole group (_run_cli_killing_process_group)
    return _quiet(_flags, {}, "Windows hide-flags unavailable") if os.name == "nt" else {"start_new_session": True}


def _clamp_timeout(timeout_s: Any) -> int:
    try:
        return max(_MIN_TIMEOUT_S, min(int(timeout_s), _MAX_TIMEOUT_S))
    except (TypeError, ValueError):
        return _DEFAULT_TIMEOUT_S


# After a whole-group SIGKILL, every pipe holder is dead, so the drain below is normally
# instant; the deadline only guards against a process outside the group still holding a pipe.
_POST_KILL_DRAIN_S = 10.0


def _kill_cli_process_group(proc) -> None:
    """SIGKILL the CLI's whole process group (POSIX; ``start_new_session`` made pgid == pid) or,
    on Windows, its process tree via ``taskkill /T /F`` — the only group-wide kill it offers."""
    if os.name == "nt":
        from hermes_cli._subprocess_compat import windows_hide_flags
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
                           check=False, creationflags=windows_hide_flags())
        return
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(proc.pid, signal.SIGKILL)  # windows-footgun: ok — POSIX only, the nt branch returned above


def _run_cli_killing_process_group(cmd, code, env, timeout):
    """Run the CLI in its own process group and kill the whole group on timeout.

    ``subprocess.run`` only kills the direct child on ``TimeoutExpired``; a grandchild that
    inherited the stdout/stderr pipes (browser_harness daemon / Chrome helper) is orphaned
    still holding them, and on Windows ``run()``'s unbounded post-kill ``communicate()`` then
    blocks on pipe EOF forever — so the tool call, plus its activity heartbeat, wedges (#106244).
    """
    proc = subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", env=env, **_group_popen_kwargs(),
    )
    try:
        stdout, stderr = proc.communicate(input=code, timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_cli_process_group(proc)
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.communicate(timeout=_POST_KILL_DRAIN_S)
        raise
    return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)


# How often a running browser_exec re-checks for the daemon its first call starts.
_OWN_DURING_CALL_S = 0.5
# Harness daemons this process drove: each latches BU_CDP_* at start and outlives every call, so a backend
# swap (/browser connect|disconnect) leaves later calls in the old browser until these are stopped.
_driven_daemons: dict[tuple[str, ...], str] = {}  # daemon identity -> home
_driven_daemons_lock = threading.Lock()
_live_generation = functools.partial(_lazy_call, "tools.browser_session_cdp", "live_cdp_generation", 0,
                                     "live CDP generation lookup failed")  # this profile's publish count
# Route cache key -> whether its last browser_exec ran on the hybrid local sidecar.
_exec_route_is_sidecar: dict[str, bool] = {}
# Route cache key -> the note from its real-profile fallback; that route stays on the fallback browser.
_real_profile_fallback: dict[str, str] = {}
_real_profile_note_delivered: set = set()


def stop_harness_daemons(home_key: Optional[str] = None) -> None:
    """Stop every harness daemon this process drove (only ``home_key``'s when given), through the
    harness's own identity-checked ``--reload``; the next browser_exec respawns one on the endpoint
    it resolves then."""
    with _driven_daemons_lock:
        daemons = sorted(key for key, home in _driven_daemons.items() if home_key in (None, home))
        for key in daemons:
            _driven_daemons.pop(key, None)
    _reload_daemons(daemons)


def _published_daemon_pid(env: dict) -> Optional[int]:
    """The pid the harness published for the daemon ``env`` drives (``harness_pid_file``: JSON
    ``{"pid", "started"}`` or a bare pid), or None when there is none."""
    try:
        raw = harness_pid_file(env).read_text(encoding="utf-8-sig").strip()
        return int(json.loads(raw)["pid"]) if raw.startswith("{") else int(raw.split()[0])
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        return None


def _own_harness_daemon_until_owned(env: dict) -> Optional[bool]:
    """Periodic check while browser_exec runs: own the daemon as soon as it publishes its pid, then
    stop (False). It also stops once the daemon can never be ours (Windows, a managed worker, a daemon
    another Hermes started), instead of re-checking every tick for the whole call."""
    return None if _own_harness_daemon(env) is None else False


def _reload_daemons(daemons) -> None:  # each ``daemon_identity`` daemon, with its own dirs
    cmd = _find_cli() if daemons else None
    if not cmd:
        return
    try:
        env = _base_subprocess_env()
    except VendorPathError:
        return
    for daemon in daemons:
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            _run_cli_killing_process_group([*cmd, "--reload"], "", daemon_env(env, daemon), 15)
        with _owned_daemon_groups_lock:
            pgid = _owned_daemon_groups.pop(daemon, None)
        if pgid is not None:
            release_process_group_once_empty(pgid)  # a daemon --reload failed to stop stays covered


# Daemon identity (``daemon_identity``: its dirs + BU_NAME) -> process group of the harness daemon this
# process owns. The harness starts its daemon with start_new_session so it outlives each CLI call (that
# is how a session persists across browser_exec calls), which also means it outlives a SIGKILLed or
# OOM-killed Hermes forever (#121095). Its group goes to this process's owner-death supervisor, so it
# dies with us however we die.
_owned_daemon_groups: dict[tuple[str, ...], int] = {}
_owned_daemon_groups_lock = threading.Lock()


def _own_harness_daemon(env: dict) -> Optional[bool]:
    """Hand the daemon behind ``env``'s BU_NAME to the owner-death supervisor.

    The harness publishes its daemon pid in its runtime dir (``harness_pid_file``: JSON
    ``{"pid", "started"}`` or a bare pid). The daemon is accepted only while it is this user's live
    ``browser_harness.daemon`` leading its own session and its spawn tag names this process, so a
    stale or planted pid file, or a daemon another Hermes started, never reaches the supervisor.
    Runs after every browser_exec (and while a long one runs, until the daemon is owned): a pid
    already owned for this daemon costs one file read.

    Returns True once the daemon is owned, False when it can never be (Windows, a managed worker, a
    daemon another Hermes started), None when there is nothing to own yet."""
    if os.name != "posix":
        return False  # Windows: the harness daemon is outside this process's job; nothing to register.
    from agent.runtime_session_store import is_worker_process
    if is_worker_process():
        # A managed per-turn worker never owns it, so a killed worker does not take the browser the
        # next turn's worker drives along (its clean exit still runs the atexit browser cleanup).
        return False
    import psutil

    daemon = daemon_identity(env)
    pid = _published_daemon_pid(env)
    if pid is None:
        logger.debug("browser_exec: no harness daemon pid published for %s", daemon[-1])
        return None
    try:
        with _owned_daemon_groups_lock:
            if _owned_daemon_groups.get(daemon) == pid:
                return True
        proc = psutil.Process(pid)
        argv = proc.cmdline()
        if (os.getsid(pid) != pid or proc.uids().real != os.getuid()  # windows-footgun: ok — posix-only, returned above
                or argv[argv.index("-m") + 1] != _DAEMON_MODULE):
            return None
        if not spawned_by_this_process(pid):
            return False  # another Hermes on the same BU_NAME started it: that Hermes owns it
    except (OSError, ValueError, IndexError, psutil.Error) as exc:
        logger.debug("browser_exec: no harness daemon to supervise for %s: %s", daemon[-1], exc)
        return None
    with _owned_daemon_groups_lock:
        replaced = _owned_daemon_groups.get(daemon)
        _owned_daemon_groups[daemon] = pid
    own_process_groups([pid])
    register_child(pid, "browser-harness-daemon")
    if replaced is not None:
        release_process_group_once_empty(replaced, timeout=0)  # the daemon it replaced; a survivor stays covered
    return True


def _route_exec(env: dict, session: str, task_id: Optional[str], local: bool, local_sidecar: bool,
                route_key: str) -> tuple[Optional[str], bool]:
    """``_route_backend`` for one browser_exec call; returns ``(error, runs on the local sidecar)``.
    A real-profile fallback is remembered per route: the route keeps its fresh browser and the note
    waits until a result carries it (``_take_real_profile_note``)."""
    route_err = _route_backend(env, session, task_id, local, force_local_sidecar=local_sidecar,
                               real_profile=route_key not in _real_profile_fallback)
    note = env.pop(_REAL_PROFILE_FALLBACK_SENTINEL, None)
    if note:
        _real_profile_fallback.setdefault(route_key, note)
    return route_err, local_sidecar or bool(env.pop(_LOCAL_FALLBACK_SENTINEL, None))


def _route_exec_current(env: dict, session: str, task_id: Optional[str], local: bool,
                        local_sidecar: bool, route_key: str) -> tuple[dict, Optional[str], bool, int]:
    """``_route_exec`` on a copy of ``env`` -> ``(routed env, error, sidecar, generation)``, re-routed (at
    most 3 tries) when /browser connect published meanwhile; ``_reload_if_republished`` covers dispatch."""
    for _attempt in range(3):
        routed = dict(env)
        generation = _live_generation()
        route_err, sidecar = _route_exec(routed, session, task_id, local, local_sidecar, route_key)
        if route_err or _live_generation() == generation:
            break
    return routed, route_err, sidecar, generation


def _reload_if_republished(env: dict, generation: int) -> None:
    """Stop this call's daemon if the profile published a new endpoint while it spawned on the old one."""
    if _live_generation() == generation:
        return
    daemon = daemon_identity(env)
    with _driven_daemons_lock:
        _driven_daemons.pop(daemon, None)
    _reload_daemons([daemon])


def _take_real_profile_note(route_key: str) -> dict:
    """The fallback note for the first result this route returns after falling back, then nothing."""
    if route_key not in _real_profile_fallback or route_key in _real_profile_note_delivered:
        return {}
    _real_profile_note_delivered.add(route_key)
    return {"real_profile_unavailable": _real_profile_fallback[route_key]}


def browser_exec(code: str, session: str = "", timeout_s: int = _DEFAULT_TIMEOUT_S,
                 task_id: Optional[str] = None, local: bool = False,
                 session_id: Optional[str] = None, turn_id: Optional[str] = None):
    """Run Python code through the browser-use CLI, and return its output"""
    from agent.redact import redact_sensitive_text
    from tools.registry import tool_error, tool_result
    if not code or not code.strip():
        return tool_error("No code provided. Pass Python that uses the pre-imported helpers, e.g. new_tab(\"https://example.com\") then print(page_info()).")

    # The verdict is carried into the route, never re-derived: the predicate resolves DNS, so a second
    # evaluation could relax the floor and still pick the cloud for a rebinding host.
    verdict = _code_url_verdict(code)
    if verdict.blocked:
        return tool_error(verdict.blocked)
    # One endpoint per call: any private URL sends the whole call to the local sidecar. A call with no URL
    # literal stays on the route the previous call took.
    route_key = _backend_cache_key(task_id, session)
    local_sidecar = verdict.local_sidecar if _URL_RE.search(code) else _exec_route_is_sidecar.get(route_key, False)

    cmd = _find_cli()
    if not cmd:
        return tool_error("browser-harness is missing from Hermes's Python environment. "
                          "Run `hermes update` to re-sync it.")

    try:
        env = _base_subprocess_env()
    except VendorPathError as exc:
        return tool_error(f"browser_exec refused: {exc}")
    if session:
        if not _SESSION_RE.match(session):
            return tool_error(f"Invalid session name {session!r}: use 1-64 letters, digits, "
                              "dashes, or underscores (e.g. 'r7k2').")
        env["BU_NAME"] = session
    env, route_err, local_sidecar, generation = _route_exec_current(
        env, session, task_id, bool(local), local_sidecar, route_key)
    if route_err:
        return tool_error(route_err)
    if local_sidecar:
        env["BU_NAME"] = _sidecar_daemon_name(session)
    fit_default_socket_dir(env)  # BU_NAME is final: a deep HOME's socket would overflow sun_path
    _exec_route_is_sidecar[route_key] = local_sidecar
    local_engine = env.pop(_BOT_DESKTOP_BROWSER_SENTINEL, None)
    bot_desktop_browser = bool(local_engine)
    _exec_local_engine.set(local_engine)  # only local browsers carry the sentinel
    autospawn = env.pop(_AUTOSPAWN_SENTINEL, None)
    cloud_id = env.pop(_CLOUD_ID_SENTINEL, None)

    # A tool call with session/turn identity creates its own target and pins the
    # harness to it. Named-session own-tab preamble stays only for direct callers
    # that are not the panel/tool path.
    private_browser = env.pop(_PRIVATE_BROWSER_SENTINEL, None)  # always pop: never exported to the CLI
    owned_gate = None
    owned_state = None
    if session_id is not None or turn_id is not None:
        from tools.browser_task_registry import RegistryError
        try:
            code, owned_gate, owned_state = _pin_owned_target(
                env, code, task_id, session_id, turn_id, autospawn=autospawn, cloud_id=cloud_id)
        except RegistryError as exc:
            return tool_error(f"browser_exec refused: {exc}")
    elif session and not private_browser:
        code = _OWN_TAB_PREAMBLE + code
    result = None
    try:
        result = _browser_exec_dispatch(
            cmd, code, env, timeout_s, task_id, session, bot_desktop_browser, generation, route_key)
    finally:
        reg_error = _finish_owned(owned_gate, owned_state)
    if reg_error and _exec_succeeded(result):
        return tool_error(f"browser_exec refused: {reg_error}")
    return result


def _exec_succeeded(result) -> bool:
    if not isinstance(result, str):
        return False
    try:
        body = json.loads(result)
    except json.JSONDecodeError:
        return False
    return body.get("success") is True and "error" not in body


def _browser_exec_dispatch(cmd, code, env, timeout_s, task_id, session, bot_desktop_browser,
                           generation, route_key):
    from agent.redact import redact_sensitive_text
    from tools.registry import tool_error, tool_result
    # Every backend: capture_screenshot() falls back to jpeg instead of hanging on a png a hidden tab never
    # renders; only on a cloud browser does it (and new_tab) bring the tab to the front (tools/browser_use_shot.py).
    from tools.browser_use_shot import ACTIVATE_NEVER, harness_command
    cmd = harness_command(cmd, activate=env.pop(_CLOUD_BROWSER_SENTINEL, None) or ACTIVATE_NEVER)

    workspace = _workspace_dir(task_id)
    if workspace:
        env["BH_AGENT_WORKSPACE"] = workspace

    # BU_AUTOSPAWN makes the CLI start a Browser Use cloud browser when no local
    # Chrome/CDP endpoint is reachable (their API key authenticates it)
    if "BU_AUTOSPAWN" not in env and is_legacy_browser_use_cloud_config(_read_browser_cfg()):
        env["BU_AUTOSPAWN"] = "1"

    timeout = _clamp_timeout(timeout_s)
    started = time.time()

    def dispatch() -> dict[str, Any]:
        _attach_vault_supervisor(env, task_id)
        with _driven_daemons_lock:
            daemon = daemon_identity(env)
            _driven_daemons[daemon] = hermes_home_key()
        # The first call starts the daemon and may run long (navigation, user code): own it as soon as
        # it publishes its pid, so a Hermes killed mid-call takes it along (the call's end owns it too).
        from agent.periodic_scheduler import schedule
        owning = schedule(lambda: _own_harness_daemon_until_owned(env), _OWN_DURING_CALL_S)
        try:
            return {"proc": _run_cli_killing_process_group(cmd, code, env, timeout)}
        except subprocess.TimeoutExpired:
            return {"error_result": tool_error(
                f"browser-use exec timed out after {timeout}s. The daemon may still be working; retry "
                f"with a larger timeout_s (max {_MAX_TIMEOUT_S}), or split the work into several calls that "
                "append to workspace files — anything already written to the workspace is preserved."
            )}
        except OSError as e:
            return {"error_result": tool_error(f"Failed to launch browser-use CLI: {e}")}
        finally:
            owning.cancel(wait=5)  # an in-flight check finishes before the post-call one runs

    if bot_desktop_browser:
        from tools.browser_session import run_fenced
        dispatched = run_fenced({"features": {"local": True}}, dispatch)
    else:
        dispatched = dispatch()
    _reload_if_republished(env, generation)
    _own_harness_daemon(env)
    if "proc" not in dispatched:
        if "error_result" in dispatched:
            return dispatched["error_result"]
        return tool_result(dispatched)
    proc = dispatched["proc"]

    # browser_vault fill registers injected values with this forced model-egress
    # boundary. Preserve raw stdout only for screenshot-path detection below.
    result = {
        "success": proc.returncode == 0,
        "exit_code": proc.returncode,
        "output": redact_sensitive_text(proc.stdout, force=True),
    }
    if workspace:
        result["workspace"] = workspace
    if session:
        result["session"] = session
    result.update(_take_real_profile_note(route_key))
    stderr = redact_sensitive_text((proc.stderr or "").strip(), force=True)
    if len(stderr) > _STDERR_CAP_CHARS:
        stderr = stderr[:_STDERR_CAP_CHARS] + "\n… (stderr truncated)"
    if stderr:
        result["stderr"] = stderr
    screenshot = _find_screenshot(proc.stdout, started)
    if screenshot:
        result["screenshot_path"] = screenshot
        native = _native_screenshot_result(result, screenshot)
        if native is not None:
            return native
    return tool_result(result)


_HEADER_BASE = (
    "Drive a real browser for JS pages, logins, forms and clicks (Browser Use CLI). `code` runs as full "
    "Python (stdlib available) with pre-imported browser helpers; stdout comes back in the result. Start `code` with a one-line "
    "comment describing the step for the user in plain language, max 60 chars "
    "(e.g. `# Searching Amazon for paper towels`) — the UI shows it as the step label.\n\n"
    "STATE: the browser session and workspace persist across calls; Python variables do NOT (fresh "
    "interpreter each call). The workspace dir is $BH_AGENT_WORKSPACE (also `workspace` in every result); "
    "functions defined in agent_helpers.py there are auto-imported into every call. For multi-item tasks "
    "('all N products / every entry'), append each batch to a JSON/CSV file in the workspace, then read it "
    "back and aggregate in code — dedupe/count/sort with Python, not in your head — and verify the "
    "collected count against what was asked before answering.\n\n"
    "Batch each sub-procedure (navigate, wait, extract, act) into one call — do not spend a call per "
    "action — but for long extractions prefer several medium calls that append to workspace files over "
    "one giant call, so progress survives timeouts."
)

_HEADER_VISION = (
    " Screenshots are attached to your context automatically: when the exec output contains a "
    "capture_screenshot() path, the image arrives with this tool's result and you inspect it directly "
    "with your own vision — never send browser screenshots to a separate vision tool."
)

_TEXT_FIRST = (
    "page_info() for state, js() for reading/extracting DOM text, fill_input(selector, text) for inputs, and "
    "js(\"document.querySelector('…').click()\") for clicks."
)

# The non-native header states a transport fact (screenshots do not ride back inside THIS
# tool's results: routing is not ``native`` — e.g. an explicit ``auxiliary.vision`` pin — or
# the route rejects tool-result media), never a verdict on the model's own vision: models
# quote "your model cannot view images" against themselves and skip user images (#131568).
# model_tools.get_tool_definitions() appends the vision_analyze pointer after
# SCREENSHOTS_DETACHED when the session actually has that tool.
SCREENSHOTS_DETACHED = " Screenshots are not attached to this tool's results."

_HEADER_TEXT_ONLY = SCREENSHOTS_DETACHED + " Work text-first: " + _TEXT_FIRST

# Lightpanda: no graphical renderer, and one CDP connection holds one page — a second
# Target.createTarget fails with TargetAlreadyLoaded (drop the new_tab() sentence once
# lightpanda-io/browser#1962 lands).
_HEADER_LIGHTPANDA = (
    " The local engine is Lightpanda (no graphical renderer, one page per session): capture_screenshot() "
    "is unavailable, so work text-first: " + _TEXT_FIRST + " Navigate with new_tab(url) exactly once, then "
    "goto_url(url) for every later navigation — a second new_tab() fails with TargetAlreadyLoaded."
)

# Pinned quick-reference for the CLI's pre-imported helpers, replacing the live
# ``browser-use skill`` fetch (uncontrolled third-party text in every schema: version
# drift, supply-chain exposure, byte-unstable prompt). A/B benchmarked ~equal.
_HELPERS_DIGEST = (
    "\n\nHELPERS (pre-imported): new_tab(url) opens/navigates (use for the FIRST navigation), goto_url(url) "
    "navigates the current tab, wait_for_load() after navigation, page_info() summarizes the current page "
    "state, js(expr) evaluates a JS expression and returns its value (js('document.title'); wrap function "
    "bodies as js('(() => {...})()') — a bare '() => {...}' returns the function itself, uncalled), "
    "fill_input(selector, text) types into inputs, click_at_xy(x, y) clicks viewport coordinates, "
    "capture_screenshot() saves and prints a screenshot path, cdp('Domain.method', **kwargs) is raw CDP — "
    "cdp('Accessibility.getFullAXTree')['nodes'] lists every element's role/name/backendDOMNodeId (filter "
    "in Python before printing; it is thousands of nodes), then cdp('DOM.getBoxModel', backendNodeId=n) "
    "gives click coordinates. ensure_real_tab() recovers from a stale/internal tab. Login walls: never guess "
    "credentials; see the vault note below if present, otherwise stop and ask the user."
)


def _description_header() -> str:
    """Header tailored to whether screenshots ride back natively in this tool's results."""
    if _lazy_call("tools.browser_lightpanda", "lightpanda_engine_status", (False, ""),
                  "lightpanda engine status unavailable")[0]:  # no screenshots, whatever the model sees
        return _HEADER_BASE + _HEADER_LIGHTPANDA
    if _lazy_call("tools.vision_tools", "_should_use_native_vision_fast_path", False, ""):
        return _HEADER_BASE + _HEADER_VISION
    return _HEADER_BASE + _HEADER_TEXT_ONLY


def _dynamic_schema_overrides() -> dict:
    overrides: dict = {"description": _description_header() + _HELPERS_DIGEST}
    # ``local`` exists ONLY when the user consented to real-profile browsing — everyone
    # else's schema carries zero extra surface. The caller memoizes on config.yaml mtime,
    # so toggling consent applies next session, not mid-chat.
    if _real_profile_consented():
        props = dict(BROWSER_EXEC_SCHEMA["parameters"]["properties"])
        props["local"] = {
            "type": "boolean", "default": False,
            "description": ("Drive the user's own local browser (a clawd-managed copy of their real "
                            "default-Chromium profile, logins/cookies included) instead of the configured "
                            "cloud browser backend. Use when the user asks to act as themselves — their "
                            "accounts, their sessions. No-op when the backend is already local. Default false."),
        }
        overrides["parameters"] = {**BROWSER_EXEC_SCHEMA["parameters"], "properties": props}
    return overrides


BROWSER_EXEC_SCHEMA = {
    "name": "browser_exec",
    # Static fallback description, used only when the managed CLI is unavailable
    "description": (_HEADER_BASE + _HELPERS_DIGEST
                    + "\n\n(The browser-use CLI is not installed yet. Install it with `clawd tools` (Browser Automation → Browser Use).)"),
    "parameters": {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "Python code to execute using the pre-imported browser helpers. Use print(...) for any data you need back."},
            "session": {"type": "string", "description": "Named isolated browser session — its own daemon and (on cloud backends) own browser, so concurrent tasks don't share tabs. Reuse the same name on every related call; omit for the shared default session."},
            "timeout_s": {"type": "integer", "default": _DEFAULT_TIMEOUT_S,
                          "description": f"Max seconds to wait for the code to finish (default {_DEFAULT_TIMEOUT_S}, max {_MAX_TIMEOUT_S})."},
        },
        "required": ["code"],
    },
}


# browser_exec is additionally gated at tool-definition time — sessions whose toolsets
# lack ``terminal`` never see it (model_tools._compute_tool_definitions). check_fn only
# answers "is browser-harness importable"; surface policy lives with the session.
from tools.registry import registry


# The LOCAL browser this context's browser_exec call ran on ("local" Chromium or "lightpanda", from its session),
# else None: a configured cloud provider did not serve a hybrid ::local sidecar, cloud-fallback or real-profile
# call, and browser.engine read afterwards may name another engine than the session's, so neither labels it.
_exec_local_engine: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("browser_exec_local_engine",
                                                                                   default=None)


def _handle_browser_exec(args: dict, task_id: Optional[str] = None, session_id: Optional[str] = None, **kw):
    """Registry handler. Refuses without the turn's existing task, session, and turn ids."""
    from hermes_cli.observability.shared_metrics_loop import record_browser_call
    from tools.browser_session_cloud import browser_backend_name
    from tools.browser_task_registry import IdentityError, resolve_call_identity
    from tools.registry import tool_error
    try:
        task, session, turn = resolve_call_identity(task_id, session_id)
    except IdentityError as exc:
        return tool_error(f"browser_exec refused: {exc}")
    token = _exec_local_engine.set(None)
    try:
        return record_browser_call(lambda: browser_exec(
            code=args.get("code", ""), session=args.get("session", "") or "",
            timeout_s=args.get("timeout_s", _DEFAULT_TIMEOUT_S), task_id=task,
            local=bool(args.get("local", False)), session_id=session, turn_id=turn,
        ), lambda: browser_backend_name(local_engine=_exec_local_engine.get()))
    finally:
        _exec_local_engine.reset(token)


registry.register(
    name="browser_exec",
    toolset="browser-use",
    schema=BROWSER_EXEC_SCHEMA,
    handler=_handle_browser_exec,
    check_fn=browser_exec_available,
    dynamic_schema_overrides=_dynamic_schema_overrides,
    emoji="🌐",
)
