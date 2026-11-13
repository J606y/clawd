"""CLAWD_HOME resolution. Never falls back to ~/.hermes or /root/.hermes."""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
# Code root only: checkout directory, or site-packages after a wheel install.
# Panel static files and sidecar modules are not assumed to live next to this
# path once installed. Use panel_resource_root() / clawd_server.resources.
PRODUCTION_HOMES = (
    Path("/root/.hermes"),
    Path.home() / ".hermes",
)


class HomeError(SystemExit):
    """Fatal home-resolution error. Message is safe to print."""


def _resolved(path: Path) -> Path:
    return path.expanduser().resolve()


def is_production_home(path: Path) -> bool:
    resolved = _resolved(path)
    for banned in PRODUCTION_HOMES:
        try:
            if resolved == _resolved(banned):
                return True
        except OSError:
            if str(resolved) == str(banned):
                return True
    return False


def resolve_home(explicit: str | None = None) -> Path:
    """Require CLAWD_HOME. HERMES_HOME must be unset or identical. Sets both."""
    raw = (explicit or os.environ.get("CLAWD_HOME") or "").strip()
    if not raw:
        raise HomeError("CLAWD_HOME is required; refusing to use ~/.hermes")
    home = _resolved(Path(raw))
    if is_production_home(home):
        raise HomeError(f"refusing production Hermes home: {home}")
    if not home.is_dir():
        try:
            home.mkdir(parents=True, mode=0o700)
        except OSError as exc:
            raise HomeError(f"cannot create CLAWD_HOME {home}: {exc.strerror}") from exc
    hermes = (os.environ.get("HERMES_HOME") or "").strip()
    if hermes:
        other = _resolved(Path(hermes))
        if other != home:
            raise HomeError("HERMES_HOME must equal CLAWD_HOME; refusing a split home")
        if is_production_home(other):
            raise HomeError("refusing to map HERMES_HOME onto a production home")
    os.environ["CLAWD_HOME"] = str(home)
    os.environ["HERMES_HOME"] = str(home)
    runtime = (os.environ.get("HERMES_RUNTIME_DIR") or "").strip()
    if not runtime:
        os.environ["HERMES_RUNTIME_DIR"] = str(home / "tools")
    return home


def assert_core_home(home: Path) -> None:
    """Fail if the core path helper does not see the same home."""
    from hermes_constants import get_hermes_home

    seen = _resolved(Path(get_hermes_home()))
    if seen != _resolved(home) or is_production_home(seen):
        raise HomeError(f"core home {seen} does not match CLAWD_HOME {home}")


def distribution_layout() -> str:
    from clawd_server.resources import layout

    return layout()


def panel_resource_root() -> Path:
    """Panel tree (dist, sidecar, plugins). Not REPO_ROOT once installed."""
    from clawd_server.resources import panel_root

    return panel_root()


def panel_static_dir() -> Path:
    from clawd_server.resources import panel_static_dir as _static

    return _static()
