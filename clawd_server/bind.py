"""Bind the panel core to a clawd home and refuse production fallbacks."""

from __future__ import annotations

import importlib
import importlib.util
import logging
import os
import sys
from pathlib import Path

from clawd_server.home import REPO_ROOT

log = logging.getLogger("clawd.panel")
_SYSTEM_HERMES = Path("/usr/local/bin/hermes")
_SYSTEM_SRC = Path("/usr/local/lib/hermes-agent")


def _refuse(mod, status: int, code: str, message: str):
    raise mod.ApiError(status, code, message)


def prepare_env(home: Path, api_base: str) -> None:
    """Set isolation env before the panel-core module is imported."""
    os.environ["CLAWD_HOME"] = str(home)
    os.environ["HERMES_HOME"] = str(home)
    os.environ["HERMES_SRC"] = str(REPO_ROOT)
    os.environ["HERMES_INSTALL"] = str(REPO_ROOT)
    os.environ["HERMES_PY"] = sys.executable
    os.environ.pop("PANEL_LIVE_TASK_DB", None)
    from clawd_server.resources import panel_root

    os.environ["PANEL_LIVE_STORE_MODULE"] = str(REPO_ROOT / "clawd_server" / "live_task_store.py")
    from clawd_server.live_browser_routes import panel_live_cdp

    os.environ["PANEL_LIVE_CDP"] = panel_live_cdp(home)
    os.environ["PANEL_API_KEY_FILE"] = str(home / "panel" / "api-server.key")
    os.environ["PANEL_API_SERVER"] = api_base
    os.environ["PANEL_SIDECAR_KEY_FILE"] = str(home / "panel" / "sidecar.key")
    os.environ.pop("HERMES_BIN", None)


def clawd_command(home: Path) -> str:
    """Explicit in-repo command. Never /usr/local/bin/hermes."""
    path = home / "bin" / "clawd"
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    script = (
        "#!/bin/sh\n"
        f"export CLAWD_HOME={_sh(home)}\n"
        'export HERMES_HOME="$CLAWD_HOME"\n'
        f"export PYTHONPATH={_sh(REPO_ROOT)}\n"
        f"exec {_sh(sys.executable)} -m clawd_server.cli_entry \"$@\"\n"
    )
    path.write_text(script, encoding="utf-8")
    path.chmod(0o700)
    return str(path)


def _sh(value: Path | str) -> str:
    import shlex
    return shlex.quote(str(value))


def bind(mod, home: Path, command: str) -> None:
    """Rebind paths and disable production restarts plus the legacy continue thread."""
    mod.HERMES_HOME = home
    mod.HERMES_BIN = command
    mod.HERMES_SRC = REPO_ROOT
    mod.HERMES_INSTALL = REPO_ROOT
    mod.HERMES_PY = sys.executable
    mod.LOG_DIR = home / "logs"
    mod.KEY_FILE = home / "panel" / "sidecar.key"
    mod.LIVE_TASK_DB = home / "panel" / "live-tasks.sqlite3"
    mod.CONTINUE_KEY_FILE = home / "panel" / "api-server.key"
    mod.CONTINUE_API = os.environ.get("PANEL_API_SERVER", "http://127.0.0.1:9")
    mod.LIVE_CDP = os.environ.get("PANEL_LIVE_CDP", getattr(mod, "LIVE_CDP", "127.0.0.1:9223"))
    mod.FILE_ROOTS.clear()
    mod.FILE_ROOTS.update({
        "uploads": {"path": home / "uploads", "label": "上传", "writable": True, "create": True},
        "workspace": {"path": home / "workspace", "label": "工作区", "writable": True, "create": True},
        "cron": {"path": home / "cron" / "output", "label": "定时任务输出", "writable": False, "create": False},
        "images": {"path": home / "images", "label": "图片", "writable": False, "create": False},
    })
    for spec in mod.FILE_ROOTS.values():
        if spec["create"]:
            spec["path"].mkdir(parents=True, mode=0o700, exist_ok=True)
        else:
            spec["path"].mkdir(parents=True, mode=0o700, exist_ok=True)
    mod.DENY_PREFIXES = (Path("/etc"), Path("/root/.ssh"), home / ".env", Path("/root/.hermes"), _SYSTEM_SRC)
    mod.LOG_JOURNAL = {}
    # Query this clawd process's actual systemd unit, not old Hermes services.
    units = []
    try:
        for line in Path("/proc/self/cgroup").read_text(encoding="utf-8").splitlines():
            fields = line.split(":", 2)
            if len(fields) != 3:
                continue
            for part in fields[2].split("/"):
                if part.startswith("clawd") and part.endswith(".service"):
                    unit = part.removesuffix(".service")
                    if unit not in units:
                        units.append(unit)
    except OSError:
        log.exception("cannot read clawd process service ownership")
    mod.SERVICES = [(unit, "clawd 服务") for unit in units]
    if not units:
        log.warning("clawd has no discoverable systemd unit; not inventing a stopped service")
    mod.SERVICES.extend([
        ("nginx", "Nginx"),
        ("x-ui", "x-ui 代理"),
        ("moss-agent", "Moss 监控"),
        ("ssh", "SSH"),
    ])
    mod.RESTARTABLE = set()

    def restart_service(name: str, force: bool = False):
        del force
        _refuse(mod, 403, "restart_disabled", "clawd 不重启系统服务；生命周期由 clawd panel 统一管理")

    def start_update(confirm):
        del confirm
        _refuse(mod, 403, "update_disabled", "clawd 不通过面板更新或重启系统 Hermes")

    def get_update_plan():
        return {"plan": "clawd 不执行系统 hermes update", "exitCode": 0}

    def get_update_status():
        return {"running": False, "state": "idle", "runId": None, "logTail": "系统更新单元已禁用"}

    mod.restart_service = restart_service
    mod.start_update = start_update
    mod.get_update_plan = get_update_plan
    mod.get_update_status = get_update_status
    saved_watch = mod._continue_watcher
    saved_tick = getattr(mod, "_continue_tick", None)
    mod._clawd_saved_continue_watcher = saved_watch
    mod._clawd_saved_continue_tick = saved_tick

    def _blocked(*_args, **_kwargs):
        raise RuntimeError("panel auto-continue is disabled; gateway/clawd_continuation owns scheduling")

    mod._continue_watcher = _blocked
    mod._continue_tick = _blocked
    from clawd_server.native import file_roots_under_home, install

    install(mod)
    file_roots_under_home(mod, home)
    if Path(mod.HERMES_BIN).resolve() == _SYSTEM_HERMES or Path(mod.HERMES_SRC).resolve() == _SYSTEM_SRC:
        raise RuntimeError("panel core still points at a system Hermes install")
    log.info("legacy panel auto-continue watcher disabled; scheduling owned by gateway/clawd_continuation")


def continuation_status() -> str:
    spec = importlib.util.find_spec("gateway.clawd_continuation")
    if spec is None:
        return "missing"
    return "present-not-started"
