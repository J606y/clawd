"""Isolation plans. Parent exports are not treated as pytest child isolation."""

from __future__ import annotations

from pathlib import Path

from constants import ENV_I_FORWARDED, ENV_I_STRIPPED, NODE_BIN, PYTEST_FILE_TIMEOUT_SECONDS, PYTHON


class Layout:
    def __init__(self, root: Path) -> None:
        self.root = root
        # env -i keeps HOME and drops CLAWD_HOME. Core then uses HOME/.clawd.
        self.account_home = root / "account-home"
        self.home = self.account_home / ".clawd"
        self.tmp = root / "tmp"
        self.basetemp = root / "pytest-basetemp"
        self.logs = root / "logs"
        self.locks = root / "locks"
        self.wheel = root / "wheel"
        self.pids = root / "pids"

    def ensure(self) -> None:
        for path in (self.account_home, self.home, self.tmp, self.basetemp, self.logs, self.locks, self.wheel, self.pids):
            path.mkdir(parents=True, exist_ok=True)


def direct_env(layout: Layout, repo: Path, extra: dict[str, str] | None = None) -> dict[str, str]:
    """Explicit subprocess env. Does not copy os.environ."""
    env = {
        "PATH": f"{NODE_BIN}:{PYTHON.parent}:/usr/bin:/bin",
        "HOME": str(layout.home),
        "CLAWD_HOME": str(layout.home),
        "HERMES_HOME": str(layout.home),
        "TMPDIR": str(layout.tmp),
        "TEMP": str(layout.tmp),
        "TMP": str(layout.tmp),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
        "PYTHONUTF8": "1",
        "PYTHONHASHSEED": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
        "HERMES_PYTHON": str(PYTHON),
        "HERMES_NODE": str(NODE_BIN / "node"),
        "HERMES_TUI_DIR": str(repo / "ui-tui"),
        "CLAWD_CLI": "1",
        "TERM": "xterm-256color",
    }
    if extra:
        env.update(extra)
    return env


def run_tests_parent_env(layout: Layout) -> dict[str, str]:
    """Environment for scripts/run_tests.sh.

    HOME survives env -i. CLAWD_HOME does not, so the forwarded HOME is the
    parent of the intended data directory. HERMES_HOME is set to that data
    directory before the wrapper starts, which keeps the wrapper from treating
    the faked HOME as a reason to read the account production tool store.
    HERMES_HOME itself is stripped from pytest children.
    """
    return {
        "PATH": f"{NODE_BIN}:{PYTHON.parent}:/usr/bin:/bin",
        "HOME": str(layout.account_home),
        "HERMES_PYTHON": str(PYTHON),
        "HERMES_GATEWAY_LOCK_DIR": str(layout.locks),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
        "CLAWD_HOME": str(layout.home),
        "HERMES_HOME": str(layout.home),
        "TMPDIR": str(layout.tmp),
    }


def run_tests_plan(repo: Path, layout: Layout) -> dict:
    return {
        "wrapper": "scripts/run_tests.sh",
        "mechanism": "env -i",
        "pretend_global_env": False,
        "forwarded": list(ENV_I_FORWARDED),
        "stripped": list(ENV_I_STRIPPED),
        "argv": core_argv(repo, layout),
        "parent_env": run_tests_parent_env(layout),
        "child_clawd_home": str(layout.home),
        "child_resolution": "unset CLAWD_HOME resolves to HOME/.clawd",
        "matches_core_resolution": layout.home == layout.account_home / ".clawd",
        "production_credentials_read": False,
        "arrangement": [
            "HOME is the isolated account home and is the home value forwarded by env -i",
            "child CLAWD_HOME is HOME/.clawd and must equal the acceptance data directory",
            "parent CLAWD_HOME is stripped; it is not proof of child isolation",
            "parent HERMES_HOME is the data directory so the wrapper does not open the account production tool store",
            "pytest --basetemp is a CLI argument and survives env -i",
            "the basetemp value stays with the flag; tests is the only discovery root",
            "HERMES_GATEWAY_LOCK_DIR is on the wrapper allowlist and is forwarded",
            "CLAWD_HOME, HERMES_HOME and TMPDIR are stripped",
            "required and native case directories stay inside the single tests discovery",
        ],
    }


def core_argv(repo: Path, layout: Layout) -> list[str]:
    # Keep the separate --basetemp token. The shell and the runner join the
    # following path; tests must remain the single discovery root.
    return [
        "bash",
        str(repo / "scripts" / "run_tests.sh"),
        "-j",
        "1",
        "--file-timeout",
        str(PYTEST_FILE_TIMEOUT_SECONDS),
        "--basetemp",
        str(layout.basetemp),
        "tests",
    ]


TUI_BUILD_HOST = Path("/root/hermes-workspace")
TUI_BUILD_ROOT_NAME = "clawd-acceptance-tui"


def _path_inside(parent: Path, child: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
    except (OSError, ValueError):
        return False
    return True


def tui_build_locations(repo: Path, token: str) -> dict:
    """Caller-owned TUI outdir and TMPDIR. Does not create or delete paths.

    The product build refuses an output inside the checkout unless it is a
    supported generated destination. Acceptance therefore uses a unique
    directory outside the checkout, never system /tmp and never an existing
    unknown path.
    """
    empty = {"ok": False, "tmpdir": None, "out": None}
    if (
        not token
        or token in {".", ".."}
        or "/" in token
        or "\\" in token
        or ".." in token
    ):
        return {**empty, "detail": "tui build token is not a single owned path component"}
    host = TUI_BUILD_HOST
    if host.is_symlink():
        return {**empty, "detail": "refusing a symlink host for the tui build"}
    owned = host / TUI_BUILD_ROOT_NAME
    if owned.is_symlink():
        return {**empty, "detail": "refusing a symlink tui build root"}
    run_dir = owned / token
    tmp = run_dir / "tmp"
    out = run_dir / "tui"
    refused = _tui_path_refused(repo, tmp, out)
    if refused:
        return {"ok": False, "detail": refused, "tmpdir": tmp, "out": out}
    return {
        "ok": True,
        "detail": "caller-owned tui outdir and TMPDIR outside the checkout",
        "tmpdir": tmp,
        "out": out,
    }


def _tui_path_refused(repo: Path, tmp: Path, out: Path) -> str | None:
    repo_root = repo.resolve()
    for path in (tmp, out):
        text = path.as_posix()
        if text == "/tmp" or text.startswith("/tmp/") or text == "/var/tmp" or text.startswith("/var/tmp/"):
            return "refusing system tmp for the tui build"
        if _path_inside(repo_root, path) or _path_inside(path, repo_root):
            return "tui build path overlaps the checkout"
        if "artifacts/final-acceptance" in text:
            return "tui build must not use the in-repo acceptance tmp"
    if out == tmp:
        return "tui outdir must not be the TMPDIR itself"
    return None
