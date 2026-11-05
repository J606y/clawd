"""The tests/ tree mirrors the source tree.

``scripts/run_tests.sh tests/<dir>/`` is how a change gets its regression
coverage run, so a test filed under a directory that does not correspond to
the code it exercises is a test nobody runs when that code changes. Two
drifts had accumulated: parallel directories for one source package
(``tests/cli`` beside ``tests/hermes_cli``, ``tests/run_agent`` beside
``tests/agent``, ``tests/state`` beside ``tests/hermes_state``) and ~250
loose files at ``tests/`` root that belonged to a package.

Filenames also stopped carrying issue numbers: ``test_89315_x.py`` reads as
noise in a directory listing and the number belongs in the docstring, where
``git log -S`` and a reader can both find it with context.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TESTS_ROOT = REPO_ROOT / "tests"

# tests/<name>/ directories that do not mirror a source directory but are
# legitimate homes: cross-cutting suites, fixtures, and script-family tests.
# "compat": frozen pre-PM updater import surface (tests/compat/README.md) — the
# audited names live only in history, so there is no source package to mirror.
# "installation": python driver for the tests/install shell e2e assets.
# "_fixtures": topic modules tests/conftest.py is split into — suite-wide
# plumbing (env filtering, live-system guard, platform gating), not tests.
# clawd_* case homes below are exact directory names, not a prefix or glob.
# A new tests/clawd_* directory is still an offender until it is listed with a
# reason. Listing a name does not skip collection.
_CLAWD_CASE_DIR_REASONS = {
    "clawd_acceptance_cases": "验收编排跨包夹具，不是源码包。",
    "clawd_auth_native": "身份与制品根契约夹具，没有同名源码包。",
    "clawd_continuation_cases": "续跑决策契约夹具，不是 gateway 包镜像。",
    "clawd_effort_cases": "档位与续跑 pending 契约夹具，不是源码包。",
    "clawd_event_native": "事件订阅身份伴随夹具，不是源码包。",
    "clawd_event_recovery_cases": "运行事件恢复契约夹具，不是源码包。",
    "clawd_file_auth_cases": "文件鉴权跨入口夹具，不是源码包。",
    "clawd_memory_native": "项目记忆身份与冻结契约夹具，不是源码包。",
    "clawd_memory_scope_cases": "记忆作用域跨 profile 夹具，不是源码包。",
    "clawd_race_cases": "审批与续跑竞态夹具，不是源码包。",
    "clawd_runtime_cases": "安装后 run-wait 与 child 计数契约夹具，不是源码包。",
    "clawd_settings_cases": "设置公共契约夹具，不是源码包。",
    "clawd_todo_delegation": "待办执行投影夹具，不是源码包。",
    "clawd_todo_native": "待办列表接线夹具，不是源码包。",
}
_NON_MIRROR_DIRS = {
    "compat", "installation", "_fixtures",
    "ci", "conformance", "dashboard", "desktop", "docker", "e2e", "evals",
    "fakes", "fixtures", "install", "integration", "manual",
    "monitoring", "perf_guards", "scripts", "secret_sources",
    "security", "skills", "verify", "website", "computer_use", "hermes_state",
    *_CLAWD_CASE_DIR_REASONS,
}

# Root-level modules whose tests sit directly in tests/ (no package to mirror).
_ROOT_MODULE_STEMS = {p.stem for p in REPO_ROOT.glob("*.py")}

_ISSUE_NUMBER = re.compile(r"(^test_\d{4,6}_)|(_\d{4,6}\.py$)")


def _source_dirs() -> set[str]:
    return {
        p.name
        for p in REPO_ROOT.iterdir()
        if p.is_dir() and (p / "__init__.py").exists()
    }


def test_every_test_directory_mirrors_a_source_directory_or_is_declared():
    """No sibling directory for a source package that already has one."""
    for name, reason in _CLAWD_CASE_DIR_REASONS.items():
        assert "*" not in name and "?" not in name and "[" not in name, name
        assert name.replace("_", "").isalnum(), name
        assert isinstance(reason, str) and reason.strip(), name
    assert set(_CLAWD_CASE_DIR_REASONS) <= _NON_MIRROR_DIRS
    source_dirs = _source_dirs()
    offenders = sorted(
        d.name
        for d in TESTS_ROOT.iterdir()
        if d.is_dir()
        and d.name not in ("__pycache__",)
        and d.name not in source_dirs
        and d.name not in _NON_MIRROR_DIRS
    )
    assert not offenders, (
        "tests/ directories that mirror no source package: "
        f"{offenders}. Put the tests under tests/<source dir>/ (tests/agent, "
        "tests/hermes_cli, ...) or, for a genuinely cross-cutting suite, add the "
        "name to _NON_MIRROR_DIRS in this file with a reason."
    )


def test_no_issue_numbers_in_test_filenames():
    """Issue numbers live in docstrings, where the reader gets context."""
    offenders = sorted(
        str(p.relative_to(REPO_ROOT))
        for p in TESTS_ROOT.rglob("test_*.py")
        if _ISSUE_NUMBER.search(p.name)
    )
    assert not offenders, (
        f"Issue numbers in test filenames: {offenders}. Drop the number from the "
        "name and cite the issue in the module docstring instead."
    )
