"""clawd panel entry. Core calls clawd_server.__main__.main(argv)."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SYSTEM_SRC = Path("/usr/local/lib/hermes-agent")


def _under_production(path: Path) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    if resolved == _SYSTEM_SRC:
        return True
    try:
        resolved.relative_to(_SYSTEM_SRC)
        return True
    except ValueError:
        return False


if (_ROOT / "pyproject.toml").is_file() and not _under_production(_ROOT) and str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _password() -> str:
    password = os.environ.get("CLAWD_PANEL_PASSWORD", "")
    if password:
        return password
    if sys.stdin.isatty():
        import getpass
        return getpass.getpass("password: ")
    return sys.stdin.read().rstrip("\n")


def resolve_serve_static(explicit: str | None) -> str:
    """Static directory for this layout. Does not build the frontend."""
    if explicit:
        return explicit
    from clawd_server.resources import panel_static_dir

    return str(panel_static_dir())


def main(argv: list[str] | None = None) -> int:
    if _under_production(_ROOT):
        print("refusing production Hermes tree as clawd import root", file=sys.stderr)
        return 2
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "panel":
        args = args[1:]
    parser = argparse.ArgumentParser(prog="clawd-panel")
    sub = parser.add_subparsers(dest="cmd")
    serve = sub.add_parser("serve")
    serve.add_argument("--home", default=None)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8787)
    serve.add_argument("--static", default=None)
    user = sub.add_parser("user")
    user_sub = user.add_subparsers(dest="user_cmd", required=True)
    add = user_sub.add_parser("add")
    add.add_argument("--home", default=None)
    add.add_argument("--user", required=True)
    if not args:
        args = ["serve"]
    parsed = parser.parse_args(args)
    if parsed.cmd == "user":
        from clawd_server.auth import add_user
        from clawd_server.home import resolve_home

        home = resolve_home(parsed.home)
        password = _password()
        if not password:
            print("password is required", file=sys.stderr)
            return 2
        add_user(home, parsed.user, password)
        print("user added")
        return 0
    from clawd_server.resources import ResourceError, export_resource_env
    from clawd_server.server import run_serve

    try:
        export_resource_env(require_panel=True)
        static = resolve_serve_static(parsed.static)
    except ResourceError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return run_serve(parsed.home, parsed.host, parsed.port, static)


if __name__ == "__main__":
    raise SystemExit(main())
