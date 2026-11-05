"""Static checks for the admin native adapter. Not executed in this change."""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from clawd_server.admin_native import BLOCKED, REPLACED, install

ROOT = Path(__file__).resolve().parents[2]
ADMIN = ROOT / "clawd_server"


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        self.status = status
        self.code = code
        self.message = message
        super().__init__(message)


def _mod() -> Any:
    return SimpleNamespace(
        ApiError=ApiError,
        CONFIG_KEY_RE=re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-]{0,127}$"),
        _is_secret_key=lambda key: "token" in key or key.endswith(".secret"),
        redact=lambda text: text.replace("secret-value", "***"),
    )


def test_install_binds_replacements_and_refuses_cli():
    mod = _mod()
    install(mod)
    assert mod._clawd_admin_native == REPLACED
    for name in REPLACED:
        assert callable(getattr(mod, name))
    try:
        mod._hermes("config", "get", "model")
    except ApiError as exc:
        assert exc.status == 501
        assert exc.code == "cli_retired"
    else:
        raise AssertionError("cli was not refused")
    try:
        mod.start_update("update")
    except ApiError as exc:
        assert exc.status == 403
        assert exc.code == "update_disabled"
    else:
        raise AssertionError("update was not refused")
    assert mod.get_update_status()["running"] is False
    assert callable(mod.config_set)
    assert callable(mod.preview_delete_profile)
    assert callable(mod.confirm_delete_profile)
    assert "delete_profile" in BLOCKED
    assert "recent_deliveries" in BLOCKED


def test_delete_default_does_not_require_a_home():
    mod = _mod()
    install(mod)
    try:
        mod.delete_profile("default")
    except ApiError as exc:
        assert exc.status == 400
        assert exc.code == "protected"
    else:
        raise AssertionError("default delete was not refused")


def test_secret_config_write_fails_before_scope():
    from clawd_server.admin_common import ConfigError
    from clawd_server.admin_config import commit_set

    mod = _mod()
    try:
        commit_set(mod, "providers.demo.token", "secret-value")
    except ConfigError as exc:
        assert exc.status == 403
        assert exc.code == "secret_key"
    else:
        raise AssertionError("secret write was not refused")


def test_sources_do_not_spawn_or_switch_env():
    for path in sorted(ADMIN.glob("admin_*.py")):
        text = path.read_text(encoding="utf-8")
        assert "subprocess" not in text, path.name
        assert "os.environ[" not in text, path.name
        assert "HERMES_BIN" not in text, path.name
        assert "redirect_stdout" not in text, path.name
    profiles = (ADMIN / "admin_profiles.py").read_text(encoding="utf-8")
    assert "profiles.delete_profile" not in profiles
    assert "profile_delete_confirmation_required" in profiles
    config = (ADMIN / "admin_config.py").read_text(encoding="utf-8")
    assert "def config_set" in config
    assert "publish=False" in config
    native = (ADMIN / "admin_native.py").read_text(encoding="utf-8")
    assert "mod.config_set" in native
