"""Static and fixture checks for panel distribution resources.

These tests must not build a wheel, install a package, download dependencies,
or invoke the panel frontend toolchain. Missing inputs fail closed.
"""

from __future__ import annotations

import hashlib
import json
import os
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _fake_checkout(root: Path) -> Path:
    _write(root / "LICENSE", "MIT License\nCopyright (c) 2025 Nous Research\n")
    _write(root / "apps" / "panel" / "dist" / "index.html", "<html>panel</html>\n")
    _write(root / "apps" / "panel" / "dist" / "assets" / "app.js", "console.log(1)\n")
    _write(root / "apps" / "panel" / "dist" / "node_modules" / "left.js", "nope")
    _write(root / "apps" / "panel" / "node_modules" / "pkg" / "index.js", "nope")
    _write(root / "apps" / "panel" / "package.json", '{"name": "clawd-panel"}\n')
    plugin = root / "apps" / "panel" / "plugins" / "browser-window-guard"
    _write(plugin / "__init__.py", "PLUGIN\n")
    _write(plugin / "live_task_store.py", "PLUGIN_STORE\n")
    _write(plugin / "plugin.yaml", "name: browser-window-guard\n")
    _write(plugin / "README.md", "notes\n")
    _write(root / "apps" / "desktop" / "secret.txt", "desktop")
    _write(root / "venv" / "bin" / "python", "venv")
    _write(root / "pyproject.toml", "[project]\nname='fixture'\n")
    return root


def _panel_resource_root(root: Path) -> Path:
    _write(root / "dist" / "index.html", "<html>nix</html>\n")
    _write(root / "dist" / "assets" / "app.js", "console.log(2)\n")
    _write(root / "dist" / "node_modules" / "left.js", "nope")
    _write(root / "node_modules" / "pkg" / "index.js", "nope")
    plugin = root / "plugins" / "browser-window-guard"
    _write(plugin / "__init__.py", "PLUGIN\n")
    _write(plugin / "live_task_store.py", "PLUGIN_STORE\n")
    _write(plugin / "plugin.yaml", "name: browser-window-guard\n")
    _write(plugin / "README.md", "notes\n")
    return root


def test_checkout_layout_is_explicit():
    from clawd_server.resources import LAYOUT_CHECKOUT, code_root, layout, panel_root

    assert layout() == LAYOUT_CHECKOUT
    assert code_root() == REPO
    assert panel_root() == REPO / "apps" / "panel"
    assert not str(code_root()).startswith("/usr/local/lib/hermes-agent")


def test_installed_layout_reads_bundle_not_repo_apps(tmp_path: Path):
    from clawd_server.resources import LAYOUT_INSTALLED, layout, panel_root, panel_static_dir, stage_distribution

    checkout = _fake_checkout(tmp_path / "src")
    package_dir = tmp_path / "site-packages" / "clawd_server"
    package_dir.mkdir(parents=True)
    dest = package_dir / "bundled"
    stage_distribution(checkout, dest)
    assert layout(package_dir) == LAYOUT_INSTALLED
    assert panel_root(package_dir) == dest / "apps" / "panel"
    assert panel_static_dir(package_dir) == dest / "apps" / "panel" / "dist"
    assert (panel_static_dir(package_dir) / "index.html").is_file()


def test_installed_missing_bundle_raises(tmp_path: Path):
    from clawd_server.resources import ResourceError, panel_static_dir

    package_dir = tmp_path / "site-packages" / "clawd_server"
    package_dir.mkdir(parents=True)
    with pytest.raises(ResourceError, match="不会在安装环境下载或构建前端"):
        panel_static_dir(package_dir)


def test_stage_copies_contract_and_skips_forbidden(tmp_path: Path):
    from clawd_server.resources import CONTRACT_VERSION, bundled_is_complete, stage_distribution

    checkout = _fake_checkout(tmp_path / "src")
    dest = tmp_path / "out"
    partial = tmp_path / "out.partial"
    partial.mkdir()
    (partial / "keep").write_text("keep", encoding="utf-8")
    stage_distribution(checkout, dest)
    assert bundled_is_complete(dest)
    assert (partial / "keep").read_text(encoding="utf-8") == "keep"
    assert not (tmp_path / "out.partial").joinpath("apps").exists()
    license_copy = (dest / "LICENSES" / "LICENSE").read_bytes()
    assert license_copy == (checkout / "LICENSE").read_bytes()
    names = [path.relative_to(dest).as_posix() for path in dest.rglob("*") if path.is_file()]
    assert "apps/panel/dist/index.html" in names
    assert "apps/panel/dist/assets/app.js" in names
    assert "apps/panel/plugins/browser-window-guard/plugin.yaml" in names
    blob = "\n".join(names)
    for marker in ("node_modules", "apps/desktop", "venv/"):
        assert marker not in blob
    contract = json.loads((dest / "RESOURCE_CONTRACT.json").read_text(encoding="utf-8"))
    assert contract["version"] == CONTRACT_VERSION == 2
    assert contract["license"] == "LICENSES/LICENSE"
    assert contract["hash"] == "sha256"
    assert "browser-window-guard" in contract["plugins"]
    assert contract["panel_core"] == "clawd_server/panel_core.py"
    assert "sidecar" not in json.dumps(contract)
    license_entry = next(item for item in contract["files"] if item["path"] == "LICENSES/LICENSE")
    assert license_entry["type"] == "file"
    assert license_entry["sha256"] == hashlib.sha256(license_copy).hexdigest()
    assert license_entry["size"] == len(license_copy)
    assert not any("node_modules" in item["path"] for item in contract["files"])


def test_unbuilt_panel_fails_and_keeps_previous_bundle(tmp_path: Path):
    from clawd_server.resources import ResourceError, stage_distribution

    checkout = _fake_checkout(tmp_path / "src")
    dest = tmp_path / "out"
    stage_distribution(checkout, dest)
    previous = (dest / "apps" / "panel" / "dist" / "index.html").read_text(encoding="utf-8")
    shutil_dist = checkout / "apps" / "panel" / "dist"
    for child in shutil_dist.iterdir():
        if child.is_dir():
            for nested in child.rglob("*"):
                if nested.is_file():
                    nested.unlink()
        elif child.is_file():
            child.unlink()
    with pytest.raises(ResourceError, match="不会下载或构建前端|不会自动构建"):
        stage_distribution(checkout, dest)
    assert (dest / "apps" / "panel" / "dist" / "index.html").read_text(encoding="utf-8") == previous


def test_filtered_tree_does_not_keep_preplaced_bundle(tmp_path: Path):
    from clawd_server.resources import ResourceError, stage_distribution

    checkout = _fake_checkout(tmp_path / "src")
    dest = tmp_path / "bundled"
    stage_distribution(checkout, dest)
    previous = (dest / "apps" / "panel" / "dist" / "index.html").read_text(encoding="utf-8")
    filtered = tmp_path / "filtered"
    filtered.mkdir()
    _write(filtered / "LICENSE", "MIT\n")
    with pytest.raises(ResourceError, match="不会使用忽略目录里预置的 bundled"):
        stage_distribution(filtered, dest)
    assert (dest / "apps" / "panel" / "dist" / "index.html").read_text(encoding="utf-8") == previous
    empty = tmp_path / "missing-bundle"
    with pytest.raises(ResourceError, match="不能发布缺少"):
        stage_distribution(filtered, empty)


def test_resource_root_supplies_panel_without_node_modules(tmp_path: Path, monkeypatch):
    from clawd_server.resources import PANEL_RESOURCE_ROOT_ENV, bundled_is_complete, stage_distribution

    filtered = tmp_path / "filtered"
    filtered.mkdir()
    _write(filtered / "LICENSE", "MIT License\n")
    resource = _panel_resource_root(tmp_path / "panel-resources")
    dest = tmp_path / "out"
    monkeypatch.delenv(PANEL_RESOURCE_ROOT_ENV, raising=False)
    stage_distribution(filtered, dest, resource_root=resource)
    assert bundled_is_complete(dest)
    names = [path.relative_to(dest).as_posix() for path in dest.rglob("*") if path.is_file()]
    assert "apps/panel/dist/index.html" in names
    assert "node_modules" not in "\n".join(names)
    assert (dest / "LICENSES" / "LICENSE").read_text(encoding="utf-8") == "MIT License\n"


def test_env_resource_root_copies_without_embedding_absolute_path(tmp_path: Path, monkeypatch):
    from clawd_server.resources import PANEL_RESOURCE_ROOT_ENV, bundled_is_complete, stage_distribution

    filtered = tmp_path / "filtered"
    filtered.mkdir()
    _write(filtered / "LICENSE", "MIT License\n")
    resource = _panel_resource_root(tmp_path / "panel-resources")
    dest = tmp_path / "out"
    monkeypatch.setenv(PANEL_RESOURCE_ROOT_ENV, str(resource))
    stage_distribution(filtered, dest)
    assert bundled_is_complete(dest)
    contract = (dest / "RESOURCE_CONTRACT.json").read_text(encoding="utf-8")
    assert str(resource) not in contract
    assert "apps/panel/dist/index.html" in contract
    names = [path.relative_to(dest).as_posix() for path in dest.rglob("*") if path.is_file()]
    assert "node_modules" not in "\n".join(names)


def test_symlink_escape_is_refused(tmp_path: Path):
    from clawd_server.resources import ResourceError, stage_distribution

    checkout = _fake_checkout(tmp_path / "src")
    outside = tmp_path / "outside.js"
    outside.write_text("LEAK", encoding="utf-8")
    link = checkout / "apps" / "panel" / "dist" / "assets" / "evil.js"
    link.symlink_to(outside)
    dest = tmp_path / "out"
    with pytest.raises(ResourceError, match="symlink"):
        stage_distribution(checkout, dest)
    assert not dest.exists()


def test_symlink_dest_is_refused(tmp_path: Path):
    from clawd_server.resources import ResourceError, stage_distribution

    checkout = _fake_checkout(tmp_path / "src")
    outside = tmp_path / "outside-dest"
    outside.mkdir()
    (outside / "keep").write_text("keep", encoding="utf-8")
    dest = tmp_path / "out"
    dest.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ResourceError, match="symlink"):
        stage_distribution(checkout, dest)
    assert (outside / "keep").read_text(encoding="utf-8") == "keep"
    assert dest.is_symlink()


def test_unknown_dest_is_not_deleted_even_with_replace(tmp_path: Path):
    from clawd_server.resources import ResourceError, stage_distribution

    checkout = _fake_checkout(tmp_path / "src")
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / "keep").write_text("keep", encoding="utf-8")
    with pytest.raises(ResourceError, match="未知数据"):
        stage_distribution(checkout, dest, replace=True)
    assert (dest / "keep").read_text(encoding="utf-8") == "keep"


def test_contention_is_rejected_without_deleting_lock(tmp_path: Path):
    import fcntl

    from clawd_server.resources import ResourceError, stage_distribution

    checkout = _fake_checkout(tmp_path / "src")
    dest = tmp_path / "out"
    lock = dest.parent / f".{dest.name}.stage.lock"
    fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
    os.write(fd, b"held")
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with pytest.raises(ResourceError, match="并发"):
            stage_distribution(checkout, dest)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    assert not dest.exists()
    assert lock.read_bytes() == b"held"


def test_canonical_replace_retains_previous_and_restores_on_failure(tmp_path: Path):
    from clawd_server.resources import ResourceError, bundled_is_complete, stage_distribution

    checkout = _fake_checkout(tmp_path / "src")
    (checkout / "clawd_server").mkdir()
    partial = checkout / "clawd_server" / "bundled.partial"
    partial.mkdir()
    (partial / "keep").write_text("keep", encoding="utf-8")
    dest = stage_distribution(checkout)
    previous = (dest / "apps" / "panel" / "dist" / "index.html").read_text(encoding="utf-8")
    (checkout / "apps" / "panel" / "dist" / "index.html").write_text("<html>new</html>\n")
    again = stage_distribution(checkout)
    assert again == dest
    assert (dest / "apps" / "panel" / "dist" / "index.html").read_text(encoding="utf-8") == "<html>new</html>\n"
    kept = list((checkout / "clawd_server").glob(".bundled.kept-*"))
    assert len(kept) == 1
    assert (kept[0] / "apps" / "panel" / "dist" / "index.html").read_text(encoding="utf-8") == previous
    assert (partial / "keep").read_text(encoding="utf-8") == "keep"

    def boom(_staging, _dest, _backup):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        stage_distribution(checkout, _before_publish=boom)
    assert bundled_is_complete(dest)
    assert (dest / "apps" / "panel" / "dist" / "index.html").read_text(encoding="utf-8") == "<html>new</html>\n"
    assert not list((checkout / "clawd_server").glob(".bundled.stage-*"))
    assert not isinstance(boom, ResourceError)


def test_tampered_or_residual_bundle_is_not_complete(tmp_path: Path):
    from clawd_server.resources import ResourceError, bundled_is_complete, stage_distribution

    checkout = _fake_checkout(tmp_path / "src")
    (checkout / "clawd_server").mkdir()
    dest = stage_distribution(checkout)
    index = dest / "apps" / "panel" / "dist" / "index.html"
    index.write_text("tampered", encoding="utf-8")
    assert not bundled_is_complete(dest)
    with pytest.raises(ResourceError, match="未知数据"):
        stage_distribution(checkout)
    assert index.read_text(encoding="utf-8") == "tampered"

    checkout2 = _fake_checkout(tmp_path / "src2")
    fresh = tmp_path / "fresh"
    stage_distribution(checkout2, fresh)
    (fresh / "apps" / "panel" / "dist" / "extra.txt").write_text("residual", encoding="utf-8")
    assert not bundled_is_complete(fresh)
    contract = json.loads((fresh / "RESOURCE_CONTRACT.json").read_text(encoding="utf-8"))
    contract["version"] = 1
    (fresh / "RESOURCE_CONTRACT.json").write_text(json.dumps(contract), encoding="utf-8")
    assert not bundled_is_complete(fresh)


def test_production_code_root_is_refused():
    from clawd_server.resources import ResourceError, code_root, under_production

    banned = Path("/usr/local/lib/hermes-agent/site-packages/clawd_server")
    assert under_production(banned)
    with pytest.raises(ResourceError, match="production"):
        code_root(banned)


def test_pyproject_and_manifest_contract():
    data = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    include = data["tool"]["setuptools"]["packages"]["find"]["include"]
    assert "apps/panel" not in include
    assert "apps/desktop" not in include
    package_data = data["tool"]["setuptools"]["package-data"]["clawd_server"]
    assert any("bundled/apps/panel/dist" in item for item in package_data)
    assert not any("node_modules" in item or "apps/desktop" in item for item in package_data)
    assert data["build-system"]["build-backend"] == "clawd_server.packaging_backend"
    ignores = data["tool"]["ruff"]["lint"]["per-file-ignores"]
    assert "apps/desktop/electron/fixtures/**" not in ignores
    manifest = (REPO / "MANIFEST.in").read_text(encoding="utf-8")
    assert "prune apps/desktop" in manifest
    assert "global-exclude */node_modules/*" in manifest
    assert "recursive-include apps/panel" not in manifest
    assert "apps/desktop" not in data["project"]["scripts"]


def test_builders_do_not_invoke_frontend_or_install():
    needles = ("npm ", "npx ", "pip install", "urllib.request", "subprocess")
    for relative in (
        "clawd_server/resources.py",
        "clawd_server/packaging_backend.py",
        "scripts/build_clawd_resources.py",
    ):
        text = (REPO / relative).read_text(encoding="utf-8")
        for needle in needles:
            assert needle not in text, (relative, needle)
        assert "shutil" not in text
        assert "rmtree" not in text
    backend = (REPO / "clawd_server/packaging_backend.py").read_text(encoding="utf-8")
    wheel = backend.split("def build_wheel", 1)[1].split("def build_sdist", 1)[0]
    editable = backend.split("def build_editable", 1)[1]
    assert "_stage()" in wheel
    assert "_stage()" not in editable


def test_nix_wires_panel_resource_root_at_wheel_build_only():
    lib_nix = (REPO / "nix/lib.nix").read_text(encoding="utf-8")
    panel_nix = (REPO / "nix/panel-resources.nix").read_text(encoding="utf-8")
    python_nix = (REPO / "nix/python.nix").read_text(encoding="utf-8")
    agent_nix = (REPO / "nix/hermes-agent.nix").read_text(encoding="utf-8")
    doc = (REPO / "docs/clawd/distribution-implementation.md").read_text(encoding="utf-8")
    ignore = (REPO / ".gitignore").read_text(encoding="utf-8")
    assert "inherit pythonSrc nodejs panelResources;" in lib_nix
    assert 'relPath == "clawd_server/bundled"' in lib_nix
    assert "node_modules" in lib_nix
    assert "importNpmLock.importNpmLock" in panel_nix
    assert "hermes_panel_sidecar.py" not in panel_nix
    assert "browser-window-guard/plugin.yaml" in panel_nix
    assert "clawd_server/bundled" not in panel_nix
    assert "npm_config_offline" in panel_nix
    assert "panelResourceRoot ? null" in python_nix
    assert "CLAWD_PANEL_RESOURCE_ROOT = panelResourceRoot" in python_nix
    assert "workspaceRoot = pythonSrc;" in python_nix
    assert "workspaceRoot = panelResourceRoot" not in python_nix
    assert "panelResourceRoot = hermesNpmLib.panelResources;" in agent_nix
    assert agent_nix.count("env = {") == 1
    env_block = agent_nix.split("env = {", 1)[1].split("};", 1)[0]
    assert "CLAWD_PANEL_RESOURCE_ROOT" not in env_block
    assert "CLAWD_PANEL_RESOURCE_ROOT =" not in agent_nix
    assert "--set CLAWD_PANEL_RESOURCE_ROOT" not in agent_nix
    for rule in (
        "/clawd_server/.bundled.kept-*/",
        "/clawd_server/.bundled.stage-*/",
        "/clawd_server/.bundled.stage.lock",
        "/clawd_server/.bundled.rejected-*/",
        "/clawd_server/bundled/",
    ):
        assert rule in ignore
    assert "panelResourceRoot ? null" in doc
    assert "CLAWD_PANEL_RESOURCE_ROOT = panelResourceRoot" in doc
    assert "hermesNpmLib.panelResources" in doc
    assert "未验证" in doc


def test_cli_entry_refuses_missing_and_production_home(monkeypatch, capsys):
    from clawd_server.cli_entry import _drop_production_bin, main

    monkeypatch.delenv("CLAWD_HOME", raising=False)
    monkeypatch.delenv("HERMES_HOME", raising=False)
    assert main([]) == 2
    assert "CLAWD_HOME" in capsys.readouterr().err

    monkeypatch.setenv("CLAWD_HOME", "/root/.hermes")
    monkeypatch.delenv("HERMES_HOME", raising=False)
    before = Path("/root/.hermes")
    existed = before.exists()
    assert main([]) == 2
    assert "production" in capsys.readouterr().err.lower()
    assert before.exists() == existed

    monkeypatch.setenv("HERMES_BIN", "/usr/local/bin/hermes")
    _drop_production_bin()
    assert "HERMES_BIN" not in os.environ


def test_serve_static_uses_layout_path_without_building():
    from clawd_server.__main__ import resolve_serve_static
    from clawd_server.resources import panel_static_dir

    assert resolve_serve_static("/tmp/explicit-static") == "/tmp/explicit-static"
    assert resolve_serve_static(None) == str(panel_static_dir(require_built=False))
