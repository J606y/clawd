"""Stage catalog. Building the catalog does not run a stage.

``run_package_build`` is the package child entry. Importing this module does
not call it. ``execute.py`` does not call it either. ``execute.run_package``
reads ``stage["argv"]`` and the child command calls ``run_package_build``.
Do not invent archive paths here.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

from classify import aggregate_exit
from constants import (
    CORE_WALL_SECONDS,
    NATIVE_CASE_DIRS,
    NODE_FULL_TIMEOUT_SECONDS,
    PYTEST_FILE_TIMEOUT_SECONDS,
    REQUIRED_CASE_SUITES,
)
from gate import forbidden_path
from isolation import Layout, core_argv


DISTRIBUTION_WHEEL_ENV = "CLAWD_DISTRIBUTION_WHEEL"
DISTRIBUTION_SDIST_ENV = "CLAWD_DISTRIBUTION_SDIST"
DISTRIBUTION_MARKER = "CLAWD_ACCEPTANCE_DISTRIBUTION"
# setuptools normalizes the pyproject name ``clawd``. The historical wheel
# prefix is a refusal, not a path this stage will open or emit.
CLAWD_ARCHIVE_PREFIX = "clawd-"
HISTORICAL_ARCHIVE_PREFIXES = ("hermes_agent-", "hermes-agent-")
OWNED_BUILD_PREFIX = "accept-build-"


def license_argv(python: Path, repo: Path, output: Path) -> list[str]:
    """Flags that exist on generate.py. Output is the capture file, not a repo-relative name."""
    return [
        str(python),
        str(repo / "scripts" / "clawd_inventory" / "generate.py"),
        "--checkout",
        str(repo),
        "--output",
        str(output),
    ]


def toolchain_status(repo: Path) -> dict:
    python = repo / ".venv" / "bin" / "python"
    node_bin = repo / ".dev-home" / "tools" / "node-26.12.1-linux-x64" / "bin"
    node = node_bin / "node"
    npm = node_bin / "npm"
    return {
        "python": str(python),
        "python_present": python.is_file(),
        "expected_python": "3.14.9",
        "node": str(node),
        "node_present": node.is_file(),
        "npm": str(npm),
        "npm_present": npm.is_file(),
        "expected_node": "v26.12.1",
        "runtime_dir": str(repo / ".dev-home" / "tools"),
        "fallback_to_system": False,
    }


def package_child_env(base: dict[str, str]) -> dict[str, str]:
    """Env for a new interpreter that will import setup.py.

    ``setup.py`` reads ``HERMES_NIX_BUILD`` at import time. Parent
    ``CLAWD_DISTRIBUTION_*`` values are stripped so an old archive cannot
    pass as this run. This function does not export those keys; only a
    successful hook return can.
    """
    env = dict(base)
    env["HERMES_NIX_BUILD"] = "1"
    env.pop(DISTRIBUTION_WHEEL_ENV, None)
    env.pop(DISTRIBUTION_SDIST_ENV, None)
    return env


def package_argv(python: Path, repo: Path, isolation_dir: Path) -> list[str]:
    """Command that calls both PEP 517 hooks. It does not embed archive paths."""
    acceptance = Path(__file__).resolve().parent
    script = (
        "import os, sys\n"
        "os.environ['HERMES_NIX_BUILD'] = '1'\n"
        "sys.path[:0] = [sys.argv[1], sys.argv[2]]\n"
        "from stages import run_package_build\n"
        "raise SystemExit(run_package_build(sys.argv[3], sys.argv[4]))\n"
    )
    return [
        str(python),
        "-c",
        script,
        str(acceptance),
        str(repo),
        str(isolation_dir),
        str(repo),
    ]


def package_result_exit(checks: list[dict] | None) -> int:
    """All required pass → 0; any fail → 1; empty, blocked, unknown, not_run → 2."""
    if not checks:
        return 2
    return aggregate_exit(checks)


def package_build_checks(
    child_exit: int | None,
    output: str,
    isolation_dir: Path,
    *,
    timed_out: bool = False,
) -> list[dict]:
    """Rows the executor must record. Manifest comparison stays not_run.

    A child exit of 0 only means both hooks returned files. It is not a
    contract pass. ``package-manifest-compared`` stays not_run in this
    function. ``execute.run_all`` may replace that row after an independent
    pytest. This function does not start that pytest.
    """
    manifest = _package_row(
        "package-manifest-compared",
        "not_run",
        "compare_distribution_archive was not run",
    )
    if timed_out:
        return [
            _package_row("package-build-wheel", "blocked", "package build timed out; not a product failure"),
            _package_row("package-build-sdist", "blocked", "package build timed out; not a product failure"),
            _package_row("package-distribution-env", "blocked", "timed out before archives could be exported"),
            manifest,
        ]
    if child_exit is None:
        return [
            _package_row("package-build-wheel", "not_run", "package build was not started"),
            _package_row("package-build-sdist", "not_run", "package build was not started"),
            _package_row("package-distribution-env", "not_run", "no build output"),
            manifest,
        ]
    if child_exit == 1 and "build_wheel failed" in output and "build_sdist failed" not in output:
        return [
            _package_row("package-build-wheel", "fail", "build_wheel raised"),
            _package_row("package-build-sdist", "not_run", "not called after build_wheel failed"),
            _package_row("package-distribution-env", "blocked", "not exported after build failure"),
            manifest,
        ]
    if child_exit == 1 and "build_sdist failed" in output:
        return [
            _package_row("package-build-wheel", "blocked", "wheel hook returned but the pair was not exported"),
            _package_row("package-build-sdist", "fail", "build_sdist raised"),
            _package_row("package-distribution-env", "blocked", "not exported after build failure"),
            manifest,
        ]
    if child_exit == 1:
        return [
            _package_row("package-build-wheel", "fail", "build failed"),
            _package_row("package-build-sdist", "fail", "build failed"),
            _package_row("package-distribution-env", "blocked", "not exported after build failure"),
            manifest,
        ]
    if child_exit == 0:
        handoff = parse_distribution_marker(output, isolation_dir)
        if handoff["may_start_manifest_test"] and handoff["env"]:
            return [
                _package_row("package-build-wheel", "pass", handoff["env"][DISTRIBUTION_WHEEL_ENV]),
                _package_row("package-build-sdist", "pass", handoff["env"][DISTRIBUTION_SDIST_ENV]),
                _package_row("package-distribution-env", "pass", "both new archive paths are exportable; contract not compared"),
                manifest,
            ]
        return [
            _package_row("package-build-wheel", "blocked", handoff["detail"]),
            _package_row("package-build-sdist", "blocked", handoff["detail"]),
            _package_row("package-distribution-env", "blocked", handoff["detail"]),
            manifest,
        ]
    if child_exit == 2:
        return [
            _package_row("package-build-wheel", "blocked", output.strip() or "package build blocked"),
            _package_row("package-build-sdist", "blocked", output.strip() or "package build blocked"),
            _package_row("package-distribution-env", "blocked", "archives were not exported"),
            manifest,
        ]
    return [
        _package_row("package-build-wheel", "unknown", f"unrecognized package exit {child_exit}"),
        _package_row("package-build-sdist", "unknown", f"unrecognized package exit {child_exit}"),
        _package_row("package-distribution-env", "unknown", "no exportable archives"),
        manifest,
    ]


def _package_row(cid: str, status: str, detail: str) -> dict:
    return {**_check(cid, "package", detail=detail), "status": status}


def classify_distribution_handoff(
    wheel: str | None,
    sdist: str | None,
    isolation_dir: Path,
) -> dict:
    """Whether the manifest pytest may start. This does not open archives.

    Both variables missing, or only one set, is blocked/2. Do not start
    ``test_distribution_package_manifest.py`` in those cases: both missing
    makes that test return without comparing, and one missing raises
    AssertionError. Neither result is a contract pass. A positive gate means
    the paths are regular files from this run, not that the contract matched.
    """
    if not wheel and not sdist:
        return _handoff(
            2,
            "blocked",
            False,
            None,
            "both CLAWD_DISTRIBUTION_* unset; manifest pytest must not start",
        )
    if not wheel or not sdist:
        return _handoff(
            2,
            "blocked",
            False,
            None,
            "one CLAWD_DISTRIBUTION_* missing; do not turn that into pytest fail/1",
        )
    wheel_path = Path(wheel)
    sdist_path = Path(sdist)
    wheel_error = _archive_error(wheel_path, isolation_dir, "wheel")
    sdist_error = _archive_error(sdist_path, isolation_dir, "sdist")
    if wheel_error or sdist_error:
        return _handoff(
            2,
            "blocked",
            False,
            None,
            wheel_error or sdist_error or "archive is not a new isolation file",
        )
    return _handoff(
        2,
        "not_run",
        True,
        {
            DISTRIBUTION_WHEEL_ENV: str(wheel_path),
            DISTRIBUTION_SDIST_ENV: str(sdist_path),
        },
        "paths only; compare_distribution_archive has not run",
    )


def parse_distribution_marker(text: str, isolation_dir: Path) -> dict:
    """Read hook output. Missing or ambiguous markers do not synthesize paths."""
    found = []
    for line in text.splitlines():
        if not line.startswith(DISTRIBUTION_MARKER + " "):
            continue
        raw = line.split(" ", 1)[1]
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return _handoff(2, "blocked", False, None, "distribution marker is not json")
        if not isinstance(payload, dict):
            return _handoff(2, "blocked", False, None, "distribution marker is not an object")
        found.append(payload)
    if not found:
        return _handoff(2, "blocked", False, None, "build output has no distribution marker")
    if len(found) != 1:
        return _handoff(2, "blocked", False, None, "ambiguous distribution markers; refusing to choose")
    payload = found[0]
    wheel = payload.get("wheel")
    sdist = payload.get("sdist")
    if not isinstance(wheel, str) or not isinstance(sdist, str):
        return _handoff(2, "blocked", False, None, "distribution marker omitted an archive path")
    return classify_distribution_handoff(wheel, sdist, isolation_dir)


def run_package_build(
    isolation_dir: str,
    repo: str,
    *,
    build_wheel=None,
    build_sdist=None,
    stdout=None,
) -> int:
    """Build wheel and sdist into a new directory under ``isolation_dir``.

    Injected callables are for tests. The acceptance command leaves them
    unset and then imports ``clawd_server.packaging_backend``. This function
    does not delete siblings, stage locks, ``.partial`` paths, or unknown
    assets. It does not set the parent process environment.
    """
    out = stdout if stdout is not None else sys.stdout
    isolation = Path(isolation_dir)
    checkout = Path(repo)
    refusal = _refuse_isolation(isolation, checkout)
    if refusal:
        print(refusal, file=out)
        return 2
    if (build_wheel is None) != (build_sdist is None):
        print("refusing a partial build dependency", file=out)
        return 2
    if build_wheel is None:
        if os.environ.get("HERMES_NIX_BUILD") != "1":
            print("HERMES_NIX_BUILD is not 1; refusing to import the build backend", file=out)
            return 2
        try:
            build_wheel, build_sdist = _load_builders()
        except ImportError as exc:
            print(f"build backend unavailable: {type(exc).__name__}: {exc}", file=out)
            return 2
    try:
        outdir = _make_unique_outdir(isolation)
    except OSError as exc:
        print(f"refusing to build; unique output directory was not created: {exc}", file=out)
        return 2
    try:
        wheel_name = build_wheel(str(outdir))
    except Exception as exc:
        print(f"build_wheel failed: {type(exc).__name__}: {exc}", file=out)
        return 1
    try:
        sdist_name = build_sdist(str(outdir))
    except Exception as exc:
        print(f"build_sdist failed: {type(exc).__name__}: {exc}", file=out)
        return 1
    wheel_path, wheel_error = _hook_archive(outdir, wheel_name, "wheel")
    sdist_path, sdist_error = _hook_archive(outdir, sdist_name, "sdist")
    if wheel_error or sdist_error or wheel_path is None or sdist_path is None:
        print(wheel_error or sdist_error or "hook did not create both archives", file=out)
        return 2
    print(_marker(wheel_path, sdist_path), file=out)
    return 0


def _load_builders():
    """Real hooks. Signatures verified in packaging_backend.py; no extra kwargs."""
    from clawd_server.packaging_backend import build_sdist, build_wheel

    return build_wheel, build_sdist


def _refuse_isolation(isolation: Path, repo: Path) -> str | None:
    if forbidden_path(isolation) or forbidden_path(repo):
        return "refusing production or forbidden path"
    if isolation.is_symlink() or not isolation.is_dir():
        return "isolation directory missing or symlink; not creating a guessed archive path"
    if "entry-packaging" in isolation.parts:
        return "refusing historical entry-packaging directory"
    bundled = repo / "clawd_server" / "bundled"
    panel = repo / "apps" / "panel"
    try:
        isolation_abs = isolation.absolute()
        if isolation_abs == repo.absolute():
            return "refusing checkout root as the archive directory"
        if isolation_abs == bundled.absolute() or _is_inside(bundled, isolation):
            return "refusing the bundle slot as the archive directory"
        if _is_inside(panel, isolation):
            return "refusing the panel tree as the archive directory"
    except OSError:
        return "isolation path could not be checked"
    return None


def _make_unique_outdir(parent: Path) -> Path:
    token = f"{os.getpid()}-{uuid.uuid4().hex}"
    path = parent / f"{OWNED_BUILD_PREFIX}{token}"
    if ".stage-" in path.name or ".rejected-" in path.name or ".partial" in path.name:
        raise OSError(f"refusing owned-stage name collision: {path.name}")
    if path.exists() or path.is_symlink():
        raise OSError(f"refusing occupied output path: {path}")
    path.mkdir(mode=0o700, exist_ok=False)
    os.chmod(path, 0o700)
    return path


def _hook_archive(outdir: Path, returned: object, kind: str) -> tuple[Path | None, str | None]:
    name = _basename_only(returned)
    if name is None:
        return None, f"{kind} hook did not return a basename"
    path = outdir / name
    error = _archive_error(path, outdir.parent, kind)
    if error:
        return None, error
    if path.parent != outdir:
        return None, f"{kind} is not in the unique output directory"
    return path, None


def _archive_error(path: Path, isolation: Path, kind: str) -> str | None:
    name = path.name
    if any(name.startswith(prefix) for prefix in HISTORICAL_ARCHIVE_PREFIXES):
        return f"refusing historical archive name: {name}"
    if "entry-packaging" in path.parts:
        return "refusing historical entry-packaging archive"
    if kind == "wheel" and not name.endswith(".whl"):
        return f"wheel hook did not return a .whl basename: {name}"
    if kind == "sdist" and not name.endswith((".tar.gz", ".tgz", ".tar", ".zip")):
        return f"sdist hook did not return an archive basename: {name}"
    if kind == "sdist" and name.endswith(".whl"):
        return "sdist hook returned a wheel name"
    if not name.startswith(CLAWD_ARCHIVE_PREFIX):
        return f"refusing non-clawd archive name: {name}"
    if path.is_symlink():
        return f"refusing symlink archive: {path}"
    if not path.is_file():
        return f"{kind} was not created as a regular file"
    parent = path.parent
    if not parent.name.startswith(OWNED_BUILD_PREFIX):
        return f"{kind} is not inside this run's unique output directory"
    if ".stage-" in parent.name or ".rejected-" in parent.name or ".partial" in parent.name:
        return f"refusing output directory that belongs to resource staging: {parent.name}"
    if not _is_inside(isolation, path):
        return f"{kind} is outside the isolation directory"
    return None


def _basename_only(returned: object) -> str | None:
    if not isinstance(returned, str) or returned in {"", ".", ".."}:
        return None
    if "/" in returned or "\\" in returned:
        return None
    if Path(returned).name != returned:
        return None
    return returned


def _is_inside(root: Path, path: Path) -> bool:
    try:
        path.absolute().relative_to(root.absolute())
    except (OSError, ValueError):
        return False
    return True


def _marker(wheel: Path, sdist: Path) -> str:
    payload = json.dumps(
        {"wheel": str(wheel), "sdist": str(sdist)},
        ensure_ascii=False,
    )
    return f"{DISTRIBUTION_MARKER} {payload}"


def _handoff(code: int, status: str, may_start: bool, env: dict | None, detail: str) -> dict:
    return {
        "exit_code": code,
        "status": status,
        "may_start_manifest_test": may_start,
        "env": env,
        "detail": detail,
        "contract_compared": False,
    }


def stage_catalog(repo: Path, layout: Layout) -> list[dict]:
    node = repo / ".dev-home" / "tools" / "node-26.12.1-linux-x64" / "bin" / "node"
    npm = node.parent / "npm"
    python = repo / ".venv" / "bin" / "python"
    panel = repo / "apps" / "panel"
    return [
        {
            "id": "core",
            "title": "核心 pytest，单次发现 tests/，纳入四组未运行用例目录",
            "timeout_seconds": CORE_WALL_SECONDS,
            "file_timeout_seconds": PYTEST_FILE_TIMEOUT_SECONDS,
            "argv": core_argv(repo, layout),
            "cwd": str(repo),
            "isolation": "run_tests_sh",
            "overlap_group": "core-pytest",
            "external": False,
        },
        {
            "id": "tui-check",
            "title": "TUI check（含 typecheck/test/lint，不另计一次）",
            "timeout_seconds": 1800,
            "argv": [str(npm), "run", "check", "--workspace", "ui-tui"],
            "cwd": str(repo),
            "isolation": "direct",
            "overlap_group": "tui-check",
            "external": False,
        },
        {
            "id": "tui-build",
            "title": "TUI build；调用侧指定工程外 outdir 与 TMPDIR",
            "timeout_seconds": 600,
            "argv": [str(npm), "run", "build", "--workspace", "ui-tui"],
            "cwd": str(repo),
            "isolation": "direct",
            "overlap_group": "tui-build",
            "external": False,
        },
        {
            "id": "tui-pty",
            "title": "TUI 真实 PTY /quit",
            "timeout_seconds": 90,
            "argv": [str(python), str(repo / "clawd"), "--tui"],
            "cwd": str(repo),
            "isolation": "direct",
            "overlap_group": "tui-pty",
            "external": False,
        },
        {
            "id": "panel-typecheck",
            "title": "面板 typecheck",
            "timeout_seconds": 300,
            "argv": [str(npm), "--prefix", str(panel), "--workspaces=false", "run", "typecheck"],
            "cwd": str(panel),
            "isolation": "direct",
            "overlap_group": "panel-typecheck",
            "external": False,
        },
        {
            "id": "panel-build",
            "title": "面板 build",
            "timeout_seconds": 600,
            "argv": [str(npm), "--prefix", str(panel), "--workspaces=false", "run", "build"],
            "cwd": str(panel),
            "isolation": "direct",
            "overlap_group": "panel-build",
            "external": False,
        },
        {
            "id": "panel-node",
            "title": "面板 Node 全量",
            "timeout_seconds": NODE_FULL_TIMEOUT_SECONDS,
            "known_duration_seconds": 395,
            "forbidden_kill_seconds": [35, 240],
            "argv": [str(node), "--test", "--test-concurrency=1"],
            "cwd": str(panel),
            "isolation": "direct",
            "overlap_group": "panel-node",
            "external": False,
        },
        {
            "id": "panel-python",
            "title": "面板 Python unittest（固定 venv，不用系统 python3）",
            "timeout_seconds": 900,
            "argv": [str(python), "-B", "-m", "unittest"],
            "cwd": str(panel),
            "isolation": "direct",
            "overlap_group": "panel-python",
            "external": False,
        },
        {
            "id": "panel-browser",
            "title": "面板浏览器",
            "timeout_seconds": 3600,
            "argv": [str(node), "scripts/test-gates.mjs", "--target=browser", "--continue-on-failure"],
            "cwd": str(panel),
            "isolation": "direct",
            "overlap_group": "panel-browser",
            "external": True,
        },
        {
            "id": "package",
            "title": "隔离目录实际构建 wheel 与 sdist；路径只在本次钩子返回后存在",
            "timeout_seconds": 600,
            "argv": package_argv(python, repo, layout.wheel),
            "cwd": str(repo),
            "isolation": "direct",
            "overlap_group": "package",
            "external": False,
            "distribution_env_keys": [DISTRIBUTION_WHEEL_ENV, DISTRIBUTION_SDIST_ENV],
            "distribution_env": None,
            "note": (
                "execute.run_package reads this argv and runs it as the child. "
                "The parent does not call run_package_build or import the build backend. "
                "After this run's marker classifies, the two archive paths are passed "
                "to an independent venv pytest and to artifact hashing. "
                "Parent CLAWD_DISTRIBUTION_WHEEL and CLAWD_DISTRIBUTION_SDIST are not "
                "forwarded to the build child."
            ),
        },
        {
            "id": "license-inventory",
            "title": "本地许可清单，不联网审计",
            "timeout_seconds": 180,
            "argv": license_argv(python, repo, layout.logs / "license-inventory.json"),
            "cwd": str(repo),
            "isolation": "direct",
            "overlap_group": "license",
            "external": False,
            "network": False,
        },
        {
            "id": "verify-integration",
            "title": "既有 verify_clawd.py，blocked 保持 blocked",
            "timeout_seconds": 600,
            "argv": [str(python), str(repo / "scripts" / "verify_clawd.py")],
            "cwd": str(repo),
            "isolation": "verify_clawd",
            "overlap_group": "verify-clawd",
            "external": True,
        },
        {
            "id": "real-model-mcp",
            "title": "真实模型与 MCP",
            "timeout_seconds": 180,
            "argv": [str(python), "-m", "clawd_server", "serve", "--home", str(layout.home), "--host", "127.0.0.1", "--port", "0"],
            "cwd": str(repo),
            "isolation": "direct",
            "overlap_group": "live-provider",
            "external": True,
        },
        {
            "id": "continuation",
            "title": "续跑真实完成回合",
            "timeout_seconds": 180,
            "argv": [],
            "cwd": str(repo),
            "isolation": "direct",
            "overlap_group": "continuation",
            "external": True,
        },
        {
            "id": "rollback",
            "title": "隔离回滚，不调用生产 panel-deploy",
            "timeout_seconds": 300,
            "argv": [str(python), str(repo / "scripts" / "clawd_cutover" / "cli.py"), "rollback", "--allow-root", "--target", "--journal", "--confirm", "ROLLBACK"],
            "cwd": str(repo),
            "isolation": "direct",
            "overlap_group": "rollback",
            "external": False,
        },
    ]


def coverage_checks(repo: Path) -> list[dict]:
    checks = planned_checks()
    for rel in NATIVE_CASE_DIRS:
        path = repo / rel
        if path.is_dir():
            checks.append(_check(rel, "core-pytest", detail="present; single tests discovery only"))
        else:
            checks.append({**_check(rel, "core-pytest", detail="not created; not a separate run; not a pass"), "status": "blocked"})
    checks.append({**_check("deploy-support", "deploy", detail="未应用模板不能记成部署支持已验证"), "status": "blocked", "required": False})
    return checks


def planned_checks() -> list[dict]:
    checks = [
        _check("core", "core-pytest"),
        _check("tui-check", "tui-check"),
        _check("tui-build", "tui-build"),
        _check("tui-pty", "tui-pty"),
        _check("panel-typecheck", "panel-typecheck"),
        _check("panel-build", "panel-build"),
        _check("panel-node", "panel-node"),
        _check("panel-python", "panel-python"),
        _check("panel-browser", "panel-browser"),
        _check("package-wheel-members", "package"),
        _check("package-installed-panel", "package"),
        _check("package-build-wheel", "package", detail="not executed; catalog argv is ready for execute.run_package"),
        _check("package-build-sdist", "package", detail="not executed; catalog argv is ready for execute.run_package"),
        _check("package-distribution-env", "package", detail="not executed; paths are exported only after this run's marker"),
        _check("package-manifest-compared", "package", detail="not executed; compare_distribution_manifest has not run"),
        _check("license-inventory", "license"),
        _check("real-model", "live-provider"),
        _check("mcp-live", "live-provider"),
        _check("continuation-real-turn", "continuation"),
        _check("rollback", "rollback"),
    ]
    for spec in REQUIRED_CASE_SUITES:
        checks.append(_check(spec["id"], "core-pytest", detail="not delivered and not executed"))
    return checks


def _check(cid: str, group: str, detail: str = "not executed") -> dict:
    return {
        "id": cid,
        "status": "not_run",
        "required": True,
        "counts_as_test": False,
        "overlap_group": group,
        "detail": detail,
    }
