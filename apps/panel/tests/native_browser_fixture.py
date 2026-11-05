"""Load native browser ownership. Does not open PANEL_LIVE_TASK_DB or the retired plugin rewrite."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

CLWD_ROOT = Path(__file__).resolve().parents[3]


def ensure_repo() -> None:
    root = str(CLWD_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)


def bind_profile(test, home: Path):
    """Point the profile registry at this home. Restores the previous override."""
    ensure_repo()
    home.mkdir(parents=True, exist_ok=True)
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    token = set_hermes_home_override(home)
    test.addCleanup(lambda: reset_hermes_home_override(token))
    env = patch.dict(os.environ, {"CLAWD_HOME": str(home), "HERMES_HOME": str(home)})
    env.start()
    test.addCleanup(env.stop)
    os.environ.pop("PANEL_LIVE_TASK_DB", None)
    return home


def install_native(mod, test=None):
    """Install the native reader and restore the sidecar callables afterwards."""
    ensure_repo()
    from clawd_server.browser_native import install

    names = ("live_task", "live_task_control", "live_task_input", "_live_task_store", "_live_task_open_gate")
    saved = {name: getattr(mod, name) for name in names}
    marker = getattr(mod, "_clawd_browser_native", None)
    install(mod)

    def restore() -> None:
        for name, value in saved.items():
            setattr(mod, name, value)
        if marker is None and hasattr(mod, "_clawd_browser_native"):
            delattr(mod, "_clawd_browser_native")
        elif marker is not None:
            mod._clawd_browser_native = marker

    if test is not None:
        test.addCleanup(restore)
    return restore


def register_page(*, task_id: str, session_id: str, turn_id: str, target_id: str,
                  browser_id: str = "/devtools/browser/fixture",
                  endpoint: str = "http://127.0.0.1:9") -> None:
    ensure_repo()
    from tools.browser_task_registry import register_created

    register_created(
        task_id=task_id,
        session_id=session_id,
        turn_id=turn_id,
        target_id=target_id,
        browser_id=browser_id,
        endpoint=endpoint,
    )
