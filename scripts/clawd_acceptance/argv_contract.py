"""Check planned commands against source text. Do not execute them to discover flags.
"""

from __future__ import annotations

from pathlib import Path


def source_text(path: Path, limit: int = 20000) -> str:
    if not path.is_file() or path.is_symlink():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")[:limit]


def has_add_argument(text: str, flag: str) -> bool:
    return f'"{flag}"' in text or f"'{flag}'" in text


def run_tests_argv_ok(repo: Path, argv: list[str]) -> tuple[bool, str]:
    runner = source_text(repo / "scripts" / "run_tests_parallel.py", 80000)
    if '"--file-timeout"' not in runner or '"-j"' not in runner:
        return False, "run_tests_parallel.py is missing -j or --file-timeout"
    if '"--basetemp"' in runner.split("OUR_FLAGS", 1)[-1][:800]:
        return False, "--basetemp is a runner flag; refusing to assume pytest forwarding"
    if "bare_passthrough" not in runner:
        return False, "runner does not document bare pytest forwarding"
    if argv[:1] != ["bash"] or not str(argv[1]).endswith("run_tests.sh"):
        return False, "core command is not scripts/run_tests.sh"
    if argv.count("tests") != 1:
        return False, "core discovery must be the single tests root"
    if "-j" not in argv or "--file-timeout" not in argv or "--basetemp" not in argv:
        return False, "core argv missing a verified runner or pytest flag"
    return True, "matches run_tests_parallel.py argparse; --basetemp is forwarded, not executed here"


def inventory_argv_ok(repo: Path, argv: list[str]) -> tuple[bool, str]:
    text = source_text(repo / "scripts" / "clawd_inventory" / "generate.py")
    if not has_add_argument(text, "--checkout") or not has_add_argument(text, "--output"):
        return False, "generate.py argparse has no --checkout/--output"
    if "--offline" in argv:
        return False, "generate.py has no --offline flag; not inventing one"
    if "不下载" not in text and "离线" not in text:
        return False, "inventory source no longer declares offline reads"
    if len(argv) < 4 or argv[2] != "--checkout" or "--output" not in argv:
        return False, "license command does not match generate.py"
    return True, "matches generate.py; this lane does not write inventory data"


def cutover_rollback_argv_ok(repo: Path, argv: list[str]) -> tuple[bool, str]:
    text = source_text(repo / "scripts" / "clawd_cutover" / "cli.py")
    for flag in ("--allow-root", "--target", "--journal", "--confirm", "--previous"):
        if not has_add_argument(text, flag):
            return False, f"cutover cli missing {flag}"
    if 'sub.add_parser("rollback"' not in text and "sub.add_parser('rollback'" not in text:
        return False, "cutover cli has no rollback subcommand"
    if "--home" in argv or "--no-production" in argv:
        return False, "rollback argv uses flags the cutover parser does not define"
    needed = ("rollback", "--allow-root", "--target", "--journal", "--confirm", "ROLLBACK")
    if any(item not in argv for item in needed):
        return False, "rollback argv is not the cutover parser contract"
    return True, "matches clawd_cutover cli.py"


def panel_gates_argv_ok(repo: Path, argv: list[str]) -> tuple[bool, str]:
    text = source_text(repo / "apps" / "panel" / "scripts" / "test-gates.mjs")
    if "--output-dir=" not in text or "--target=" not in text:
        return False, "test-gates.mjs does not accept the joined flags"
    if any(item == "--output-dir" for item in argv):
        return False, "test-gates.mjs rejects a space-separated --output-dir"
    if not any(item.startswith("--output-dir=") for item in argv):
        return False, "browser command missing --output-dir="
    if "--target=browser" not in argv:
        return False, "browser command missing --target=browser"
    return True, "matches test-gates.mjs parseArgs"


def server_argv_ok(repo: Path, argv: list[str]) -> tuple[bool, str]:
    text = source_text(repo / "clawd_server" / "__main__.py")
    for flag in ("--home", "--host", "--port", "--user"):
        if not has_add_argument(text, flag):
            return False, f"clawd_server parser missing {flag}"
    if "serve" not in argv and "user" not in argv:
        return False, "server command is not serve or user add"
    return True, "matches clawd_server argparse"


def clawd_home_contract_ok(repo: Path) -> tuple[bool, str]:
    text = source_text(repo / "hermes_constants.py", 40000)
    if 'CLAWD_DIR_NAME = ".clawd"' not in text:
        return False, "core no longer resolves an unset CLAWD_HOME to ~/.clawd"
    if "return home_of() / CLAWD_DIR_NAME" not in text:
        return False, "resolve_clawd_home no longer uses HOME/.clawd when CLAWD_HOME is unset"
    return True, "env -i forwards HOME; child CLAWD_HOME is HOME/.clawd"
