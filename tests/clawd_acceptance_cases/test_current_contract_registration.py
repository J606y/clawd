"""Register the current fixture contracts. Prepared only. Not a pass.

Reads source text and walks directories. Does not import product modules,
build, install, or execute the other tests. Authoring did not run this file.
"""

from __future__ import annotations

import ast
import json
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TESTS = REPO / "tests"
LAYOUT = TESTS / "test_tests_tree_layout.py"
MANIFEST_TEST = TESTS / "clawd_server" / "test_distribution_package_manifest.py"
VENDOR_TEST = TESTS / "clawd_server" / "test_browser_vendor_distribution.py"
RUNNER = REPO / "scripts" / "run_tests_parallel.py"
PANEL_TEST = REPO / "apps" / "panel" / "tests" / "transcript-continue.test.mjs"
LIVE_TAIL = REPO / "apps" / "panel" / "src" / "react" / "pages" / "chat" / "live-tail.tsx"
PACKAGE_JSON = REPO / "apps" / "panel" / "package.json"

VENDOR_LINES = (
    "recursive-include tools/vendor/browser_harness_0_1_13 *",
    "exclude tools/vendor/browser_harness_0_1_13/PKG-INFO",
    "exclude tools/vendor/browser_harness_0_1_13/README.md",
    "exclude tools/vendor/browser_harness_0_1_13/pyproject.toml",
    "exclude tools/vendor/browser_harness_0_1_13/setup.cfg",
    "exclude tools/vendor/browser_harness_0_1_13/src/mcp_server.py",
    "recursive-exclude tools/vendor/browser_harness_0_1_13 *.egg-info/*",
    "recursive-exclude tools/vendor/browser_harness_0_1_13 __pycache__/*",
    "prune tools/vendor/browser_harness_0_1_13/src/browser_harness.egg-info",
)
PANEL_DEPTH = (
    "recursive-include clawd_server/bundled/apps/panel/dist *",
    "recursive-include clawd_server/bundled/apps/panel/sidecar *.py",
    "recursive-include clawd_server/bundled/apps/panel/plugins *",
)


def _tree(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"))


def _assigned(path: Path, name: str) -> ast.AST:
    for node in _tree(path).body:
        if isinstance(node, ast.Assign):
            targets = node.targets
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
            value = node.value
        else:
            continue
        if value is not None and any(isinstance(item, ast.Name) and item.id == name for item in targets):
            return value
    raise AssertionError(f"{name} is not assigned in {path}")


def _literal(path: Path, name: str):
    return ast.literal_eval(_assigned(path, name))


def _function_source(path: Path, name: str) -> str:
    source = path.read_text(encoding="utf-8")
    for node in _tree(path).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    raise AssertionError(f"{name} missing in {path}")


def _non_mirror_clawd_dirs() -> set[str]:
    source_packages = {
        path.name
        for path in REPO.iterdir()
        if path.is_dir() and (path / "__init__.py").is_file()
    }
    return {
        path.name
        for path in TESTS.iterdir()
        if path.is_dir()
        and path.name.startswith("clawd_")
        and path.name not in source_packages
        and path.name != "__pycache__"
    }


def test_vendor_manifest_lines_match_without_weakening_archive_checks() -> None:
    manifest = (REPO / "MANIFEST.in").read_text(encoding="utf-8").splitlines()
    declared = _literal(MANIFEST_TEST, "MANIFEST_LINES")
    vendor_declared = _literal(VENDOR_TEST, "MANIFEST_LINES")
    assert declared == manifest
    assert vendor_declared == manifest
    assert "prune plugins/web" not in manifest
    assert "prune web" in manifest
    assert "prune apps/desktop" in manifest
    assert "prune hermes_cli/web_dist" in manifest
    search = _literal(MANIFEST_TEST, "REQUIRED_SEARCH")
    assert "plugins/web/brave_free/provider.py" in search
    assert "plugins/web/brave_free/plugin.yaml" in search
    assert "plugins/web/ddgs/provider.py" in search
    assert "plugins/web/ddgs/_search_worker.py" in search
    for line in (*VENDOR_LINES, *PANEL_DEPTH):
        assert line in manifest
    include_at = manifest.index("recursive-include tools/vendor/browser_harness_0_1_13 *")
    assert include_at < manifest.index("global-exclude *.pyc")
    text = MANIFEST_TEST.read_text(encoding="utf-8")
    assert "distribution_path_is_pruned" in text
    assert 'f"/{prefix}" in' not in text
    assert "assert manifest == MANIFEST_LINES" in text
    assert "len(data) != size or hashlib.sha256(data).hexdigest() != digest" in text
    assert "hash/size mismatch:" in text
    assert "forbidden_reason(" in text
    assert 'problems.append(reason)' in text
    assert "ArchiveMismatch" in text
    package_data = _literal(MANIFEST_TEST, "PACKAGE_DATA")
    assert "bundled/apps/panel/dist/*/*/*" in package_data
    assert not any("browser_harness" in item or "**" in item for item in package_data)
    assert '["package-data"]["clawd_server"] == PACKAGE_DATA' in text
    assert '["license-files"] == ["LICENSE"]' in text


def test_continuation_fixture_drops_success_cap_and_keeps_stops() -> None:
    text = PANEL_TEST.read_text(encoding="utf-8")
    assert "AUTO_CONTINUE_LIMIT" not in text
    assert "limited, true" not in text
    assert "limited === true" not in text
    assert "成功次数不是展示门槛" in text
    assert "fifth.limited, false" in text
    assert "人话占有下一轮，不是次数解锁" in text
    assert "不靠把成功次数门槛调大或清掉" in text
    assert "CLAWD_CONTINUATION_KIND" in text
    assert "CLAWD_CONTINUATION_MARK" in text
    assert "后台子任务失败" in text
    assert "已停止" in text
    assert "执行失败" in text
    assert "waiting_for_approval" in text
    assert "respondApproval" in text
    assert "<ApprovalCard" in text
    assert "doesNotMatch(page, /已连续自动继续/)" in text
    assert "doesNotMatch(tail, /已连续自动继续/)" in text


def test_live_tail_recognizes_kind_and_marks_without_changing_rendering() -> None:
    text = LIVE_TAIL.read_text(encoding="utf-8")
    assert "function isLiveContinuation" in text
    assert "run.display_kind === CLAWD_CONTINUATION_KIND" in text
    assert "if (run.display_kind) return false" in text
    assert "text.startsWith(CLAWD_CONTINUATION_MARK)" in text
    assert "text.startsWith(AUTO_CONTINUE_MARK)" in text
    assert "isLiveContinuation(run)" in text
    assert (
        '<NoticeRow turn={{ role: "notice", title: "子任务已完成，自动继续", '
        "failed: false, detail: run.userText }} />"
    ) in text
    assert '<div className="turn user">' in text
    assert "<ApprovalCard" in text
    assert "pace={pace}" in text
    assert "<AssistantBody" in text
    assert "已连续自动继续" not in text
    assert "run.userText.startsWith(AUTO_CONTINUE_MARK)" not in text


def test_clawd_case_dirs_are_exact_names_and_discovery_still_collects_them() -> None:
    reasons = _literal(LAYOUT, "_CLAWD_CASE_DIR_REASONS")
    assert isinstance(reasons, dict) and reasons
    assert set(reasons) == _non_mirror_clawd_dirs()
    for name, reason in reasons.items():
        assert "*" not in name and "?" not in name and "[" not in name
        assert name.replace("_", "").isalnum()
        assert isinstance(reason, str) and reason.strip()
        assert (TESTS / name).is_dir()
    assert "clawd_server" not in reasons
    assert "clawd_projects" not in reasons
    assert (REPO / "clawd_server" / "__init__.py").is_file()
    assert (REPO / "clawd_projects" / "__init__.py").is_file()

    layout = LAYOUT.read_text(encoding="utf-8")
    starred = _assigned(LAYOUT, "_NON_MIRROR_DIRS")
    assert isinstance(starred, ast.Set)
    assert any(
        isinstance(item, ast.Starred)
        and isinstance(item.value, ast.Name)
        and item.value.id == "_CLAWD_CASE_DIR_REASONS"
        for item in starred.elts
    )
    gate = _function_source(LAYOUT, "test_every_test_directory_mirrors_a_source_directory_or_is_declared")
    assert "assert not offenders" in gate
    assert "d.name not in _NON_MIRROR_DIRS" in gate
    assert "startswith" not in gate
    assert "fnmatch" not in gate

    skips = _literal(RUNNER, "_SKIP_PARTS")
    assert skips == {"integration", "e2e", "docker"}
    assert not (set(reasons) & skips)
    discover = _function_source(RUNNER, "_discover_files")
    assert 'rglob("test_*.py")' in discover
    assert "root.is_file()" in discover
    pyproject = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["tool"]["pytest"]["ini_options"]["testpaths"] == ["tests"]
    assert "python_files" not in pyproject["tool"]["pytest"]["ini_options"]

    collected: list[Path] = []
    for name in reasons:
        found = sorted((TESTS / name).rglob("test_*.py"))
        assert found, name
        for path in found:
            assert not (set(path.parts) & skips)
            assert path.name.startswith("test_")
            collected.append(path)
    assert Path(__file__).resolve() in {path.resolve() for path in collected}

    package = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))
    script = package["scripts"]["test:node"]
    assert "tests/*.test.mjs" in script
    assert PANEL_TEST.is_file()
    assert PANEL_TEST.parent.name == "tests"
    assert PANEL_TEST.name.endswith(".test.mjs")
