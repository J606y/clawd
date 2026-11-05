"""Acceptance execution. Importing this module does not start any stage."""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import subprocess
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from classify import classify_external_case, classify_mcp, classify_run_payload, classify_timeout, inclusion_from_log, inspect_wheel_names, timeout_rejected
from constants import (
    CORE_WALL_SECONDS,
    FORBIDDEN_KILL_SECONDS,
    MANIFEST_PATH,
    NATIVE_CASE_DIRS,
    REQUESTED_MODEL,
    REQUIRED_CASE_SUITES,
)
from argv_contract import clawd_home_contract_ok, cutover_rollback_argv_ok, panel_gates_argv_ok, run_tests_argv_ok, server_argv_ok
from provider_config import inspect_provider
from gate import case_suites_ready, forbidden_path, inventory_ready, load_manifest, manifest_allows_run
from isolation import Layout, direct_env, run_tests_parent_env, run_tests_plan, tui_build_locations
from pty_tui import run_pty
from fingerprints import TrustedArchiveHandoff
from report_build import assemble, default_identity
from resume import decide, load_state, pid_alive
from stages import (
    DISTRIBUTION_SDIST_ENV,
    DISTRIBUTION_WHEEL_ENV,
    classify_distribution_handoff,
    coverage_checks,
    license_argv,
    package_argv,
    package_build_checks,
    package_child_env,
    package_result_exit,
    parse_distribution_marker,
    planned_checks,
    stage_catalog,
    toolchain_status,
)

MANIFEST_PYTEST_REL = "tests/clawd_server/test_distribution_package_manifest.py"
MANIFEST_PYTEST_TIMEOUT = 600
_INTERNAL_LICENSE_SUFFIXES = (
    "ui-tui/packages/hermes-ink/package.json",
    "apps/panel/package.json",
    "apps/shared/package.json",
    "ui-tui/package.json",
)


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def leaf(cid: str, status: str, detail: str, group: str, *, required: bool = True) -> dict:
    return {
        "id": cid,
        "status": status,
        "required": required,
        "counts_as_test": False,
        "overlap_group": group,
        "detail": detail[:500],
    }


def spawn(argv: list[str], env: dict[str, str], cwd: str, log_path: Path, pid_path: Path, timeout: int) -> dict:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab") as handle:
        handle.write(("\n$ " + " ".join(argv) + "\n").encode())
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdout=handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        pid_path.write_text(json.dumps({"pid": proc.pid, "argv": argv}) + "\n", encoding="utf-8")
        try:
            code = proc.wait(timeout=timeout)
            return {"exit_code": code, "timed_out": False, "pid": proc.pid}
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                proc.kill()
            proc.wait(timeout=15)
            return {"exit_code": 124, "timed_out": True, "pid": proc.pid}


def _modules(path: Path) -> bool:
    return (path / "node_modules").is_dir()


def _read(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _log_offset(path: Path) -> int:
    if path.is_symlink() or not path.is_file():
        return 0
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _read_added(path: Path, offset: int) -> str:
    if path.is_symlink() or not path.is_file():
        return ""
    try:
        with path.open("rb") as handle:
            handle.seek(max(offset, 0))
            return handle.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _refuse_owned_log(path: Path) -> str | None:
    if path.is_symlink() or forbidden_path(path):
        return "拒绝符号链接或禁止路径上的阶段日志"
    parent = path.parent
    if parent.is_symlink() or forbidden_path(parent):
        return "拒绝符号链接或禁止路径上的日志目录"
    return None


def run_core(repo: Path, layout: Layout) -> dict:
    plan = run_tests_plan(repo, layout)
    argv_ok, argv_detail = run_tests_argv_ok(repo, plan["argv"])
    home_ok, home_detail = clawd_home_contract_ok(repo)
    if not argv_ok or not home_ok or not plan.get("matches_core_resolution"):
        return _blocked_stage("core", argv_detail if not argv_ok else home_detail)
    if any(str(item) in {spec["rel_dir"] for spec in REQUIRED_CASE_SUITES} or str(item) in NATIVE_CASE_DIRS for item in plan["argv"]):
        return _blocked_stage("core", "refusing a second discovery root for case directories")
    if any(str(item) in {"35", "240"} for item in plan["argv"]):
        return _blocked_stage("core", "core argv contains a forbidden 35/240 kill timeout")
    log_path = layout.logs / "core.log"
    outcome = spawn(
        plan["argv"],
        plan["parent_env"],
        str(repo),
        log_path,
        layout.pids / "core.json",
        CORE_WALL_SECONDS,
    )
    text = _read(log_path)
    status = "pass" if outcome["exit_code"] == 0 else ("blocked" if outcome["timed_out"] else "fail")
    checks = [leaf("core", status, f"exit {outcome['exit_code']}; child CLAWD_HOME is HOME/.clawd", "core-pytest")]
    for spec in REQUIRED_CASE_SUITES:
        included = inclusion_from_log(text, spec["rel_dir"], outcome["exit_code"])
        checks.append(leaf(spec["id"], included, "single tests/ discovery; not a second run", "core-pytest"))
    for rel in NATIVE_CASE_DIRS:
        if not (repo / rel).is_dir():
            checks.append(leaf(rel, "blocked", "not created; not a separate run; not a pass", "core-pytest"))
            continue
        included = inclusion_from_log(text, rel, outcome["exit_code"])
        checks.append(leaf(rel, included, "single tests/ discovery; not a second run", "core-pytest"))
    return {
        "id": "core",
        "status": status,
        "exit_code": outcome["exit_code"],
        "log": str(log_path),
        "detail": "run_tests.sh env -i forwards HOME; child CLAWD_HOME is HOME/.clawd",
        "checks": checks,
        "annotation": {"passed_count_sum_into_summary": False},
    }


def run_npm(stage: dict, repo: Path, layout: Layout, modules: Path) -> dict:
    if not _modules(modules):
        return _blocked_stage(stage["id"], f"{modules} missing; will not npm install")
    return _command_stage(stage, repo, layout, direct_env(layout, repo))


def run_tui_build(stage: dict, repo: Path, layout: Layout) -> dict:
    """Caller-owned outdir and TMPDIR. Does not edit the TUI productOutput guard."""
    if not _modules(repo):
        return _blocked_stage("tui-build", f"{repo} missing node_modules; will not npm install")
    token = f"run-{os.getpid()}-{time.time_ns()}"
    plan = _prepare_tui_build(repo, token)
    if not plan["ok"]:
        return _blocked_stage("tui-build", str(plan["detail"]))
    tmp = str(plan["tmpdir"])
    out = str(plan["out"])
    if tmp == "/tmp" or tmp.startswith("/tmp/") or out == "/tmp" or out.startswith("/tmp/"):
        return _blocked_stage("tui-build", "refusing system tmp for the tui build")
    env = direct_env(layout, repo)
    env["TMPDIR"] = tmp
    env["TEMP"] = tmp
    env["TMP"] = tmp
    argv = [*stage["argv"], "--", "--source", str(repo), "--out", out]
    spawned = {**stage, "argv": argv}
    result = _command_stage(spawned, repo, layout, env)
    result["detail"] = f"{result['detail']}; caller outdir {out}; TMPDIR {tmp}; productOutput guard unchanged"
    return result


def _prepare_tui_build(repo: Path, token: str) -> dict:
    plan = tui_build_locations(repo, token)
    if not plan["ok"]:
        return plan
    tmp = plan["tmpdir"]
    out = plan["out"]
    if not isinstance(tmp, Path) or not isinstance(out, Path):
        return {**plan, "ok": False, "detail": "tui build plan has no paths"}
    if any(path.exists() or path.is_symlink() for path in (tmp, out, tmp.parent)):
        return {**plan, "ok": False, "detail": "refusing to overwrite an existing tui build path"}
    owned = tmp.parents[1]
    if owned.is_symlink() or (owned.exists() and not owned.is_dir()):
        return {**plan, "ok": False, "detail": "tui build root is not an owned directory"}
    try:
        tmp.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        return {**plan, "ok": False, "detail": f"tui TMPDIR was not created: {exc}"}
    if out.exists() or out.is_symlink():
        return {**plan, "ok": False, "detail": "tui outdir appeared before the build; refusing"}
    return plan


def run_panel_node(repo: Path, layout: Layout, stage: dict) -> dict:
    timeout = int(stage["timeout_seconds"])
    rejected = timeout_rejected(timeout, node_wall=True)
    if rejected or timeout in FORBIDDEN_KILL_SECONDS:
        return _blocked_stage("panel-node", rejected or "forbidden kill timeout")
    panel = repo / "apps" / "panel"
    files = sorted((panel / "tests").glob("*.test.mjs"))
    if not files:
        return _blocked_stage("panel-node", "no panel node tests found")
    argv = [str(repo / ".dev-home/tools/node-26.12.1-linux-x64/bin/node"), "--test", "--test-concurrency=1"]
    argv.extend(str(path.relative_to(panel)) for path in files)
    log_path = layout.logs / "panel-node.log"
    outcome = spawn(argv, direct_env(layout, repo), str(panel), log_path, layout.pids / "panel-node.json", timeout)
    text = _read(log_path)
    assertion = "AssertionError" in text or "not ok" in text
    chromium = "chromium" in text and ("ENOENT" in text or "not found" in text)
    if outcome["timed_out"]:
        status = classify_timeout(
            configured=timeout,
            killed=True,
            assertion_failure=assertion and not chromium,
            node_wall=True,
        )
    elif outcome["exit_code"] == 0:
        status = "pass"
    elif chromium and not assertion:
        status = "blocked"
    else:
        status = "fail"
    detail = f"wall {timeout}s; known full suite about 395s; 35/240 are not product failures"
    return _done("panel-node", status, outcome["exit_code"], log_path, detail, "panel-node")


def run_panel_python(repo: Path, layout: Layout, stage: dict) -> dict:
    python = repo / ".venv" / "bin" / "python"
    if not python.is_file():
        return _blocked_stage("panel-python", "pinned venv python missing; system python3 is not a fallback")
    panel = repo / "apps" / "panel"
    env = direct_env(layout, repo)
    env.pop("PYTHONPATH", None)
    log_path = layout.logs / "panel-python.log"
    worst = "pass"
    code = 0
    rank = {"pass": 0, "blocked": 1, "fail": 2}
    for args in (
        ["-B", "-m", "unittest", "discover", "-s", "tests", "-p", "test*.py"],
        ["-B", "-m", "unittest", "discover", "-s", "sidecar", "-p", "test*.py"],
    ):
        outcome = spawn([str(python), *args], env, str(panel), log_path, layout.pids / "panel-python.json", 450)
        current = "pass"
        if outcome["timed_out"]:
            current = "blocked"
            code = 124
        elif outcome["exit_code"] != 0:
            current = "fail"
            code = outcome["exit_code"]
        if rank[current] > rank[worst]:
            worst = current
    return _done("panel-python", worst, code, log_path, "venv python, explicit env, not package.json python3", "panel-python")


def run_browser(repo: Path, layout: Layout, stage: dict) -> dict:
    chromium = Path("/usr/bin/chromium")
    if not chromium.is_file():
        return _blocked_stage("panel-browser", "chromium missing; not mocked")
    panel = repo / "apps" / "panel"
    argv = list(stage["argv"]) + [f"--output-dir={layout.logs / 'browser-gates'}"]
    ok, detail = panel_gates_argv_ok(repo, argv)
    if not ok:
        return _blocked_stage("panel-browser", detail)
    log_path = layout.logs / "panel-browser.log"
    outcome = spawn(argv, direct_env(layout, repo), str(panel), log_path, layout.pids / "panel-browser.json", stage["timeout_seconds"])
    status = "pass" if outcome["exit_code"] == 0 else ("blocked" if outcome["timed_out"] else "fail")
    detail = "test-gates relocates TMPDIR to /root/hermes-workspace/p-tmp; parent CLAWD_HOME is not its isolation proof"
    return _done("panel-browser", status, outcome["exit_code"], log_path, detail, "panel-browser")


def run_package(repo: Path, layout: Layout, install_prefix: Path | None, stage: dict | None = None) -> dict:
    """Build both archives via the catalog command. Does not import the backend here.

    The child environment sets HERMES_NIX_BUILD before the process starts.
    Archive paths come only from this run's marker. Old wheels are not globbed,
    and parent distribution paths are not forwarded. A positive marker is not
    a contract pass; the manifest pytest stays not_run until run_all starts it.
    """
    python = repo / ".venv" / "bin" / "python"
    if not python.is_file():
        return _package_blocked(repo, layout, install_prefix, "固定 venv python 缺失；不回落系统 Python")
    if layout.wheel.is_symlink() or forbidden_path(layout.wheel):
        return _package_blocked(repo, layout, install_prefix, "隔离目录是符号链接或禁止路径；不创建猜测档案")
    try:
        layout.wheel.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return _package_blocked(repo, layout, install_prefix, f"隔离目录未能创建: {exc}")
    if layout.wheel.is_symlink() or not layout.wheel.is_dir():
        return _package_blocked(repo, layout, install_prefix, "隔离目录缺失或是符号链接")
    env = package_child_env(direct_env(layout, repo))
    if env.get("HERMES_NIX_BUILD") != "1":
        return _package_blocked(repo, layout, install_prefix, "HERMES_NIX_BUILD 未在子进程启动前设为 1")
    if DISTRIBUTION_WHEEL_ENV in env or DISTRIBUTION_SDIST_ENV in env:
        return _package_blocked(repo, layout, install_prefix, "拒绝把父进程旧分发路径转给构建子进程")
    argv = _package_stage_argv(stage, python, repo, layout)
    if argv is None:
        return _package_blocked(repo, layout, install_prefix, "包命令不是固定 venv python")
    refused = _package_argv_refused(argv)
    if refused:
        return _package_blocked(repo, layout, install_prefix, refused)
    timeout = 600
    if stage and isinstance(stage.get("timeout_seconds"), int) and stage["timeout_seconds"] > 0:
        timeout = stage["timeout_seconds"]
    log_path = layout.logs / "package.log"
    log_refused = _refuse_owned_log(log_path)
    if log_refused:
        return _package_blocked(repo, layout, install_prefix, log_refused)
    offset = _log_offset(log_path)
    outcome = spawn(argv, env, str(repo), log_path, layout.pids / "package.json", timeout)
    text = _read_added(log_path, offset)
    checks = package_build_checks(
        None if outcome["timed_out"] else outcome["exit_code"],
        text,
        layout.wheel,
        timed_out=bool(outcome["timed_out"]),
    )
    handoff = None
    distribution_env = None
    if not outcome["timed_out"] and outcome["exit_code"] == 0:
        handoff = parse_distribution_marker(text, layout.wheel)
        exported = handoff.get("env") if handoff.get("may_start_manifest_test") else None
        if isinstance(exported, dict):
            wheel = exported.get(DISTRIBUTION_WHEEL_ENV)
            sdist = exported.get(DISTRIBUTION_SDIST_ENV)
            if isinstance(wheel, str) and wheel and isinstance(sdist, str) and sdist:
                distribution_env = {
                    DISTRIBUTION_WHEEL_ENV: wheel,
                    DISTRIBUTION_SDIST_ENV: sdist,
                }
    checks.append(_wheel_members_from_handoff(handoff, layout.wheel))
    installed = _installed_panel(repo, install_prefix)
    checks.append(leaf("package-installed-panel", installed[0], installed[1], "package"))
    status, code = _status_from_checks(checks)
    if outcome["timed_out"]:
        detail = f"子进程退出 {outcome['exit_code']}；超时记 blocked/{code}，不是产品失败；未启动开档案 pytest"
    else:
        detail = (
            f"子进程退出 {outcome['exit_code']}；分类退出 {code}；"
            "路径只来自本次标记；没有 glob 旧包；开档案 pytest 尚未运行"
        )
    return {
        "id": "package",
        "status": status,
        "exit_code": code,
        "log": str(log_path),
        "detail": detail,
        "checks": checks,
        "distribution_env": distribution_env,
    }


def _package_blocked(repo: Path, layout: Layout, install_prefix: Path | None, detail: str) -> dict:
    checks = package_build_checks(None, "", layout.wheel)
    checks.append(leaf("package-wheel-members", "not_run", "未打开档案；没有 glob 旧包", "package"))
    installed = _installed_panel(repo, install_prefix)
    checks.append(leaf("package-installed-panel", installed[0], installed[1], "package"))
    status, code = _status_from_checks(checks)
    return {
        "id": "package",
        "status": status,
        "exit_code": code,
        "log": "",
        "detail": detail,
        "checks": checks,
        "distribution_env": None,
    }


def _package_stage_argv(stage: dict | None, python: Path, repo: Path, layout: Layout) -> list[str] | None:
    argv = None
    if stage and isinstance(stage.get("argv"), list) and stage["argv"]:
        argv = [str(item) for item in stage["argv"]]
    else:
        argv = package_argv(python, repo, layout.wheel)
    if not argv or Path(argv[0]) != python:
        return None
    return argv


def _package_argv_refused(argv: list[str]) -> str | None:
    if any("setuptools.build_meta" in item for item in argv):
        return "拒绝私有 setuptools.build_meta 包命令"
    if len(argv) > 2 and argv[1] == "-c":
        script = argv[2]
        nix_at = script.find("HERMES_NIX_BUILD")
        backend_at = script.find("from stages import")
        if backend_at < 0:
            backend_at = script.find("packaging_backend")
        if nix_at < 0 or (backend_at >= 0 and nix_at > backend_at):
            return "包命令在导入构建后端之前没有设置 HERMES_NIX_BUILD"
    return None


def _wheel_members_from_handoff(handoff: dict | None, isolation: Path) -> dict:
    if not handoff or not handoff.get("may_start_manifest_test"):
        return leaf("package-wheel-members", "not_run", "没有本次新 wheel 标记；没有 glob 旧包", "package")
    env = handoff.get("env") if isinstance(handoff.get("env"), dict) else {}
    raw = env.get(DISTRIBUTION_WHEEL_ENV)
    sdist = env.get(DISTRIBUTION_SDIST_ENV)
    if not isinstance(raw, str) or not raw:
        return leaf("package-wheel-members", "blocked", "wheel 路径缺失；没有 glob 旧包", "package")
    again = classify_distribution_handoff(raw, sdist if isinstance(sdist, str) else None, isolation)
    if not again.get("may_start_manifest_test"):
        return leaf("package-wheel-members", "blocked", again.get("detail") or "wheel 不是本次隔离档案", "package")
    try:
        names = zipfile.ZipFile(raw).namelist()
    except (OSError, zipfile.BadZipFile) as exc:
        return leaf(
            "package-wheel-members",
            "blocked",
            f"标记 wheel 不是可读 zip（{type(exc).__name__}）；没有改去 glob 同级旧包",
            "package",
        )
    inspected = inspect_wheel_names(names)
    detail = ",".join(inspected["missing"]) or "成员存在；这不是契约核对"
    return leaf("package-wheel-members", inspected["members_status"], detail, "package")


def _status_from_checks(checks: list[dict]) -> tuple[str, int]:
    code = package_result_exit(checks)
    if code == 1:
        return "fail", 1
    if code == 0:
        return "pass", 0
    return "blocked", 2


def _pending_distribution_env(result: dict | None) -> dict | None:
    if not isinstance(result, dict):
        return None
    env = result.get("distribution_env")
    if not isinstance(env, dict):
        return None
    wheel = env.get(DISTRIBUTION_WHEEL_ENV)
    sdist = env.get(DISTRIBUTION_SDIST_ENV)
    if not isinstance(wheel, str) or not wheel or not isinstance(sdist, str) or not sdist:
        return None
    for item in result.get("checks") or []:
        if item.get("id") == "package-manifest-compared" and item.get("status") != "not_run":
            return None
    return {
        DISTRIBUTION_WHEEL_ENV: wheel,
        DISTRIBUTION_SDIST_ENV: sdist,
    }


def compare_distribution_manifest(repo: Path, layout: Layout, distribution_env: dict | None) -> dict | None:
    """Open the two new archives with a direct venv pytest. Never uses run_tests.sh.

    A missing variable keeps the existing not_run row. Both strings are
    re-checked before spawn. Parent distribution paths are not copied in.
    """
    if not isinstance(distribution_env, dict):
        return None
    wheel = distribution_env.get(DISTRIBUTION_WHEEL_ENV)
    sdist = distribution_env.get(DISTRIBUTION_SDIST_ENV)
    if not isinstance(wheel, str) or not wheel or not isinstance(sdist, str) or not sdist:
        return None
    handoff = classify_distribution_handoff(wheel, sdist, layout.wheel)
    if not handoff.get("may_start_manifest_test") or not isinstance(handoff.get("env"), dict):
        return leaf(
            "package-manifest-compared",
            "blocked",
            handoff.get("detail") or "两个路径不是本次新档案；未启动开档案 pytest",
            "package",
        )
    python = repo / ".venv" / "bin" / "python"
    if not python.is_file():
        return leaf(
            "package-manifest-compared",
            "blocked",
            "固定 venv python 缺失；未启动开档案 pytest；不回落系统 Python",
            "package",
        )
    env = direct_env(layout, repo)
    env.pop(DISTRIBUTION_WHEEL_ENV, None)
    env.pop(DISTRIBUTION_SDIST_ENV, None)
    env[DISTRIBUTION_WHEEL_ENV] = handoff["env"][DISTRIBUTION_WHEEL_ENV]
    env[DISTRIBUTION_SDIST_ENV] = handoff["env"][DISTRIBUTION_SDIST_ENV]
    argv = [str(python), "-m", "pytest", "-q", MANIFEST_PYTEST_REL]
    log_path = layout.logs / "package-manifest.log"
    if _refuse_owned_log(log_path):
        return leaf("package-manifest-compared", "blocked", "拒绝符号链接上的开档案日志", "package")
    offset = _log_offset(log_path)
    outcome = spawn(argv, env, str(repo), log_path, layout.pids / "package-manifest.json", MANIFEST_PYTEST_TIMEOUT)
    return _manifest_pytest_check(outcome, _read_added(log_path, offset))


def _manifest_pytest_check(outcome: dict, text: str) -> dict:
    if outcome.get("timed_out"):
        return leaf("package-manifest-compared", "blocked", "开档案 pytest 超时；不是产品失败", "package")
    code = outcome.get("exit_code")
    if code == 0 and _pytest_output_is_empty(text):
        return leaf("package-manifest-compared", "blocked", "pytest 退出 0 但输出为空或没有用例，不是开包通过", "package")
    if code == 0:
        return leaf("package-manifest-compared", "pass", "退出 0；本次两个新档案已交给契约核对", "package")
    if code == 1 and _pytest_missing(text):
        return leaf("package-manifest-compared", "blocked", "固定 venv 没有 pytest；不是契约失败", "package")
    if code == 1:
        return leaf("package-manifest-compared", "fail", "退出 1；档案契约断言失败", "package")
    if code in {2, 3, 4, 5, 124}:
        return leaf("package-manifest-compared", "blocked", f"pytest 退出 {code}；不是通过", "package")
    return leaf("package-manifest-compared", "unknown", f"无法识别的 pytest 退出 {code}", "package")


def _pytest_output_is_empty(text: str) -> bool:
    compact = text.strip()
    if not compact:
        return True
    lowered = compact.lower()
    return "no tests ran" in lowered or "collected 0" in lowered


def _pytest_missing(text: str) -> bool:
    lowered = text.lower()
    return "no module named pytest" in lowered or "no module named 'pytest'" in lowered


def _replace_check(checks: list[dict], cid: str, replacement: dict) -> list[dict]:
    return [item for item in checks if item.get("id") != cid] + [replacement]


def _refresh_package_stage(layout: Layout, stages: list[dict], checks: list[dict]) -> None:
    package_checks = [
        item
        for item in checks
        if item.get("overlap_group") == "package" or str(item.get("id") or "").startswith("package-")
    ]
    status, code = _status_from_checks(package_checks)
    detail = "开档案 pytest 已折入 package 检查；缺变量没有启动，空结果不是通过"
    for stage in stages:
        if stage.get("id") == "package":
            stage["status"] = status
            stage["exit_code"] = code
            stage["detail"] = detail
    state_path = layout.logs / "package.state.json"
    previous: dict = {}
    if state_path.is_file() and not state_path.is_symlink():
        try:
            loaded = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            loaded = {}
        if isinstance(loaded, dict):
            previous = loaded
    result = previous.get("result") if isinstance(previous.get("result"), dict) else {"id": "package"}
    result["id"] = "package"
    result["status"] = status
    result["exit_code"] = code
    result["detail"] = detail
    result["checks"] = package_checks
    pid = previous.get("pid")
    write_json(state_path, {"state": status, "pid": pid if isinstance(pid, int) else None, "result": result})


def _installed_panel(repo: Path, prefix: Path | None) -> tuple[str, str]:
    if prefix is None:
        return "blocked", "no isolated install prefix; source tree presence is not an installed layout"
    if forbidden_path(prefix) or repo in prefix.resolve().parents or prefix.resolve() == repo:
        return "blocked", "refusing source tree or production path as install proof"
    index = prefix / "index.html"
    sidecar = prefix / "hermes_panel_sidecar.py"
    if index.is_file() and sidecar.is_file():
        return "pass", "isolated prefix contains panel index and sidecar"
    return "blocked", "install prefix does not contain panel static and sidecar"


def argparse_flags(text: str) -> set[str]:
    """Flags declared by add_argument. Does not import or execute the extractor."""
    flags: set[str] = set()
    for line in text.splitlines():
        if "add_argument" not in line:
            continue
        flags.update(re.findall(r"""['"](--[A-Za-z0-9][-A-Za-z0-9]*)['"]""", line))
    return flags


def _flag_value(argv: list[str], flag: str) -> str | None:
    for index, item in enumerate(argv):
        if item == flag and index + 1 < len(argv):
            return str(argv[index + 1])
        if str(item).startswith(flag + "="):
            return str(item).split("=", 1)[1]
    return None


def license_signature_ok(repo: Path, argv: list[str]) -> tuple[bool, str]:
    """Match argv to the full generate.py argparse text. Parse is not legal review."""
    path = repo / "scripts" / "clawd_inventory" / "generate.py"
    if path.is_symlink() or not path.is_file():
        return False, "generate.py missing; not inventing an extractor entry"
    if forbidden_path(path):
        return False, "generate.py path is forbidden"
    text = path.read_text(encoding="utf-8", errors="replace")
    flags = argparse_flags(text)
    used = [str(item).split("=", 1)[0] for item in argv if str(item).startswith("-")]
    unknown = [item for item in used if item not in flags]
    if unknown:
        return False, "license argv uses flags generate.py does not define: " + " ".join(unknown)
    if "--checkout" not in flags or "--output" not in flags:
        return False, "generate.py argparse has no --checkout/--output"
    if "--checkout" not in argv or "--output" not in argv:
        return False, "license entry must pass the real --checkout and --output"
    output = _flag_value(argv, "--output")
    if output is None or not Path(output).is_absolute():
        return False, "license --output must be an absolute capture path"
    if Path(output).name != "license-inventory.json":
        return False, "license capture file must be license-inventory.json"
    return True, "matches generate.py argparse; parse is not a legal conclusion"


def _license_file_ready(path: Path) -> bool:
    return path.is_file() and not path.is_symlink() and not forbidden_path(path)


def run_license(repo: Path, layout: Layout) -> dict:
    ready = inventory_ready(repo)
    if not ready["ok"]:
        return _license_done("blocked", 2, ready["detail"], Path(""))
    output = layout.logs / "license-inventory.json"
    argv = _license_argv(repo, layout)
    ok, detail = license_signature_ok(repo, argv)
    if not ok or "npm" in argv or "audit" in argv:
        return _license_done("blocked", 2, detail or "no offline executable entry; network audit refused", Path(""))
    log_path = layout.logs / "license-inventory.log"
    if _refuse_owned_log(log_path):
        return _license_done("blocked", 2, "拒绝符号链接上的许可日志", Path(""))
    env = direct_env(layout, repo, {"CLAWD_LICENSE_AUDIT": "offline"})
    outcome = spawn(argv, env, str(repo), log_path, layout.pids / "license-inventory.json", 180)
    if outcome["timed_out"]:
        return _license_done("blocked", 2, "提取器超时；不是产品失败，也不是合规通过", log_path)
    if _license_file_ready(output):
        classified = classify_license_inventory(output)
        return {
            "id": "license-inventory",
            "status": classified["status"],
            "exit_code": classified["exit_code"],
            "log": str(log_path),
            "detail": classified["detail"],
            "checks": classified["checks"],
            "extractor_parse": classified.get("extractor_parse", "blocked"),
            "legal_review": "blocked",
            "unknown_scopes": classified.get("unknown_scopes") or [],
            "recorded_license_fields": classified.get("recorded_license_fields") or [],
        }
    if outcome["exit_code"] == 1:
        return _license_done("fail", 1, "提取器退出 1；没有可捕获清单", log_path)
    if outcome["exit_code"] != 0:
        return _license_done("blocked", 2, f"提取器退出 {outcome['exit_code']} 且没有可捕获清单；不是合规通过", log_path)
    return _license_done("blocked", 2, "提取器退出 0 之后没有可读清单，不是合规通过", log_path)


def _license_argv(repo: Path, layout: Layout) -> list[str]:
    return license_argv(repo / ".venv" / "bin" / "python", repo, layout.logs / "license-inventory.json")


def _license_done(status: str, code: int, detail: str, log_path: Path) -> dict:
    scope = "许可范围未读到；unknown 不能当合规通过"
    packaged = "packaged 未读到；unknown 不能当合规通过"
    row_status = "fail" if status == "fail" else "blocked"
    return _license_stage(
        status,
        code,
        detail,
        log_path,
        _license_rows(status, row_status, row_status, detail, scope, packaged),
    )


def _license_stage(status: str, code: int, detail: str, log_path: Path, checks: list[dict]) -> dict:
    return {
        "id": "license-inventory",
        "status": status,
        "exit_code": code,
        "log": str(log_path) if str(log_path) else "",
        "detail": detail,
        "checks": checks,
        "extractor_parse": "blocked",
        "legal_review": "blocked",
        "unknown_scopes": [],
        "recorded_license_fields": [],
    }


def _license_rows(
    inventory_status: str,
    scope_status: str,
    packaged_status: str,
    inventory_detail: str,
    scope_detail: str,
    packaged_detail: str,
) -> list[dict]:
    return [
        leaf("license-inventory", inventory_status, inventory_detail, "license"),
        leaf("license-scope-unknown", scope_status, scope_detail, "license"),
        leaf("license-packaged-unknown", packaged_status, packaged_detail, "license"),
    ]


def _license_result(
    status: str,
    code: int,
    detail: str,
    checks: list[dict],
    *,
    extractor_parse: str = "blocked",
    unknown_scopes: list | None = None,
    recorded_license_fields: list | None = None,
) -> dict:
    if status == "pass":
        status = "blocked"
        code = 2
    return {
        "status": status,
        "exit_code": code,
        "detail": detail,
        "checks": checks,
        "extractor_parse": extractor_parse,
        "legal_review": "blocked",
        "unknown_scopes": unknown_scopes or [],
        "recorded_license_fields": recorded_license_fields or [],
    }


def _internal_package_hit(path_text: str) -> str | None:
    normalized = path_text.replace("\\", "/").rstrip("/")
    for suffix in _INTERNAL_LICENSE_SUFFIXES:
        if normalized == suffix or normalized.endswith("/" + suffix):
            return suffix
    return None


def classify_license_inventory(path: Path) -> dict:
    """Split extractor parse from legal review. This function never grants a license.

    ``license-inventory`` may pass when the JSON is readable and the four
    package fields are present. That pass is parse completeness only.
    Scope and packaged rows stay blocked. Unknown is not a pass. A
    non-unknown license field is not a fail and is not authorization.
    Root sibling MIT does not spill. Written authorization is not invented.
    """
    unread = _license_rows(
        "blocked",
        "blocked",
        "blocked",
        "提取器退出 0 之后没有可读清单，不是合规通过",
        "许可范围未读到；unknown 不能当合规通过",
        "packaged 未读到；unknown 不能当合规通过",
    )
    if path.is_symlink() or forbidden_path(path) or not path.is_file():
        return _license_result("blocked", 2, unread[0]["detail"], unread)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _license_result("blocked", 2, "清单不是 JSON；退出 0 不是合规通过", unread)
    if not isinstance(data, dict):
        return _license_result("blocked", 2, "清单不是对象；退出 0 不是合规通过", unread)
    failures: list[str] = []
    notes: list[str] = []
    if data.get("network_audit") is not False:
        failures.append("network_audit 不是 false；离线提取契约被破坏，不是法律结论")
    conclusion = data.get("compliance_conclusion")
    if conclusion is None:
        notes.append("compliance_conclusion 缺失")
    elif conclusion != "not_determined":
        failures.append(
            "compliance_conclusion 不是 not_determined；提取器契约禁止写出合规结论；"
            "这不是法律审查通过，也不是已证明的书面授权义务"
        )
    membership = data.get("packaged_membership")
    if "packaged_membership" not in data or membership is None:
        packaged_detail = "packaged_membership 缺失；不能当合规通过，也不把缺失写成书面授权义务"
    elif membership == "unknown":
        packaged_detail = "packaged_membership=unknown 单独 blocked；不是入包证明，也不是合规通过"
    else:
        packaged_detail = (
            f"packaged_membership={membership} 只是提取字段，不是入包证明，不是合规通过；非 unknown 不直接判失败"
        )
    dependencies = data.get("dependencies")
    if dependencies is None:
        notes.append("dependencies 缺失")
    elif not isinstance(dependencies, list):
        failures.append("dependencies 不是数组")
    else:
        for row in dependencies:
            if not isinstance(row, dict):
                failures.append("依赖行不是对象")
                break
            if row.get("license") != "unknown" or row.get("packaged") != "unknown":
                notes.append("依赖行 license/packaged 非 unknown 只是 metadata，不是裁定")
                break
    components_raw = data.get("components")
    components = components_raw if isinstance(components_raw, list) else []
    if not isinstance(components_raw, list):
        notes.append("components 缺失")
    seen: set[str] = set()
    unknown_scopes: list[dict] = []
    recorded: list[dict] = []
    license_fields_readable = isinstance(components_raw, list)
    for component in components:
        if not isinstance(component, dict):
            failures.append("component 不是对象")
            license_fields_readable = False
            continue
        path_text = str(component.get("path") or "")
        hit = _internal_package_hit(path_text)
        license_value = component.get("license")
        if hit:
            if hit in seen:
                continue
            seen.add(hit)
            metadata = ""
            if component.get("manifest_license") not in (None, "unknown"):
                metadata = "；manifest_license 只是元数据，不是授权"
            if not isinstance(license_value, str):
                license_fields_readable = False
                unknown_scopes.append(
                    {
                        "path": hit,
                        "status": "blocked",
                        "license": None,
                        "reason": "license 字段不可读；不能当通过，也不是已经成立的额外授权义务" + metadata,
                    }
                )
            elif license_value in {"", "unknown"}:
                unknown_scopes.append(
                    {
                        "path": hit,
                        "status": "blocked",
                        "license": license_value,
                        "reason": "提取器字段 unknown 或空；不是法律排除，不是已经成立的额外授权义务，不能当通过" + metadata,
                    }
                )
            else:
                recorded.append(
                    {
                        "path": hit,
                        "status": "blocked",
                        "license": license_value,
                        "reason": "提取器记录了非 unknown 字段；不是法律授权；根 MIT 不外溢；不能当通过" + metadata,
                    }
                )
        elif license_value == "MIT" and component.get("license_basis") != "sibling_notice_file":
            notes.append(f"MIT 缺少 sibling_notice_file: {path_text}；提取规则缺口，不是法律排除")
        elif license_value == "MIT" and component.get("license_basis") == "sibling_notice_file":
            notes.append(f"同目录 MIT 只覆盖该 component，不外溢: {path_text}")
    for suffix in _INTERNAL_LICENSE_SUFFIXES:
        if suffix in seen:
            continue
        license_fields_readable = False
        unknown_scopes.append(
            {
                "path": suffix,
                "status": "blocked",
                "license": None,
                "reason": "包未出现在清单；缺失不是通过，也不是已证明的书面授权义务",
            }
        )
    if data.get("errors") is True:
        notes.append("清单 errors 为 true")
    data_complete = (
        not failures
        and license_fields_readable
        and isinstance(dependencies, list)
        and conclusion == "not_determined"
        and "packaged_membership" in data
        and data.get("errors") is False
        and data.get("network_audit") is False
    )
    if failures:
        inventory_status = "fail"
        extractor_parse = "fail"
        inventory_detail = "提取器契约失败，不是法律合规审查: " + "; ".join(failures[:6])
    elif not data_complete:
        inventory_status = "blocked"
        extractor_parse = "blocked"
        inventory_detail = "提取器数据不完整，解析未完成；这不是法律合规通过"
        extra = "；".join(notes[:4])
        if extra:
            inventory_detail = f"{inventory_detail}；{extra}"
    else:
        inventory_status = "pass"
        extractor_parse = "pass"
        inventory_detail = "提取器清单解析成功且四包字段可读；这不是法律合规通过"
        extra = "；".join(notes[:4])
        if extra:
            inventory_detail = f"{inventory_detail}；{extra}"
    scope_detail = _license_scope_detail(unknown_scopes, recorded)
    checks = _license_rows(
        inventory_status,
        "blocked",
        "blocked",
        inventory_detail,
        scope_detail,
        packaged_detail,
    )
    if any(item["status"] == "fail" for item in checks):
        stage_status, code = "fail", 1
    else:
        stage_status, code = "blocked", 2
    return _license_result(
        stage_status,
        code,
        inventory_detail,
        checks,
        extractor_parse=extractor_parse,
        unknown_scopes=unknown_scopes,
        recorded_license_fields=recorded,
    )


def _license_scope_detail(unknown_scopes: list[dict], recorded: list[dict]) -> str:
    parts = ["法律合规审查未做，不造授权"]
    if unknown_scopes:
        names = ", ".join(str(item.get("path") or "") for item in unknown_scopes)
        parts.append(f"当前未知单独 blocked: {names}")
        parts.append("提取器 unknown 或缺失不是排除，也不是已经成立的额外授权义务，不能当合规通过")
    if recorded:
        shown = ", ".join(f"{item.get('path')}={item.get('license')}" for item in recorded)
        parts.append(f"提取器字段 {shown} 不是法律授权")
        parts.append("根 MIT 与 sibling_notice_file 不外溢")
    return "；".join(parts)


def run_verify(repo: Path, layout: Layout) -> dict:
    python = repo / ".venv" / "bin" / "python"
    log_path = layout.logs / "verify-integration.log"
    outcome = spawn(
        [str(python), str(repo / "scripts" / "verify_clawd.py")],
        direct_env(layout, repo),
        str(repo),
        log_path,
        layout.pids / "verify-integration.json",
        600,
    )
    report_path = repo / "artifacts" / "binding" / "report.json"
    observations = _verify_observations(report_path)
    blocked = any(item["status"] == "blocked" for item in observations)
    failed = any(item["status"] == "fail" for item in observations) or outcome["exit_code"] == 1
    if failed:
        status = "fail"
    elif blocked or outcome["exit_code"] != 0 or not observations:
        status = "blocked"
    else:
        status = "pass"
    checks = [leaf("verify-integration", status, "probe blocked rows are preserved and not upgraded", "verify-clawd")]
    checks.extend(observations)
    return {
        "id": "verify-integration",
        "status": status,
        "exit_code": outcome["exit_code"],
        "log": str(log_path),
        "detail": "verify_clawd keeps its own artifacts/binding isolation and may reset that directory",
        "checks": checks,
    }


def _verify_observations(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, dict):
        return []
    rows = []
    for name, item in data.items():
        status = item.get("status") if isinstance(item, dict) else "unknown"
        if status not in {"pass", "fail", "blocked"}:
            status = "unknown"
        detail = item.get("detail", "") if isinstance(item, dict) else ""
        rows.append(leaf(f"verify:{name}", status, str(detail), "verify-clawd", required=False))
    return rows


def run_model(repo: Path, layout: Layout, provider_config: Path | None) -> dict:
    config = Path(provider_config) if provider_config is not None else None
    inspected = inspect_provider(config)
    if not inspected["ok"] or config is None:
        return _external_blocked("real-model-mcp", inspected["detail"])
    target = layout.home / "config.yaml"
    target.write_bytes(config.read_bytes())
    identity, payload, detail = _authorized_turn(repo, layout)
    external = classify_external_case(payload, requested_model=REQUESTED_MODEL)
    served = classify_run_payload(payload) if payload else {"status": "blocked", "identity": identity, "detail": detail}
    mcp_status = str(payload.get("_mcp_status") or "blocked") if payload else "blocked"
    checks = [
        leaf("real-model", external["status"], external["detail"], "live-provider"),
        leaf("served-runtime-model", served["status"], served["detail"], "live-provider", required=False),
        leaf("mcp-live", mcp_status, "empty or missing MCP session stays blocked", "live-provider"),
    ]
    if external["status"] == "fail" or mcp_status == "fail":
        status = "fail"
    elif external["status"] == "pass" and mcp_status == "pass":
        status = "pass"
    else:
        status = "blocked"
    return {
        "id": "real-model-mcp",
        "status": status,
        "exit_code": 0 if status == "pass" else (1 if status == "fail" else 2),
        "log": str(layout.logs / "real-model.log"),
        "detail": detail,
        "checks": checks,
        "identity": served.get("identity") or identity,
        "payload": _safe_payload(payload),
    }


def run_continuation(payload: dict | None) -> dict:
    if not payload:
        status, detail = "blocked", "no real delegated completion; test-not-llm is not a pass"
    elif payload.get("marker"):
        status, detail = "blocked", "test-not-llm marker cannot pass"
    else:
        external = classify_external_case(payload, requested_model=REQUESTED_MODEL)
        if external["status"] == "fail":
            status, detail = "fail", external["detail"]
        elif external["status"] == "pass" and payload.get("delegation") is True:
            status, detail = "pass", "delegated completion requested the acceptance model; runtime field was not assumed"
        else:
            status, detail = "blocked", "ping or lifecycle ready is not a continuation turn"
    return _done("continuation", status, None, Path(""), detail, "continuation", check_id="continuation-real-turn")


def run_rollback(repo: Path, layout: Layout, explicit: str | None) -> dict:
    if explicit:
        path = Path(explicit)
        if forbidden_path(path) or path.name == "panel-deploy.sh":
            return _blocked_stage("rollback", "refused production rollback")
        return _blocked_stage("rollback", "explicit rollback entry is not the cutover cli contract")
    journal = _isolated_journal(layout)
    if journal is None:
        return _blocked_stage("rollback", "no isolated cutover journal; cli was not invoked; production deploy was not called")
    recorded = _journal_paths(journal, layout.root)
    if recorded is None:
        return _blocked_stage("rollback", "journal paths are missing or outside the acceptance layout")
    argv = [
        str(repo / ".venv" / "bin" / "python"),
        str(repo / "scripts" / "clawd_cutover" / "cli.py"),
        "rollback",
        "--allow-root",
        str(layout.root),
        "--target",
        recorded["target"],
        "--journal",
        str(journal),
        "--confirm",
        "ROLLBACK",
    ]
    if recorded.get("previous"):
        argv.extend(["--previous", recorded["previous"]])
    ok, detail = cutover_rollback_argv_ok(repo, argv)
    if not ok:
        return _blocked_stage("rollback", detail)
    log_path = layout.logs / "rollback.log"
    outcome = spawn(argv, direct_env(layout, repo), str(repo), log_path, layout.pids / "rollback.json", 300)
    status = "pass" if outcome["exit_code"] == 0 else "fail"
    return _done("rollback", status, outcome["exit_code"], log_path, "isolated cutover cli rollback; confirm does not cross production", "rollback")


def _journal_paths(journal: Path, root: Path) -> dict | None:
    try:
        data = json.loads(journal.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("target"), str):
        return None
    target = Path(data["target"])
    previous = data.get("previous")
    if not _inside(root, target) or forbidden_path(target):
        return None
    found = {"target": str(target)}
    if isinstance(previous, str):
        previous_path = Path(previous)
        if not _inside(root, previous_path) or forbidden_path(previous_path):
            return None
        found["previous"] = str(previous_path)
    return found


def _inside(root: Path, path: Path) -> bool:
    try:
        return path.is_relative_to(root)
    except (OSError, ValueError):
        return False


def _isolated_journal(layout: Layout) -> Path | None:
    root = layout.root
    for path in (layout.logs / "journal-swapped.json", root / "journal-swapped.json"):
        if path.is_file() and not path.is_symlink() and path.is_relative_to(root):
            return path
    return None


def _runtime(payload: dict) -> dict:
    raw = payload.get("runtime")
    return raw if isinstance(raw, dict) else {}


def _safe_payload(payload: dict | None) -> dict | None:
    if not payload:
        return None
    blob = json.dumps(payload, ensure_ascii=False)
    runtime = _runtime(payload)
    return {
        "status": payload.get("status"),
        "runtime": {"model": runtime.get("model"), "provider": runtime.get("provider")},
        "delegation": "async_delegation_complete" in blob,
        "marker": "test-not-llm" in blob or "test-executor-not-llm" in blob,
    }


def _authorized_turn(repo: Path, layout: Layout) -> tuple[dict, dict | None, str]:
    identity = default_identity()
    python = repo / ".venv" / "bin" / "python"
    log_path = layout.logs / "real-model.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    env = direct_env(layout, repo, {"CLAWD_PANEL_PASSWORD": "acceptance-isolated"})
    added_argv = [str(python), "-m", "clawd_server", "user", "add", "--home", str(layout.home), "--user", "accept"]
    serve_argv = [str(python), "-m", "clawd_server", "serve", "--home", str(layout.home), "--host", "127.0.0.1", "--port", "0"]
    if not server_argv_ok(repo, added_argv)[0] or not server_argv_ok(repo, serve_argv)[0]:
        log_path.write_text("server argv does not match clawd_server argparse\n", encoding="utf-8")
        return identity, None, "server command does not match source argparse"
    added = subprocess.run(
        added_argv,
        cwd=str(repo),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if added.returncode != 0:
        log_path.write_text("user add failed\n", encoding="utf-8")
        return identity, None, "isolated user add failed"
    handle = log_path.open("ab")
    proc = subprocess.Popen(
        serve_argv,
        cwd=str(repo),
        env=env,
        stdout=handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        url = _wait_ready(log_path, proc, 90)
        if not url:
            return identity, None, "isolated server did not become ready"
        host = url.split("://", 1)[1]
        digest = hashlib.sha256(b"hermes-panel:v2:accept\nacceptance-isolated").hexdigest()
        headers = {"Cookie": f"hp_auth={digest}", "Host": host, "Content-Type": "application/json"}
        request_body = json.dumps({"input": "ping", "model": REQUESTED_MODEL}, ensure_ascii=False).encode()
        status, body = _request(url + "/panel-api/v1/runs", request_body, headers)
        if status not in {200, 201, 202}:
            return identity, None, f"run not accepted HTTP {status}"
        try:
            run_id = json.loads(body).get("run_id", "")
        except json.JSONDecodeError:
            return identity, None, "run response was not json"
        payload = _poll_run(url, run_id, headers) or {"status": "unknown"}
        payload["_requested_model"] = REQUESTED_MODEL
        mcp_status, mcp_body = _request(url + "/panel-sidecar/mcp", None, {"Cookie": headers["Cookie"], "Host": host})
        payload["_mcp_status"] = classify_mcp(mcp_status, mcp_body)
        identity = classify_run_payload(payload)["identity"]
        identity["observed"] = _runtime(payload).get("model")
        identity["parent_agent_checked"] = False
        return identity, payload, "polled isolated run; requested model sent; secrets not logged"
    finally:
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except OSError:
                proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except OSError:
                    proc.kill()
                proc.wait(timeout=5)
        handle.close()


def _wait_ready(log_path: Path, proc: subprocess.Popen, seconds: int) -> str:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if proc.poll() is not None:
            return ""
        text = _read(log_path)
        for line in text.splitlines():
            if line.startswith("CLAWD_PANEL_READY") and "url=" in line:
                return line.split("url=", 1)[1].strip()
        time.sleep(0.2)
    return ""


def _request(url: str, body: bytes | None, headers: dict[str, str]) -> tuple[int, bytes]:
    method = "POST" if body is not None else "GET"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=40) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0, b""


def _poll_run(url: str, run_id: str, headers: dict[str, str]) -> dict | None:
    if not run_id:
        return None
    deadline = time.time() + 120
    while time.time() < deadline:
        status, body = _request(url + "/panel-api/v1/runs/" + run_id, None, headers)
        if status != 200:
            time.sleep(1)
            continue
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return None
        if payload.get("status") in {"completed", "failed", "error", "cancelled"}:
            return payload
        time.sleep(1)
    return {"status": "timeout"}


def _command_stage(stage: dict, repo: Path, layout: Layout, env: dict[str, str]) -> dict:
    log_path = layout.logs / f"{stage['id']}.log"
    outcome = spawn(stage["argv"], env, stage["cwd"], log_path, layout.pids / f"{stage['id']}.json", stage["timeout_seconds"])
    if outcome["timed_out"]:
        status = "blocked"
    elif outcome["exit_code"] == 0:
        status = "pass"
    else:
        status = "fail"
    return _done(stage["id"], status, outcome["exit_code"], log_path, stage["title"], stage["overlap_group"])


def _done(sid: str, status: str, code: int | None, log_path: Path, detail: str, group: str, *, extra: list[dict] | None = None, check_id: str | None = None) -> dict:
    checks = extra if extra is not None else [leaf(check_id or sid, status, detail, group)]
    return {
        "id": sid,
        "status": status,
        "exit_code": code,
        "log": str(log_path) if str(log_path) else "",
        "detail": detail,
        "checks": checks,
    }


def _blocked_stage(sid: str, detail: str) -> dict:
    return _done(sid, "blocked", 2, Path(""), detail, sid)


def _external_blocked(sid: str, detail: str) -> dict:
    return {
        "id": sid,
        "status": "blocked",
        "exit_code": 2,
        "log": "",
        "detail": detail,
        "checks": [
            leaf("real-model", "blocked", detail, "live-provider"),
            leaf("mcp-live", "blocked", detail, "live-provider"),
        ],
        "identity": default_identity(),
    }


def execute_stage(stage: dict, repo: Path, layout: Layout, options: dict) -> dict:
    sid = stage["id"]
    state_path = layout.logs / f"{sid}.state.json"
    previous = load_state(state_path)
    raw_pid = previous.get("pid")
    pid = raw_pid if isinstance(raw_pid, int) else None
    choice = decide(
        state=str(previous.get("state") or "absent"),
        pid=pid,
        resume_interrupted=bool(options.get("resume_interrupted")),
        rerun=bool(options.get("rerun")),
    )
    if choice == "attach":
        return _done(sid, "in_progress", None, layout.logs / f"{sid}.log", "live process exists; not started again", stage["overlap_group"])
    if choice == "refuse":
        return _done(sid, "interrupted", None, layout.logs / f"{sid}.log", "interrupted and --resume-interrupted not set", stage["overlap_group"])
    if choice == "skip" and previous.get("result"):
        return previous["result"]
    write_json(state_path, {"state": "running", "pid": None})
    result = _dispatch(stage, repo, layout, options)
    pid_file = layout.pids / f"{sid}.json"
    pid = None
    if pid_file.is_file():
        try:
            pid = json.loads(pid_file.read_text(encoding="utf-8")).get("pid")
        except (OSError, json.JSONDecodeError):
            pid = None
    if not isinstance(pid, int):
        pid = None
    write_json(state_path, {"state": result["status"], "pid": pid if pid_alive(pid) else None, "result": result})
    return result


def _dispatch(stage: dict, repo: Path, layout: Layout, options: dict) -> dict:
    sid = stage["id"]
    if sid == "core":
        return run_core(repo, layout)
    if sid == "tui-check":
        return run_npm(stage, repo, layout, repo)
    if sid == "tui-build":
        return run_tui_build(stage, repo, layout)
    if sid == "tui-pty":
        outcome = run_pty(repo, layout, layout.logs / "tui-pty.log")
        return _done("tui-pty", outcome["status"], outcome.get("exit_code"), layout.logs / "tui-pty.log", outcome["detail"], "tui-pty")
    if sid in {"panel-typecheck", "panel-build"}:
        return run_npm(stage, repo, layout, repo / "apps" / "panel")
    if sid == "panel-node":
        return run_panel_node(repo, layout, stage)
    if sid == "panel-python":
        return run_panel_python(repo, layout, stage)
    if sid == "panel-browser":
        return run_browser(repo, layout, stage)
    if sid == "package":
        prefix = Path(options["install_prefix"]) if options.get("install_prefix") else None
        return run_package(repo, layout, prefix, stage)
    if sid == "license-inventory":
        return run_license(repo, layout)
    if sid == "verify-integration":
        return run_verify(repo, layout)
    if sid == "real-model-mcp":
        config = Path(options["provider_config"]) if options.get("provider_config") else None
        return run_model(repo, layout, config)
    if sid == "continuation":
        return run_continuation(options.get("model_payload"))
    if sid == "rollback":
        return run_rollback(repo, layout, options.get("rollback_entry"))
    return _blocked_stage(sid, "unknown stage")


def gate_view(repo: Path, implementation_complete: bool) -> dict:
    loaded = load_manifest(repo / "docs" / "clawd" / "implementation-completion-manifest.json")
    contract = manifest_allows_run(loaded, implementation_complete)
    cases = case_suites_ready(repo)
    inventory = inventory_ready(repo)
    tools = toolchain_status(repo)
    tools_ok = tools["python_present"] and tools["node_present"] and tools["npm_present"]
    allowed = bool(contract["run_allowed"] and cases["ok"] and inventory["ok"] and tools_ok)
    return {
        "manifest_path": str(MANIFEST_PATH),
        "manifest": loaded,
        "contract": contract,
        "cases": cases,
        "inventory": inventory,
        "toolchain_ready": tools_ok,
        "run_allowed": allowed,
        "implemented_means": "code delivered, not tests passed",
        "tests_passed": False,
    }


def _context(repo: Path, layout: Layout, gate: dict) -> dict:
    """Shared report fields. phase and executed are not included.

    Callers must set those once in ``_report_kwargs``. Spreading this dict
    into ``assemble`` and also passing ``phase=`` raises TypeError.
    """
    return {
        "gate": gate,
        "isolation": run_tests_plan(repo, layout),
        "toolchain": toolchain_status(repo),
        "stages": stage_catalog(repo, layout),
        "checks": _checks_for_gate(repo, gate),
        "identity": default_identity(),
    }


def _report_kwargs(
    repo: Path,
    layout: Layout,
    gate: dict,
    *,
    phase: str,
    executed: bool,
    stages: list[dict] | None = None,
    checks: list[dict] | None = None,
    identity: dict | None = None,
    wheel_dir: TrustedArchiveHandoff | None = None,
) -> dict:
    """One mapping for assemble. phase is set here and must not be repeated."""
    fields = _context(repo, layout, gate)
    if stages is not None:
        fields["stages"] = stages
    if checks is not None:
        fields["checks"] = checks
    if identity is not None:
        fields["identity"] = identity
    fields["executed"] = executed
    fields["phase"] = phase
    fields["repo"] = repo
    fields["wheel_dir"] = wheel_dir
    return fields


def _stored_checks(result: dict) -> list[dict]:
    """Replay stored rows without turning an empty or failed stage into a pass."""
    rows = [item for item in (result.get("checks") or []) if isinstance(item, dict)]
    status = result.get("status")
    sid = str(result.get("id") or "stored-stage")
    detail = str(result.get("detail") or "")
    if status == "fail" and not any(item.get("status") == "fail" and item.get("required", True) for item in rows):
        rows.append(leaf(sid, "fail", detail or "stored stage failed; empty or non-fail checks are not a pass", "replay"))
    elif status == "blocked" and not any(
        item.get("status") in {"fail", "blocked"} and item.get("required", True) for item in rows
    ):
        rows.append(leaf(sid, "blocked", detail or "stored stage blocked; not a pass", "replay"))
    elif not rows:
        if status == "pass":
            rows.append(leaf(sid, "blocked", "stored pass had no checks; not a pass", "replay"))
        elif status in {"unknown", "interrupted", "not_run"}:
            rows.append(leaf(sid, str(status), detail or "stored stage had no checks", "replay"))
        elif status == "in_progress":
            rows.append(leaf(sid, "unknown", detail or "stored stage still in progress", "replay"))
        else:
            rows.append(leaf(sid, "unknown", detail or "stored stage had no checks", "replay"))
    return rows


def _checks_for_gate(repo: Path, gate: dict) -> list[dict]:
    checks = coverage_checks(repo)
    cases = {item["id"]: item for item in gate.get("cases", {}).get("checks", [])}
    updated = []
    for check in checks:
        case = cases.get(check["id"])
        if case and case.get("status") != "ready":
            updated.append({**check, "status": "blocked", "detail": case.get("detail", "not delivered")})
        elif check["id"] == "license-inventory" and not gate.get("inventory", {}).get("ok", False):
            updated.append({**check, "status": "blocked", "detail": gate.get("inventory", {}).get("detail", "inventory blocked")})
        else:
            updated.append(check)
    return updated


def _trusted_archive_handoff(layout: Layout, package_result: dict | None) -> TrustedArchiveHandoff:
    """Paths from this run's package stage. Not a directory to list."""
    wheel = None
    sdist = None
    if isinstance(package_result, dict):
        env = package_result.get("distribution_env")
        if isinstance(env, dict):
            raw_wheel = env.get(DISTRIBUTION_WHEEL_ENV)
            raw_sdist = env.get(DISTRIBUTION_SDIST_ENV)
            if isinstance(raw_wheel, str):
                wheel = raw_wheel
            if isinstance(raw_sdist, str):
                sdist = raw_sdist
    return TrustedArchiveHandoff(layout.wheel, wheel, sdist)


def _linked_acceptance_root(layout: Layout) -> bool:
    return layout.root.is_symlink()


def prepare(repo: Path, layout: Layout) -> dict:
    linked = _linked_acceptance_root(layout)
    if not linked:
        layout.ensure()
    gate = gate_view(repo, False)
    report = assemble(**_report_kwargs(
        repo,
        layout,
        gate,
        phase="prepare",
        executed=False,
        wheel_dir=_trusted_archive_handoff(layout, None),
    ))
    if linked:
        return report
    write_json(layout.root / "prepare-plan.json", {"acceptance_exit": 2, "report": report})
    write_json(layout.root / "report.json", report)
    return report


def run_all(repo: Path, layout: Layout, options: dict) -> int:
    if not options.get("run") or not options.get("implementation_complete"):
        return 2
    if _linked_acceptance_root(layout):
        return 2
    layout.ensure()
    gate = gate_view(repo, True)
    if not gate["run_allowed"]:
        report = assemble(**_report_kwargs(
            repo,
            layout,
            gate,
            phase="run",
            executed=False,
            wheel_dir=_trusted_archive_handoff(layout, None),
        ))
        write_json(layout.root / "report.json", report)
        return 2
    stages = []
    checks: list[dict] = []
    identity = default_identity()
    payload = None
    package_env = None
    package_result = None
    for stage in stage_catalog(repo, layout):
        if stage["id"] == "continuation":
            options = {**options, "model_payload": payload}
        result = execute_stage(stage, repo, layout, options)
        if stage["id"] == "real-model-mcp":
            identity = result.get("identity") or identity
            payload = result.get("payload")
        if stage["id"] == "package":
            package_env = _pending_distribution_env(result)
            package_result = result
        stages.append({key: result.get(key) for key in ("id", "status", "exit_code", "log", "detail")})
        checks.extend(_stored_checks(result))
    manifest = compare_distribution_manifest(repo, layout, package_env)
    if manifest is not None:
        if any(item.get("id") == "package-manifest-compared" for item in checks):
            checks = _replace_check(checks, "package-manifest-compared", manifest)
        else:
            checks.append(manifest)
        _refresh_package_stage(layout, stages, checks)
    seen = {item.get("id") for item in checks}
    for item in coverage_checks(repo):
        if item.get("id") not in seen:
            checks.append(item)
    report = assemble(**_report_kwargs(
        repo,
        layout,
        gate,
        phase="run",
        executed=True,
        stages=stages,
        checks=checks,
        identity=identity,
        wheel_dir=_trusted_archive_handoff(layout, package_result),
    ))
    write_json(layout.root / "report.json", report)
    return int(report["summary"]["exit_code"])


def report_only(repo: Path, layout: Layout) -> int:
    gate = gate_view(repo, False)
    if _linked_acceptance_root(layout):
        report = assemble(**_report_kwargs(
            repo,
            layout,
            gate,
            phase="report",
            executed=False,
            stages=[{"id": "acceptance-root", "status": "blocked", "exit_code": 2, "log": "", "detail": "symlink"}],
            checks=[leaf("acceptance-root", "blocked", "acceptance root is a symlink; refusing to read it as this report", "replay")],
            wheel_dir=_trusted_archive_handoff(layout, None),
        ))
        return int(report["summary"]["exit_code"])
    stored = layout.logs
    stages = []
    checks: list[dict] = []
    executed = False
    package_result = None
    saw_record = False
    if stored.is_symlink():
        saw_record = True
        detail = "logs directory is a symlink; refusing to read it as this report"
        stages.append({"id": "stored-logs", "status": "blocked", "exit_code": 2, "log": "", "detail": detail})
        checks.append(leaf("stored-logs", "blocked", detail, "replay"))
    elif stored.is_dir():
        for stage in stage_catalog(repo, layout):
            path = stored / f"{stage['id']}.state.json"
            if path.is_symlink():
                saw_record = True
                detail = "stored stage state is a symlink; refusing to read it as this report"
                stages.append({"id": stage["id"], "status": "blocked", "exit_code": 2, "log": "", "detail": detail})
                checks.append(leaf(stage["id"], "blocked", detail, "replay"))
                continue
            state = load_state(path)
            result = state.get("result") if isinstance(state, dict) else None
            if isinstance(result, dict):
                saw_record = True
                executed = True
                if stage["id"] == "package":
                    package_result = result
                stages.append({key: result.get(key) for key in ("id", "status", "exit_code", "log", "detail")})
                checks.extend(_stored_checks(result))
            elif path.is_file() and state.get("state") == "unknown":
                saw_record = True
                detail = "stored stage state is unreadable; not a pass"
                stages.append({"id": stage["id"], "status": "blocked", "exit_code": 2, "log": str(path), "detail": detail})
                checks.append(leaf(stage["id"], "blocked", detail, "replay"))
    if not saw_record:
        checks = coverage_checks(repo)
        stages = stage_catalog(repo, layout)
        executed = False
        package_result = None
    report = assemble(**_report_kwargs(
        repo,
        layout,
        gate,
        phase="report",
        executed=executed,
        stages=stages,
        checks=checks,
        wheel_dir=_trusted_archive_handoff(layout, package_result),
    ))
    if layout.root.exists() and layout.root.is_dir() and not layout.root.is_symlink():
        write_json(layout.root / "report.json", report)
    return int(report["summary"]["exit_code"])
