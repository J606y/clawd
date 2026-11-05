"""clawd command entry.

Isolates data directories before any Hermes import, then dispatches to the
existing CLI. Official web/desktop and upstream update/install paths are
refused here so they cannot restore those products or overwrite an existing
Hermes install.
"""

from __future__ import annotations

import os
import sys

_REFUSALS = {
    "dashboard": "refused: clawd does not provide the official web dashboard. Use `clawd panel`.",
    "desktop": "refused: clawd does not provide the official desktop app.",
    "gui": "refused: clawd does not provide the official desktop app.",
    "webapp": "refused: clawd does not provide the official desktop webapp.",
    "update": (
        "refused: clawd does not run the upstream Hermes update. "
        "That path would restore the official UI or overwrite an existing Hermes install."
    ),
}


def _command_name(argv: list[str]) -> str:
    from hermes_cli._parser import command_argv

    words = command_argv(argv)
    return words[0] if words else ""


def _panel_argv(argv: list[str]) -> list[str]:
    from hermes_cli._parser import command_argv

    words = command_argv(argv)
    if words and words[0] == "panel":
        return words[1:]
    return []


def dispatch_panel(argv: list[str]) -> None:
    """Call ``clawd_server.__main__.main(argv)``. Do not invent that package."""
    try:
        from clawd_server.__main__ import main as panel_main
    except ModuleNotFoundError as exc:
        missing = (exc.name or "").split(".", 1)[0]
        if missing not in {"clawd_server", ""}:
            raise
        print(
            "refused: clawd panel requires clawd_server, which is not available in this environment.",
            file=sys.stderr,
        )
        raise SystemExit(2) from exc
    sys.argv = ["clawd-panel", *argv]
    result = panel_main(argv)
    if isinstance(result, int) and result != 0:
        raise SystemExit(result)


def _refuse(command: str) -> None:
    print(_REFUSALS[command], file=sys.stderr)
    raise SystemExit(2)


def _pin_clawd_bin() -> None:
    """Name this process's clawd launcher for TUI children. Never point them at hermes."""
    argv0 = sys.argv[0] if sys.argv else ""
    candidate = os.path.abspath(argv0) if argv0 and not argv0.startswith("-") else ""
    if candidate and os.path.basename(candidate) in {"clawd", "clawd.exe"} and os.path.isfile(candidate):
        os.environ["CLAWD_BIN"] = candidate
        return
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    launcher = os.path.join(root, "clawd")
    if os.path.isfile(launcher):
        os.environ["CLAWD_BIN"] = launcher


def main(argv: list[str] | None = None) -> None:
    """Product entry. Sets CLAWD_HOME isolation, then runs the shared CLI."""
    from hermes_constants import isolate_inherited_hermes_env

    isolate_inherited_hermes_env()
    os.environ["CLAWD_CLI"] = "1"
    _pin_clawd_bin()
    if argv is not None:
        sys.argv = [sys.argv[0] or "clawd", *argv]
    args = sys.argv[1:]
    command = _command_name(args)
    if command == "panel":
        dispatch_panel(_panel_argv(args))
        return
    if command in _REFUSALS:
        _refuse(command)
    from hermes_cli.main import main as hermes_main

    hermes_main()


if __name__ == "__main__":
    main()
