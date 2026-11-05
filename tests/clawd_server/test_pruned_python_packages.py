"""Distribution boundary for official UI, not bundled web search.

Does not build the project wheel, does not install, and does not delete
``build/`` or ``*.egg-info``. The miniature archives below are fixtures.
Search providers under ``plugins/web`` must remain. Official ``web/``,
``apps/desktop``, and ``hermes_cli/web_dist`` must not.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import tarfile
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _checker():
    path = REPO / "tests/clawd_server/test_distribution_package_manifest.py"
    spec = importlib.util.spec_from_file_location("clawd_manifest_checker_for_prune", path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load archive checker: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tar_bytes(files: dict[str, bytes], prefix: str = "probe-0.0.0/") -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, data in files.items():
            info = tarfile.TarInfo(prefix + name)
            info.size = len(data)
            info.mtime = 0
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def test_declarations_keep_search_plugins_and_prune_official_ui():
    import tomllib

    from clawd_server.resources import (
        distribution_path_is_pruned,
        merge_pruned_exclude_package_data,
        pruned_package_excludes,
    )

    parsed = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    found = parsed["tool"]["setuptools"]["packages"]["find"]
    assert "namespaces" not in found
    assert "plugins" in found["include"]
    assert "plugins.*" in found["include"]
    assert "tui_gateway" in found["include"]
    assert "clawd_server" in found["include"]
    assert found["exclude"] == list(pruned_package_excludes())
    assert "plugins.web" not in found["exclude"]
    assert "plugins.web.*" not in found["exclude"]
    assert "plugins" not in found["exclude"]
    assert "plugins.*" not in found["exclude"]
    excluded = parsed["tool"]["setuptools"]["exclude-package-data"]
    assert "plugins" not in excluded
    assert excluded["hermes_cli"] == ["web_dist/*"]
    merged = merge_pruned_exclude_package_data(None)
    assert "web/*" not in merged.get("plugins", [])
    assert merged["hermes_cli"] == ["web_dist/*"]
    tools = parsed["tool"]["setuptools"]["package-data"]["tools"]
    assert "vendor/browser_harness_0_1_13/LICENSE" in tools
    assert "bot_desktop/launcher.sh" in tools
    plugins_data = parsed["tool"]["setuptools"]["package-data"]["plugins"]
    assert "**/plugin.yaml" in plugins_data
    manifest = (REPO / "MANIFEST.in").read_text(encoding="utf-8").splitlines()
    assert "prune web" in manifest
    assert "prune plugins/web" not in manifest
    assert "prune apps/desktop" in manifest
    assert "prune hermes_cli/web_dist" in manifest
    assert "prune plugins" not in manifest
    nix = (REPO / "nix/lib.nix").read_text(encoding="utf-8")
    assert 'builtins.elem "web" components' not in nix
    assert 'relPath == "web" || lib.hasPrefix "web/" relPath' in nix
    assert 'lib.hasPrefix "hermes_cli/web_dist/" relPath' in nix
    assert 'lib.hasPrefix "apps/desktop/" relPath' in nix
    assert 'topComponent == "build"' in nix
    flake = (REPO / "flake.nix").read_text(encoding="utf-8")
    assert "plugins/web search providers stay" in flake
    setup = (REPO / "setup.py").read_text(encoding="utf-8")
    assert "apply_distribution_boundary" in setup
    assert "make_pruned_build_py" in setup
    assert "release_distribution_boundary" in setup
    backend = (REPO / "clawd_server/packaging_backend.py").read_text(encoding="utf-8")
    assert "begin_distribution_boundary_env" in backend
    assert "end_distribution_boundary_env" in backend
    assert "does not exclude ``plugins.web``" in backend
    for klass in ("_GuardedSdist", "_GuardedBdistWheel"):
        body = setup.split(f"class {klass}", 1)[1].split("\nclass ", 1)[0]
        assert body.index("stage_distribution") < body.index("super().run(")
    assert not distribution_path_is_pruned("plugins/web/brave_free/provider.py")
    assert not distribution_path_is_pruned("plugins/web/ddgs/_search_worker.py")
    assert not distribution_path_is_pruned("hermes_cli/web_routers/app.py")
    assert not distribution_path_is_pruned("tools/bot_desktop/launcher.sh")
    assert distribution_path_is_pruned("web/index.html")
    assert distribution_path_is_pruned("hermes_cli/web_dist/index.html")
    assert distribution_path_is_pruned("apps/desktop/package.json")
    for relative in (
        "plugins/web/brave_free/__init__.py",
        "plugins/web/brave_free/provider.py",
        "plugins/web/brave_free/plugin.yaml",
        "plugins/web/ddgs/__init__.py",
        "plugins/web/ddgs/provider.py",
        "plugins/web/ddgs/plugin.yaml",
        "plugins/web/ddgs/_search_worker.py",
    ):
        assert (REPO / relative).is_file(), relative


def test_prune_web_does_not_match_search_plugins():
    from setuptools.command.egg_info import FileList

    listed = FileList()
    listed.files = [
        "plugins/web/brave_free/provider.py",
        "plugins/web/ddgs/plugin.yaml",
        "plugins/web/ddgs/_search_worker.py",
        "plugins/keep/__init__.py",
        "plugins/platforms/discord/adapter.py",
        "web/index.html",
        "apps/desktop/package.json",
        "hermes_cli/web_dist/index.html",
        "hermes_cli/web_routers/app.py",
        "tools/bot_desktop/launcher.sh",
        "tools/vendor/browser_harness_0_1_13/LICENSE",
    ]
    listed.prune("web")
    assert "web/index.html" not in listed.files
    assert "plugins/web/brave_free/provider.py" in listed.files
    assert "plugins/web/ddgs/plugin.yaml" in listed.files
    assert "plugins/web/ddgs/_search_worker.py" in listed.files
    assert "plugins/keep/__init__.py" in listed.files
    listed.prune("apps/desktop")
    listed.prune("hermes_cli/web_dist")
    assert "apps/desktop/package.json" not in listed.files
    assert "hermes_cli/web_dist/index.html" not in listed.files
    assert "plugins/web/brave_free/provider.py" in listed.files
    assert "plugins/keep/__init__.py" in listed.files
    assert "plugins/platforms/discord/adapter.py" in listed.files
    assert "hermes_cli/web_routers/app.py" in listed.files
    assert "tools/bot_desktop/launcher.sh" in listed.files
    assert "tools/vendor/browser_harness_0_1_13/LICENSE" in listed.files


def test_finalized_repo_build_base_is_refused_and_not_deleted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    stale = tmp_path / "build" / "lib" / "plugins" / "web" / "__init__.py"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"STALE")
    sources = tmp_path / "probe.egg-info" / "SOURCES.txt"
    sources.parent.mkdir()
    sources.write_text("plugins/web/__init__.py\n", encoding="utf-8")

    from setuptools import Distribution

    from clawd_server.resources import ResourceError, apply_distribution_boundary

    dist = Distribution({"name": "probe", "version": "0.0.0", "packages": ["plugins", "plugins.web"]})
    build = dist.get_command_obj("build")
    build.ensure_finalized()
    with pytest.raises(ResourceError, match="build"):
        apply_distribution_boundary(dist)
    assert stale.read_bytes() == b"STALE"
    assert "plugins/web/__init__.py" in sources.read_text(encoding="utf-8")
    assert "plugins.web" in dist.packages


def test_stale_sources_and_build_lib_do_not_reenter_either_archive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    pytest.importorskip("setuptools.command.bdist_wheel")
    from setuptools import Distribution
    from setuptools.command.bdist_wheel import bdist_wheel
    from setuptools.command.sdist import sdist

    from clawd_server.resources import (
        apply_distribution_boundary,
        make_pruned_build_py,
        release_distribution_boundary,
    )

    project = tmp_path / "proj"
    (project / "plugins" / "web").mkdir(parents=True)
    (project / "plugins" / "keep").mkdir()
    (project / "plugins" / "__init__.py").write_text("", encoding="utf-8")
    (project / "plugins" / "web" / "__init__.py").write_text("LEAKED\n", encoding="utf-8")
    (project / "plugins" / "web" / "plugin.yaml").write_text("name: web\n", encoding="utf-8")
    (project / "web").mkdir()
    (project / "web" / "index.html").write_text("OFFICIAL\n", encoding="utf-8")
    (project / "apps" / "desktop").mkdir(parents=True)
    (project / "apps" / "desktop" / "package.json").write_text("{}\n", encoding="utf-8")
    (project / "plugins" / "keep" / "__init__.py").write_text("KEEPME\n", encoding="utf-8")
    (project / "plugins" / "keep" / "plugin.yaml").write_text("name: keep\n", encoding="utf-8")
    (project / "stale-cache-marker.txt").write_text("STALE-MARKER\n", encoding="utf-8")
    stale = project / "build" / "lib" / "plugins" / "web" / "__init__.py"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"STALE")
    egg = project / "boundaryprobe.egg-info"
    egg.mkdir()
    stale_sources = "plugins/web/__init__.py\nplugins/keep/__init__.py\nstale-cache-marker.txt\n"
    (egg / "SOURCES.txt").write_text(stale_sources, encoding="utf-8")
    (project / "MANIFEST.in").write_text(
        "recursive-include plugins *\nprune web\nprune apps/desktop\nprune hermes_cli/web_dist\n",
        encoding="utf-8",
    )
    out = tmp_path / "out"
    out.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setenv("TMPDIR", str(tmp_path / "stage-parent"))
    (tmp_path / "stage-parent").mkdir()

    class _Sdist(sdist):
        def finalize_options(self):
            apply_distribution_boundary(self.distribution)
            super().finalize_options()

        def run(self):
            try:
                super().run()
            finally:
                release_distribution_boundary(self.distribution)

    class _Wheel(bdist_wheel):
        def finalize_options(self):
            apply_distribution_boundary(self.distribution)
            super().finalize_options()

        def run(self):
            try:
                super().run()
            finally:
                release_distribution_boundary(self.distribution)

    def _dist():
        dist = Distribution(
            {
                "name": "boundaryprobe",
                "version": "0.0.0",
                "packages": ["plugins", "plugins.web", "plugins.keep"],
                "package_data": {"plugins": ["**/plugin.yaml"]},
                "include_package_data": True,
            }
        )
        dist.script_name = "setup.py"
        dist.cmdclass = {
            "sdist": _Sdist,
            "bdist_wheel": _Wheel,
            "build_py": make_pruned_build_py(),
        }
        return dist

    sdist_cmd = _dist().get_command_obj("sdist")
    sdist_cmd.dist_dir = str(out)
    sdist_cmd.formats = ["gztar"]
    sdist_cmd.ensure_finalized()
    sdist_cmd.run()
    wheel_cmd = _dist().get_command_obj("bdist_wheel")
    wheel_cmd.dist_dir = str(out)
    wheel_cmd.ensure_finalized()
    wheel_cmd.run()

    wheels = list(out.glob("*.whl"))
    sdists = list(out.glob("*.tar.gz"))
    assert len(wheels) == 1
    assert len(sdists) == 1
    with zipfile.ZipFile(wheels[0]) as archive:
        wheel_names = [item.replace("\\", "/") for item in archive.namelist()]
        wheel_bytes = b"".join(archive.read(name) for name in archive.namelist())
    with tarfile.open(sdists[0], "r:*") as archive:
        sdist_names = [item.name.replace("\\", "/") for item in archive.getmembers() if item.isfile()]
        sdist_bytes = b"".join(archive.extractfile(item).read() for item in archive.getmembers() if item.isfile() and archive.extractfile(item))
    for names in (wheel_names, sdist_names):
        assert any(name.endswith("plugins/web/__init__.py") for name in names)
        assert any("plugins/keep/__init__.py" in name for name in names)
        assert not any(name.endswith("web/index.html") or name.endswith("apps/desktop/package.json") for name in names)
        assert not any(name.endswith("stale-cache-marker.txt") for name in names)
    assert b"KEEPME" in wheel_bytes
    assert b"LEAKED" in wheel_bytes
    assert b"name: web" in wheel_bytes
    assert b"STALE" not in wheel_bytes
    assert b"OFFICIAL" not in sdist_bytes
    assert b"STALE-MARKER" not in sdist_bytes
    assert stale.read_bytes() == b"STALE"
    assert (egg / "SOURCES.txt").read_text(encoding="utf-8") == stale_sources
    assert (project / "build" / "lib" / "plugins" / "web").is_dir()


def test_both_archives_check_hash_required_members_and_forbidden_web(tmp_path: Path):
    checker = _checker()
    clean = checker._fixture_tree()
    wheel = tmp_path / "clean.whl"
    wheel.write_bytes(checker._zip_bytes(clean))
    report = checker.compare_distribution_archive(wheel)
    for relative in checker.REQUIRED_RELATIVE:
        assert relative in report["contract_files"]
    contract = clean[checker.CONTRACT_NAME]
    listed = __import__("json").loads(contract.decode("utf-8"))["files"]
    sample = next(item for item in listed if item["path"] == "apps/panel/dist/index.html")
    assert sample["sha256"] == hashlib.sha256(clean["apps/panel/dist/index.html"]).hexdigest()
    assert sample["size"] == len(clean["apps/panel/dist/index.html"])

    sdist_files = {checker.BUNDLE_MARKER + name: data for name, data in clean.items()}
    sdist_files["LICENSE"] = b"MIT\n"
    sdist = tmp_path / "clean.tar.gz"
    sdist.write_bytes(_tar_bytes(sdist_files))
    sdist_report = checker.compare_distribution_archive(sdist)
    assert "apps/panel/dist/index.html" in sdist_report["contract_files"]

    leaked = dict(clean)
    buffer = io.BytesIO(checker._zip_bytes(leaked))
    with zipfile.ZipFile(buffer, "a") as archive:
        archive.writestr("web/index.html", b"OFFICIAL\n")
        archive.writestr("hermes_cli/web_dist/index.html", b"OFFICIAL\n")
        archive.writestr("plugins/web/brave_free/provider.py", b"SEARCH\n")
        archive.writestr("plugins/web/ddgs/provider.py", b"SEARCH\n")
    dirty = tmp_path / "dirty.whl"
    dirty.write_bytes(buffer.getvalue())
    with pytest.raises(checker.ArchiveMismatch, match="forbidden pruned tree"):
        checker.compare_distribution_archive(dirty)

    dirty_sdist = dict(sdist_files)
    dirty_sdist["web/index.html"] = b"OFFICIAL\n"
    dirty_sdist["clawd_server/bundled/apps/desktop/secret.txt"] = b"no\n"
    dirty_sdist["plugins/web/ddgs/_search_worker.py"] = b"SEARCH\n"
    dirty_tar = tmp_path / "dirty.tar.gz"
    dirty_tar.write_bytes(_tar_bytes(dirty_sdist))
    with pytest.raises(checker.ArchiveMismatch, match="forbidden pruned tree"):
        checker.compare_distribution_archive(dirty_tar)

    mismatched = dict(clean)
    mismatched["apps/panel/dist/index.html"] = b"<html>changed</html>\n"
    bad_hash = tmp_path / "bad-hash.whl"
    bad_hash.write_bytes(checker._zip_bytes(mismatched))
    with pytest.raises(checker.ArchiveMismatch, match="hash/size mismatch"):
        checker.compare_distribution_archive(bad_hash)

    omitted = dict(clean)
    payload = __import__("json").loads(omitted[checker.CONTRACT_NAME].decode("utf-8"))
    payload["files"] = [item for item in payload["files"] if item["path"] != "apps/panel/dist/index.html"]
    omitted[checker.CONTRACT_NAME] = __import__("json").dumps(payload, sort_keys=True).encode("utf-8")
    omitted.pop("apps/panel/dist/index.html")
    missing = tmp_path / "missing-required.whl"
    missing.write_bytes(checker._zip_bytes(omitted))
    with pytest.raises(checker.ArchiveMismatch, match="contract omits required"):
        checker.compare_distribution_archive(missing)
