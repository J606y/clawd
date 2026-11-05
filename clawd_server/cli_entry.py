"""In-repo config/skill command. Not /usr/local/bin/hermes."""

from __future__ import annotations

import os
import sys
from pathlib import Path

_SYSTEM_HERMES = Path("/usr/local/bin/hermes")
_SYSTEM_SRC = Path("/usr/local/lib/hermes-agent")


def _under_production(path: Path) -> bool:
    try:
        resolved = path.expanduser().resolve()
    except OSError:
        resolved = path
    if resolved == _SYSTEM_SRC or resolved == _SYSTEM_HERMES:
        return True
    try:
        resolved.relative_to(_SYSTEM_SRC)
        return True
    except ValueError:
        return False


def _prepare_import_path() -> int | None:
    root = Path(__file__).resolve().parents[1]
    if _under_production(root):
        print("refusing production Hermes tree as clawd import root", file=sys.stderr)
        return 2
    if (root / "pyproject.toml").is_file() and str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return None


def _drop_production_bin() -> None:
    raw = (os.environ.get("HERMES_BIN") or "").strip()
    if not raw:
        return
    candidate = Path(raw).expanduser()
    if candidate == _SYSTEM_HERMES or _under_production(candidate):
        os.environ.pop("HERMES_BIN", None)
        return
    try:
        resolved = candidate.resolve()
    except OSError:
        return
    if resolved == _SYSTEM_HERMES or _under_production(resolved):
        os.environ.pop("HERMES_BIN", None)


def main(argv: list[str] | None = None) -> int:
    refused = _prepare_import_path()
    if refused:
        return refused
    _drop_production_bin()
    from clawd_server.home import HomeError, resolve_home

    try:
        resolve_home()
    except HomeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if argv is not None:
        sys.argv = [sys.argv[0], *argv]
    from hermes_cli.main import main as cli_main

    result = cli_main()
    return int(result or 0)


if __name__ == "__main__":
    raise SystemExit(main())
